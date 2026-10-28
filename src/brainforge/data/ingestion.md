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
- Use other groups only when plugin types or the brain instructions ask for them.

## Concept format

~~~markdown
---
type: System
title: Data platform
description: The central data platform that consolidates reporting data.
tags: [infrastructure]
sources:
  - id: architecture-review-2026-10-28
    resource: ../sources/architecture-review-2026-10-28-3f2a9c41b7d0.md
    title: Architecture review 2026-10-28
---

# Current state

The data platform runs on PostgreSQL 16 (p. 2).[^architecture-review-2026-10-28]

# History

- 2026-10-28: upgraded from PostgreSQL 15 to 16, explicitly stated (p. 2).[^architecture-review-2026-10-28]

[^architecture-review-2026-10-28]: Architecture review 2026-10-28
~~~

- `type` is required. Always set `title` and a one-sentence `description`.
- Add the given source concept to `sources` of **every** concept you create or change. The `resource` is the path relative to the concept file, e.g. `../sources/<file>.md`. Use the source id given in the task. Keep all existing `sources` entries.
- Attribute each claim with a footnote `[^<source id>]` and define each footnote once, at the end of the file. Put page, slide or transcript-time locations into the claim text, e.g. "(p. 3)", "(slide 4)" or "(00:14:32)". In a transcript, the speaker of a `[hh:mm:ss] Speaker: …` line is who said it; record commitments with the speaker as owner unless the line names someone else.
- Link related concepts with relative links, e.g. `[Alice Smith](../entities/alice-smith.md)`.
- When the document names who is responsible for a concept, record it as written in frontmatter `owner`, e.g. `owner: Alice Smith` or a list. Approvals are routed by it.
- Set other frontmatter fields only when the document states them.
- Do not write `generated`, `verified`, `approval`, `audience`, `index.md` or `log.md`. BrainForge maintains them.

## Updating knowledge

- **Explicit change:** when the document explicitly supersedes an earlier statement ("moved to", "changed to", "replaces"), update `# Current state` and move the earlier statement, with its footnote, into `# History`.
- **Conflict:** when statements disagree without explicit supersession, keep both under `# Conflicts`, each with its footnote. Do not pick a winner.
- Add `# History` or `# Conflicts` only when there is something to put there; never write placeholder entries.
- **Discarded concepts:** a concept with `approval: discarded` was rejected by a person. Never create a replacement for it. Update it only when the document adds new information about it; it then awaits approval again.
