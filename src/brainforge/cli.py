"""`brainforge` command-line interface."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.exceptions import TyperException

from brainforge import (
    agents,
    approval,
    gitops,
    ingest,
    integrations,
    okf,
    parsers,
    plugins,
    procedures,
    query,
    schedule,
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
for sub, name in [(brain_app, "brain"), (knowledge_app, "knowledge"), (inbox_app, "inbox"),
                  (procedures_app, "procedures"), (schedule_app, "schedule"),
                  (ci_app, "ci"), (skills_app, "skills"), (workspace_app, "workspace"), (plugin_app, "plugin")]:
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
    emit(results, "\n".join(f"{r['procedure']}: {r['status']}" + (f" -> {r['report']}" if r.get("report") else "")
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
        out = schedule.write_github_procedures(b, package)
        emit({"workflow": b.rel(out)}, f"Wrote {b.rel(out)}; commit and push it to activate.")
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
def ci_remind(since: Annotated[str | None, typer.Option(
        help="Only items changed after this commit (the reminder after an ingestion).")] = None) -> None:
    """Print the approval reminder for a GitHub issue (Markdown with @mentions); nothing if all is approved."""
    b = current()
    _sync(b)
    text = approval.reminder(b, _repo_url(), since=since)
    if text:
        typer.echo(text)


@ci_app.command("decide")
def ci_decide(author: Annotated[str, typer.Option(help="GitHub login of the person who commented.")]) -> None:
    """Apply the `approve …` / `discard …` lines of a GitHub comment (stdin) as that team member, and push."""
    b = current()
    commands = approval.parse_commands(sys.stdin.read())
    if not commands:
        return
    member = next((m for m in b.manifest.team if m.github and m.github.lower() == author.lower()), None)
    if not member:
        typer.echo(f"@{author} has no `github` entry in the brain.yaml team, so nothing was decided.")
        raise typer.Exit(1)
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
             f"scripts: {', '.join(f.stem for f in p.files('scripts'))}" if p.files("scripts") else ""]
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


@app.command()
def mcp(
    brains: Annotated[list[Path] | None, typer.Argument(help="Brain directories (default: the current brain).")] = None,
    pull: Annotated[bool, typer.Option("--pull/--no-pull", help="git pull --ff-only every 5 minutes.")] = True,
    approvals: Annotated[bool, typer.Option("--approvals", help="Also let the client record your decisions "
                                            "(pending, approve, discard): commits by your git identity, "
                                            "pushed when the brain has a remote.")] = False,
) -> None:
    """Serve brains over MCP (stdio) for Claude Desktop and other MCP clients; read-only unless --approvals."""
    from brainforge import mcp_server  # the MCP SDK is only needed here

    roots = [brain_mod.find(p) for p in brains] if brains else [current()]
    if len({b.id for b in roots}) < len(roots):
        raise BrainError("two brains share the same id")
    mcp_server.serve(roots, pull, approvals)


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
