# Project management

- **Risk or issue:** a risk may still happen; an issue already has. When a risk occurs, set the risk's `state: occurred` and create an issue that links to it.
- **Milestones:** when a date moves, update `date`, keep `baseline` at the first planned date, and record the move under `# History`.
- **Decisions:** record what was decided, by whom and when. Use `state: proposed` until the document says it was decided, with `decide_by` when it says by when.
- **Completion:** when a document reports that an action, milestone or dependency is finished ("the test plan went out on Monday"), set its state (`done`, `achieved`, `met`) and add a line under `# History`. "Almost done" or "in progress" is not finished. A missed milestone gets `state: missed` and keeps its date until a new one is stated.
- **Updates:** a note or update by a team member ("# Update by Alice Smith, …") is first-hand news about their own work. Update the items it reports on, record new commitments as action items with that person as owner, and record what's blocked as an issue or dependency.
- **Action items:** one per task and owner. A sentence like "Alice will send the contract by Friday" is an action item with `owner: Alice`; resolve "Friday" to a date only when the document's date makes it unambiguous.
- **Dependencies:** record each one the document states ("depends on", "waiting for", "needs … from") as its own Dependency, and link it from the item that depends on it. When a milestone or action waits for another item (an action, a dependency, a decision), add that item's concept id to its `depends_on`.
- **Project status:** when the document states the overall status, set `rag` on the concept for the project itself, whatever its type, and keep earlier values under `# History`. Never infer it.
- Link each item to the milestones, decisions and people it concerns.
