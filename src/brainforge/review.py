"""Review and visibility: what is confirmed, who reviews what, and what an audience may see.

Frontmatter only, no state store: `review` (proposed | confirmed | discarded, absent = confirmed) and
`visibility` (one of brain.yaml's levels, absent = the most restricted). BrainForge sets both after
every agent run; only people change them, and every decision is a commit by the person who made it.
"""

from __future__ import annotations

import contextlib
import fnmatch
import re
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from brainforge import gitops, okf
from brainforge.brain import Brain, BrainError, TeamMember


def state(c: okf.Concept) -> str:
    return str(c.meta.get("review") or "confirmed")


def level(brain: Brain, c: okf.Concept) -> str | None:
    levels = brain.manifest.visibility
    return str(c.meta.get("visibility") or levels[-1]) if levels else None


def url(path: Path) -> str:
    """Clickable link that opens the note in Obsidian (any vault containing the path)."""
    return "obsidian://open?path=" + quote(str(path.resolve()))


def _head_meta(root: Path, rel: str) -> dict[str, Any]:
    text = gitops.show(root, "HEAD", rel)
    try:
        return (okf.split_frontmatter(text)[0] or {}) if text else {}
    except okf.OKFError:
        return {}


def stamp(brain: Brain, c: okf.Concept, rel: str, propose: bool = True) -> None:
    """After an agent run: the change awaits review, and visibility stays what people set (or the default)."""
    old = _head_meta(brain.root, rel)
    rules = brain.manifest.needs_review
    if propose and (not rules or any(_matches(c.meta, r) for r in rules)):
        c.meta["review"] = "proposed"
    elif old.get("review"):
        c.meta["review"] = old["review"]
    else:
        c.meta.pop("review", None)
    levels = brain.manifest.visibility
    vis = old.get("visibility") or (levels[-1] if levels else None)
    if vis:
        c.meta["visibility"] = vis
    else:
        c.meta.pop("visibility", None)


# --- who reviews what ---------------------------------------------------------------------------

def _mentions(text: str, m: TeamMember) -> bool:
    return any(n and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.I) for n in [m.name, m.email, *m.aliases])


def _matches(meta: dict[str, Any], rule: dict[str, str]) -> bool:
    def hit(pattern: str, value: Any) -> bool:
        values = value if isinstance(value, list) else [value]
        return any(v is not None and fnmatch.fnmatchcase(str(v).lower(), pattern.lower()) for v in values)

    return all(hit(pattern, meta.get(key)) for key, pattern in rule.items())


def reviewers(brain: Brain, c: okf.Concept) -> list[TeamMember]:
    """The item's owners if they are on the team, else the first member whose `reviews` rules match."""
    team = brain.manifest.team
    owner = c.meta.get("owner")
    text = " ".join(map(str, owner)) if isinstance(owner, list) else str(owner or "")
    owners = [m for m in team if text and _mentions(text, m)]
    if owners:
        return owners
    return next(([m] for m in team if any(_matches(c.meta, r) for r in m.reviews)), [])


def me(brain: Brain) -> TeamMember | None:
    email = gitops.git(brain.root, "config", "user.email", check=False).strip().lower()
    name = gitops.git(brain.root, "config", "user.name", check=False).strip().lower()
    for m in brain.manifest.team:
        if (email and (m.email or "").lower() == email) or (name and m.name.lower() == name):
            return m
    return None


@dataclass
class Item:
    concept: okf.Concept
    rel: str  # knowledge/<group>/<slug>.md
    reviewers: list[TeamMember]
    since: str | None  # commit of the last confirmed version; None for a new item

    def as_dict(self, brain: Brain) -> dict[str, Any]:
        c = self.concept
        sources = c.sources()
        at = str((c.meta.get("generated") or {}).get("at", ""))
        return {
            "id": okf.concept_id(brain.knowledge, c.path), "path": self.rel, "url": url(c.path),
            "type": c.meta.get("type"), "title": c.title, "owner": c.meta.get("owner"),
            "visibility": level(brain, c), "kind": "changed" if self.since else "new", "since": self.since,
            "source": (sources[-1].get("title") or sources[-1].get("id")) if sources else None,
            "proposed_at": at or None, "reviewers": [m.name for m in self.reviewers],
        }


def last_confirmed(root: Path, rel: str) -> tuple[str, str] | None:
    """(commit, text) of the newest committed version that was confirmed."""
    for sha in gitops.git(root, "log", "--format=%H", "--", rel, check=False).split():
        text = gitops.show(root, sha, rel)
        try:
            meta = okf.split_frontmatter(text)[0] if text else None
        except okf.OKFError:
            continue
        if meta is not None and str(meta.get("review") or "confirmed") == "confirmed":
            return sha, text or ""
    return None


def pending(brain: Brain) -> list[Item]:
    items = []
    for c in okf.read_all(brain.knowledge):
        if state(c) == "proposed":
            rel = brain.rel(c.path)
            found = last_confirmed(brain.root, rel)
            items.append(Item(c, rel, reviewers(brain, c), found[0] if found else None))
    return items


def queue(brain: Brain, who: TeamMember | None) -> list[Item]:
    """Pending items for one person; everything when the brain has no team."""
    items = pending(brain)
    if not brain.manifest.team:
        return items
    return [i for i in items if who in i.reviewers] if who else items


def scan(brain: Brain) -> dict[str, Any]:
    """Who still has items to review. Members with nothing pending are listed with an empty list."""
    items = pending(brain)
    by = {m.name: [i.rel for i in items if m in i.reviewers] for m in brain.manifest.team}
    unassigned = [i.rel for i in items if not i.reviewers]
    ats = [datetime.fromisoformat(a) for i in items if (a := i.as_dict(brain)["proposed_at"])]
    oldest = (datetime.now(UTC) - min(ats)).days if ats else None
    return {"pending": len(items), "oldest_days": oldest, "reviewers": by, "unassigned": unassigned,
            "items": [i.as_dict(brain) for i in items]}


# --- decisions ----------------------------------------------------------------------------------

def resolve(brain: Brain, ref: str) -> Path:
    rel = ref.strip().removeprefix("knowledge/").removesuffix(".md")
    path = brain.knowledge / f"{rel}.md"
    if not path.is_file():
        raise BrainError(f"no such concept: {rel}")
    return path


def confirm(brain: Brain, refs: list[str], visibility: str | None = None) -> list[Path]:
    levels = brain.manifest.visibility
    if visibility and visibility not in levels:
        raise BrainError(f"unknown visibility '{visibility}'; brain.yaml defines {levels or 'none'}")
    who = gitops.user(brain.root)
    if not who:
        raise BrainError("set git user.email or user.name to record who confirmed")
    paths = [resolve(brain, r) for r in refs]
    for path in paths:
        c = okf.read(path)
        c.meta["review"] = "confirmed"
        if visibility:
            c.meta["visibility"] = visibility
        verified = c.meta.get("verified") or []
        verified = [verified] if isinstance(verified, dict) else list(verified)
        c.meta["verified"] = [*verified, {"by": f"human:{who}", "at": okf.now_iso()}]
        okf.write(c)
    return paths


def discard(brain: Brain, refs: list[str]) -> list[tuple[Path, str]]:
    """A change to a confirmed item is undone (git restores that version); a new item becomes a
    tombstone, so the next document doesn't recreate it. Returns (path, what happened)."""
    done = []
    for path in [resolve(brain, r) for r in refs]:
        found = last_confirmed(brain.root, brain.rel(path))
        if found:
            path.write_text(found[1], encoding="utf-8")
            done.append((path, f"restored the version confirmed in {found[0][:8]}"))
        else:
            c = okf.read(path)
            c.meta["review"] = "discarded"
            okf.write(c)
            done.append((path, "discarded"))
    return done


# --- audiences ----------------------------------------------------------------------------------

def visible(brain: Brain, c: okf.Concept, audience: str) -> bool:
    """Confirmed, and at the audience's level or more open. Unknown levels are never visible."""
    levels = brain.manifest.visibility
    lvl = level(brain, c)
    return state(c) == "confirmed" and lvl in levels and levels.index(lvl) <= levels.index(audience)


def check_audience(brain: Brain, audience: str) -> None:
    if audience not in brain.manifest.visibility:
        raise BrainError(f"unknown audience '{audience}'; brain.yaml visibility levels: {brain.manifest.visibility or 'none'}")


@contextlib.contextmanager
def view(brain: Brain, audience: str, extra: list[Path] | None = None) -> Iterator[tuple[Path, set[str]]]:
    """A throwaway copy holding only what `audience` may see, with fresh index files and no archive,
    log or brain config. Agents for that audience work there. Yields (root, visible knowledge paths)."""
    check_audience(brain, audience)
    allowed = {brain.rel(c.path) for c in okf.read_all(brain.knowledge) if visible(brain, c, audience)}
    with tempfile.TemporaryDirectory(prefix="brainforge-view-") as tmp:
        root = Path(tmp)
        for rel in sorted(allowed) + [brain.rel(p) for p in extra or []]:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(brain.root / rel, root / rel)
        (root / "knowledge").mkdir(exist_ok=True)
        okf.write_indexes(root / "knowledge")
        yield root, allowed
