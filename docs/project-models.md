# Project models

A project model is the method a project follows: its phases, and the steps in each. With one, BrainForge knows where the project stands and what comes next, and the steering report proposes it: "Delivery has started. Create the kickoff for Bob, by 7 Nov?"

```
$ brainforge plan
Website relaunch · pm/client-project v1 · phase delivery since 2026-10-28
  sales     done
  delivery  kickoff open (Bob Lee, due 2026-11-07) · requirements not started · status-reporting not started (optional) · acceptance not started
  closure   not started
```

## A model is a Markdown file

```markdown
---
name: client-project
version: 1
description: A client project from first contact to closure.
phases:
  - name: sales
    steps:
      - {name: qualification, title: Qualification, type: Action Item}
      - {name: contract, title: Contract signed, type: Milestone}
  - name: delivery
    steps:
      - {name: kickoff, title: Kickoff meeting, type: Milestone, within_days: 10}
      - {name: status-reporting, title: Regular status reporting set up, type: Action Item, optional: true}
---

# Client project

## delivery/kickoff

A meeting with the client within the first two weeks. Agenda: goals and scope, team and roles, plan and milestones, reporting, risks. Invite the client's sponsor.
```

| Key | Meaning |
|---|---|
| `phases[].name`, `steps[].name` | Lowercase kebab-case, unique. A step's id is `<phase>/<step>`. |
| `title` | Shown to people and agents. |
| `type` | The plugin type of the step's item, such as `Milestone`. Must be a type of an installed plugin. |
| `within_days` | Due this many days after its phase started. Later, and not done, it is a *step late* signal. |
| `optional` | The phase is complete without it. |

The body is guidance for agents: what a good kickoff covers, who signs off acceptance. The steering agent reads all of it.

## Following a model

```yaml
# brain.yaml
model: pm/client-project      # <plugin>/<model>, or just <model>
```

- A model is found in the brain's own `models/<model>.md` first, then in a plugin's `models/`. To adapt a plugin's model for one brain, copy it to `models/`.
- **The current phase** is `phase` on the project item, a concept of a plugin type with a `phase` field (pm: `knowledge/projects/*.md`, type Project). It is a fact like any other: a document states it, or a lead approves steering's proposal to move on. Git history says since when.
- **Steps are done through items**: concepts with `step: delivery/kickoff`. The ingestion agent tags them when a document reports on a step; steering proposes them when a phase starts. A step is done when it has items and all of them are finished (the type's `done` values).
- Proposals that `set` a `phase` or `step` must name one of the model's.

## What it adds

- `brainforge plan`: the phases, the steps, and what is open (`--json` for the details).
- **Signals** (`brainforge signals`): *phase* (none set), *next step* (a step of the current phase has no item yet), *step late* (past `within_days`), *phase complete* (every required step is done: move on?), *out of order* (a step of an earlier phase isn't done, or one of a later phase has started). Questions go to the items' owners, or to the leads for a step nobody has yet.
- **Steering** gets the model, the position and the guidance, and proposes: create the kickoff milestone with an owner and a date, move the project to the next phase, ask a lead who takes the contract.
- **The briefing** starts with the phase and its open steps; reports and queries know where the project stands.

## Your own model

Each company has its own method. Keep it in a repository of its own, as a plugin with a `models/` folder:

```
acme-method/
├── plugin.yaml          name: acme-method, description: …   (types are optional)
└── models/
    ├── fixed-price.md
    └── time-and-material.md
```

```bash
brainforge plugin add https://github.com/acme/acme-method.git        # copies it into plugins/acme-method/
git add plugins && git commit -m "plugin: acme-method"
# brain.yaml: model: acme-method/fixed-price
brainforge plan
```

The copy is versioned with the brain, so a running project keeps its method until someone runs `plugin add` again (review the change with `git diff`). The model's step types must come from an installed plugin, such as pm, or from types the method's plugin defines itself.

## Limits

- One model and one project item per brain. A brain for several projects needs a brain per project, or the hierarchy of brains on the roadmap.
- The model is linear: phases in order, steps without dependencies of their own. Use `depends_on` on the items for those.
- Moving to the next phase is never automatic: steering proposes it, a lead approves.
- A phase starts on the day of the commit that set it, not on a date a document gives ("as of 28 October"). Ingest promptly, or let the steering report point out the difference, as it does.
