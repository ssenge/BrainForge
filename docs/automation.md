# Automation: GitHub Actions and cron

Nothing runs permanently. Cron and GitHub Actions only call the CLI, and git holds all state.

## Inbox ingestion on push

```bash
brainforge ci install [--package "brainforge @ git+https://…"]
git add .github && git commit -m "ci: brainforge ingest" && git push
```

What `.github/workflows/brainforge-ingest.yml` does:
- Triggers on pushes that touch `inbox/**`, and on manual dispatch.
- Checks out the **branch head**, not the event commit. A re-run therefore always sees the latest ledger, and duplicates are skipped.
- Installs BrainForge and the agent CLI, then runs `brainforge inbox process --push`.
- Puts all runs in one `concurrency` group: `brainforge-<repo>`, never cancelled.
- Guards against loops: pushes made with `GITHUB_TOKEN` don't trigger workflows, and commits carrying a `BrainForge-Run:` trailer are skipped.

Secrets, under Settings → Secrets and variables → Actions:

| Agent | Secret |
|---|---|
| Claude Code | `ANTHROPIC_API_KEY`, or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` (subscription; check the terms of use) |
| Codex | `OPENAI_API_KEY` |

Commit trailers give batch-level instructions:

```
ingest: steering committee

Ingest-Note: Focus on new delivery risks
```

If a push is rejected because someone else pushed first, the run fails. Re-running it is safe.

## Scheduled procedures

`brainforge schedule tick` runs every procedure whose most recent scheduled slot has no successful report after it:
- The slot is computed in the procedure's own timezone, so daylight-saving changes are handled.
- The "last run" is read from the latest report's frontmatter in git.
- Delayed triggers, duplicate triggers and manual runs are therefore all harmless.
- A procedure that has never run fires only within one day of its slot.

### GitHub Actions

```bash
brainforge schedule install --provider github-actions
```

This generates `.github/workflows/brainforge-procedures.yml` from the procedures' frontmatter:
- one `cron` + `timezone` entry per scheduled procedure (GitHub supports IANA timezones);
- `workflow_dispatch` with an optional `procedure` input to run one procedure now.

Run the command again after changing a schedule. GitHub may delay scheduled runs, and `tick` tolerates that.

### Local cron

```bash
brainforge schedule install --provider cron      # or --print to see the line
```

This adds one crontab entry that runs `schedule tick` every 15 minutes in the brain directory. The entry carries your current `PATH`, so cron can find the agent CLI. Output goes to `.brainforge/cron.log`.
