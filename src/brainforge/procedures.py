"""Declarative Markdown procedures: load, validate, run, render."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from brainforge import agents, gitops, okf
from brainforge.brain import AgentConfig, Brain, Layer, blob_hash, instruction_layers, render_layers
from brainforge.ingest import require_clean

FORMATS = {"docx": ".docx", "pptx": ".pptx", "html": ".html", "pdf": ".pdf"}


class ProcedureError(Exception):
    pass


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["markdown", "docx", "pptx", "html", "pdf"] = "markdown"
    directory: str | None = None  # default: reports/<name>
    filename: str = "{date}"
    template: str | None = None  # pandoc --reference-doc, e.g. templates/corporate.pptx


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cron: str
    timezone: str = "UTC"

    @field_validator("cron")
    @classmethod
    def _cron(cls, value: str) -> str:
        try:
            CronSim(value, datetime.now(UTC))
        except CronSimError as exc:
            raise ValueError(f"invalid cron expression: {exc}") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown IANA timezone '{value}'") from None
        return value


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_self: bool = Field(default=True, validation_alias="self")
    ancestors: Any = None
    descendants: Any = None
    siblings: Any = None

    @field_validator("ancestors", "descendants", "siblings")
    @classmethod
    def _phase_b(cls, value: Any) -> Any:
        if value and not (isinstance(value, dict) and value.get("include") is False):
            raise ValueError("hierarchy scopes are not supported yet (planned for Phase B)")
        return value


class Procedure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: int | str = 1
    description: str = ""
    agent: Literal["claude", "codex", "command"] | None = None
    knowledge: Scope = Scope()
    output: Output = Field(default_factory=Output)
    schedule: Schedule | None = None
    path: Path = Field(exclude=True)
    body: str = Field("", exclude=True)

    @property
    def directory(self) -> str:
        return (self.output.directory or f"reports/{self.name}").strip("/")

    def tz(self) -> Any:
        return ZoneInfo(self.schedule.timezone) if self.schedule else datetime.now().astimezone().tzinfo


def load(path: Path) -> Procedure:
    try:
        meta, body = okf.split_frontmatter(path.read_text(encoding="utf-8"))
    except okf.OKFError as exc:
        raise ProcedureError(f"{path.name}: {exc}") from exc
    meta = dict(meta or {})
    meta.setdefault("name", path.stem.lower())
    try:
        return Procedure.model_validate({**meta, "path": path, "body": body})
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ProcedureError(f"{path.name}: {details}") from exc


def load_all(brain: Brain) -> tuple[list[Procedure], list[okf.Issue]]:
    procs, issues = [], []
    for path in sorted(brain.procedures.glob("*.md")):
        try:
            procs.append(load(path))
        except ProcedureError as exc:
            issues.append(okf.Issue("error", brain.rel(path), str(exc)))
    seen: dict[str, Procedure] = {}
    for p in procs:
        if p.name in seen:
            issues.append(okf.Issue("error", brain.rel(p.path), f"duplicate procedure name '{p.name}'"))
        seen[p.name] = p
        if p.output.template and not (brain.root / p.output.template).is_file():
            issues.append(okf.Issue("error", brain.rel(p.path), f"template not found: {p.output.template}"))
    return procs, issues


def get(brain: Brain, name: str) -> Procedure:
    procs, _ = load_all(brain)
    for p in procs:
        if p.name == name:
            return p
    known = ", ".join(p.name for p in procs) or "none"
    raise ProcedureError(f"unknown procedure '{name}' (known: {known})")


def reports(brain: Brain, proc: Procedure) -> list[tuple[str, Path]]:
    """(generated.at, path) of this procedure's reports, oldest first."""
    found = []
    for path in (brain.root / proc.directory).glob("*.md"):
        try:
            meta, _ = okf.split_frontmatter(path.read_text(encoding="utf-8"))
        except okf.OKFError:
            continue
        if meta and (meta.get("procedure") or {}).get("name") == proc.name:
            found.append((str((meta.get("generated") or {}).get("at", "")), path))
    return sorted(found)


def last_run(brain: Brain, proc: Procedure) -> datetime | None:
    found = [at for at, _ in reports(brain, proc) if at]
    return datetime.fromisoformat(found[-1]) if found else None


def render(brain: Brain, md: Path, fmt: str, template: str | None) -> Path:
    if not shutil.which("pandoc"):
        raise ProcedureError(f"output format '{fmt}' needs pandoc (https://pandoc.org/installing.html)")
    out = md.with_suffix(FORMATS[fmt])
    cmd = ["pandoc", str(md), "-o", str(out), "--resource-path", str(md.parent)]
    if template and fmt in ("docx", "pptx"):
        cmd += ["--reference-doc", str(brain.root / template)]
    if fmt == "html":
        cmd += ["--standalone"]
    if fmt == "pdf" and shutil.which("typst"):
        cmd += ["--pdf-engine", "typst"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProcedureError(f"pandoc failed: {proc.stderr.strip()}")
    return out


def build_prompt(brain: Brain, proc: Procedure, layers: list[Layer], report_rel: str,
                 previous: Path | None, changed: list[str] | None) -> str:
    prefix = os.path.relpath(brain.knowledge, (brain.root / report_rel).parent)
    prev = f"`{brain.rel(previous)}`" if previous else "none (this is the first report)"
    if changed is None:
        delta = "No previous report: consider all knowledge."
    else:
        delta = "\n".join(f"- `{p}`" for p in changed) or "- (no knowledge changes since the previous report)"
    return f"""You are the BrainForge reporting agent. Your working directory is the root of a brain repository.

{render_layers(layers)}

# Task

Run procedure `{proc.name}` (version {proc.version}) and write the report to `{report_rel}`.

- Cite concepts with links relative to the report file, e.g. `[Integration testing]({prefix}/milestones/integration-testing.md)`.
- Previous report: {prev}
- Knowledge files changed since the previous report:
{delta}
- Write only `{report_rel}`, markdown body only, without YAML frontmatter.
"""


def run(brain: Brain, proc: Procedure, cfg: AgentConfig, *, instruction: str | None = None,
        force: bool = False, commit: bool = True, now: datetime | None = None) -> dict:
    root = brain.root
    now = now or datetime.now(UTC)
    date = now.astimezone(proc.tz()).date().isoformat()
    report_rel = f"{proc.directory}/{proc.output.filename.format(date=date, name=proc.name)}.md"
    report = root / report_rel
    if report.exists() and not force:
        raise ProcedureError(f"{report_rel} already exists; use --force to regenerate it")
    if proc.output.format != "markdown" and not shutil.which("pandoc"):
        raise ProcedureError(f"output format '{proc.output.format}' needs pandoc (https://pandoc.org/installing.html)")

    history = [p for _, p in reports(brain, proc) if p != report]
    previous = history[-1] if history else None
    prev_commit = None
    if previous:
        meta, _ = okf.split_frontmatter(previous.read_text(encoding="utf-8"))
        prev_commit = ((meta or {}).get("brainforge") or {}).get("commit")
    changed = gitops.changed_since(root, prev_commit, "knowledge") if prev_commit else None

    require_clean(brain)
    proc_text = proc.path.read_text(encoding="utf-8")
    extra = [Layer(f"Procedure {proc.name} ({brain.rel(proc.path)})", f"{brain.rel(proc.path)}@{blob_hash(proc_text)}", proc.body)]
    if instruction:
        extra.append(Layer("Invocation (--instruction)", f"cli@{blob_hash(instruction)}", instruction))
    layers = instruction_layers(brain, "reporting", extra)
    base = gitops.head(root)
    baseline = gitops.untracked(root)

    with gitops.lock(root):
        try:
            agents.run(cfg, build_prompt(brain, proc, layers, report_rel, previous, changed), root, write=True)
            stray = [p for c, p in gitops.status(root) if p != report_rel and not (c == "??" and p in baseline)]
            if stray:
                raise ProcedureError(f"agent changed files other than the report: {', '.join(stray)}")
            if not report.is_file() or not report.read_text(encoding="utf-8").strip():
                raise ProcedureError(f"agent did not write {report_rel}")
            _, body = okf.split_frontmatter(report.read_text(encoding="utf-8"))
            concept = okf.Concept(report, {}, body)
            bundle = brain.knowledge.resolve()
            cited = sorted({t for t in okf.links(concept, brain.knowledge) if t.is_relative_to(bundle) and t.suffix == ".md"})
            broken = [os.path.relpath(t, report.parent) for t in cited if not t.exists()]
            sources = [{"resource": os.path.relpath(t, report.parent)} for t in cited if t.exists()]
            meta = {
                "type": "Report",
                "title": f"{proc.name} {date}",
                "procedure": {"name": proc.name, "version": proc.version},
                "brain": brain.id,
                "generated": {"by": agents.actor(cfg), "at": okf.now_iso(now)},
                "sources": sources,
                "brainforge": {
                    "commit": base,
                    "previous_report": brain.rel(previous) if previous else None,
                    "agent": f"{cfg.adapter}/{cfg.model or 'default'}",
                    "instructions": [layer.source for layer in layers],
                    "broken_citations": broken,
                },
            }
            report.write_text(okf.render(meta, body), encoding="utf-8")
            outputs = [report_rel]
            if proc.output.format != "markdown":
                outputs.append(brain.rel(render(brain, report, proc.output.format, proc.output.template)))
            sha = None
            if commit:
                sha = gitops.commit(root, f"report: {proc.name} {date}\n\nBrainForge-Procedure: {proc.name}@{proc.version}\n"
                                          f"BrainForge-Knowledge-Commit: {base}", outputs)
        except Exception:
            gitops.rollback(root, baseline)
            raise
    return {"procedure": proc.name, "report": report_rel, "outputs": outputs, "commit": sha,
            "cited": len(sources), "broken_citations": broken, "previous": brain.rel(previous) if previous else None}
