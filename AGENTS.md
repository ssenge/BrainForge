# AGENTS.md

Start here if you are an AI coding agent working on **BrainForge**, the framework itself. For using a *brain* (searching, asking, ingesting), see the [README](README.md) and `brainforge mcp` / `brainforge skills install`.

BrainForge is a Python CLI that turns documents into an OKF v0.2 knowledge base inside a Git repository (a "brain"), using a headless coding agent (Claude Code, Codex or any command) for the extraction step. Status: Phase A, one brain per repository, is complete. Phase B, a hierarchy of brains, is planned (README → Roadmap).

## Commands

```bash
uv sync                                   # set up .venv (Python 3.12+)
uv run pytest -q                          # whole suite, ~40 s, no LLM calls
uv run ruff check src tests               # lint (line length 120)
uv run pyright                            # type check, must stay at 0 errors
uv run brainforge --help                  # the CLI from source
```

Run all three checks before you commit. They are all clean on `main`.

## Where things are

| Path | What |
|---|---|
| `src/brainforge/cli.py` | Typer CLI; every command lives here and calls into the modules below |
| `src/brainforge/ingest.py` | The ingestion pipeline: hash → dedupe → parse → agent → validate → commit, or rollback |
| `src/brainforge/okf.py` | OKF v0.2: frontmatter, validation, `index.md`/`log.md` generation, deterministic search |
| `src/brainforge/brain.py` | `brain.yaml` model, brain discovery, `init`, instruction layers |
| `src/brainforge/agents.py` | Agent adapters (`claude`, `codex`, `command`) and the exact CLI flags they run with |
| `src/brainforge/gitops.py` | Thin wrapper around the `git` CLI: status, commit, rollback, pull/push, lock |
| `src/brainforge/parsers.py` | PDF/DOCX/PPTX/HTML/MD → text with page and slide markers |
| `src/brainforge/approval.py` | Approval status, approver routing, the pending scanner, audience views |
| `src/brainforge/query.py` | Read-only question answering by an agent, with citation checks |
| `src/brainforge/procedures.py` | Markdown procedures → reports (pandoc for DOCX/PPTX/HTML/PDF) |
| `src/brainforge/schedule.py` | `schedule tick`, plus generators for cron entries and GitHub workflows |
| `src/brainforge/mcp_server.py` | Read-only MCP server (`list_brains`, `search`, `read`) for Claude Desktop and others |
| `src/brainforge/integrations.py` | Agent skill install, Obsidian vault config |
| `src/brainforge/data/` | All prompts and templates: `policy.md` (non-overridable), `ingestion.md`, `query.md`, `reporting.md`, `init/`, `workflows/`, `skills/` |
| `tests/` | pytest; `fake_agent.py` is a deterministic stand-in for the agent CLI |
| `docs/` | User docs: knowledge profile, manifest, procedures, automation, security |

## Invariants: don't break these

- **Git is the undo.** The agent edits the brain in place. On failure BrainForge restores the tree with git (`gitops.rollback`). No sandboxes, staging copies or recovery files.
- **One commit per document or report**, with `BrainForge-*` trailers. Commit only the paths the run touched. Never force-push.
- **Idempotency** comes from the SHA-256 of the document bytes. The ledger is the set of committed source concepts (`knowledge/sources/*.md`, `brainforge.sha256`). No other state store.
- **Deterministic code checks every agent run.** After a run, `git status` must show changes only in `knowledge/` (for reports, only the report file), no deletions, valid OKF, every changed concept citing the new source, and old provenance kept. Keep LLM behavior out of the deterministic modules.
- **People approve, not agents.** After every agent run BrainForge sets `approval: pending` and restores `audience`; the agent's values never survive. Audience-restricted reports and queries run on `approval.view`, a copy holding only what that audience may see.
- **Documents are untrusted data.** They go inside a `<document>` block, and `data/policy.md` is always the first, non-overridable instruction layer.
- **BrainForge never handles credentials.** Agent CLIs log themselves in. Don't read, store, log or pass API keys.
- **Agent isolation:** Claude Code runs with `--bare` (API key) or `--setting-sources "" --strict-mcp-config` (subscription), and with file tools only. Keep it that way.
- **OKF v0.2 with relative links.** Unknown frontmatter keys must survive a round trip. `index.md` and `log.md` are generated; never edit them by hand or let the agent edit them.
- **Phase B seams:** `parent` and `children` in `brain.yaml`, and hierarchy scopes in procedures, are deliberately rejected with "not supported yet". Don't half-implement them.

## Conventions

- **Don't overengineer.** Prefer git, the stdlib and existing dependencies over new mechanisms. Mark deliberate shortcuts with a `# ponytail:` comment that names the limit and the upgrade path.
- **External tools run as subprocesses:** `git`, `claude`, `codex`, `pandoc`. No GitPython, no agent SDKs.
- **Typer 0.27 vendors click.** Use `typer.Exit`, `typer.Abort` and `typer.exceptions.TyperException`; never `import click`.
- **CLI exit codes:** 0 ok, 1 failure, 2 usage or config error (`BrainError`), 3 partial failure.
- **Command output goes through `emit()` in `cli.py`**, which honours the global `--json`.
- **The MCP SDK is v2:** `from mcp.server.mcpserver import MCPServer`. `ToolAnnotations` fields are snake_case (`read_only_hint`).
- **Tests are hermetic:** `conftest.py` isolates git config and points `BRAINFORGE_AGENT_COMMAND` at `fake_agent.py`. When you add agent behavior, teach the fake agent a mode instead of calling a real LLM.
- **Commit messages** use a short prefix (`feat:`, `docs:`, `ingestion:`, …) and a one-line summary.

## Gotchas

- GitHub doesn't apply `paths:` filters to the push that creates a branch, so the inbox workflow won't fire on a repository's first push. Use `gh workflow run brainforge-ingest.yml`.
- Generated workflows install BrainForge with `--package "brainforge @ git+https://github.com/ssenge/BrainForge.git"`, because the package isn't on PyPI yet.
- Desktop apps (Claude Desktop) don't inherit the shell `PATH`, so MCP configs need the absolute path to `brainforge`.
- Changes to `src/brainforge/data/*.md` change agent behavior. Re-run the tests, and for real behavior changes do one manual run with a real agent (e.g. `--model claude-haiku-5-5`).
