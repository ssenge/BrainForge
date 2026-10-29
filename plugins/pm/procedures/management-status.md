---
name: management-status
version: 1
description: Management status - milestones, risks and decisions needed, from the leads' updates in a round.
knowledge:
  self: true
output:
  format: markdown
  directory: reports/management-status
# Uncomment schedule and round: the round opens on Tuesday 08:00, and the report at the end of Wednesday closes it.
# schedule:
#   cron: "59 23 * * 3"
#   timezone: Europe/Berlin
# round:
#   opens: "0 8 * * 2"
#   from: [Carol Diaz]               # e.g. the leads; default: the whole team
---

# Management status

## Objective

Give management the project's position: status, milestones, risks, and what needs their decision.

## Instructions

1. Start from the round's updates, if this report closes a round, and the latest operational status report under `reports/operational-status/`, if there is one from this week.
2. State the overall status only as recorded (`rag` on the project); otherwise write "Overall status: not stated".
3. List milestones with baseline date, current date and state. Point out moved and missed dates.
4. List the risks and issues that need management attention, and the decisions that are pending.
5. Name who sent no update, plainly and without judging.
6. Cite every statement. Name missing information instead of guessing.

## Structure

- Summary
- Milestones (table: milestone, baseline, current date, state)
- Risks and issues for management
- Decisions needed
- Updates in this round: who sent one, who didn't

## Style

Short. Lead with what management has to act on.
