# Procedures and reports

A procedure is a Markdown file in `procedures/`: YAML frontmatter configures it, and the body holds instructions for the reporting agent.

```markdown
---
name: weekly-report              # default: file name
version: 1
description: Weekly customer report.
agent: claude                    # optional adapter override
knowledge:
  self: true                     # ancestors/descendants: Phase B
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

## Rules for runs and output

- If a report for the same period already exists, the run is refused unless you pass `--force`. Git keeps the old version.
- Formats other than Markdown are rendered with `pandoc`, and the Markdown copy is always kept. PDF uses `typst` when it is installed.

Scheduling is covered in [automation.md](automation.md).
