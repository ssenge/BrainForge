---
name: client-project
version: 1
description: A client project from first contact to closure. A demo; bring your own with `brainforge plugin add`.
phases:
  - name: sales
    steps:
      - {name: qualification, title: Qualification, type: Action Item}
      - {name: proposal, title: Proposal sent, type: Milestone}
      - {name: contract, title: Contract signed, type: Milestone}
  - name: delivery
    steps:
      - {name: kickoff, title: Kickoff meeting, type: Milestone, within_days: 10}
      - {name: requirements, title: Requirements agreed, type: Milestone, within_days: 30}
      - {name: status-reporting, title: Regular status reporting set up, type: Action Item, within_days: 14, optional: true}
      - {name: acceptance, title: Client acceptance, type: Milestone}
  - name: closure
    steps:
      - {name: invoice, title: Final invoice, type: Action Item, within_days: 14}
      - {name: lessons, title: Lessons learned, type: Milestone, within_days: 30}
      - {name: handover, title: Handover and archive, type: Action Item, optional: true}
---

# Client project

A client project goes from sales through delivery to closure. A phase is complete when its steps are done or cancelled; optional steps don't hold it up. The leads move the project on.

## sales/qualification

Is it worth pursuing: the client's need, budget, decision makers and timeline, and whether the team can deliver. The owner is whoever leads the sales conversation.

## sales/proposal

Scope, approach, plan, team, price and assumptions, sent to the client's decision maker.

## sales/contract

Signed by both sides. Until then, delivery work is at the team's own risk: say so if delivery starts early.

## delivery/kickoff

A meeting with the client within the first two weeks. Agenda: goals and scope, team and roles on both sides, plan and milestones, how status is reported and decisions are taken, known risks. Invite the client's sponsor. The minutes go into the brain.

## delivery/requirements

What will be built, agreed with the client in writing. Open questions become decisions with a `decide_by` date.

## delivery/status-reporting

Who reports what to whom, and when: for example a round for the operational status and one for management.

## delivery/acceptance

The client confirms that the result meets the agreed requirements. Name who on the client side accepts, and how.

## closure/invoice

The final invoice, after acceptance.

## closure/lessons

A short session with the team: what went well, what to do differently, what to keep for the next project.

## closure/handover

Hand over what the client keeps, archive what the team keeps, and close access that is no longer needed.
