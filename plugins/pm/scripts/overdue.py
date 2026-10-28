"""Overdue items: open actions and dependencies past their due date, and milestones whose date passed
without being achieved. Usage: brainforge plugin run pm overdue [YYYY-MM-DD]. Exits 1 if any are overdue."""

import sys
from datetime import date

from brainforge import approval, okf
from brainforge import brain as brain_mod

DONE = {"done", "cancelled", "met", "achieved"}
WHEN = {"Action Item": "due", "Dependency": "due", "Milestone": "date"}

brain = brain_mod.find()
today = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
late = []
for c in okf.read_all(brain.knowledge):
    key = WHEN.get(str(c.meta.get("type")))
    if not key or approval.state(c) == "discarded" or str(c.meta.get("state", "")).lower() in DONE:
        continue
    try:
        when = date.fromisoformat(str(c.meta.get(key))[:10])
    except ValueError:
        continue  # no date yet: `brainforge pending` shows it as a gap
    if when < today:
        late.append((when, c))

print(f"{len(late)} overdue item(s) as of {today}" + (":" if late else "."))
for when, c in sorted(late, key=lambda x: x[0]):
    owner = f", owner: {c.meta['owner']}" if c.meta.get("owner") else ""
    pending = " (awaiting approval)" if approval.state(c) == "pending" else ""
    print(f"  {okf.concept_id(brain.knowledge, c.path)}  {c.meta.get('type')} {when}{owner}: {c.title}{pending}")
sys.exit(1 if late else 0)
