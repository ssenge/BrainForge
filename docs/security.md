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

**Residual risk:** an injected document can still plant false "facts" that are attributed to that document. Provenance makes them traceable, and `brainforge knowledge verify` lets a human mark reviewed concepts.

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
