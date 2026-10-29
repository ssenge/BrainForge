---
name: operational-status
version: 1
description: Operational status for the client, from the team's updates in a round.
knowledge:
  self: true
output:
  format: markdown
  directory: reports/operational-status
# A round collects the team's updates first. Uncomment schedule and round: the round opens on Monday 08:00, and
# the report on Tuesday 18:00 closes it, ready for the client on Wednesday.
# schedule:
#   cron: "0 18 * * 2"
#   timezone: Europe/Berlin
# round:
#   opens: "0 8 * * 1"
#   from: [Alice Smith, Bob Lee]   # default: the whole team
# audience: client                 # only knowledge approved for the client
---

# Operational status

## Objective

Tell the client where the work stands this week, from the team's updates and the knowledge.

## Instructions

1. Start from the round's updates, if this report closes a round, and the concepts that changed since the previous report.
2. Per workstream or person: what was done, what's next, what's blocked.
3. List open action items with owner and due date; mark those past due as overdue.
4. Name who sent no update, plainly and without judging; their items show their last known state.
5. Cite every statement. Name missing information instead of guessing.

## Structure

- Summary
- Progress: done and next
- Blockers and help needed
- Open actions (table: action, owner, due, overdue)
- Updates in this round: who sent one, who didn't

## Style

Concise and factual, written for the client.
