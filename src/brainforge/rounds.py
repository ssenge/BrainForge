"""Rounds: asynchronous status meetings. People send updates while a round is open; a report closes it.

- A procedure with `round:` opens one on its `opens` cron; its next scheduled run closes the round and its
  report reads the updates. Rounds of different procedures can be open at the same time.
- An update is anything a participant sends while their round is open: a note, a file in the inbox, a reply
  on the round's GitHub issue (that one counts only for its issue's round). No update is fine: the report says so.
- Nothing is stored for a round. Ingestion records on each source who sent it, when, and which rounds it
  counts for (`brainforge.from`, `sent`, `rounds`); everything else follows from the crons.
- The round page (`reports/rounds/<procedure>/<date>.md`) shows who has sent what. GitHub renders it with the
  repository's access control; it changes with every update.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from cronsim import CronSim

from brainforge import approval, briefing, okf, procedures
from brainforge.brain import Brain, BrainError, TeamMember

LABEL = "brainforge-round"
MARKER_RE = re.compile(r"<!-- brainforge-round: (\S+) -->")


@dataclass
class Round:
    proc: procedures.Procedure
    opened: datetime  # in the procedure's timezone
    closes: datetime
    members: list[TeamMember]

    @property
    def id(self) -> str:
        return f"{self.proc.name}/{self.opened:%Y-%m-%d}"

    @property
    def page(self) -> str:
        return f"reports/rounds/{self.id}.md"

    @property
    def tz(self) -> tzinfo:
        return self.proc.tz()

    def open_at(self, at: datetime) -> bool:
        return self.opened <= at < self.closes


@dataclass
class Update:
    sender: str
    sent: datetime
    rel: str  # the source concept
    title: str
    description: str


def latest(brain: Brain, proc: procedures.Procedure, at: datetime) -> Round | None:
    """The procedure's round opened most recently at or before `at`, open or closed."""
    if not (proc.round and proc.schedule):
        return None
    # cronsim looks strictly before or after its start: one second later counts a round opening right at `at`
    opened = next(CronSim(proc.round.opens, at.astimezone(proc.tz()) + timedelta(seconds=1), reverse=True))
    closes = next(CronSim(proc.schedule.cron, opened))
    members = [approval.member(brain, n) for n in proc.round.members] or list(brain.manifest.team)
    return Round(proc, opened, closes, members)


def all_latest(brain: Brain, at: datetime | None = None) -> list[Round]:
    """Each round procedure's latest round. A procedure naming unknown people is left out (brain validate says so)."""
    found = []
    for proc in procedures.load_all(brain)[0]:
        try:
            if r := latest(brain, proc, at or datetime.now(UTC)):
                found.append(r)
        except BrainError:
            continue
    return found


def counting(brain: Brain, sender: str, sent: datetime, round_id: str | None = None) -> list[Round]:
    """The rounds an update counts for: open when it was sent, with the sender taking part."""
    return [r for r in all_latest(brain, sent) if r.open_at(sent) and any(m.name == sender for m in r.members)
            and round_id in (None, r.id)]


def _datetime(value: Any) -> datetime:
    at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def updates(brain: Brain, r: Round) -> list[Update]:
    found = []
    for path in sorted((brain.knowledge / "sources").glob("*.md")):
        try:
            c = okf.read(path)
        except okf.OKFError:
            continue
        bf = c.meta.get("brainforge") or {}
        if r.id in (bf.get("rounds") or []):
            found.append(Update(str(bf.get("from")), _datetime(bf.get("sent")), brain.rel(path), c.title,
                                c.description or ""))
    return sorted(found, key=lambda u: u.sent)


def missing(brain: Brain, r: Round) -> list[TeamMember]:
    sent = {u.sender for u in updates(brain, r)}
    return [m for m in r.members if m.name not in sent]


def report_for(brain: Brain, round_id: str) -> Path | None:
    """The report that closed this round, if it was written."""
    for proc in procedures.load_all(brain)[0]:
        if proc.name != round_id.split("/")[0]:
            continue
        for _, path in procedures.reports(brain, proc):
            meta, _ = okf.split_frontmatter(path.read_text(encoding="utf-8"))
            if ((meta or {}).get("round") or {}).get("id") == round_id:
                return path
    return None


def _when(at: datetime, tz: tzinfo) -> str:
    return f"{at.astimezone(tz):%a %Y-%m-%d %H:%M}"


# --- views ---------------------------------------------------------------------------------------

def page(brain: Brain, r: Round) -> str:
    """The round as markdown: who sent what, who hasn't. Depends only on the brain, so rewriting it is a no-op."""
    here = (brain.root / r.page).parent
    report = report_for(brain, r.id)
    ups = updates(brain, r)
    lines = [f"# Round {r.id}", "",
             f"Updates for the `{r.proc.name}` report, open from {_when(r.opened, r.tz)} until "
             f"{_when(r.closes, r.tz)} ({r.proc.schedule.timezone if r.proc.schedule else 'UTC'}).",
             *([f"Report: [{brain.rel(report)}]({os.path.relpath(report, here)})"] if report else []), "",
             "| Who | Sent | Update |", "|---|---|---|"]
    def cell(s: str) -> str:
        return " ".join(s.split()).replace("|", "\\|")

    for m in r.members:
        mine = [u for u in ups if u.sender == m.name]
        lines += [f"| {m.name} | {u.sent.astimezone(r.tz):%a %d %b %H:%M} | [{cell(u.title)}]"
                  f"({os.path.relpath(brain.root / u.rel, here)})" + (f": {cell(u.description)}" if u.description else "")
                  + " |" for u in mine]
        if not mine:
            lines.append(f"| {m.name} | | {'no update' if report else 'not yet'} |")
    return "\n".join(lines) + "\n\nGenerated by BrainForge; it changes with every update.\n"


def write_pages(brain: Brain, rs: list[Round]) -> list[str]:
    """(Re)write these rounds' pages; returns the ones that changed."""
    changed = []
    for r in rs:
        path, text = brain.root / r.page, page(brain, r)
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            changed.append(r.page)
    return changed


def status(brain: Brain, at: datetime | None = None) -> list[dict[str, Any]]:
    at = at or datetime.now(UTC)
    out = []
    for r in all_latest(brain, at):
        report = report_for(brain, r.id)
        out.append({"id": r.id, "open": r.open_at(at), "opened": r.opened.isoformat(), "closes": r.closes.isoformat(),
                    "page": r.page, "report": brain.rel(report) if report else None,
                    "updates": [{"from": u.sender, "sent": u.sent.astimezone(r.tz).isoformat(), "source": u.rel,
                                 "title": u.title}
                                for u in updates(brain, r)],
                    "missing": [m.name for m in missing(brain, r)]})
    return out


def waiting(brain: Brain, who: TeamMember | None, at: datetime | None = None) -> list[dict[str, Any]]:
    """Open rounds: for a person, those still waiting for their update; for everyone, all of them."""
    at = at or datetime.now(UTC)
    out = []
    for r in all_latest(brain, at):
        if not r.open_at(at):
            continue
        miss = missing(brain, r)
        if who is None or who in miss:
            out.append({"id": r.id, "closes": _when(r.closes, r.tz), "page": r.page, "missing": [m.name for m in miss]})
    return out


def prompt(r: Round, ups: list[Update], miss: list[TeamMember], paths: bool = True) -> str:
    """The report agent's view of the round it closes. An audience report gets no paths: the update sources
    may not be meant for that audience, and what they brought in reached the knowledge it sees anyway."""
    lines = ["", "# Round", "",
             f"This report closes round `{r.id}`, open from {_when(r.opened, r.tz)} until {_when(r.closes, r.tz)}. "
             "Updates its participants sent" + (", as source concepts you can cite:" if paths else
                                                 " (what they brought in is part of the knowledge you see):")]
    lines += [f"- {u.sender}, {_when(u.sent, r.tz)}" + (f": `{u.rel}`" if paths else "") for u in ups] or ["- none"]
    if miss:
        lines.append(f"No update from {', '.join(m.name for m in miss)}. That is fine: say so plainly, without judging.")
    return "\n".join(lines) + "\n"


# --- GitHub --------------------------------------------------------------------------------------

def issue_title(r: Round) -> str:
    return f"Round {r.id}: updates until {r.closes:%a %d %b %H:%M}"


def issue_body(brain: Brain, r: Round, repo_url: str | None, today: date | None = None) -> str:
    def link(rel: str) -> str:
        return f"{repo_url}/blob/HEAD/{rel}" if repo_url else rel

    today = today or date.today()
    due = briefing.deadlines(brain)
    parts = [f"<!-- brainforge-round: {r.id} -->",
             f"**Round `{r.id}`**: updates for the `{r.proc.name}` report, until {_when(r.closes, r.tz)} "
             f"({r.proc.schedule.timezone if r.proc.schedule else 'UTC'}).", "",
             "Reply here with your update, in your own words: what changed, what's next, what's blocked. BrainForge "
             "takes it into the brain and asks you to confirm what it understood. You can also send it with "
             "`brainforge note \"…\"`, through your agent, or as a file in `inbox/`. Everyone with access to this "
             "repository can read it. No update is fine too: the report says so.", "",
             f"Who has sent one: [round page]({link(r.page)})"]
    for m in r.members:
        parts += ["", f"@{m.github}" if m.github else f"{m.name} (no `github` login in brain.yaml)"]
        mine = [d for d in due if m in d.owners]
        parts += [f"- [{d.concept.title}]({link(brain.rel(d.concept.path))}) ({d.concept.meta.get('type')}, due "
                  f"{d.when}{', overdue' if d.when < today else ''})" for d in mine] or ["- no open items with a date"]
    return "\n".join(parts)


def _gh(brain: Brain, *args: str) -> str:
    if not shutil.which("gh"):
        raise BrainError("round issues need the GitHub CLI (gh)")
    proc = subprocess.run(["gh", *args], cwd=brain.root, capture_output=True, text=True)
    if proc.returncode:
        raise BrainError(f"gh {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc.stdout


def sync_issues(brain: Brain, repo_url: str | None = None, at: datetime | None = None) -> list[str]:
    """One open issue per open round: create it when the round opens, close it once the report is written.
    Safe to call often; GitHub's open issues are the state."""
    at = at or datetime.now(UTC)
    rs = all_latest(brain, at)
    if not rs:
        return []
    listed = json.loads(_gh(brain, "issue", "list", "--label", LABEL, "--state", "open", "--json", "number,body",
                            "--limit", "200"))
    found = {m.group(1): i["number"] for i in listed if (m := MARKER_RE.search(i.get("body") or ""))}
    done = []
    for r in rs:
        if r.open_at(at) and r.id not in found:
            _gh(brain, "label", "create", LABEL, "--color", "1D76DB", "--force")
            _gh(brain, "issue", "create", "--title", issue_title(r), "--label", LABEL,
                "--body", issue_body(brain, r, repo_url))
            done.append(f"opened {r.id}")
    latest_ids = {r.id: r for r in rs}
    for rid, number in found.items():
        r = latest_ids.get(rid)
        if r and r.open_at(at):
            continue
        report = report_for(brain, rid)
        if report is None and r:  # closed, but its report is still to come
            continue
        rel = brain.rel(report) if report else None
        text = (f"The round has closed. Report: [{rel}]({repo_url}/blob/HEAD/{rel})" if rel and repo_url else
                f"The round has closed. Report: `{rel}`" if rel else "The round has closed without a report.")
        _gh(brain, "issue", "close", str(number), "--comment", text)
        done.append(f"closed {rid}")
    return done
