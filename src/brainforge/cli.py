"""`brainforge` command-line interface."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.exceptions import TyperException

from brainforge import (
    agents,
    approval,
    briefing,
    gitops,
    ingest,
    integrations,
    okf,
    parsers,
    plan,
    plugins,
    procedures,
    proposals,
    query,
    rounds,
    schedule,
    signals,
)
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
plugin_app = typer.Typer(no_args_is_help=True, help="Plugins: domain types, instructions, procedures and scripts.")
workspace_app = typer.Typer(no_args_is_help=True, help="Open the brain in Obsidian.")
message_app = typer.Typer(no_args_is_help=True, help="Messages for team members. They are files in the brain "
                          "repository: everyone with access to it can read them.")
proposals_app = typer.Typer(no_args_is_help=True, help="Proposals from a steering procedure: a lead approves, "
                            "BrainForge carries them out.")
for sub, name in [(brain_app, "brain"), (knowledge_app, "knowledge"), (inbox_app, "inbox"),
                  (procedures_app, "procedures"), (schedule_app, "schedule"), (ci_app, "ci"), (skills_app, "skills"),
                  (workspace_app, "workspace"), (plugin_app, "plugin"), (message_app, "message"),
                  (proposals_app, "proposals")]:
    app.add_typer(sub, name=name)

STATE: dict[str, Any] = {"brain": None, "json": False}

AgentOpt = Annotated[str | None, typer.Option("--agent", help="Agent adapter: claude, codex, command.")]
ModelOpt = Annotated[str | None, typer.Option("--model", help="Model for the agent CLI.")]
InstructionOpt = Annotated[str | None, typer.Option("--instruction", "-i", help="Extra instruction for this run.")]
NoCommitOpt = Annotated[bool, typer.Option("--no-commit", help="Apply changes but do not commit them.")]
AllowSecretsOpt = Annotated[bool, typer.Option("--allow-secrets", help="Ingest even if the text looks like it "
                                               "contains credentials (false positives).")]
PushOpt = Annotated[bool, typer.Option("--push", help="Pull before and push after, rebasing if the remote moved on "
                                       "(never forces). Always on with `git: push: true` in brain.yaml.")]


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


def wants_push(b: Brain, push: bool) -> bool:
    return push or b.manifest.git.push



# --- init / brain ------------------------------------------------------------------------------

@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Directory for the new brain.")] = Path("."),
    name: Annotated[str | None, typer.Option(help="Display name.")] = None,
    brain_id: Annotated[str | None, typer.Option("--id", help="Stable brain id (default: directory name).")] = None,
    agent: Annotated[str, typer.Option(help="Default agent adapter: claude, codex, command.")] = "claude",
    plugin: Annotated[list[str] | None, typer.Option("--plugin", help="Add a plugin, e.g. pm (repeatable).")] = None,
) -> None:
    """Create a new brain (directory layout, brain.yaml, empty OKF bundle, git repo)."""
    b = brain_mod.init(directory, name, brain_id, agent)
    added = [plugins.add(b, p) for p in plugin or []]
    if added:
        gitops.commit(b.root, "brainforge: add plugin " + ", ".join(p.name for p in added), ["plugins"])
    emit({"id": b.id, "root": str(b.root), "plugins": [p.name for p in added]},
         f"Initialized brain '{b.id}' in {b.root}" + "".join(f" with plugin {p.name}" for p in added))


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
    """Validate manifest, layout, OKF knowledge, plugin fields, procedures and graph health."""
    b = current()
    issues = brain_mod.check_layout(b)
    issues += [okf.Issue(i.level, f"knowledge/{i.path}", i.message)
               for i in okf.validate_bundle(b.knowledge) + okf.graph_issues(b.knowledge)]
    issues += procedures.load_all(b)[1]
    known = plugins.types(b)
    issues += [i for p in okf.concept_files(b.knowledge) for i in plugins.check(b, p, known)]
    issues += [okf.Issue("warning", b.rel(p), "sidecar without a matching document") for p in ingest.inbox_documents(b)[1]]
    issues += plan.check(b)
    levels = b.manifest.audiences
    issues += [okf.Issue("error", b.rel(c.path), f"audience '{c.meta['audience']}' is not one of {levels}")
               for c in okf.read_all(b.knowledge) if levels and "audience" in c.meta and c.meta["audience"] not in levels]
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
    files: Annotated[list[Path], typer.Argument(help="Documents to ingest (md, txt, pdf, docx, pptx, html, vtt, srt).")],
    instruction: InstructionOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Re-ingest even if this content was ingested before.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Run the agent and validate, then undo everything.")] = False,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
    allow_secrets: AllowSecretsOpt = False,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Ingest documents: extract knowledge with the agent, archive the original, commit."""
    b = current()
    cfg = agents.resolve(b, agent, model)
    outcomes = _ingest_and_push(b, wants_push(b, push), lambda: [
        ingest.ingest(b, f, cfg, instruction=instruction, force=force, dry_run=dry_run,
                      commit=wants_commit(b, no_commit), allow_secrets=allow_secrets) for f in files])
    raise typer.Exit(_report_outcomes(outcomes))


def _ingest_and_push(b: Brain, push: bool, run: Callable[[], list[ingest.Outcome]]) -> list[ingest.Outcome]:
    """Ingest, then push if asked. If the push loses a race on the same files, the run is undone and the
    remote pulled; ingest again on top of it. Idempotent: what the other side ingested is skipped."""
    if push:
        gitops.pull(b.root)
    for attempt in range(1, 4):
        start = gitops.head(b.root)
        outcomes = run()
        if not push or gitops.head(b.root) == start:
            return outcomes
        try:
            gitops.publish(b.root, start)
            return outcomes
        except gitops.Stale as exc:
            if attempt == 3:
                raise
            typer.echo(f"warning: {', '.join(exc.paths[:3])} changed on the remote meanwhile; "
                       f"ingesting again on top of it (attempt {attempt + 1} of 3)", err=True)
    raise AssertionError("unreachable")


@app.command("note")
def note_cmd(
    text: Annotated[str, typer.Argument(help="What the brain should know, e.g. a correction; '-' reads stdin.")],
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Ingest a typed note as a source document by you, e.g. "The go-live moved to 2026-12-01". In an open
    round you take part in, it is your update."""
    b = current()
    outcomes = _ingest_note(b, agents.resolve(b, agent, model), sys.stdin.read() if text == "-" else text,
                            approval.author(b), wants_push(b, push), wants_commit(b, no_commit))
    raise typer.Exit(_report_outcomes(outcomes))


def _ingest_note(b: Brain, cfg: brain_mod.AgentConfig, text: str, sender: str, push: bool, commit: bool = True,
                 round_id: str | None = None, what: str = "Note") -> list[ingest.Outcome]:
    if not text.strip():
        raise BrainError(f"the {what.lower()} is empty")
    name, content = ingest.note(sender, text, datetime.now().astimezone(), what)
    with tempfile.TemporaryDirectory() as tmp:  # ingestion archives the note like any document
        path = Path(tmp) / name
        path.write_text(content, encoding="utf-8")
        return _ingest_and_push(b, push, lambda: [
            ingest.ingest(b, path, cfg, commit=commit, sender=sender, round_id=round_id)])


@inbox_app.command("process")
def inbox_process(
    instruction: InstructionOpt = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
    allow_secrets: AllowSecretsOpt = False,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Ingest every document in inbox/ (sidecars and Ingest-Note trailers apply)."""
    b = current()
    cfg = agents.resolve(b, agent, model)
    outcomes = _ingest_and_push(b, wants_push(b, push), lambda: ingest.process_inbox(
        b, cfg, instruction=instruction, dry_run=dry_run, commit=wants_commit(b, no_commit),
        allow_secrets=allow_secrets)[0])
    for p in ingest.inbox_documents(b)[1]:
        typer.echo(f"warning: {b.rel(p)}: sidecar without a matching document", err=True)
    raise typer.Exit(_report_outcomes(outcomes))


# --- knowledge ---------------------------------------------------------------------------------

@knowledge_app.command("search")
def knowledge_search(
    text: Annotated[str | None, typer.Argument(help="Words that must all appear.")] = None,
    type_: Annotated[str | None, typer.Option("--type", help="OKF type, e.g. Decision.")] = None,
    tag: Annotated[str | None, typer.Option(help="Tag.")] = None,
    status: Annotated[str | None, typer.Option(help="draft, stable or deprecated.")] = None,
    approval_: Annotated[str | None, typer.Option("--approval", help="pending, approved or discarded "
                                                "(default: everything but discarded).")] = None,
    links_to: Annotated[str | None, typer.Option("--links-to", help="Concepts linking to this concept id.")] = None,
    linked_from: Annotated[str | None, typer.Option("--linked-from", help="Concepts this concept id links to.")] = None,
    limit: Annotated[int, typer.Option(help="Maximum results.")] = 20,
) -> None:
    """Deterministic search over the knowledge bundle (no agent)."""
    b = current()
    hits = okf.search(b.knowledge, text, type=type_, tag=tag, status=status, approval=approval_, links_to=links_to,
                      linked_from=linked_from)[:limit]
    data = [{"path": b.rel(c.path), "id": okf.concept_id(b.knowledge, c.path), "url": approval.url(c.path),
             "type": c.meta.get("type"), "title": c.title, "description": c.description,
             "approval": approval.state(c), "audience": approval.audience_of(b, c), "score": s} for s, c in hits]
    emit(data, "\n".join(f"{d['path']}  [{d['type']}] {d['title']}" + (f" — {d['description']}" if d["description"] else "")
                         for d in data) or "No matches.")


@knowledge_app.command("query")
def knowledge_query(
    question: Annotated[str, typer.Argument(help="Question to answer from the knowledge.")],
    instruction: InstructionOpt = None,
    audience: Annotated[str | None, typer.Option(help="Answer only from approved knowledge "
                                                 "meant for this audience (one of brain.yaml audiences).")] = None,
    agent: AgentOpt = None,
    model: ModelOpt = None,
) -> None:
    """Answer a question from the knowledge, with citations (read-only)."""
    b = current()
    result = query.query(b, question, agents.resolve(b, agent, model), instruction=instruction, audience=audience)
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


# --- approval ----------------------------------------------------------------------------------

ItemsArg = Annotated[list[str], typer.Argument(help="Concept ids or paths, e.g. milestones/go-live; "
                                                "add @<version> from `pending` to stop if it changed since.")]


def _item_line(d: dict) -> str:
    extra = [f"owner: {d['owner']}" if d["owner"] else "", f"from {d['source']}" if d["source"] else "",
             f"changed since {d['since'][:8]}" if d["since"] else "", f"missing {', '.join(d['gaps'])}" if d["gaps"] else ""]
    return f"{ {'new': '+', 'changed': '~', 'expired': '!'}[d['kind']]} {d['id']}  [{d['type']}] {d['title']}" + (
        f"  ({', '.join(e for e in extra if e)})" if any(extra) else "")


def _team_lines(b: Brain, status: dict) -> list[str]:
    lines = [f"{status['pending']} item(s) await approval"
             + (f" (oldest: {status['oldest_days']} day(s))" if status["pending"] else ".")]
    width = max([len(n) for n in status["approvers"]] + [10])
    for name, items in [*status["approvers"].items(), *([("unassigned", status["unassigned"])] if status["unassigned"] else [])]:
        listing = f"{len(items):>3}  " + ", ".join(okf.concept_id(b.knowledge, b.root / p) for p in items) if items else "  done"
        lines.append(f"  {name:<{width}} {listing}")
    return lines


def _sync(b: Brain) -> None:
    if warning := approval.sync(b):
        typer.echo(f"warning: {warning}", err=True)


@app.command("pending")
def pending_cmd(
    team: Annotated[bool, typer.Option("--team", help="Per team member: who still has items to approve.")] = False,
    for_: Annotated[str | None, typer.Option("--for", help="Another team member's items (name or email).")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Every pending item.")] = False,
    check: Annotated[bool, typer.Option("--check", help="Exit 1 if anything is pending.")] = False,
) -> None:
    """Knowledge awaiting approval: yours by default (+ new, ~ changed, ! expired); --team: who still has to act."""
    b = current()
    _sync(b)
    if team:
        status = approval.team_status(b)
        emit(status, "\n".join(_team_lines(b, status)))
        raise typer.Exit(1 if check and status["pending"] else 0)
    who = None
    if b.manifest.team and not all_:
        if for_:
            who = next((m for m in b.manifest.team if for_.lower() in (m.name.lower(), (m.email or "").lower())), None)
            if not who:
                raise BrainError(f"no team member '{for_}' in brain.yaml")
        elif not (who := approval.me(b)):
            raise BrainError(f"you ({gitops.user(b.root) or 'no git user'}) are not on the brain.yaml team; "
                             "use --all, --team or --for NAME")
    items = [i.as_dict(b) for i in approval.pending_for(b, who)]
    head = f"{len(items)} item(s) awaiting approval" + (f" by {who.name}" if who else "")
    emit(items, "\n".join([head + (":" if items else "."), *map(_item_line, items)]))
    raise typer.Exit(1 if check and items else 0)


def _decision(b: Brain, verb: str, items: list[str], audience: str | None, no_commit: bool, push: bool) -> None:
    result = approval.decide(b, verb, items, audience=audience, commit=wants_commit(b, no_commit),
                             push=wants_push(b, push))
    for warning in result["warnings"]:
        typer.echo(f"warning: {warning}", err=True)
    sha = result["commit"]
    emit(result, "\n".join(result["lines"]) + (f"\n[{sha[:8]}]" if sha else ""))


@app.command("briefing")
def briefing_cmd(
    for_: Annotated[str | None, typer.Option("--for", help="Another team member (name, alias, email or login).")] = None,
    hook: Annotated[bool, typer.Option("--hook", help="For an agent's session start: a note to the agent, "
                                       "nothing when there is nothing to tell.")] = False,
) -> None:
    """What needs you: messages, open rounds, overdue and soon-due items, and approvals. Everyone's if you aren't on
    the team."""
    b = current()
    _sync(b)
    who = briefing.find(b, for_)
    data = briefing.briefing(b, who)
    body = briefing.text(data)
    if hook:
        if body:
            typer.echo(f"BrainForge briefing for {who.name if who else 'the whole team'} (brain {b.id}). Tell the "
                       "user about it in a few lines at the start of your first answer, then help with their "
                       f"request.\n{body}")
        return
    emit(data, body or "Nothing needs you right now.")


@message_app.command("send")
def message_send(
    to: Annotated[str, typer.Argument(help="Team member: name, alias, email or GitHub login.")],
    text: Annotated[str, typer.Argument(help="The message; '-' reads stdin.")],
    about: Annotated[str | None, typer.Option("--about", help="The item it is about, e.g. risks/vendor-delay.")] = None,
    push: PushOpt = False,
) -> None:
    """Leave a message that the member's next briefing shows. Everyone with access to the brain can read it."""
    b = current()
    m = approval.member(b, to)
    body = sys.stdin.read() if text == "-" else text
    [rel] = gitops.change(b.root, f"message: to {m.name}",
                          lambda: [briefing.send(b, m, body, approval.author(b), about)], wants_push(b, push))
    emit({"to": m.name, "path": rel},
         f"Left {rel} for {m.name}.\nnote: everyone with access to this brain repository can read it.")


@message_app.command("dismiss")
def message_dismiss(
    ids: Annotated[list[str] | None, typer.Argument(help="Message ids, as the briefing shows them.")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Dismiss all your messages.")] = False,
    push: PushOpt = False,
) -> None:
    """Remove your messages from your briefing. Git history keeps them."""
    b = current()
    if not ids and not all_:
        raise BrainError("name the messages to dismiss, or use --all")
    who = approval.me(b)
    if who is None:
        raise BrainError(f"your git identity ({gitops.user(b.root) or 'not set'}) is not on this brain's team")
    rels = gitops.change(b.root, "message: dismissed {n}", lambda: briefing.dismiss(b, who, None if all_ else ids),
                         wants_push(b, push))
    emit({"dismissed": rels}, f"Dismissed {len(rels)} message(s).")


@app.command("calendar")
def calendar_cmd(
    for_: Annotated[str | None, typer.Option("--for", help="Another team member's deadlines.")] = None,
    everyone: Annotated[bool, typer.Option("--all", help="Everyone's deadlines.")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to this .ics file instead of stdout.")] = None,
) -> None:
    """Open deadlines as an iCalendar file to import into any calendar: yours, or everyone's if you aren't on the team."""
    b = current()
    ics = briefing.calendar(b, None if everyone else briefing.find(b, for_))
    if output is None:
        sys.stdout.write(ics)
        return
    output.write_bytes(ics.encode("utf-8"))
    n = ics.count("BEGIN:VEVENT")
    emit({"calendar": str(output), "events": n}, f"Wrote {output} with {n} deadline(s). Import it again to update them.")


@app.command("approve")
def approve_cmd(
    items: ItemsArg,
    audience: Annotated[str | None, typer.Option("--audience", "-a", help="Also set who may see it.")] = None,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
) -> None:
    """Approve pending knowledge (records you in OKF `verified`)."""
    _decision(current(), "approve", items, audience, no_commit, push)


@app.command("discard")
def discard_cmd(items: ItemsArg, no_commit: NoCommitOpt = False, push: PushOpt = False) -> None:
    """Discard pending knowledge: a change is undone, a new item is kept only as a hidden tombstone."""
    _decision(current(), "discard", items, None, no_commit, push)


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
    push = wants_push(b, push)
    if push:
        gitops.pull(b.root)
    start = gitops.head(b.root)
    result = procedures.run(b, proc, cfg, instruction=instruction, force=force, commit=wants_commit(b, no_commit))
    if push and result["commit"]:
        gitops.publish(b.root, start)
    text = f"Wrote {', '.join(result['outputs'])} ({result['cited']} cited concepts)"
    if note := procedures.waiting_note(result):
        text += f"\nnote: built while items awaited a person ({note}); listed in the report's frontmatter"
    emit(result, text + "".join(f"\nwarning: broken citation {c}" for c in result["broken_citations"]))


@app.command("rounds")
def rounds_cmd() -> None:
    """Rounds (asynchronous status meetings): the latest of each, who has sent an update and who hasn't."""
    b = current()
    _sync(b)
    data = rounds.status(b)
    lines = []
    for r in data:
        state = f"open until {r['closes'][:16]}" if r["open"] else f"closed {r['closes'][:16]}"
        lines.append(f"{r['id']}  {state}  {r['report'] or r['page']}")
        lines += [f"  {u['from']}  {u['sent'][:16]}  {u['title']} ({u['source']})" for u in r["updates"]]
        if r["missing"]:
            lines.append(f"  {'no update' if r['report'] else 'not yet'}: {', '.join(r['missing'])}")
    emit(data, "\n".join(lines) or "No rounds: add `round:` to a scheduled procedure (docs/procedures.md).")


@app.command("plan")
def plan_cmd() -> None:
    """Where the project stands in its model (brain.yaml `model`): phases, steps, what is open."""
    b = current()
    pos = plan.position(b)
    if pos is None:
        emit(None, "No project model: set `model:` in brain.yaml (docs/project-models.md).")
        return
    emit(pos.as_dict(b), plan.text(b, pos))


@app.command("signals")
def signals_cmd(
    for_: Annotated[str | None, typer.Option("--for", help="Only this team member's items.")] = None,
) -> None:
    """What needs steering, found by code: overdue, at risk (waits for something late), slipping, gaps."""
    b = current()
    who = approval.member(b, for_) if for_ else None
    found = [s for s in signals.compute(b) if who is None or who in s.owners]
    emit([s.as_dict() for s in found], "\n".join(s.line() for s in found) or "No signals.")


@proposals_app.command("list")
def proposals_list() -> None:
    """Proposals waiting for a lead, from the latest report of each procedure that proposes."""
    b = current()
    _sync(b)
    data = proposals.waiting(b)
    lines = []
    for w in data:
        lines += [f"{w['report']} ({w['procedure']}):", *("  " + line for line in proposals.lines(
            b, w["proposals"], lambda cid: f"knowledge/{cid}.md"))]
    emit(data, "\n".join(lines) or "No proposals waiting.")


ProcedureOpt = Annotated[str | None, typer.Option("--procedure", help="The procedure whose latest proposals to "
                                                  "decide (needed when several procedures propose).")]


def _proposals(verb: str, numbers: list[int], procedure: str | None, edit: str | None, push: bool) -> None:
    b = current()
    who = approval.me(b)
    if who is None:
        raise BrainError(f"your git identity ({gitops.user(b.root) or 'not set'}) is not on this brain's team")
    result = proposals.decide(b, verb, numbers, who, target=procedure, edit=edit, push=wants_push(b, push))
    emit(result, "\n".join(result["lines"]) + (f"\n[{result['commit'][:8]}]" if result["commit"] else ""))
    raise typer.Exit(0 if not result["failed"] else 1 if result["failed"] == len(numbers) else 3)


@proposals_app.command("approve")
def proposals_approve(
    numbers: Annotated[list[int], typer.Argument(help="Proposal numbers, e.g. 1 3.")],
    procedure: ProcedureOpt = None,
    edit: Annotated[str | None, typer.Option("--edit", help="Your own words for one proposal: the message text, "
                                             "the new value, or the new item's title.")] = None,
    push: PushOpt = False,
) -> None:
    """Carry out proposals, as you (leads only): messages are sent, fields set, items created."""
    _proposals("approve", numbers, procedure, edit, push)


@proposals_app.command("discard")
def proposals_discard(
    numbers: Annotated[list[int], typer.Argument(help="Proposal numbers.")],
    procedure: ProcedureOpt = None,
    push: PushOpt = False,
) -> None:
    """Drop proposals (leads only)."""
    _proposals("discard", numbers, procedure, None, push)


# --- scheduling & CI ---------------------------------------------------------------------------

@schedule_app.command("tick")
def schedule_tick(
    procedure: Annotated[str | None, typer.Option("--procedure", help="Only consider this procedure.")] = None,
    no_commit: NoCommitOpt = False,
    push: PushOpt = False,
) -> None:
    """Run every scheduled procedure that is due (safe to call often)."""
    b = current()
    push = wants_push(b, push)
    if push:
        gitops.pull(b.root)
    start = gitops.head(b.root)
    results = schedule.tick(b, only=procedure, commit=wants_commit(b, no_commit))
    if push and any(r.get("commit") for r in results):
        gitops.publish(b.root, start)
    emit(results, "\n".join(f"{r['procedure']}: {r['status']}"
                            + (f" -> {r.get('report') or r.get('page')}" if r.get("report") or r.get("page") else "")
                            + (f" [{note}]" if (note := procedures.waiting_note(r)) else "")
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
        out = [schedule.write_github_procedures(b, package), schedule.write_github_rounds(b, package),
               schedule.write_github_steering(b, package)]
        rels = [b.rel(p) for p in out if p]
        emit({"workflows": rels}, f"Wrote {', '.join(rels)}; commit and push to activate.")
    else:
        raise BrainError("provider must be 'cron' or 'github-actions'")


@ci_app.command("install")
def ci_install(
    package: Annotated[str, typer.Option(help="pip spec CI uses to install BrainForge.")] = "brainforge",
    remind: Annotated[str, typer.Option(help="Cron (UTC) for the approval reminder issue.")] = "0 7 * * 1-5",
) -> None:
    """Write the GitHub Actions workflows: ingest documents pushed to inbox/, and, when team members have a
    `github` login, approval reminders in an issue whose comments approve or discard."""
    b = current()
    out = [schedule.write_github_ingest(b, package)]
    if any(m.github for m in b.manifest.team):
        out.append(schedule.write_github_approvals(b, package, remind))
    rels = [b.rel(p) for p in out]
    emit({"workflows": rels}, f"Wrote {', '.join(rels)}; add the agent secret, then commit and push them.")


def _repo_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}" if repo else None


@ci_app.command("remind")
def ci_remind(
    since: Annotated[str | None, typer.Option(
        help="Only items changed after this commit (the reminder after an ingestion).")] = None,
    notify: Annotated[bool, typer.Option("--notify", help="First leave a message with the approvers of each newly "
                                         "overdue item, once per item, commit and push it, and mention them.")] = False,
) -> None:
    """Print the reminder for a GitHub issue (Markdown with @mentions); nothing if nothing needs anyone."""
    b = current()
    _sync(b)
    parts = []
    if notify:
        start, sent = gitops.head(b.root), briefing.notify(b)
        if sent:
            gitops.commit(b.root, f"notify: {len(sent)} overdue item(s)", [rel for _, _, rel in sent])
            if gitops.has_upstream(b.root):
                gitops.publish(b.root, start)
        parts.append(briefing.github(b, sent, _repo_url()))
    parts.append(approval.reminder(b, _repo_url(), since=since))
    if text := "\n\n".join(p for p in parts if p):
        typer.echo(text)


@ci_app.command("decide")
def ci_decide(
    author: Annotated[str, typer.Option(help="GitHub login of the person who commented.")],
    proposals_of: Annotated[str | None, typer.Option("--proposals", help="The report of a proposals issue: its "
                                                     "`approve 1 3` / `discard 2` lines decide on those.")] = None,
) -> None:
    """Apply the `approve …` / `discard …` lines of a GitHub comment (stdin) as that team member, and push."""
    if proposals_of:
        _decide_proposals(current(), author, proposals_of, sys.stdin.read())
    else:
        _decide_comment(current(), author, sys.stdin.read())


@ci_app.command("proposals")
def ci_proposals() -> None:
    """Open a GitHub issue for the leads when a report has proposals; close it once they are decided (needs gh)."""
    b = current()
    _sync(b)
    done = proposals.sync_issues(b, _repo_url())
    emit(done, "\n".join(done) or "No proposal issues to open or close.")


def _decide_proposals(b: Brain, author: str, report: str, text: str) -> None:
    commands = proposals.parse(text)
    if not commands:
        return
    member = _github_member(b, author)
    email = member.email or f"{member.github}@users.noreply.github.com"
    lines, failed, total = [], 0, 0
    for verb, numbers, edit in commands:
        total += 1
        try:
            result = proposals.decide(b, verb, numbers, member, target=report, edit=edit,
                                      author=f"{member.name} <{email}>", push=True)
        except (BrainError, gitops.GitError) as exc:
            failed += 1
            lines.append(f"- `{verb} {' '.join(map(str, numbers))}` failed: {exc}")
            continue
        failed += bool(result["failed"])
        lines += [f"- {line}" + (f" ({result['commit'][:7]})" if result["commit"] else "") for line in result["lines"]]
        for s in result["sent"]:  # the mention is what notifies them on GitHub
            to = approval.member(b, s["to"])
            lines.append(f"  - to {'@' + to.github if to.github else to.name}: {s['text']}")
    left = next((len(w["proposals"]) for w in proposals.waiting(b) if w["report"] == report), 0)
    typer.echo("\n".join(lines) + (f"\n\n{left} proposal(s) still waiting." if left else "\n\nAll proposals decided."))
    raise typer.Exit(0 if not failed else 1 if failed == total else 3)


@ci_app.command("rounds")
def ci_rounds() -> None:
    """Open a GitHub issue for each round that opened, close those whose report is written (needs gh)."""
    b = current()
    _sync(b)
    done = rounds.sync_issues(b, _repo_url())
    emit(done, "\n".join(done) or "No round issues to open or close.")


@ci_app.command("reply")
def ci_reply(
    author: Annotated[str, typer.Option(help="GitHub login of the person who replied.")],
    round_id: Annotated[str | None, typer.Option("--round", help="The round of the issue replied on.")] = None,
) -> None:
    """A reply on a round's GitHub issue (stdin): `approve …` / `discard …` lines decide; anything else is the
    person's update, ingested as theirs and pushed. Prints the answer to post."""
    b = current()
    text = sys.stdin.read()
    if approval.parse_commands(text):
        _decide_comment(b, author, text)
        return
    member = _github_member(b, author)
    [o] = _ingest_note(b, agents.resolve(b), text, member.name, True, round_id=round_id or None, what="Update")
    if o.status != "ingested":
        typer.echo(f"Your update could not be taken in: {o.message}")
        raise typer.Exit(1)
    counted = (okf.read(b.root / o.source).meta.get("brainforge") or {}).get("rounds") if o.source else None
    lines = [f"Thanks, @{author}. " + ("Your update is in." if counted else
                                       f"Round {round_id} has closed; your update went into the brain anyway."
                                       if round_id else "Your note is in.")]
    # The ingestion commit is HEAD after the push. A round update's changes wait for its sender.
    if changes := approval.reminder(b, _repo_url(), only=member if counted else None, since="HEAD~1"):
        lines += ["", "This is what the brain took from it. If it is right, reply with the `approve …` lines; "
                  "if not, correct it in another reply.", "", changes]
    else:
        lines.append("It didn't change anything in the brain.")
    typer.echo("\n".join(lines))


def _github_member(b: Brain, login: str) -> brain_mod.TeamMember:
    member = next((m for m in b.manifest.team if m.github and m.github.lower() == login.lower()), None)
    if not member:
        typer.echo(f"@{login} has no `github` entry in the brain.yaml team, so nothing was done.")
        raise typer.Exit(1)
    return member


def _decide_comment(b: Brain, author: str, text: str) -> None:
    commands = approval.parse_commands(text)
    if not commands:
        return
    member = _github_member(b, author)
    email = member.email or f"{member.github}@users.noreply.github.com"
    baseline, lines, failed = gitops.untracked(b.root), [], 0
    for verb, refs, audience in commands:
        try:
            result = approval.decide(b, verb, refs, audience=audience, by=email, author=f"{member.name} <{email}>",
                                     push=True)
            lines += [f"- {line} ({result['commit'][:7]})" for line in result["lines"]]
        except (BrainError, gitops.GitError) as exc:
            gitops.rollback(b.root, baseline)
            failed += 1
            lines.append(f"- `{verb} {' '.join(refs)}` failed: {exc}")
    rest = approval.reminder(b, _repo_url(), only=member)
    typer.echo("\n".join(lines) + ("\n\nStill waiting for you:\n" + rest if rest else "\n\nNothing else waits for you."))
    raise typer.Exit(0 if not failed else 1 if failed == len(commands) else 3)


# --- plugins -----------------------------------------------------------------------------------

@plugin_app.command("add")
def plugin_add(source: Annotated[str, typer.Argument(
        help="A bundled plugin name (e.g. pm), a plugin folder, or a git URL (URL#folder for a plugin inside a "
             "repository).")]) -> None:
    """Copy a plugin into the brain's plugins/ folder (again: update it). Commit it to share it."""
    b = current()
    p = plugins.add(b, source)
    parts = [f"types: {', '.join(t.type for t in p.types)}" if p.types else "",
             f"procedures: {', '.join(f.stem for f in p.files('procedures', '*.md'))}" if p.files("procedures") else "",
             f"scripts: {', '.join(f.stem for f in p.files('scripts'))}" if p.files("scripts") else "",
             f"models: {', '.join(f'{p.name}/{f.stem}' for f in p.files('models', '*.md'))}" if p.files("models") else ""]
    emit({"plugin": p.name, "version": p.version, "path": b.rel(p.path)},
         f"Added plugin {p.name} v{p.version} in {b.rel(p.path)}/ ({'; '.join(x for x in parts if x)}).\n"
         f"Commit it: git add plugins && git commit -m \"plugin: {p.name}\"")


@plugin_app.command("list")
def plugin_list() -> None:
    """Plugins in this brain, and those that ship with BrainForge."""
    try:
        here = plugins.installed(current())
    except BrainError:
        here = []
    names = {p.name for p in here}
    rows = [(p, "installed") for p in here] + [(p, "available") for p in plugins.available() if p.name not in names]
    emit([{"name": p.name, "version": p.version, "status": s, "description": p.description} for p, s in rows],
         "\n".join(f"{p.name} v{p.version} ({s}): {p.description}" for p, s in rows) or "No plugins.")


@plugin_app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def plugin_run(
    plugin: Annotated[str, typer.Argument(help="Plugin name.")],
    script: Annotated[str, typer.Argument(help="Script in the plugin's scripts/ folder.")],
    args: Annotated[list[str] | None, typer.Argument(help="Arguments for the script.")] = None,
) -> None:
    """Run a plugin script in the brain root."""
    raise typer.Exit(plugins.run_script(current(), plugin, script, args or []))


# --- integrations ------------------------------------------------------------------------------

@skills_app.command("install")
def skills_install(
    agent: Annotated[str, typer.Argument(help="claude or codex.")],
    user: Annotated[bool, typer.Option("--user", help="Install for your user instead of this brain repo.")] = False,
) -> None:
    """Install the BrainForge skill for Claude Code or Codex; in a brain, Claude Code also gets the briefing hook."""
    base = Path.home() if user else current().root
    path = integrations.install_skill(agent, base)
    hook = integrations.install_hook(base) if agent == "claude" and not user else None
    emit({"skill": str(path), "hook": str(hook) if hook else None},
         f"Installed {path}" + (f"\nSessions in this brain start with the briefing ({hook})" if hook else ""))


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


@app.command()
def mcp(
    brains: Annotated[list[Path] | None, typer.Argument(help="Brain directories (default: the current brain).")] = None,
    pull: Annotated[bool, typer.Option("--pull/--no-pull", help="git pull --ff-only every 5 minutes.")] = True,
    write: Annotated[bool, typer.Option("--write", help="Also let the client act as you: approve and discard, "
                                        "send and dismiss messages. Commits by your git identity, pushed when the "
                                        "brain has a remote.")] = False,
) -> None:
    """Serve brains over MCP (stdio) for Claude Desktop and other MCP clients; read-only unless --write."""
    from brainforge import mcp_server  # the MCP SDK is only needed here

    roots = [brain_mod.find(p) for p in brains] if brains else [current()]
    if len({b.id for b in roots}) < len(roots):
        raise BrainError("two brains share the same id")
    mcp_server.serve(roots, pull, write)


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
