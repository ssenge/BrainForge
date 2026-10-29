"""Scheduling without a server: `tick` runs whatever is due; cron or GitHub Actions just call it."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cronsim import CronSim

from brainforge import agents, gitops, procedures, rounds
from brainforge.brain import Brain, BrainError, data_text
from brainforge.procedures import Procedure

# ponytail: a never-run procedure only fires within a day of its slot, so installing a weekly
# schedule on Monday doesn't produce a report on Monday. Raise if catch-up after long outages matters.
FIRST_RUN_GRACE = timedelta(days=1)


def last_slot(proc: Procedure, now: datetime) -> datetime:
    assert proc.schedule is not None
    return next(CronSim(proc.schedule.cron, now.astimezone(proc.tz()), reverse=True))


def is_due(proc: Procedure, last_run: datetime | None, now: datetime) -> bool:
    """Due when the most recent scheduled slot has no successful run after it.

    The state is the latest report in git, so delayed or repeated triggers are harmless.
    """
    slot = last_slot(proc, now)
    if last_run is None:
        return now - slot <= FIRST_RUN_GRACE
    return last_run < slot


def tick(brain: Brain, *, only: str | None = None, now: datetime | None = None, commit: bool = True) -> list[dict]:
    now = now or datetime.now(UTC)
    procs, _ = procedures.load_all(brain)
    results = []
    for proc in procs:
        if proc.schedule is None or (only and proc.name != only):
            continue
        if not is_due(proc, procedures.last_run(brain, proc), now):
            results.append({"procedure": proc.name, "status": "not-due"})
            continue
        try:
            cfg = agents.resolve(brain, proc.agent)
            result = procedures.run(brain, proc, cfg, commit=commit, now=now)
            results.append({**result, "status": "ran"})
        except (procedures.ProcedureError, agents.AgentError, BrainError) as exc:
            results.append({"procedure": proc.name, "status": "failed", "message": str(exc)})
    # Round pages: a new one when a round opens, the report link once its report is written.
    pages = rounds.write_pages(brain, [r for r in rounds.all_latest(brain, now) if not only or r.proc.name == only])
    if pages:
        sha = gitops.commit(brain.root, f"rounds: {len(pages)} page(s)", pages) if commit else None
        results += [{"procedure": page.split("/")[2], "status": "round page", "page": page, "commit": sha}
                    for page in pages]
    return results


def cron_line(brain: Brain) -> str:
    exe = shutil.which("brainforge") or sys.argv[0]
    root = shlex.quote(str(brain.root))
    log = shlex.quote(str(brain.root / ".brainforge" / "cron.log"))
    path = shlex.quote(os.environ.get("PATH", "/usr/bin:/bin"))
    return f"*/15 * * * * cd {root} && PATH={path} {shlex.quote(exe)} schedule tick >> {log} 2>&1  # brainforge:{brain.root}"


def install_cron(brain: Brain) -> str:
    """Add (or replace) one crontab entry for this brain that calls `schedule tick` every 15 minutes."""
    line = cron_line(brain)
    (brain.root / ".brainforge").mkdir(exist_ok=True)
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [ln for ln in (current.stdout if current.returncode == 0 else "").splitlines()
             if f"# brainforge:{brain.root}" not in ln]
    proc = subprocess.run(["crontab", "-"], input="\n".join([*lines, line]) + "\n", capture_output=True, text=True)
    if proc.returncode != 0:
        raise BrainError(f"crontab update failed: {proc.stderr.strip()}")
    return line


# --- GitHub Actions ----------------------------------------------------------------------------

AGENT_INSTALL = {
    "claude": "npm install -g @anthropic-ai/claude-code",
    "codex": "npm install -g @openai/codex",
    "command": "true  # install your agent command here",
}


def _workflow(template: str, brain: Brain, package: str, **extra: str) -> str:
    text = data_text(f"workflows/{template}")
    values = {"PACKAGE": package, "AGENT_INSTALL": AGENT_INSTALL[brain.manifest.agent.adapter], **extra}
    for key, value in values.items():
        text = text.replace(f"__{key}__", value)
    return text


def write_github_procedures(brain: Brain, package: str = "brainforge") -> Path:
    """Generate the procedures workflow from procedure frontmatter (the single source of truth)."""
    procs, issues = procedures.load_all(brain)
    if issues:
        raise BrainError("fix procedure errors first: " + "; ".join(str(i) for i in issues))
    schedules = [
        f"    - cron: \"{cron}\"\n      timezone: \"{p.schedule.timezone}\"  # {p.name}{what}"
        for p in procs if p.schedule
        for cron, what in [(p.schedule.cron, ""), *([(p.round.opens, ": round opens")] if p.round else [])]
    ]
    on_schedule = "  schedule:\n" + "\n".join(schedules) + "\n" if schedules else ""
    needs_pandoc = any(p.output.format != "markdown" for p in procs)
    pandoc = "sudo apt-get update -q && sudo apt-get install -y -q pandoc" if needs_pandoc else "true"
    text = _workflow("procedures.yml", brain, package, SCHEDULE=on_schedule, PANDOC=pandoc)
    out = brain.root / ".github" / "workflows" / "brainforge-procedures.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def write_github_rounds(brain: Brain, package: str = "brainforge") -> Path | None:
    """Round issues and replies on them; only for brains with a round procedure."""
    if not any(p.round for p in procedures.load_all(brain)[0]):
        return None
    out = brain.root / ".github" / "workflows" / "brainforge-rounds.yml"
    out.write_text(_workflow("rounds.yml", brain, package), encoding="utf-8")
    return out


def write_github_ingest(brain: Brain, package: str = "brainforge") -> Path:
    out = brain.root / ".github" / "workflows" / "brainforge-ingest.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_workflow("ingest.yml", brain, package), encoding="utf-8")
    return out


def write_github_approvals(brain: Brain, package: str = "brainforge", remind: str = "0 7 * * 1-5") -> Path:
    out = brain.root / ".github" / "workflows" / "brainforge-approvals.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_workflow("approvals.yml", brain, package, REMIND=remind), encoding="utf-8")
    return out
