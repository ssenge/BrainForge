# Project management

- **Risk or issue:** a risk may still happen; an issue already has. When a risk occurs, set the risk's `state: occurred` and create an issue that links to it.
- **Milestones:** when a date moves, update `date`, keep `baseline` at the first planned date, and record the move under `# History`.
- **Decisions:** record what was decided, by whom and when. Use `state: proposed` until the document says it was decided.
- **Action items:** one per task and owner. A sentence like "Alice will send the contract by Friday" is an action item with `owner: Alice`; resolve "Friday" to a date only when the document's date makes it unambiguous.
- **Dependencies:** record each one the document states ("depends on", "waiting for", "needs … from") as its own Dependency, and link it from the item that depends on it.
- **Project status:** when the document states the overall status, set `rag` on the concept for the project itself, whatever its type, and keep earlier values under `# History`. Never infer it.
- Link each item to the milestones, decisions and people it concerns.
