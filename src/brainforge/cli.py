"""`brainforge` command-line interface."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.exceptions import TyperException

from brainforge import agents, gitops, ingest, integrations, okf, parsers, procedures, query, schedule
from brainforge import brain as brain_mod
from brainforge.brain import Brain, BrainError

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False,
                  help="BrainForge: Git-native, agentic knowledge management on OKF bundles.")
brain_app = typer.Typer(no_args_is_help=True, help="Inspect and validate the brain.")
knowledge_app = typer.Typer(no_args_is_help=True, help="Search, query and maintain knowledge.")
inbox_app = typer.Typer(no_args_is_help=True, help="Process documents dropped into inbox/.")
procedures_app = typer.Typer(no_args_is_help=True, help="List and validate procedures.")
schedule_app = typer.Typer(no_args_is_help=True, help="Run scheduled procedures (cron / GitHub Actions).")
ci_app = typer.Typer(no_args_is_help=True, help="GitHub Actions integration.")
skills_app = typer.Typer(no_args_is_help=True, help="Install BrainForge skills for coding agents.")
workspace_app = typer.Typer(no_args_is_help=True, help="Open the brain in Obsidian.")
for sub, name in [(brain_app, "brain"), (knowledge_app, "knowledge"), (inbox_app, "inbox"),
                  (procedures_app, "procedures"), (schedule_app, "schedule"), (ci_app, "ci"),
                  (skills_app, "skills"), (workspace_app, "workspace")]:
    app.add_typer(sub, name=name)

STATE: dict[str, Any] = {"brain": None, "json": False}

AgentOpt = Annotated[str | None, typer.Option("--agent", help="Agent adapter: claude, codex, command.")]
ModelOpt = Annotated[str | None, typer.Option("--model", help="Model for the agent CLI.")]
InstructionOpt = Annotated[str | None, typer.Option("--instruction", "-i", help="Extra instruction for this run.")]
NoCommitOpt = Annotated[bool, typer.Option("--no-commit", help="Apply changes but do not commit them.")]
PushOpt = Annotated[bool, typer.Option("--push", help="Pull before and push after (never forces).")]


@app.callback()
def _global(
    brain: Annotated[Path | None, typer.Option("--brain", "-b", envvar="BRAINFORGE_BRAIN",
                                               help="Brain directory (default: nearest brain.yaml upwards).")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    STATE.update(brain=brain, json=json_out)


def current() -> Brain:
    return brain_mod.find(STATE["brain"])


def emit(data: Any, text: str) -> None:
    typer.echo(json.dumps(data, indent=2, default=str) if STATE["json"] else text)


def emit_issues(issues: list[okf.Issue], ok: str) -> int:
    errors = [i for i in issues if i.level == "error"]
    lines = [str(i) for i in issues] or [ok]
    emit([i.__dict__ for i in issues], "\n".join(lines))
    return 1 if errors else 0


def wants_commit(b: Brain, no_commit: bool) -> bool:
    return b.manifest.git.commit and not no_commit


# --- init / brain ------------------------------------------------------------------------------

@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Directory for the new brain.")] = Path("."),
    name: Annotated[str | None, typer.Option(help="Display name.")] = None,
    brain_id: Annotated[str | None, typer.Option("--id", help="Stable brain id (default: directory name).")] = None,
    agent: Annotated[str, typer.Option(help="Default agent adapter: claude, codex, command.")] = "claude",
) -> None:
    """Create a new brain (directory layout, brain.yaml, empty OKF bundle, git repo)."""
    b = brain_mod.init(directory, name, brain_id, agent)
    emit({"id": b.id, "root": str(b.root)}, f"Initialized brain '{b.id}' in {b.root}")


@brain_app.command("show")
def brain_show() -> None:
    """Summarize the brain."""
    b = current()
    types = Counter(str(c.meta.get("type", "?")) for c in okf.read_all(b.knowledge))
    docs, _ = ingest.inbox_documents(b)
    procs, _ = procedures.load_all(b)
    data = {"id": b.id, "name": b.manifest.name, "root": str(b.root), "agent": b.manifest.agent.model_dump(exclude_none=True),
            "concepts": dict(sorted(types.items())), "inbox": [p.name for p in docs], "procedures": [p.name for p in procs],
            "head": gitops.head(b.root)}
    lines = [f"{b.manifest.name} ({b.id})", f"  root:       {b.root}", f"  agent:      {b.manifest.agent.adapter}"
             + (f" / {b.manifest.agent.model}" if b.manifest.agent.model else ""),
             f"  concepts:   {sum(types.values())} " + (f"({', '.join(f'{k}: {v}' for k, v in sorted(types.items()))})" if types else ""),
             f"  inbox:      {len(docs)} document(s)", f"  procedures: {', '.join(data['procedures']) or 'none'}"]
    emit(data, "\n".join(lines))


@brain_app.command("validate")
def brain_validate() -> None:
    """Validate manifest, layout, OKF knowledge and procedures."""
    b = current()
    issues = brain_mod.check_layout(b)
    issues += [okf.Issue(i.level, f"knowledge/{i.path}", i.message) for i in okf.validate_bundle(b.knowledge)]
    issues += procedures.load_all(b)[1]
    issues += [okf.Issue("warning", b.rel(p), "sidecar without a matching document") for p in ingest.inbox_documents(b)[1]]
    raise typer.Exit(emit_issues(issues, f"Brain '{b.id}' is valid."))


# --- ingestion ---------------------------------------------------------------------------------

def _report_outcomes(outcomes: list[ingest.Outcome]) -> int:
    lines = []
    for o in outcomes:
        mark = {"ingested": "ingested", "skipped": "skipped ", "dry-run": "dry-run ", "failed": "FAILED  "}[o.status]
        detail = f" -> {o.source}" if o.source and o.status != "skipped" else ""
        lines.append(f"{mark} {o.document}{detail}: {o.message}" + (f" [{o.commit[:8]}]" if o.commit else ""))
        lines += [f"           + {p}" for p in o.created] + [f"           ~ {p}" for p in o.updated]
    emit([o.as_dict() for o in outcomes], "\n".join(lines) or "Nothing to ingest.")
    failed = sum(o.status == "failed" for o in outcomes)
    return 0 if not failed else 1 if failed == len(outcomes) else 3


@app.command("ingest")
def ingest_cmd(
    files: Annotated[list[Path], typer.Argument(help="Documents to ingest (md, txt, pdf, docx, pptx, html).")],
    instruction: InstructionOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Re-ingest even if this content was ingested before.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Run the agent and validate, then undo everything.")] = False,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Ingest documents: extract knowledge with the agent, archive the original, commit."""
    b = current()
    cfg = agents.resolve(b, agent, model)
    if push:
        gitops.pull(b.root)
    outcomes = [ingest.ingest(b, f, cfg, instruction=instruction, force=force, dry_run=dry_run,
                              commit=wants_commit(b, no_commit)) for f in files]
    if push and any(o.commit for o in outcomes):
        gitops.push(b.root)
    raise typer.Exit(_report_outcomes(outcomes))


@inbox_app.command("process")
def inbox_process(
    instruction: InstructionOpt = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Ingest every document in inbox/ (sidecars and Ingest-Note trailers apply)."""
    b = current()
    cfg = agents.resolve(b, agent, model)
    if push:
        gitops.pull(b.root)
    outcomes, orphans = ingest.process_inbox(b, cfg, instruction=instruction, dry_run=dry_run,
                                             commit=wants_commit(b, no_commit))
    for p in orphans:
        typer.echo(f"warning: {b.rel(p)}: sidecar without a matching document", err=True)
    if push and gitops.head(b.root):
        gitops.push(b.root)
    raise typer.Exit(_report_outcomes(outcomes))


# --- knowledge ---------------------------------------------------------------------------------

@knowledge_app.command("search")
def knowledge_search(
    text: Annotated[str | None, typer.Argument(help="Words that must all appear.")] = None,
    type_: Annotated[str | None, typer.Option("--type", help="OKF type, e.g. Decision.")] = None,
    tag: Annotated[str | None, typer.Option(help="Tag.")] = None,
    status: Annotated[str | None, typer.Option(help="draft, stable or deprecated.")] = None,
    links_to: Annotated[str | None, typer.Option("--links-to", help="Concepts linking to this concept id.")] = None,
    linked_from: Annotated[str | None, typer.Option("--linked-from", help="Concepts this concept id links to.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum results.")] = 20,
) -> None:
    """Deterministic search over the knowledge bundle (no agent)."""
    b = current()
    hits = okf.search(b.knowledge, text, type=type_, tag=tag, status=status, links_to=links_to,
                      linked_from=linked_from)[:limit]
    data = [{"path": b.rel(c.path), "id": okf.concept_id(b.knowledge, c.path), "type": c.meta.get("type"),
             "title": c.title, "description": c.description, "score": s} for s, c in hits]
    emit(data, "\n".join(f"{d['path']}  [{d['type']}] {d['title']}" + (f" — {d['description']}" if d["description"] else "")
                         for d in data) or "No matches.")


@knowledge_app.command("query")
def knowledge_query(
    question: Annotated[str, typer.Argument(help="Question to answer from the knowledge.")],
    instruction: InstructionOpt = None,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Answer a question from the knowledge, with citations (read-only)."""
    b = current()
    result = query.query(b, question, agents.resolve(b, agent, model), instruction=instruction)
    emit(result, result["answer"])
    for c in result["unknown_citations"]:
        typer.echo(f"warning: cited file does not exist: {c}", err=True)


@knowledge_app.command("validate")
def knowledge_validate() -> None:
    """Check OKF v0.2 conformance and the BrainForge profile."""
    b = current()
    raise typer.Exit(emit_issues(okf.validate_bundle(b.knowledge), "Knowledge bundle is valid."))


@knowledge_app.command("index")
def knowledge_index() -> None:
    """Regenerate index.md files (e.g. after resolving a merge conflict)."""
    b = current()
    changed = okf.write_indexes(b.knowledge)
    emit([b.rel(p) for p in changed], f"Rebuilt {len(changed)} index file(s).")


@knowledge_app.command("verify")
def knowledge_verify(
    concept: Annotated[str, typer.Argument(help="Concept id or path, e.g. decisions/go-live.")],
    no_commit: NoCommitOpt = False,
) -> None:
    """Record a human review of a concept (OKF `verified`, trust tier human-reviewed)."""
    b = current()
    rel = concept.removeprefix("knowledge/").removesuffix(".md")
    path = b.knowledge / f"{rel}.md"
    if not path.is_file():
        raise BrainError(f"no such concept: {rel}")
    who = gitops.user(b.root)
    if not who:
        raise BrainError("set git user.email or user.name to record who verified")
    c = okf.read(path)
    verified = c.meta.get("verified") or []
    verified = [verified] if isinstance(verified, dict) else list(verified)
    c.meta["verified"] = [*verified, {"by": f"human:{who}", "at": okf.now_iso()}]
    okf.write(c)
    sha = gitops.commit(b.root, f"verify: {rel}", [b.rel(path)]) if wants_commit(b, no_commit) else None
    emit({"concept": rel, "by": f"human:{who}", "commit": sha}, f"Verified {rel} as human:{who}")


# --- procedures --------------------------------------------------------------------------------

@procedures_app.command("list")
def procedures_list() -> None:
    """List procedures with schedule and last run."""
    b = current()
    procs, issues = procedures.load_all(b)
    data = [{"name": p.name, "version": p.version, "description": p.description, "format": p.output.format,
             "schedule": p.schedule.model_dump() if p.schedule else None,
             "last_run": procedures.last_run(b, p)} for p in procs]
    lines = [f"{d['name']} (v{d['version']}, {d['format']})"
             + (f"  schedule: {d['schedule']['cron']} {d['schedule']['timezone']}" if d["schedule"] else "")
             + f"  last run: {d['last_run'] or 'never'}" + (f"\n    {d['description']}" if d["description"] else "")
             for d in data]
    emit(data, "\n".join(lines + [str(i) for i in issues]) or "No procedures.")


@procedures_app.command("validate")
def procedures_validate() -> None:
    """Validate all procedure files."""
    raise typer.Exit(emit_issues(procedures.load_all(current())[1], "Procedures are valid."))


@app.command("run")
def run_cmd(
    procedure: Annotated[str, typer.Argument(help="Procedure name.")],
    instruction: InstructionOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing report for this period.")] = False,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Run a procedure now and write its report."""
    b = current()
    proc = procedures.get(b, procedure)
    cfg = agents.resolve(b, agent or proc.agent, model)
    if push:
        gitops.pull(b.root)
    result = procedures.run(b, proc, cfg, instruction=instruction, force=force, commit=wants_commit(b, no_commit))
    if push and result["commit"]:
        gitops.push(b.root)
    text = f"Wrote {', '.join(result['outputs'])} ({result['cited']} cited concepts)"
    emit(result, text + "".join(f"\nwarning: broken citation {c}" for c in result["broken_citations"]))


# --- scheduling & CI ---------------------------------------------------------------------------

@schedule_app.command("tick")
def schedule_tick(
    procedure: Annotated[str | None, typer.Option("--procedure", help="Only consider this procedure.")] = None,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
) -> None:
    """Run every scheduled procedure that is due (safe to call often)."""
    b = current()
    if push:
        gitops.pull(b.root)
    results = schedule.tick(b, only=procedure, commit=wants_commit(b, no_commit))
    if push and any(r.get("commit") for r in results):
        gitops.push(b.root)
    emit(results, "\n".join(f"{r['procedure']}: {r['status']}" + (f" -> {r['report']}" if r.get("report") else "")
                            + (f" ({r['message']})" if r.get("message") else "") for r in results)
         or "No scheduled procedures.")
    raise typer.Exit(1 if any(r["status"] == "failed" for r in results) else 0)


@schedule_app.command("install")
def schedule_install(
    provider: Annotated[str, typer.Option(help="cron or github-actions.")],
    package: Annotated[str, typer.Option(help="pip spec CI uses to install BrainForge.")] = "brainforge",
    print_only: Annotated[bool, typer.Option("--print", help="cron: print the entry instead of installing it.")] = False,
) -> None:
    """Install the scheduler: one crontab entry, or a GitHub Actions workflow."""
    b = current()
    if provider == "cron":
        line = schedule.cron_line(b) if print_only else schedule.install_cron(b)
        emit({"cron": line}, line if print_only else f"Installed crontab entry:\n{line}")
    elif provider == "github-actions":
        out = schedule.write_github_procedures(b, package)
        emit({"workflow": b.rel(out)}, f"Wrote {b.rel(out)}; commit and push it to activate.")
    else:
        raise BrainError("provider must be 'cron' or 'github-actions'")


@ci_app.command("install")
def ci_install(
    package: Annotated[str, typer.Option(help="pip spec CI uses to install BrainForge.")] = "brainforge",
) -> None:
    """Write the GitHub Actions workflow that ingests documents pushed to inbox/."""
    b = current()
    out = schedule.write_github_ingest(b, package)
    emit({"workflow": b.rel(out)}, f"Wrote {b.rel(out)}; add the agent secret, then commit and push it.")


# --- integrations ------------------------------------------------------------------------------

@skills_app.command("install")
def skills_install(
    agent: Annotated[str, typer.Argument(help="claude or codex.")],
    user: Annotated[bool, typer.Option("--user", help="Install for your user instead of this brain repo.")] = False,
) -> None:
    """Install the BrainForge skill for Claude Code or Codex."""
    base = Path.home() if user else current().root
    path = integrations.install_skill(agent, base)
    emit({"skill": str(path)}, f"Installed {path}")


@workspace_app.command("open")
def workspace_open(
    launch: Annotated[bool, typer.Option("--launch/--no-launch", help="Launch Obsidian.")] = True,
) -> None:
    """Configure the brain as an Obsidian vault (graph colours by type) and open it."""
    b = current()
    vault = integrations.obsidian_config(b)
    url = integrations.open_obsidian(b) if launch else None
    emit({"vault": str(b.root), "config": str(vault), "url": url},
         f"Obsidian vault: {b.root}" + (f"\nOpened {url} (if nothing happens: Obsidian → Open folder as vault)" if url else ""))


# --- entry point -------------------------------------------------------------------------------

def run(argv: list[str] | None = None) -> int:
    try:
        result = app(args=argv, prog_name="brainforge", standalone_mode=False)
    except typer.Exit as exc:
        return exc.exit_code
    except typer.Abort:
        return 1
    except TyperException as exc:  # usage errors
        show = getattr(exc, "show", None)
        show() if callable(show) else typer.echo(f"error: {exc}", err=True)
        return getattr(exc, "exit_code", 2)
    except BrainError as exc:
        typer.echo(f"error: {exc}", err=True)
        return 2
    except (gitops.GitError, agents.AgentError, ingest.IngestError, procedures.ProcedureError,
            okf.OKFError, parsers.ParseError) as exc:
        typer.echo(f"error: {exc}", err=True)
        return 1
    return result if isinstance(result, int) else 0


def main() -> None:
    sys.exit(run())
