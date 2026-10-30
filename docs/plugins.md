# Plugins

The core of BrainForge is domain-neutral: entities, topics, sources, approvals, reports. A plugin adds what a domain needs, such as project management (`plugins/pm`, bundled), on top of that.

```bash
brainforge plugin list                  # installed in this brain, and bundled ones
brainforge plugin add pm                # a bundled plugin by name, or a path to a plugin folder
brainforge plugin add https://github.com/acme/brainforge-crm.git            # a plugin repository
brainforge plugin add https://github.com/acme/plugins.git#plugins/crm       # a plugin inside one
git add plugins && git commit -m "plugin: pm"
brainforge plugin run <plugin> <script> # a plugin script
brainforge init my-project --plugin pm  # a new brain with plugins
```

`plugin add` copies the plugin into the brain's `plugins/<name>/`. The folder is the installation:
- it is versioned with the brain, so CI and teammates need nothing extra;
- you can adapt the copy for one brain;
- `plugin add` again replaces the copy (review the change with `git diff`); from a git URL, it takes the latest commit of the default branch;
- deleting the folder removes the plugin.

## Layout

```
plugins/<name>/
├── plugin.yaml                  name, version, description, types
├── instructions/<activity>.md   ingestion.md, query.md, reporting.md (optional)
├── procedures/*.md              procedures, like the brain's own (optional)
├── scripts/                     commands for `brainforge plugin run` (optional)
├── models/*.md                  project models, e.g. a company's method (optional)
└── README.md
```

## `plugin.yaml`

```yaml
name: pm                          # must match the folder name; lowercase, kebab-case
version: 1
description: Project management.
types:
  - type: Action Item             # the frontmatter `type`
    group: actions                # folder under knowledge/
    description: A task someone committed to.
    stale_after_days: 30          # optional review period, see below
    deadline: due                 # optional: overdue once this date passes...
    done: {state: [done, cancelled]}   # ...unless a field has one of these values
    fields:
      owner: {description: "who does it, as written", expected: true}
      due: {format: date, expected: true}
      state: {values: [open, done, cancelled]}
```

Each field may have:
- `description`: what to record, shown to the ingestion agent;
- `values`: the allowed values (case-insensitive);
- `format: date`: a `YYYY-MM-DD` date;
- `expected: true`: approvers see a missing value as a gap (`brainforge pending`, the GitHub reminder, Claude).

`stale_after_days` gives the type a review period. Every change to such an item sets OKF `stale_after` to now plus the period. When it passes, the approved item is listed as expired in `brainforge pending`, so its approvers confirm it (approve: a new period starts) or retire it (discard: `status: deprecated`). Reports list expired items in their frontmatter.

`deadline` names a field with `format: date`. An item of that type whose date has passed is overdue unless one of its `done` fields has one of the listed values. Overdue and soon-due items appear in `brainforge briefing`, in the calendar export, and once per item in a message to their owner (see the README, "Deadlines, messages and the briefing"). The ingestion agent is told to set the done value, with a citation, when a document reports the item finished.

Avoid `status` as a field name: OKF uses it for the concept's lifecycle (`draft`, `stable`, `deprecated`). The pm plugin uses `state`.

## How the parts are used

| Part | Effect |
|---|---|
| Types | For ingestion, BrainForge generates an instruction layer that lists the types, their groups and fields. After every agent run, a value outside `values` or a malformed date is an error, so the run is rolled back. `brain validate` reports the same, plus types filed in the wrong group. |
| `instructions/<activity>.md` | An instruction layer after the framework defaults and before the brain's own `instructions.md`, so a brain can refine what a plugin says. The framework policy always comes first and can't be overridden. |
| `procedures/*.md` | Listed and run like the brain's own (`brainforge run status-report`). A procedure in the brain's `procedures/` with the same name replaces the plugin's. |
| `models/*.md` | [Project models](project-models.md): phases and steps a project follows. A brain follows one with `model: <plugin>/<model>` in `brain.yaml`. A plugin can be only that: a company's method in its own repository. |
| `scripts/` | Run only by `brainforge plugin run <plugin> <script> [args…]`, in the brain root. A `.py` script runs with BrainForge's Python, so it can `import brainforge`; anything else must be executable. Exit codes pass through. |

## Trust

A plugin is code and instructions in the brain repository. Review it like any other change: its instructions steer the agents, and its scripts run with your permissions when you call them. Agents can't change plugins, because they may only write `knowledge/`.
