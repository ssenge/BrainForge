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
visibility: [client, internal]   # optional: your own levels, most open first (see below)
team:                            # optional: who reviews what (see below)
  - name: Alice Smith
    email: alice@example.com     # matched against `git config user.email`
    aliases: [Alice]             # other spellings documents use for owners
  - name: Bob Lee
    reviews: [{type: Risk}]      # also reviews unowned items matching these rules
needs_review: [{type: Decision}, {type: Action Item}]   # optional: default is everything
# parent: / children:            # reserved for Phase B (federation); rejected for now
```

- Unknown keys are rejected, so a typo fails `brainforge brain validate` with exit code 2.
- Directory names are fixed by convention (`inbox/`, `archive/`, `knowledge/`, `procedures/`, `templates/`, `reports/`); there is nothing to configure.
- **Agent selection order:**
  1. `--agent` / `--model` flags;
  2. `BRAINFORGE_AGENT` / `BRAINFORGE_MODEL` / `BRAINFORGE_AGENT_COMMAND` environment variables;
  3. this file.
- **Visibility levels** are names you choose, ordered from most open to most restricted: `[client, internal]` for a consulting project, `[public, internal, confidential]` for a company, `[clear, green, amber, red]` for the Traffic Light Protocol. New knowledge gets the last, most restricted level until a reviewer opens it up. A report or query for an audience sees only confirmed knowledge at that level or more open. Without the list, everything is visible to everyone.
- **Team and routing:** an item's reviewers are the team members named in its `owner` field (by name, email or alias). An item without such an owner goes to the first member whose `reviews` rules match. A rule maps frontmatter keys to case-insensitive globs, so `{type: "*"}` makes a catch-all, and `{workstream: "*ws-03*"}` routes by workstream. Order the list from specific to catch-all. Without a team, everyone sees every pending item.
- **`needs_review`** limits review to items matching these rules. Everything else is accepted as written by the agent, but its visibility still defaults to the most restricted level.
- A brain is found by walking up from the current directory to the nearest `brain.yaml`. You can also set it with `--brain` or `BRAINFORGE_BRAIN`.
