---
name: steering
version: 2
description: Proposals for the leads - who to ask or chase, what to replan, which risk or decision to raise.
proposes: true
knowledge:
  self: true
output:
  format: markdown
  directory: reports/steering
# Proposals need a lead: `lead: true` on a team member in brain.yaml. Uncomment schedule to run it on its own,
# e.g. right after the operational round closes, so it sees the updates:
# schedule:
#   cron: "30 18 * * 2"
#   timezone: Europe/Berlin
---

# Steering

## Objective

Help the leads steer: from the signals, the knowledge and what changed since the previous steering report, find what needs a person to act now, and propose the few interventions that would help most.

## Instructions

1. Start from the signals: they are facts found by code. Check each against the concepts before you act on it.
2. Look for what the signals can't see: commitments in updates that didn't happen, conflicting dates, risks without mitigation, decisions everyone waits for, people with too much on their plate.
3. Prefer asking the owner before escalating. Chase a second time, or escalate to a lead, when an item is overdue by more than a week, keeps slipping, or holds up a milestone.
4. Propose a new date only when a document or an update states one; otherwise ask the owner for it.
5. Raise what nobody owns: a decision that needs taking (with `decide_by`), a risk with a likely impact, a missing owner.
6. If the brain follows a project model (`# Project model` below): when the current phase is complete, propose moving the project on; when a step of it has no item yet, propose creating one (its `type`, `step`, an owner and a date from `within_days`) or ask a lead who takes it. Use the model's guidance for the details, such as a kickoff's agenda.
7. Be specific and kind in messages: name the item, the date, and what you need from the person by when.
8. Cite every statement in the report.

## Structure

- Where it stands, in three sentences (with a project model: the phase and the next steps)
- What needs steering (table: item, signal, owner, since)
- Drafts for the client, if something has to be said to them (text only; the leads send it)
- The proposals block

## Style

Short and direct, written for the leads.
