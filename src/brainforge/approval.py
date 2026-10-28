"""Approval and audiences: what people have approved, who approves what, and what an audience may see.

Frontmatter only, no state store: `approval` (pending | approved | discarded, absent = approved) and
`audience` (one of brain.yaml's `audiences`, absent = the most restricted). BrainForge sets both after
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
    return str(c.meta.get("approval") or "approved")


def audience_of(brain: Brain, c: okf.Concept) -> str | None:
    levels = brain.manifest.audiences
    return str(c.meta.get("audience") or levels[-1]) if levels else None


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
    """After an agent run: the change awaits approval, and the audience stays what people set (or the default)."""
    old = _head_meta(brain.root, rel)
    rules = brain.manifest.needs_approval
    if propose and (not rules or any(_matches(c.meta, r) for r in rules)):
        c.meta["approval"] = "pending"
    elif old.get("approval"):
        c.meta["approval"] = old["approval"]
    else:
        c.meta.pop("approval", None)
    levels = brain.manifest.audiences
    aud = old.get("audience") or (levels[-1] if levels else None)
    if aud:
        c.meta["audience"] = aud
    else:
        c.meta.pop("audience", None)


# --- who approves what --------------------------------------------------------------------------

def _mentions(text: str, m: TeamMember) -> bool:
    return any(n and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.I) for n in [m.name, m.email, *m.aliases])


def _matches(meta: dict[str, Any], rule: dict[str, str]) -> bool:
    def hit(pattern: str, value: Any) -> bool:
        values = value if isinstance(value, list) else [value]
        return any(v is not None and fnmatch.fnmatchcase(str(v).lower(), pattern.lower()) for v in values)

    return all(hit(pattern, meta.get(key)) for key, pattern in rule.items())


def approvers(brain: Brain, c: okf.Concept) -> list[TeamMember]:
    """The item's owners if they are on the team, else the first member whose `approves` rules match."""
    team = brain.manifest.team
    owner = c.meta.get("owner")
    text = " ".join(map(str, owner)) if isinstance(owner, list) else str(owner or "")
    owners = [m for m in team if text and _mentions(text, m)]
    if owners:
        return owners
    return next(([m] for m in team if any(_matches(c.meta, r) for r in m.approves)), [])


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
    approvers: list[TeamMember]
    since: str | None  # commit of the last approved version; None for a new item

    def as_dict(self, brain: Brain) -> dict[str, Any]:
        c = self.concept
        sources = c.sources()
        at = str((c.meta.get("generated") or {}).get("at", ""))
        return {
            "id": okf.concept_id(brain.knowledge, c.path), "path": self.rel, "url": url(c.path),
            "type": c.meta.get("type"), "title": c.title, "owner": c.meta.get("owner"),
            "audience": audience_of(brain, c), "kind": "changed" if self.since else "new", "since": self.since,
            "source": (sources[-1].get("title") or sources[-1].get("id")) if sources else None,
            "pending_since": at or None, "approvers": [m.name for m in self.approvers],
        }


def last_approved(root: Path, rel: str) -> tuple[str, str] | None:
    """(commit, text) of the newest committed version that was approved."""
    for sha in gitops.git(root, "log", "--format=%H", "--", rel, check=False).split():
        text = gitops.show(root, sha, rel)
        try:
            meta = okf.split_frontmatter(text)[0] if text else None
        except okf.OKFError:
            continue
        if meta is not None and str(meta.get("approval") or "approved") == "approved":
            return sha, text or ""
    return None


def pending(brain: Brain) -> list[Item]:
    items = []
    for c in okf.read_all(brain.knowledge):
        if state(c) == "pending":
            rel = brain.rel(c.path)
            found = last_approved(brain.root, rel)
            items.append(Item(c, rel, approvers(brain, c), found[0] if found else None))
    return items


def pending_for(brain: Brain, who: TeamMember | None) -> list[Item]:
    """Pending items for one person; everything when the brain has no team or no person is given."""
    items = pending(brain)
    return [i for i in items if who in i.approvers] if who and brain.manifest.team else items


def team_status(brain: Brain) -> dict[str, Any]:
    """Who still has items to approve. Members with nothing pending are listed with an empty list."""
    items = pending(brain)
    by = {m.name: [i.rel for i in items if m in i.approvers] for m in brain.manifest.team}
    unassigned = [i.rel for i in items if not i.approvers]
    ats = [datetime.fromisoformat(a) for i in items if (a := i.as_dict(brain)["pending_since"])]
    oldest = (datetime.now(UTC) - min(ats)).days if ats else None
    return {"pending": len(items), "oldest_days": oldest, "approvers": by, "unassigned": unassigned,
            "items": [i.as_dict(brain) for i in items]}


# --- decisions ----------------------------------------------------------------------------------

def resolve(brain: Brain, ref: str) -> Path:
    rel = ref.strip().removeprefix("knowledge/").removesuffix(".md")
    path = brain.knowledge / f"{rel}.md"
    if not path.is_file():
        raise BrainError(f"no such concept: {rel}")
    return path


def approve(brain: Brain, refs: list[str], audience: str | None = None) -> list[Path]:
    levels = brain.manifest.audiences
    if audience and audience not in levels:
        raise BrainError(f"unknown audience '{audience}'; brain.yaml defines {levels or 'none'}")
    who = gitops.user(brain.root)
    if not who:
        raise BrainError("set git user.email or user.name to record who approved")
    paths = [resolve(brain, r) for r in refs]
    for path in paths:
        c = okf.read(path)
        c.meta["approval"] = "approved"
        if audience:
            c.meta["audience"] = audience
        verified = c.meta.get("verified") or []
        verified = [verified] if isinstance(verified, dict) else list(verified)
        c.meta["verified"] = [*verified, {"by": f"human:{who}", "at": okf.now_iso()}]
        okf.write(c)
    return paths


def discard(brain: Brain, refs: list[str]) -> list[tuple[Path, str]]:
    """A change to an approved item is undone (git restores that version); a new item becomes a
    tombstone, so the next document doesn't recreate it. Returns (path, what happened)."""
    done = []
    for path in [resolve(brain, r) for r in refs]:
        found = last_approved(brain.root, brain.rel(path))
        if found:
            path.write_text(found[1], encoding="utf-8")
            done.append((path, f"restored the version approved in {found[0][:8]}"))
        else:
            c = okf.read(path)
            c.meta["approval"] = "discarded"
            okf.write(c)
            done.append((path, "discarded"))
    return done


# --- audiences ----------------------------------------------------------------------------------

def visible(brain: Brain, c: okf.Concept, audience: str) -> bool:
    """Approved, and meant for this audience or a wider one. Unknown audiences are never visible."""
    levels = brain.manifest.audiences
    aud = audience_of(brain, c)
    return state(c) == "approved" and aud in levels and levels.index(aud) <= levels.index(audience)


def check_audience(brain: Brain, audience: str) -> None:
    if audience not in brain.manifest.audiences:
        raise BrainError(f"unknown audience '{audience}'; brain.yaml audiences: {brain.manifest.audiences or 'none'}")


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
