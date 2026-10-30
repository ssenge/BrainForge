# pm: project management plugin

Adds project-management knowledge to a brain: milestones, decisions, action items and a RAID log (risks, assumptions, issues, dependencies), plus status reports, two of them fed by rounds (asynchronous status meetings), and a steering report with proposals for the leads. Actions, milestones and dependencies have deadlines, so overdue items reach their owners (`brainforge briefing`, messages, calendar).

```bash
brainforge plugin add pm                 # or: brainforge init my-project --plugin pm
git add plugins && git commit -m "plugin: pm"
```

| Part | What it adds |
|---|---|
| `plugin.yaml` | Types and their frontmatter fields. `milestones/` Milestone (`date`, `baseline`, `state`), `decisions/` Decision, `actions/` Action Item (`owner`, `due`, `state`), `risks/` Risk, `issues/` Issue, `assumptions/` Assumption, `dependencies/` Dependency, `projects/` Project (`rag`). Deadlines: an action item or dependency is overdue after its `due` date unless done (`done`, `cancelled`, `met`), a milestone after its `date` unless `achieved` or `cancelled`, a proposed decision after its `decide_by`. Milestones and actions list what they wait for in `depends_on`, so a late item puts the ones waiting for it at risk. Values are checked after every ingestion; missing `owner`, `due` or `date` show up as gaps in `brainforge pending`. The project status expires after 14 days, risks, issues and assumptions after 30, and then come back for review. |
| `instructions/ingestion.md` | How to tell a risk from an issue, keep a milestone's baseline date, record decisions and commitments, and mark items finished when a document says so. |
| `instructions/query.md` | Status questions start from the latest status report and name overdue actions. |
| `procedures/status-report.md` | `brainforge run status-report`: milestones, decisions, open actions, RAID. Uncomment `audience` or `schedule` as needed. |
| `procedures/steering.md` | `brainforge run steering`: from the [signals](../../docs/procedures.md#steering-signals-and-proposals) (overdue, at risk, slipping, gaps) and the updates, up to seven proposals for the leads: ask or chase someone, set a new date or owner, raise a risk or a decision. A lead approves them (`brainforge proposals approve 1 3`), and BrainForge carries them out. Needs `lead: true` on at least one team member; uncomment `schedule` to run it after a round closes. |
| `procedures/operational-status.md`, `procedures/management-status.md` | Two reports with a [round](../../docs/procedures.md#rounds-asynchronous-status-meetings) each, an asynchronous status meeting: the team sends updates from Monday to Tuesday for the client report, the leads from Tuesday to Wednesday for management. Uncomment `schedule` and `round` to use them. |

Suggested `brain.yaml` lines, so people approve the project items but not every person or topic page:

```yaml
needs_approval: [{type: Milestone}, {type: Decision}, {type: Action Item}, {type: Risk}, {type: Issue}, {type: Project}]
```

To change a type or the report for one brain, edit its copy under `plugins/pm/`, or override the report with a procedure of the same name in `procedures/`. `brainforge plugin add pm` again replaces the copy with the bundled version.
