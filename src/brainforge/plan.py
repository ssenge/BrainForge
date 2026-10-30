"""Project models: the phases a project goes through and the steps in each, so steering knows what comes next.

- A model is a Markdown file: frontmatter with `phases`, each with `steps`; the body is guidance for agents, such
  as what a good kickoff covers. It lives in a plugin's `models/` or the brain's own `models/`, whose copy of a
  model wins. A company's models come from their own repository: `brainforge plugin add <git-url>`.
- `model:` in brain.yaml names the one the brain follows (`acme-method/fixed-price`, or just `fixed-price`).
  Without it, nothing here applies.
- The current phase is a fact people approve: `phase` on the project item (a concept of a plugin type with a
  `phase` field, such as pm's Project). Steering proposes moving on; git history says when the phase started.
- Steps are done through items: concepts with `step: <phase>/<step>`, which steering proposes when their phase
  starts, or ingestion tags. A step is done when it has items and all of them are finished.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from brainforge import approval, gitops, okf, plugins
from brainforge.brain import Brain, BrainError, Layer, blob_hash

NAME_RE = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    title: str = ""
    type: str | None = None  # the plugin type of its item, e.g. Milestone
    optional: bool = False  # the phase is complete without it
    within_days: int | None = Field(default=None, gt=0)  # due this many days after its phase started

    @property
    def label(self) -> str:
        return self.title or self.name.replace("-", " ").capitalize()


class Phase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    title: str = ""
    steps: list[Step] = []


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    version: int | str = 1
    description: str = ""
    phases: list[Phase] = Field(min_length=1)
    ref: str = Field("", exclude=True)  # as brain.yaml names it
    path: Path = Field(exclude=True)
    body: str = Field("", exclude=True)

    @model_validator(mode="after")
    def _unique(self) -> Model:
        names = [p.name for p in self.phases]
        ids = [f"{p.name}/{s.name}" for p in self.phases for s in p.steps]
        if dupes := sorted({n for n in names + ids if (names + ids).count(n) > 1}):
            raise ValueError(f"names must be unique: {', '.join(dupes)}")
        return self

    def phase(self, name: Any) -> Phase | None:
        return next((p for p in self.phases if p.name == str(name or "").strip().lower()), None)

    def step_ids(self) -> list[str]:
        return [f"{p.name}/{s.name}" for p in self.phases for s in p.steps]


def find(brain: Brain, ref: str) -> Path:
    """`fixed-price` or `acme-method/fixed-price`: the brain's `models/fixed-price.md`, else the plugin's."""
    plugin, _, name = ref.strip().rpartition("/")
    own = brain.root / "models" / f"{name}.md"
    if own.is_file():
        return own
    found = [p.path / "models" / f"{name}.md" for p in plugins.installed(brain) if plugin in ("", p.name)]
    found = [f for f in found if f.is_file()]
    if len(found) != 1:
        raise BrainError(f"model '{ref}': " + ("found in several plugins; name it as <plugin>/<model>" if found else
                         f"no models/{name}.md in the brain or {'plugin ' + plugin if plugin else 'any plugin'}"))
    return found[0]


def load(brain: Brain) -> Model | None:
    """The model the brain follows (brain.yaml `model`), or None."""
    ref = brain.manifest.model
    if not ref:
        return None
    path = find(brain, ref)
    try:
        meta, body = okf.split_frontmatter(path.read_text(encoding="utf-8"))
        m = Model.model_validate({"name": path.stem, **(meta or {}), "path": path, "body": body, "ref": ref})
    except (okf.OKFError, ValidationError) as exc:
        raise BrainError(f"model {brain.rel(path)}: {exc}") from exc
    known = plugins.types(brain)
    if bad := sorted({s.type for p in m.phases for s in p.steps if s.type and s.type not in known}):
        raise BrainError(f"model {brain.rel(path)}: unknown type {', '.join(bad)} (types: {', '.join(known)})")
    return m


def check_value(brain: Brain, name: str, value: Any) -> str | None:
    """`phase` and `step` must name a phase or step of the model, when the brain follows one."""
    if name not in ("phase", "step") or not (m := load(brain)):
        return None
    allowed = [p.name for p in m.phases] if name == "phase" else m.step_ids()
    return None if str(value) in allowed else f"{name} must be one of {', '.join(allowed)}, not '{value}'"


# --- where the project stands ---------------------------------------------------------------------

@dataclass
class StepState:
    phase: Phase
    step: Step
    items: list[okf.Concept]
    done: bool

    @property
    def id(self) -> str:
        return f"{self.phase.name}/{self.step.name}"


@dataclass
class Position:
    model: Model
    project: okf.Concept | None  # the item holding `phase`
    phase: Phase | None  # the current one
    since: date | None  # when it became current, from git history
    steps: list[StepState]

    def of(self, phase: Phase) -> list[StepState]:
        return [s for s in self.steps if s.phase is phase]

    def complete(self, phase: Phase) -> bool:
        return all(s.done for s in self.of(phase) if not s.step.optional)

    def after(self, phase: Phase) -> Phase | None:
        phases = self.model.phases
        return phases[phases.index(phase) + 1] if phase is not phases[-1] else None

    def as_dict(self, brain: Brain) -> dict[str, Any]:
        return {"model": self.model.ref, "version": self.model.version,
                "project": okf.concept_id(brain.knowledge, self.project.path) if self.project else None,
                "phase": self.phase.name if self.phase else None, "since": self.since.isoformat() if self.since else None,
                "phases": [{"name": p.name, "complete": self.complete(p),
                            "steps": [{"id": s.id, "title": s.step.label, "optional": s.step.optional,
                                       "state": "done" if s.done else "open" if s.items else "not started",
                                       "items": [okf.concept_id(brain.knowledge, c.path) for c in s.items]}
                                      for s in self.of(p)]} for p in self.model.phases]}


def since(brain: Brain, project: okf.Concept, phase: str) -> date | None:
    """The day the project item's `phase` last changed to `phase`, from its git history."""
    out = gitops.git(brain.root, "log", "--format=%x00%aI", "-p", "-U0", "--", brain.rel(project.path), check=False)
    for chunk in out.split("\0")[1:]:  # newest first
        head, _, diff = chunk.partition("\n")
        if re.search(rf"^\+phase:\s*['\"]?{re.escape(phase)}['\"]?\s*$", diff, re.M | re.I):
            return date.fromisoformat(head[:10])
    return None


def position(brain: Brain) -> Position | None:
    m = load(brain)
    if m is None:
        return None
    known = plugins.types(brain)
    holders = {t.type for t in known.values() if "phase" in t.fields}
    concepts = [c for c in okf.read_all(brain.knowledge) if approval.state(c) != "discarded"
                and str(c.meta.get("status", "")).lower() != "deprecated"]
    projects = [c for c in concepts if str(c.meta.get("type")) in holders]
    project = next((c for c in projects if c.meta.get("phase")), projects[0] if projects else None)
    current = m.phase(project.meta.get("phase")) if project else None
    steps = []
    for p in m.phases:
        for s in p.steps:
            items = [c for c in concepts if str(c.meta.get("step", "")).strip() == f"{p.name}/{s.name}"]
            done = bool(items) and all(plugins.finished(c, known.get(str(c.meta.get("type")))) for c in items)
            steps.append(StepState(p, s, items, done))
    return Position(m, project, current, since(brain, project, current.name) if project and current else None, steps)


def check(brain: Brain) -> list[okf.Issue]:
    """For `brain validate`: the model loads, the project item exists, `step` values name steps of the model."""
    try:
        pos = position(brain)
    except BrainError as exc:
        return [okf.Issue("error", "brain.yaml", str(exc))]
    if pos is None:
        return []
    issues = [] if pos.project else [okf.Issue("warning", "knowledge/", "the brain follows a project model but has no "
                                               "project item to hold its `phase`")]
    ids = set(pos.model.step_ids())
    issues += [okf.Issue("warning", brain.rel(c.path), f"step '{c.meta['step']}' is not in model {pos.model.ref}")
               for c in okf.read_all(brain.knowledge) if c.meta.get("step") and str(c.meta["step"]).strip() not in ids]
    return issues


# --- views ---------------------------------------------------------------------------------------

def _step(brain: Brain, s: StepState) -> str:
    if s.done:
        return f"{s.step.name} ✓"
    if not s.items:
        return f"{s.step.name} not started" + (" (optional)" if s.step.optional else "")
    c = next(c for c in s.items if not plugins.finished(c, plugins.types(brain).get(str(c.meta.get("type")))))
    when = plugins.deadline(brain, c)
    who = ", ".join(x for x in [str(c.meta.get("owner") or ""), f"due {when}" if when else ""] if x)
    return f"{s.step.name} open" + (f" ({who})" if who else "")


def text(brain: Brain, pos: Position) -> str:
    title = pos.project.title if pos.project else brain.manifest.name
    where = (f"phase {pos.phase.name}" + (f" since {pos.since}" if pos.since else "") if pos.phase else
             "no phase set (`phase` on the project item)")
    lines = [f"{title} · {pos.model.ref} v{pos.model.version} · {where}"]
    width = max(len(p.name) for p in pos.model.phases)
    current = pos.model.phases.index(pos.phase) if pos.phase else -1
    for i, p in enumerate(pos.model.phases):
        states = pos.of(p)
        if i > current and not any(s.items for s in states):
            what = "not started"
        elif i < current and pos.complete(p):
            what = "done"
        else:
            what = " · ".join(_step(brain, s) for s in states) or "no steps"
        lines.append(f"  {p.name:<{width}}  {what}")
    return "\n".join(lines)


def briefing(brain: Brain) -> dict[str, Any] | None:
    """The phase and its open steps, for the briefing."""
    try:
        pos = position(brain)
    except BrainError:
        return None  # brain validate says why
    if pos is None or pos.phase is None:
        return None
    return {"model": pos.model.ref, "phase": pos.phase.name, "since": pos.since.isoformat() if pos.since else None,
            "open": [{"id": s.id, "title": s.step.label, "state": "open" if s.items else "not started"}
                     for s in pos.of(pos.phase) if not s.done]}


def prompt(brain: Brain) -> str:
    """The model and the project's place in it, for the steering agent."""
    pos = position(brain)
    if pos is None:
        return ""
    project = okf.concept_id(brain.knowledge, pos.project.path) if pos.project else None
    lines = ["", "# Project model", "",
             f"The project follows the model `{pos.model.ref}` (version {pos.model.version}). Code worked out where "
             "it stands from `phase` on the project item and the items with `step:`:", "", text(brain, pos), ""]
    for p in pos.model.phases:
        lines += [f"- {s.id} \"{s.step.label}\"" + (f", within {s.step.within_days} days of the phase start"
                                                     if s.step.within_days else "")
                  + (f", item type {s.step.type}" if s.step.type else "") + (", optional" if s.step.optional else "")
                  + f": {'done' if s.done else 'open' if s.items else 'no item yet'}"
                  + (f" ({', '.join(okf.concept_id(brain.knowledge, c.path) for c in s.items)})" if s.items else "")
                  for s in pos.of(p)]
    lines += ["", "To move the project on, propose a `set` of `phase` on "
              + (f"`{project}`" if project else "a new project item (`create`)")
              + ". To start a step, propose a `create` of its item with `step: <phase>/<step>`, an owner and a date, "
              "or a `set` of `step` on an existing item that is that step. Use the model's guidance below, for "
              "example for an agenda.", "", "## Model guidance", "", pos.model.body.strip() or "(none)"]
    return "\n".join(lines) + "\n"


def layers(brain: Brain, activity: str) -> list[Layer]:
    """Ingestion learns the steps, to tag items with them; reports and queries learn where the project stands."""
    try:
        pos = position(brain)
    except BrainError:
        return []
    if pos is None:
        return []
    if activity == "ingestion":
        text_ = "\n".join([
            f"# Project model {pos.model.ref}", "",
            "The project goes through these phases and steps. When a document reports on one of them (planned, "
            "held, done, moved), set `step: <phase>/<step>` on the item for it, and keep `step` on items that have "
            "it. Set `phase` on the project item only when a document states that the project moved to another "
            "phase.", "", *(f"- `{s.id}`: {s.step.label}" for s in pos.steps)])
    else:
        text_ = f"# Project model\n\nWhere the project stands, worked out by code:\n\n{text(brain, pos)}\n"
    rel = brain.rel(pos.model.path)
    return [Layer(f"Project model ({rel})", f"{rel}@{blob_hash(text_)}", text_)]


def step_signals(brain: Brain, today: date) -> list[tuple[str, okf.Concept, str, str, str, list[okf.Concept]]]:
    """(kind, concept, id, detail, question, items) for the signals: what the model says needs doing."""
    try:
        pos = position(brain)
    except BrainError:
        return []
    if pos is None:
        return []
    m = pos.model

    def virtual(s: StepState) -> okf.Concept:  # a step without an item: the model is where it is defined
        return okf.Concept(m.path, {"type": "Step", "title": s.step.label})

    if pos.phase is None:
        target = pos.project or okf.Concept(m.path, {"type": "Model", "title": m.name})
        cid = okf.concept_id(brain.knowledge, pos.project.path) if pos.project else m.ref
        return [("phase", target, cid, f"no phase set; model {m.ref} starts with {m.phases[0].name}",
                 f"Which phase of {m.ref} is the project in?", [])]
    out = []
    current, start = pos.phase, pos.since
    index = m.phases.index(current)
    for s in pos.steps:
        i = m.phases.index(s.phase)
        late = start + timedelta(days=s.step.within_days) if start and s.step.within_days and i == index else None
        if i == index and not s.items:
            out.append(("next step", virtual(s), s.id,
                        f"phase {current.name}" + (f" since {start}" if start else "") + f": no item for {s.id} yet"
                        + (f", planned by {late}" if late else "") + (" (optional)" if s.step.optional else ""),
                        f"{current.name.capitalize()} has started. Who takes \"{s.step.label}\", and by when?", []))
        elif late and not s.done and today > late:
            out.append(("step late", s.items[0], s.id, f"planned within {s.step.within_days} days of the phase "
                        f"start, by {late}, and not done", f"\"{s.step.label}\" was planned by {late}. Is it done, "
                        "or what is the new date?", s.items))
        elif i < index and not s.done and not s.step.optional:
            out.append(("out of order", s.items[0] if s.items else virtual(s), s.id,
                        f"step of the earlier phase {s.phase.name}, not done", f"Is \"{s.step.label}\" still "
                        "needed, or is it done or cancelled?", s.items))
        elif i > index and s.items:
            out.append(("out of order", s.items[0], s.id, f"step of the later phase {s.phase.name} has an item, but "
                        f"the project is in {current.name}", f"Has the project moved on to {s.phase.name}?", s.items))
    if pos.complete(current) and pos.project:
        nxt = pos.after(current)
        out.append(("phase complete", pos.project, okf.concept_id(brain.knowledge, pos.project.path),
                    f"every required step of {current.name} is done" + (f"; next: {nxt.name}" if nxt else
                                                                         "; it is the last phase"),
                    f"Move the project on to {nxt.name}?" if nxt else "Is the project finished?", []))
    return out
