# pm: project management plugin

Adds project-management knowledge to a brain: milestones, decisions, action items and a RAID log (risks, assumptions, issues, dependencies), plus a status report and an overdue check.

```bash
brainforge plugin add pm                 # or: brainforge init my-project --plugin pm
git add plugins && git commit -m "plugin: pm"
```

| Part | What it adds |
|---|---|
| `plugin.yaml` | Types and their frontmatter fields. `milestones/` Milestone (`date`, `baseline`, `state`), `decisions/` Decision, `actions/` Action Item (`owner`, `due`, `state`), `risks/` Risk, `issues/` Issue, `assumptions/` Assumption, `dependencies/` Dependency, `projects/` Project (`rag`). Values are checked after every ingestion; missing `owner`, `due` or `date` show up as gaps in `brainforge pending`. |
| `instructions/ingestion.md` | How to tell a risk from an issue, keep a milestone's baseline date, and record decisions and commitments. |
| `instructions/query.md` | Status questions start from the latest status report and name overdue actions. |
| `procedures/status-report.md` | `brainforge run status-report`: milestones, decisions, open actions, RAID. Uncomment `require_approval`, `audience` or `schedule` as needed. |
| `scripts/overdue.py` | `brainforge plugin run pm overdue [YYYY-MM-DD]`: open actions and dependencies past due, milestones whose date passed. Exits 1 if any, for CI. |

Suggested `brain.yaml` lines, so people approve the project items but not every person or topic page:

```yaml
needs_approval: [{type: Milestone}, {type: Decision}, {type: Action Item}, {type: Risk}, {type: Issue}, {type: Project}]
```

To change a type or the report for one brain, edit its copy under `plugins/pm/`, or override the report with a procedure of the same name in `procedures/`. `brainforge plugin add pm` again replaces the copy with the bundled version.
