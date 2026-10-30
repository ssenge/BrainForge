"""Signals: what needs steering, found by code in the brain and its git history, without an agent.

- overdue: past its deadline and not done (plugin types with `deadline`);
- slipping: its date moved later again and again, or by weeks; git history keeps every date it had;
- at risk: it waits for an item (`waits_for`) that is late, due after it, without a date, or at risk itself;
- gap: an open item without an expected field, such as an owner or a date;
- with a project model (plan.py): the phase is complete, a step of the current phase has no item yet (next step),
  a step is late for its phase, a step is out of order, or no phase is set.

The steering procedure reads them; round issues and briefings turn them into questions for the item's owners.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from brainforge import approval, gitops, okf, plan, plugins
from brainforge.brain import Brain, TeamMember

SLIP_MOVES = 2  # ponytail: fixed thresholds; brain.yaml settings if teams want others
SLIP_DAYS = 14
ORDER = ("phase", "overdue", "at risk", "step late", "phase complete", "next step", "slipping", "out of order", "gap")


@dataclass
class Signal:
    kind: str
    concept: okf.Concept
    id: str
    owners: list[TeamMember]
    detail: str  # the facts, e.g. "due 2026-11-05, 6 days ago"
    question: str  # what to ask its owners

    def as_dict(self) -> dict[str, Any]:
        c = self.concept
        return {"kind": self.kind, "id": self.id, "type": c.meta.get("type"), "title": c.title,
                "owner": c.meta.get("owner"), "owners": [m.name for m in self.owners], "detail": self.detail,
                "question": self.question, "url": approval.url(c.path)}

    def line(self) -> str:
        owner = f", owner: {self.concept.meta['owner']}" if self.concept.meta.get("owner") else ""
        return f"{self.kind}: {self.id} \"{self.concept.title}\" ({self.concept.meta.get('type')}{owner}): {self.detail}"


def dates(brain: Brain, rel: str, field: str) -> list[date]:
    """Every value the date field had, oldest first, from the file's git history."""
    out = gitops.git(brain.root, "log", "--reverse", "-p", "-U0", "--format=", "--", rel, check=False)
    found: list[date] = []
    for m in re.finditer(rf"^\+{re.escape(field)}:\s*['\"]?(\d{{4}}-\d{{2}}-\d{{2}})", out, re.M):
        d = date.fromisoformat(m.group(1))
        if not found or found[-1] != d:
            found.append(d)
    return found


def compute(brain: Brain, today: date | None = None) -> list[Signal]:
    today = today or date.today()
    known = plugins.types(brain)
    if not known:
        return []
    items = {okf.concept_id(brain.knowledge, c.path): c for c in okf.read_all(brain.knowledge)
             if str(c.meta.get("type")) in known and approval.state(c) != "discarded"
             and str(c.meta.get("status", "")).lower() != "deprecated"}
    open_ = {i: c for i, c in items.items() if not plugins.finished(c, known[str(c.meta["type"])])}
    due = {i: plugins.deadline(brain, c, known) for i, c in open_.items()}
    found: list[Signal] = []

    def add(kind: str, i: str, detail: str, question: str) -> None:
        found.append(Signal(kind, open_[i], i, approval.approvers(brain, open_[i]), detail, question))

    for i, when in due.items():
        if when and when < today:
            add("overdue", i, f"due {when}, {(today - when).days} day(s) ago",
                f"It was due {when}. Is it done, or what is the new date?")

    risky: dict[str, str | None] = {}  # id → why it is at risk (None: it isn't)

    def risk(i: str, seen: frozenset[str] = frozenset()) -> str | None:
        if i not in risky:
            t = known[str(open_[i].meta["type"])]
            waits = [w for w in plugins.ids(open_[i].meta.get(t.waits_for) if t.waits_for else None)
                     if w in open_ and w not in seen | {i}]
            whys = []
            for w in waits:
                title, when, mine = open_[w].title, due[w], due[i]
                if when and when < today:
                    whys.append(f"waits for {w} \"{title}\", overdue since {when}")
                elif when and mine and when > mine:
                    whys.append(f"waits for {w} \"{title}\", due {when}, after its own date {mine}")
                elif not when and known[str(open_[w].meta["type"])].deadline:
                    whys.append(f"waits for {w} \"{title}\", which has no date")
                elif risk(w, seen | {i}):
                    whys.append(f"waits for {w} \"{title}\", which is at risk")
            risky[i] = "; ".join(whys) or None
        return risky[i]

    for i in open_:
        if why := risk(i):
            add("at risk", i, why, f"It {why}. Can it still make {due[i] or 'its date'}, or what is the new date?")

    for i, c in open_.items():
        t, when = known[str(c.meta["type"])], due[i]
        if not (t.deadline and when):
            continue
        history = dates(brain, brain.rel(c.path), t.deadline)
        planned = [d for d in (history[0] if history else None,
                               plugins.a_date(c.meta.get(t.baseline)) if t.baseline else None) if d]
        first = min(planned) if planned else when
        later = sum(b > a for a, b in zip(history, history[1:], strict=False))
        if when > first and (later >= SLIP_MOVES or (when - first).days >= SLIP_DAYS):
            add("slipping", i, f"moved {later or 1} time(s), from {first} to {when}",
                f"Its date moved from {first} to {when}. Is {when} realistic now?")

    for i, c in open_.items():
        if gaps := plugins.gaps(brain, c, known):
            add("gap", i, f"no {', '.join(gaps)}", f"It has no {' or '.join(gaps)} yet. What should it be?")

    leads = [m for m in brain.manifest.team if m.lead]
    for kind, c, i, detail, question, items in plan.step_signals(brain, today):
        owners: list[TeamMember] = []
        for m in (m for x in items for m in approval.approvers(brain, x)):
            owners += [] if m in owners else [m]
        found.append(Signal(kind, c, i, owners or leads, detail, question))
    return sorted(found, key=lambda s: (ORDER.index(s.kind), s.id))


def prompt(found: list[Signal], today: date | None = None) -> str:
    """The signals for an agent: facts it can rely on, found by code."""
    lines = ["", "# Signals", "", f"Found by code in the brain and its git history, as of {today or date.today()}:"]
    return "\n".join(lines + ([f"- {s.line()}" for s in found] or ["- none"])) + "\n"
