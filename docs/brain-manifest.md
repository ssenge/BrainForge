# Brain manifest (`brain.yaml`)

```yaml
schema_version: 1                # required to be 1; future versions come with a migration command
id: consulting/customer-a        # stable id: lowercase, [a-z0-9._-], segments separated by "/"
name: Customer A                 # display name
description: Account brain       # optional
agent:
  adapter: claude                # claude | codex | command
  model: claude-opus-5-5         # optional; passed to the agent CLI
  allowed: [claude]              # optional: adapters allowed to receive this brain's documents
  command: "my-agent --flag"     # adapter: command only (prompt on stdin, cwd = brain root)
git:
  commit: true                   # commit every ingestion/report (override per run: --no-commit)
  push: false                    # reserved; pushing is per run with --push
# parent: / children:            # reserved for Phase B (federation); rejected for now
```

- Unknown keys are rejected, so a typo fails `brainforge brain validate` with exit code 2.
- Directory names are fixed by convention (`inbox/`, `archive/`, `knowledge/`, `procedures/`, `templates/`, `reports/`); there is nothing to configure.
- **Agent selection order:**
  1. `--agent` / `--model` flags;
  2. `BRAINFORGE_AGENT` / `BRAINFORGE_MODEL` / `BRAINFORGE_AGENT_COMMAND` environment variables;
  3. this file.
- A brain is found by walking up from the current directory to the nearest `brain.yaml`. You can also set it with `--brain` or `BRAINFORGE_BRAIN`.
