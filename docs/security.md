# Security model

BrainForge processes confidential documents with LLM agents. This document describes what protects the brain and what does not.

## Documents are untrusted

- The parsed document goes into the prompt inside a `<document>` block marked as untrusted data.
- The framework policy is always the first instruction layer and cannot be overridden. It tells the agent never to follow instructions found in documents.
- The agent gets file tools only: Claude Code runs with `--tools Read,Write,Edit,Glob,Grep` and Codex with `--sandbox workspace-write`. There is no shell and no network.
- After every run, **deterministic code checks what changed, using `git status`**:
  - only files under `knowledge/` may change (for reports, only the report file);
  - nothing may be deleted;
  - every changed concept must cite the new source;
  - existing provenance must be kept;
  - the result must be valid OKF.
- Anything else rolls the whole run back.

**Residual risk:** an injected document can still plant false "facts" that are attributed to that document. Provenance makes them traceable, and every item the agent creates or changes stays `pending` until a person approves it. The agent cannot approve its own output: BrainForge overwrites `approval` and `audience` after every run.

## Audiences: what a report or query may see

- Reports and queries for an audience (`audience: client` in a procedure, `--audience client` on a query) run the agent on a throwaway copy of the brain that holds only approved concepts meant for that audience or a wider one. Index files are rebuilt for the copy; the archive, log and configuration are left out.
- After a report, BrainForge rejects it if it cites any concept outside that set.
- **Residual risk:** the copy is a separate folder, not a sandbox. The agent isn't told where the brain is, but file tools could read outside the copy if it went looking. The citation check catches cited leaks, not paraphrased ones. An audience is only as good as the approver's choice.

## Data leaves your machine only through the agent you chose

- BrainForge sends document content only to the configured agent CLI.
- `agent.allowed` in `brain.yaml` lists the adapters that may receive a brain's data. An override outside that list fails.
- Credentials stay with the agent CLI. BrainForge never reads, stores or logs them.
- Agent runs are isolated from your personal agent setup, so your hooks, plugins, global CLAUDE.md and MCP servers neither see nor shape brain data:
  - with an API key: `--bare`;
  - with a subscription login: `--setting-sources "" --strict-mcp-config`;
  - Codex: `--ignore-user-config`.

## Git safety

- BrainForge never force-pushes.
- It refuses to run when tracked files have uncommitted changes, so it never mixes its changes with yours.
- It commits only the paths it touched (`git commit -- <paths>`).
- A per-repository lock (`.git/brainforge.lock`) prevents two concurrent local runs. In CI, the workflow's `concurrency` group does the same.
- Repository permissions are git's permissions: BrainForge never accesses anything your git credentials can't.
