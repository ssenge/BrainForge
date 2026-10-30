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
  push: false                    # true: every command that commits also pushes (else per run: --push)
audiences: [client, internal]    # optional: your own audiences, widest first (see below)
team:                            # optional: who approves what (see below)
  - name: Alice Smith
    email: alice@example.com     # matched against `git config user.email`
    aliases: [Alice]             # other spellings documents use for owners
    github: alice-s              # optional: GitHub login, for reminders and approving by issue comment
    lead: true                   # optional: decides on steering proposals; any one lead can
  - name: Bob Lee
    approves: [{type: Risk}]     # also approves unowned items matching these rules
needs_approval: [{type: Decision}, {type: Action Item}]   # optional: default is everything
# parent: / children:            # reserved for Phase B (federation); rejected for now
```

- Unknown keys are rejected, so a typo fails `brainforge brain validate` with exit code 2.
- Directory names are fixed by convention (`inbox/`, `archive/`, `knowledge/`, `procedures/`, `templates/`, `reports/`); there is nothing to configure.
- **Agent selection order:**
  1. `--agent` / `--model` flags;
  2. `BRAINFORGE_AGENT` / `BRAINFORGE_MODEL` / `BRAINFORGE_AGENT_COMMAND` environment variables;
  3. this file.
- **Audiences** are names you choose, ordered from widest to narrowest: `[client, internal]` for a consulting project, `[public, internal, confidential]` for a company, `[clear, green, amber, red]` for the Traffic Light Protocol. New knowledge is meant for the last, narrowest audience until an approver widens it. A report or query for an audience sees only approved knowledge meant for that audience or a wider one. Without the list, everything is visible to everyone.
- **Team and routing:** an item's approvers are the team members named in its `owner` field (by name, email or alias). An item without such an owner goes to the first member whose `approves` rules match. A rule maps frontmatter keys to case-insensitive globs, so `{type: "*"}` makes a catch-all and `{tags: "*security*"}` routes by tag. Order the list from specific to catch-all. Without a team, everyone sees every pending item.
- **`github`** lets the `brainforge-approvals` workflow @mention the person and accept their issue comments as their decisions (see the README, "Approving from GitHub").
- **`lead`** marks the people who steer: proposals from a procedure with `proposes: true` wait for them, and any one of them can approve or discard ([procedures.md](procedures.md#steering-signals-and-proposals)). Name more than one, so nothing waits for a single person.
- **`needs_approval`** limits approval to items matching these rules. Everything else is accepted as the agent wrote it, but its audience still defaults to the narrowest one.
- A brain is found by walking up from the current directory to the nearest `brain.yaml`. You can also set it with `--brain` or `BRAINFORGE_BRAIN`.
