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
output:
  format: markdown               # markdown | docx | pptx | html | pdf
  directory: reports/weekly      # default: reports/<name>
  filename: "{date}"             # {date} in the schedule's timezone, {name}
  template: templates/corporate.pptx   # pandoc --reference-doc for docx/pptx
schedule:                        # optional
  cron: "0 8 * * 3"
  timezone: Europe/Berlin
round:                           # optional, needs a schedule: see Rounds below
  opens: "0 8 * * 1"             # cron, in the schedule's timezone
  from: [Alice Smith, Bob]       # default: the whole team
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
  unapproved: [risks/data-migration-delay]   # awaited approval when the report ran
  expired: []                    # approved, but past their review date
```

To reproduce a report's input, run `git checkout <commit>`.

## Approval and audiences

- **Approvals never hold a report back.** Every report lists in its frontmatter what still awaited approval (`unapproved`) and what was past its review date (`expired`) when it ran, and `run` and `schedule tick` print the counts.
- **`audience: <name>`** builds the report only from approved knowledge meant for that audience or a wider one. The agent works on a throwaway copy that contains just those concepts, with fresh index files and no archive, log or configuration, so internal items aren't there to leak. Afterwards BrainForge rejects the report if it cites any concept outside that set. The report records `audience` in its frontmatter.
- Without `audience`, reports see everything and mark facts from pending items as "(unconfirmed)".

## Rounds: asynchronous status meetings

A round replaces a status meeting. Instead of dialling in at the same time, the participants send an update while the round is open, and the procedure's report closes it.

- **When:** a round opens at its `opens` cron and closes at the procedure's next scheduled run, whose report reads the updates. With the example above, the round runs from Monday 08:00 to the Wednesday report. Several procedures can have rounds, open at the same time, for example an operational one for the client from Monday to Tuesday and a management one from Tuesday to Wednesday.
- **Who:** `from` lists team members (name, alias, email or GitHub login from `brain.yaml`); without it, the whole team takes part.
- **What counts as an update:** anything a participant sends while their round is open:
  - `brainforge note "…"`, or their agent (Claude Code through the skill, Claude Desktop through `add_note` of `brainforge mcp --write`);
  - a file pushed to `inbox/`, for example a voice memo transcribed on the phone; the commit's author is the sender. Name it `update-….md` (or `.txt`) when it is your own update rather than, say, minutes;
  - a reply on the round's GitHub issue ([automation.md](automation.md#rounds-on-github)); it counts only for that issue's round.

  One update counts for every open round the sender takes part in. Ingestion records on the source who sent it, when, and which rounds it counts for (`brainforge.from`, `sent`, `rounds`).
- **Approval:** what the brain took from someone's own update (a note, an agent note, a reply on the round's issue, or an inbox file named `update-…`) waits for their approval, not the usual approvers: they know best what they meant. Other documents brought in during a round, such as minutes, count for the round but keep the usual routing.
- **No update is fine.** The report says who sent none, plainly, and their items keep their last known state.
- **The report** gets the round's updates, as source concepts it can cite, and the names of who sent none, and records them in its frontmatter: `round: {id: weekly-report/2026-11-02, updates: […], missing: [Bob Lee]}`. A report with an `audience` gets the names only, since the update sources may not be meant for that audience.
- **The round page** `reports/rounds/<procedure>/<date>.md` shows who has sent what, with links, and the report once it is written. Ingestion and `schedule tick` keep it current; GitHub shows it with the repository's access control, and so does Obsidian.

```bash
brainforge rounds        # the latest round of each procedure: open or closed, who has sent an update, who hasn't
brainforge briefing      # lists open rounds still waiting for your update
```

## Rules for runs and output

- If a report for the same period already exists, the run is refused unless you pass `--force`. Git keeps the old version.
- Formats other than Markdown are rendered with `pandoc`, and the Markdown copy is always kept. PDF uses `typst` when it is installed.

Scheduling is covered in [automation.md](automation.md).
