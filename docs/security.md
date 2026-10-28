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

- Reports and queries for an audience (`audience: client` in a procedure, `--audience client` on a query) run the agent on a throwaway copy of the brain that holds only approved concepts meant for that audience or a wider one. Index files are rebuilt for the copy; the archive, log and configuration are left out. Links to concepts the audience may not see are removed, text included, because a link's text is usually the hidden concept's title. `approve --audience` lists such links, so the approver can widen a linked item (a person, say) when that is intended.
- After a report, BrainForge rejects it if it cites any concept outside that set.
- **Residual risk:** the copy is a separate folder, not a sandbox. The agent isn't told where the brain is, but file tools could read outside the copy if it went looking. The citation check catches cited leaks, not paraphrased ones. An audience is only as good as the approver's choice.

## Decisions by people, on what they saw

- `approve` and `discard` pull first and stop if the pull changed a named item. A pinned version (`item@3f2a1c9`) must still be the item's latest commit.
- Pushes never merge a decision into content the person didn't see. When both sides changed the same file, BrainForge undoes its own commits instead of merging.
- **Claude Desktop** (`brainforge mcp --approvals`, off by default) decides as the clone's git identity and accepts only `id@version` refs, which the agent gets from `pending`. The server's instructions tell the agent to ask the user about every item. Whether it does is up to the agent, as with any tool, so turn the option on only for a client you trust to ask.
- **Issue comments** decide only when the commenter has write access (`author_association` OWNER, MEMBER or COLLABORATOR) *and* is listed with that `github` login in `brain.yaml`. The comment reaches BrainForge as an environment variable, never as part of the script, and is parsed for `approve`/`discard` lines only.
- **Residual risk:** anyone who can push to the repository can also edit `brain.yaml` or the knowledge directly; approvals record who decided, they don't replace repository permissions. Each team member may decide on any item, not only their own: the routing tells people what to look at, it isn't access control.

## Plugins

- A plugin lives in the brain repository (`plugins/<name>/`), so it is reviewed and versioned like everything else. Its instructions come after the framework policy, which they can't override.
- Plugin scripts run only when someone calls `brainforge plugin run`, with that person's permissions; nothing runs them automatically. Treat them like any code you run.
- Agents can't change plugins: the post-run check allows changes only in `knowledge/`.

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
