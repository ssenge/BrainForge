# BrainForge

BrainForge turns documents into a structured, Git-versioned knowledge base, called a **brain**, that AI agents maintain and query. Each brain is a plain Git repository. Knowledge is stored as an [Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md) bundle: Markdown files with YAML frontmatter, readable without any tooling.

- **Git is the source of truth.** Every ingestion and every report is a commit; `git revert` undoes it.
- **No server or database.** Everything runs locally from the CLI. GitHub Actions is optional.
- **Bring your own agent.** Claude Code, Codex, or any command, run headless with each CLI's own login.
- **Extraction, not invention.** Every claim carries a footnote back to its source document. Conflicting statements are kept side by side, not overwritten.

> Status: Phase A, a single brain per repository. Parent/child brains and federation across repositories are planned for Phase B; see [`concept/implementation-plan.md`](concept/implementation-plan.md).

## Install

```bash
uv tool install git+<this repository>   # or: uv sync && uv run brainforge ...
```

Requirements:
- Python 3.12+ and `git`.
- An agent CLI: [Claude Code](https://docs.claude.com/en/docs/claude-code) (`claude`) or [Codex](https://github.com/openai/codex) (`codex`).
- Optional: [`pandoc`](https://pandoc.org) for DOCX, PPTX, HTML or PDF reports.

## Quick start

```bash
brainforge init quantum-research && cd quantum-research
brainforge ingest ~/papers/qaoa-review.pdf --instruction "Focus on optimization techniques"
brainforge knowledge search qaoa
brainforge knowledge query "Which QAOA optimization techniques are described?"
brainforge run weekly-report
```

`init` creates:

```
quantum-research/
├── brain.yaml          manifest: id, name, agent
├── instructions.md     guidance for every agent run (+ optional instructions/<activity>.md)
├── inbox/              drop documents here (+ <file>.sidecar.md)
├── archive/<sha12>/    originals, after successful ingestion
├── knowledge/          the OKF bundle (index.md and log.md are generated)
├── procedures/         Markdown procedures, e.g. weekly-report.md
├── templates/          reference.docx / corporate.pptx for rendered reports
└── reports/            generated reports
```

## Two ways to ingest

```
Local:  brainforge ingest path/to/doc.pdf [--instruction "…"]
        → the agent runs on your machine with your login → one commit → push when you like

Inbox:  git add inbox/doc.pdf [inbox/doc.pdf.sidecar.md]; git commit; git push
        → GitHub Actions runs `brainforge inbox process --push` (set up with `brainforge ci install`)
```

Both paths run the same pipeline:
1. Hash and deduplicate the document.
2. Parse it to text.
3. Create a source concept for it.
4. Let the agent edit `knowledge/` **in place**.
5. Validate the agent's changes:
   - only `knowledge/` was touched;
   - nothing was deleted;
   - every changed concept cites the new source;
   - existing provenance is kept;
   - the files are valid OKF.
6. Commit. If anything fails, the changes are rolled back with git.

`brainforge inbox process` also works locally, without CI.

### Telling the agent what to focus on

| Layer (lowest → highest precedence) | Where |
|---|---|
| Framework policy, which cannot be overridden | built in |
| Framework defaults for ingestion, query and reporting | built in |
| Brain instructions | `instructions.md`, `instructions/<activity>.md` |
| Commit trailer, for every document in that commit | `Ingest-Note: Focus on milestone changes` |
| Sidecar, for one document | `inbox/minutes.pdf.sidecar.md` |
| Invocation | `--instruction "…"` |

Instructions shift what the agent pays attention to; they never narrow what gets extracted. The source concept records the layers each run used, with content hashes.

### Idempotency

- A document is identified by the SHA-256 of its content.
- The ledger is the set of committed source concepts, so your laptop and CI share it through git.
- Ingesting the same bytes again, under any filename, does nothing. In the inbox, the duplicate is simply removed.
- A failed run leaves the working tree exactly as it was, so retrying is always safe.
- `--force` re-ingests a document and reuses its source concept.

## Agents and authentication

BrainForge never touches credentials; it runs the agent CLI, which logs itself in.

- **Selecting the agent:**
  1. `--agent` / `--model` flags;
  2. `BRAINFORGE_AGENT` / `BRAINFORGE_MODEL` environment variables;
  3. `agent:` in `brain.yaml`.
- **Restricting providers:** `agent.allowed` in `brain.yaml` limits which providers may see the brain's documents.

| Where | Claude Code | Codex |
|---|---|---|
| Local | `claude login` (subscription) or `ANTHROPIC_API_KEY` | `codex login` or `OPENAI_API_KEY` |
| GitHub Actions | secret `ANTHROPIC_API_KEY`, or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` | secret `OPENAI_API_KEY` |

Agent runs are isolated from your personal agent setup (hooks, plugins, CLAUDE.md, MCP servers):
- With an API key, BrainForge passes `--bare`.
- With a subscription login, it passes `--setting-sources "" --strict-mcp-config`.
- Codex runs with `--ignore-user-config --ignore-rules --ephemeral`.

The agent only gets file tools: no shell and no network.

With `adapter: command`, any program works. It receives the prompt on stdin, runs in the brain root, and gets `BRAINFORGE_MODE` set to `write` or `read`.

## Commands

| Command | Purpose |
|---|---|
| `init [DIR]` | Create a brain |
| `brain show`, `brain validate` | Summary; validate manifest, layout, knowledge and procedures |
| `ingest FILE…` | Ingest documents (`--instruction`, `--dry-run`, `--force`, `--no-commit`, `--push`) |
| `inbox process` | Ingest everything in `inbox/` |
| `knowledge search [TEXT]` | Deterministic search (`--type`, `--tag`, `--status`, `--links-to`, `--linked-from`) |
| `knowledge query QUESTION` | Agent answer with citations, read-only |
| `knowledge validate`, `knowledge index` | OKF checks; regenerate `index.md` files |
| `knowledge verify CONCEPT` | Record a human review (OKF `verified`) |
| `procedures list`, `procedures validate` | Procedures with schedule and last run |
| `run PROCEDURE` | Write a report now |
| `schedule tick` | Run whatever is due (call it from cron or CI) |
| `schedule install --provider cron\|github-actions` | Install the scheduler |
| `ci install` | GitHub Actions workflow for inbox ingestion |
| `skills install claude\|codex [--user]` | Agent skill that wraps the CLI |
| `workspace open` | Open the brain as an Obsidian vault |

Global options:
- `--brain PATH`: by default the brain is found from the current directory upwards, the way git finds its repo.
- `--json`: machine-readable output.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | failure |
| 2 | usage or config error |
| 3 | partial failure |

## Documentation

- [Knowledge profile](docs/knowledge-profile.md): how BrainForge uses OKF.
- [Brain manifest](docs/brain-manifest.md)
- [Procedures and reports](docs/procedures.md)
- [Automation: GitHub Actions and cron](docs/automation.md)
- [Security model](docs/security.md)
- Concept and plan: [`concept/`](concept/)

## Development

```bash
uv sync
uv run pytest            # uses a deterministic fake agent; no LLM calls
uv run ruff check src tests && uv run pyright
```

## License

Apache-2.0
