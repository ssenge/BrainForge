---
name: status-report
version: 1
description: Project status report - milestones, decisions, actions and the RAID log.
knowledge:
  self: true
output:
  format: markdown
  directory: reports/status
# require_approval: true   # wait until every pending item is approved
# audience: client         # only knowledge approved for this audience (brain.yaml audiences)
# schedule:
#   cron: "0 8 * * 1"
#   timezone: Europe/Berlin
---

# Status report

## Objective

Give the reader the project status at a glance: where it stands, what changed since the previous report, and what needs attention.

## Instructions

1. Read the previous report, if one is given, and the concepts that changed since then.
2. State the overall status only as recorded (`rag` on the project); otherwise write "Overall status: not stated".
3. List milestones with baseline date, current date and state. Point out moved dates.
4. List open action items with owner and due date; mark those past due as overdue.
5. Cite every statement. Name missing information instead of guessing.

## Structure

- Summary
- Milestones (table: milestone, baseline, current date, state)
- Decisions since the previous report
- Open actions (table: action, owner, due, overdue)
- Risks and issues
- Assumptions and dependencies

## Style

Concise and factual. Tables over prose where it helps.
