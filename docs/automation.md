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

If someone else pushed first, the run rebases onto their commits and pushes, as long as the two changed different files. If both changed the same file (another ingestion, or a decision on knowledge this run also changed), the run undoes its commits, pulls, and ingests again on top of the other side's work, up to three times. That is safe because ingestion is idempotent: a document the other side already ingested is skipped. A decision (`approve`, `discard`) never retries on its own, because the person decided on a version that is gone.

## Approvals from GitHub issues

When team members in `brain.yaml` have a `github` login, `brainforge ci install` also writes `.github/workflows/brainforge-approvals.yml`:
- **Reminder** (`schedule`, `--remind CRON` in UTC, default weekdays 07:00; manual dispatch; and after each run of `brainforge-ingest`): `brainforge ci remind` lists pending items per approver. After an ingestion, `--since <commit>` limits it to the items that ingestion created or changed. The scheduled and manual runs add `--notify`: first they leave a message with the owner of each newly overdue item, commit and push it, and @mention those owners. The message's path names the item and its date, and git history remembers it after it was dismissed, so each owner hears about an item once per date. The text is posted to the open issue labelled `brainforge-approvals` (created on first use), so the @mentioned people get notified. When nothing is pending, nothing is posted.
- **Decisions** (`issue_comment` in that issue): `brainforge ci decide --author <login>` reads the comment and applies its `approve …` / `discard …` lines as the team member with that login, pushes, and replies with the outcome. Quoted lines (`> …`) are ignored.
- Same `concurrency` group as ingestion, so decisions and ingestion runs never overlap on GitHub.
- No agent and no secret: it uses the workflow's own `GITHUB_TOKEN` with `contents: write` and `issues: write`.

## Scheduled procedures

`brainforge schedule tick` runs every procedure whose most recent scheduled slot has no successful report after it, then updates the round pages (a new page when a round opens, the report link once it closes):
- The slot is computed in the procedure's own timezone, so daylight-saving changes are handled.
- The "last run" is read from the latest report's frontmatter in git.
- Delayed triggers, duplicate triggers and manual runs are therefore all harmless.
- A procedure that has never run fires only within one day of its slot.

### GitHub Actions

```bash
brainforge schedule install --provider github-actions
```

This generates `.github/workflows/brainforge-procedures.yml` from the procedures' frontmatter:
- one `cron` + `timezone` entry per scheduled procedure (GitHub supports IANA timezones), and one for each round's opening;
- `workflow_dispatch` with an optional `procedure` input to run one procedure now.

Run the command again after changing a schedule or a round. GitHub may delay scheduled runs, and `tick` tolerates that.

### Rounds on GitHub

When a procedure has a [round](procedures.md#rounds-asynchronous-status-meetings), the same command also writes `.github/workflows/brainforge-rounds.yml`. It needs team members with a `github` login in `brain.yaml` and the agent secret of `brainforge-ingest`:
- **Round issues** (after each run of `brainforge-procedures`, and manual dispatch): `brainforge ci rounds` opens an issue labelled `brainforge-round` for each round that opened. It @mentions the participants, lists each one's open items with a date, and links the round page. Once the round's report is written, it closes the issue with a link to the report. Open issues are the state, so running it again does nothing new.
- **Replies** (`issue_comment` on a round issue): `brainforge ci reply --author <login> --round <id>` ingests the reply as that person's update for that round, pushes, and answers with what the brain took from it, as `approve …` lines for them. A reply with `approve …` / `discard …` lines decides instead, as in the approvals issue.
- Only people with write access who are listed with their `github` login can send updates this way. Replies run without a `concurrency` group, since GitHub would cancel all but the newest waiting run; overlapping runs rebase or retry their push.

### Local cron

```bash
brainforge schedule install --provider cron      # or --print to see the line
```

This adds one crontab entry that runs `schedule tick` every 15 minutes in the brain directory. The entry carries your current `PATH`, so cron can find the agent CLI. Output goes to `.brainforge/cron.log`.
