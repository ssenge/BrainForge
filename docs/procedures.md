# Procedures and reports

A procedure is a Markdown file in `procedures/`: YAML frontmatter configures it, and the body holds instructions for the reporting agent. [Plugins](plugins.md) can bring procedures too, in `plugins/<name>/procedures/`; one in the brain's `procedures/` with the same name replaces the plugin's.

```markdown
---
name: weekly-report              # default: file name
version: 1
description: Weekly customer report.
agent: claude                    # optional adapter override
knowledge:
  self: true                     # ancestors/descendants: Phase B
audience: client                 # optional: one of brain.yaml's audiences
require_approval: true           # optional: refuse to run while knowledge awaits approval
output:
  format: markdown               # markdown | docx | pptx | html | pdf
  directory: reports/weekly      # default: reports/<name>
  filename: "{date}"             # {date} in the schedule's timezone, {name}
  template: templates/corporate.pptx   # pandoc --reference-doc for docx/pptx
schedule:                        # optional
  cron: "0 8 * * 3"
  timezone: Europe/Berlin
---

# Weekly Customer Report

## Instructions
1. Summarize progress, milestone changes, risks, decisions, next steps.
2. Cite every statement. Name missing information. Never invent a status.

## Structure
- Executive Summary …
```

## Running a procedure

```bash
brainforge procedures list
brainforge run weekly-report [--instruction "…"] [--force] [--push]
```

The agent receives three things:
- the instruction layers: framework policy and reporting defaults, `instructions.md`, `instructions/reporting.md`, the procedure body, and `--instruction`;
- the **previous report**;
- the **list of knowledge files changed since the previous report's commit**, taken from `git diff`.

The agent writes only the report file. If it touches anything else, the run is rolled back.

BrainForge then adds frontmatter to the report:

```yaml
type: Report
procedure: { name: weekly-report, version: 1 }
brain: consulting/customer-a
generated: { by: brainforge/claude-opus-5-5, at: … }
sources: [{ resource: ../../knowledge/milestones/integration-testing.md }, …]   # cited concepts
brainforge:
  commit: 85b002f…               # knowledge state the report was built from
  previous_report: reports/weekly/2026-10-23.md
  instructions: [framework:policy.md@0.1.0, procedures/weekly-report.md@6ac1…]
  broken_citations: []           # links to concepts that don't exist
```

To reproduce a report's input, run `git checkout <commit>`.

## Approval gate and audiences

- **`require_approval: true`** refuses the run while anything awaits approval and names who still has to act, e.g. `3 item(s) await approval: Bob Lee (1), Carol Lead (2)`. Scheduled runs fail the same way, so the report is never built on unapproved knowledge. `brainforge run <name> --allow-pending` overrides it.
- **`audience: <name>`** builds the report only from approved knowledge meant for that audience or a wider one. The agent works on a throwaway copy that contains just those concepts, with fresh index files and no archive, log or configuration, so internal items aren't there to leak. Afterwards BrainForge rejects the report if it cites any concept outside that set. The report records `audience` in its frontmatter.
- Without `audience`, reports see everything and mark facts from pending items as "(unconfirmed)".

## Rules for runs and output

- If a report for the same period already exists, the run is refused unless you pass `--force`. Git keeps the old version.
- Formats other than Markdown are rendered with `pandoc`, and the Markdown copy is always kept. PDF uses `typst` when it is installed.

Scheduling is covered in [automation.md](automation.md).
