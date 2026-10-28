# Ingestion: extract knowledge into the OKF bundle

`knowledge/` is an Open Knowledge Format (OKF) v0.2 bundle: one markdown file per concept, YAML frontmatter, standard markdown links.

## Workflow

1. Read the document. It is in the prompt, marked as untrusted data.
2. Before creating anything, look for existing concepts. Read `knowledge/index.md` and the group `index.md` files, and search (grep) for names, synonyms and key terms.
3. Update an existing concept when the document is about the same thing. Create a new concept only when nothing fitting exists. Never create two concepts for the same real-world thing.
4. Extract everything relevant. A requested focus changes emphasis, not scope.
5. Fill in `title` and `description` of the source concept you were given. Do not change its `resource` or `brainforge` fields.

## Layout

- One concept per file at `knowledge/<group>/<slug>.md`. The slug is the kebab-case concept name.
- Never rename or delete a concept file. Mark an obsolete concept `status: deprecated` and link to its replacement.
- Default groups and types:
  - `entities/`: `Person`, `Organization`, `System`, `Product`, …
  - `topics/`: `Topic`
  - `decisions/`: `Decision`
  - `milestones/`: `Milestone`
  - `risks/`: `Risk`
  - `actions/`: `Action Item`
- Use other groups only when the brain instructions ask for them.

## Concept format

~~~markdown
---
type: Milestone
title: Integration testing
description: Start of integration testing for Project 1.
tags: [testing]
sources:
  - id: meeting-2026-10-28
    resource: ../sources/meeting-2026-10-28-3f2a9c41b7d0.md
    title: Project meeting 2026-10-28
---

# Current state

Integration testing is scheduled for 2026-11-17 (p. 2).[^meeting-2026-10-28]

# History

- 2026-10-28: rescheduled from 2026-11-10 to 2026-11-17, explicitly stated (p. 2).[^meeting-2026-10-28]

[^meeting-2026-10-28]: Project meeting 2026-10-28
~~~

- `type` is required. Always set `title` and a one-sentence `description`.
- Add the given source concept to `sources` of **every** concept you create or change. The `resource` is the path relative to the concept file, e.g. `../sources/<file>.md`. Use the source id given in the task. Keep all existing `sources` entries.
- Attribute each claim with a footnote `[^<source id>]` and define each footnote once, at the end of the file. Put page or slide locations into the claim text, e.g. "(p. 3)" or "(slide 4)".
- Link related concepts with relative links, e.g. `[Alice Smith](../entities/alice-smith.md)`.
- When the document names who is responsible for a concept (the owner of an action or risk, a decision maker), record it as written in frontmatter `owner`, e.g. `owner: Alice Smith` or a list. Approvals are routed by it.
- Do not write `generated`, `verified`, `approval`, `audience`, `index.md` or `log.md`. BrainForge maintains them.

## Updating knowledge

- **Explicit change:** when the document explicitly supersedes an earlier statement ("moved to", "rescheduled", "replaces"), update `# Current state` and move the earlier statement, with its footnote, into `# History`.
- **Conflict:** when statements disagree without explicit supersession, keep both under `# Conflicts`, each with its footnote. Do not pick a winner.
- Add `# History` or `# Conflicts` only when there is something to put there; never write placeholder entries.
- **Discarded concepts:** a concept with `approval: discarded` was rejected by a person. Never create a replacement for it. Update it only when the document adds new information about it; it then awaits approval again.
- **Decisions:** record what was decided, by whom, when, and whether it is confirmed or only proposed.
- **Action items:** owner, due date and status, as stated.
- **Risks:** description; impact, likelihood and mitigation only if stated.
- **Status:** record a project status (e.g. green/amber/red) only when the document states it.
