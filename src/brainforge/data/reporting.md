# Reporting: write a report from the knowledge bundle

- Base the report only on `knowledge/`, plus the previous report if one is given. Navigate with the `index.md` files and search (grep).
- Cite facts with markdown links to concept files, using paths relative to the report file. The task gives the prefix.
- Name missing information explicitly ("Not documented: …"). Never invent status indicators (green/amber/red), dates or numbers. A status is reported only when the knowledge states it; otherwise it is "Unknown".
- Prefix interpretation with "Derived:" and cite what it rests on.
- Concepts with `review: proposed` are not confirmed by a person yet: mark what rests on them as "(unconfirmed)". Ignore concepts with `review: discarded`.
- Write only the report file named in the task. Write the markdown body only, without YAML frontmatter; BrainForge adds the metadata.
