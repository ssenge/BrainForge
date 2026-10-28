"""Approval and audiences: what people have approved, who approves what, and what an audience may see.

Frontmatter only, no state store: `approval` (pending | approved | discarded, absent = approved) and
`audience` (one of brain.yaml's `audiences`, absent = the most restricted). BrainForge sets both after
every agent run; only people change them, and every decision is a commit by the person who made it.
An item reference may pin the version someone looked at (`milestones/go-live@3f2a1c9`, the commit
that last changed it); a decision on a newer version stops instead of approving unseen content.
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

from brainforge import gitops, okf, plugins
from brainforge.brain import Brain, BrainError, TeamMember


def state(c: okf.Concept) -> str:
    return str(c.meta.get("approval") or "approved")


def expired(c: okf.Concept, now: datetime | None = None) -> bool:
    """Approved, but past its `stale_after`: someone should confirm it is still true."""
    at = c.meta.get("stale_after")
    if not at or state(c) != "approved" or c.meta.get("status") == "deprecated":
        return False
    try:
        return datetime.fromisoformat(str(at)) < (now or datetime.now(UTC))
    except ValueError:
        return False


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


def version(root: Path, rel: str) -> str | None:
    """The commit that last changed the item: the version someone saw when they looked at it."""
    return gitops.git(root, "log", "-1", "--format=%H", "--", rel, check=False).strip() or None


def _wider(brain: Brain, c: okf.Concept) -> list[str]:
    """Audiences wider than the item's own: what "approve for …" can offer."""
    levels, aud = brain.manifest.audiences, audience_of(brain, c)
    return levels[:levels.index(aud)] if aud in levels else []


@dataclass
class Item:
    concept: okf.Concept
    rel: str  # knowledge/<group>/<slug>.md
    approvers: list[TeamMember]
    since: str | None  # commit of the last approved version; None for a new item
    expired: bool = False  # approved, but past its review date

    def as_dict(self, brain: Brain) -> dict[str, Any]:
        c = self.concept
        sources = c.sources()
        at = str((c.meta.get("generated") or {}).get("at", ""))
        return {
            "id": okf.concept_id(brain.knowledge, c.path), "path": self.rel, "url": url(c.path),
            "type": c.meta.get("type"), "title": c.title, "owner": c.meta.get("owner"),
            "audience": audience_of(brain, c), "wider": _wider(brain, c),
            "kind": "expired" if self.expired else "changed" if self.since else "new", "since": self.since,
            "stale_after": c.meta.get("stale_after"),
            "source": (sources[-1].get("title") or sources[-1].get("id")) if sources else None,
            "pending_since": at or None, "approvers": [m.name for m in self.approvers],
            "version": (version(brain.root, self.rel) or "")[:7] or None,
            "gaps": plugins.gaps(brain, c),
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
    """Everything awaiting a person: pending items, and approved ones past their review date."""
    items, now = [], datetime.now(UTC)
    for c in okf.read_all(brain.knowledge):
        rel = brain.rel(c.path)
        if state(c) == "pending":
            found = last_approved(brain.root, rel)
            items.append(Item(c, rel, approvers(brain, c), found[0] if found else None))
        elif expired(c, now):
            items.append(Item(c, rel, approvers(brain, c), None, expired=True))
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

def rel_of(brain: Brain, ref: str) -> str:
    """`milestones/go-live@3f2a1c9` → `knowledge/milestones/go-live.md`, whether or not it exists yet."""
    name = ref.partition("@")[0].strip().removeprefix("knowledge/").removesuffix(".md")
    return brain.rel(brain.knowledge / f"{name}.md")


def resolve(brain: Brain, ref: str) -> Path:
    """The item's path. Raises Stale if the reference pins a version and the item changed since."""
    rel, seen = rel_of(brain, ref), ref.partition("@")[2].strip()
    path = brain.root / rel
    if not path.is_file():
        raise BrainError(f"no such concept: {okf.concept_id(brain.knowledge, path)}")
    if seen and not (version(brain.root, rel) or "").startswith(seen):
        raise gitops.Stale([rel])
    return path


def approve(brain: Brain, refs: list[str], audience: str | None = None, by: str | None = None) -> list[Path]:
    """`by` records who approved (default: the git user)."""
    levels = brain.manifest.audiences
    if audience and audience not in levels:
        raise BrainError(f"unknown audience '{audience}'; brain.yaml defines {levels or 'none'}")
    who = by or gitops.user(brain.root)
    if not who:
        raise BrainError("set git user.email or user.name to record who approved")
    paths = [resolve(brain, r) for r in refs]
    for path in paths:
        c = okf.read(path)
        if expired(c) and not plugins.renew(brain, c):  # confirmed as still true
            c.meta.pop("stale_after", None)
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
    tombstone, so the next document doesn't recreate it; an expired item is retired (deprecated).
    Returns (path, what happened)."""
    done = []
    for path in [resolve(brain, r) for r in refs]:
        c = okf.read(path)
        if expired(c):  # no longer true: retire it
            c.meta["status"] = "deprecated"
            okf.write(c)
            done.append((path, "retired (status: deprecated)"))
            continue
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


def sync(brain: Brain, guard: list[str] | None = None) -> str | None:
    """Pull first, so people decide on what the team sees. Offline is fine (the push check catches
    changes later): returns a warning instead. Raises Stale if the pull changed a `guard` path."""
    try:
        gitops.pull(brain.root, guard)
    except gitops.Stale:
        raise
    except gitops.GitError as exc:
        return f"pull failed, using the local state: {exc}"
    return None


def decide(brain: Brain, verb: str, refs: list[str], *, audience: str | None = None, by: str | None = None,
           author: str | None = None, commit: bool = True, push: bool = False) -> dict[str, Any]:
    """Pull, approve or discard, commit, push. Stops when an item changed since the person looked at it,
    so nobody decides on content they haven't seen; a decision that loses that race is undone again.
    `by` is recorded in `verified`, `author` on the commit (both default to the git user)."""
    if verb not in ("approve", "discard"):
        raise BrainError(f"unknown decision '{verb}'")
    sha = None
    try:
        warning = sync(brain, [rel_of(brain, r) for r in refs if "@" not in r])  # pinned versions are checked exactly
        start = gitops.head(brain.root)
        if verb == "approve":
            done = [(p, _approved_line(brain, p, audience)) for p in approve(brain, refs, audience, by)]
        else:
            done = [(p, f"{okf.concept_id(brain.knowledge, p)}: {what}") for p, what in discard(brain, refs)]
        if commit:
            ids = [okf.concept_id(brain.knowledge, p) for p, _ in done]
            sha = gitops.commit(brain.root, f"{verb}: {', '.join(ids[:3])}{' …' if len(ids) > 3 else ''}\n\n"
                                + "\n".join(f"- {line}" for _, line in done), [brain.rel(p) for p, _ in done], author)
            if push:
                gitops.publish(brain.root, start)
    except gitops.Stale as exc:
        ids = ", ".join(okf.concept_id(brain.knowledge, brain.root / p) for p in exc.paths)
        raise gitops.GitError(f"{ids} changed since you looked, so nothing was decided; "
                              "check again with `brainforge pending`") from None
    return {"items": [brain.rel(p) for p, _ in done], "lines": [line for _, line in done], "commit": sha,
            "warnings": [warning] if warning else []}


def _approved_line(brain: Brain, path: Path, audience: str | None) -> str:
    hidden = hidden_links(brain, path, audience) if audience else []
    waiting = [h for h in hidden if state(okf.read(brain.knowledge / f"{h}.md")) == "pending"]
    narrower = [h for h in hidden if h not in waiting]
    notes = ([f"links left out of {audience} views: {', '.join(narrower)}"] if narrower else []) + (
        [f"links to items still awaiting approval: {', '.join(waiting)}"] if waiting else [])
    return (f"approved {okf.concept_id(brain.knowledge, path)}" + (f" for {audience}" if audience else "")
            + (f" ({'; '.join(notes)})" if notes else ""))


# --- GitHub comments ----------------------------------------------------------------------------

COMMAND_RE = re.compile(r"^\s*`?\s*(approve|discard)\s+([^`\n]+)", re.I | re.M)


def parse_commands(text: str) -> list[tuple[str, list[str], str | None]]:
    """(verb, items, audience) per `approve …` / `discard …` line. Quoted lines (`> …`) don't count."""
    commands = []
    for verb, rest in COMMAND_RE.findall(text):
        words, refs, audience = re.sub(r"(--audience|-a)=", r"\1 ", rest).split(), [], None
        while words:
            w = words.pop(0)
            if w in ("--audience", "-a") and words:
                audience = words.pop(0)
            else:
                refs.append(w)
        if refs:
            commands.append((verb.lower(), refs, audience))
    return commands


def reminder(brain: Brain, repo_url: str | None = None, only: TeamMember | None = None) -> str:
    """Markdown for a GitHub issue: @mentions per approver, a link and a ready-to-copy command per item.
    Empty when nothing is pending. `only`: just that person's item lines."""
    items = [i for i in pending(brain) if only is None or only in i.approvers]

    def line(i: Item) -> str:
        d = i.as_dict(brain)
        ref = f"{d['id']}@{d['version']}" if d["version"] else d["id"]
        title = f"[{d['title']}]({repo_url}/blob/{d['version']}/{i.rel})" if repo_url and d["version"] else d["title"]
        diff = f", [changes]({repo_url}/compare/{d['since'][:7]}...{d['version']})" if repo_url and d["since"] else ""
        source = f", from {d['source']}" if d["source"] else ""
        gaps = f", missing {', '.join(d['gaps'])}" if d["gaps"] else ""
        return f"- {title} ({d['kind']}{source}{gaps}{diff}): `approve {ref}`"

    if only or not items:
        return "\n".join(map(line, items))
    groups = [(f"@{m.github}" if m.github else f"{m.name} (no `github` login in brain.yaml)",
               [i for i in items if m in i.approvers]) for m in brain.manifest.team]
    groups.append(("Unassigned" if brain.manifest.team else "Pending", [i for i in items if not i.approvers]))
    parts = [f"**{len(items)} item(s) await approval.** Reply with one command per line: `approve <item>`, "
             "`approve <item> --audience <name>` or `discard <item>`. The version after `@` makes sure "
             "you decide on what you saw. For an expired item, approve confirms it is still true and "
             "discard retires it."]
    for head, group in groups:
        if group:
            parts += ["", head, *map(line, group)]
    return "\n".join(parts)


# --- audiences ----------------------------------------------------------------------------------

def visible(brain: Brain, c: okf.Concept, audience: str) -> bool:
    """Approved, and meant for this audience or a wider one. Unknown audiences are never visible."""
    levels = brain.manifest.audiences
    aud = audience_of(brain, c)
    return state(c) == "approved" and aud in levels and levels.index(aud) <= levels.index(audience)


def hidden_links(brain: Brain, path: Path, audience: str) -> list[str]:
    """Concepts this one links to that `audience` may not see; its views leave those links out."""
    bundle = brain.knowledge.resolve()
    targets = {t for t in okf.links(okf.read(path), bundle)
               if t.suffix == ".md" and t.is_file() and t.is_relative_to(bundle) and t.name not in okf.RESERVED}
    return sorted(okf.concept_id(bundle, t) for t in targets if not visible(brain, okf.read(t), audience))


def check_audience(brain: Brain, audience: str) -> None:
    if audience not in brain.manifest.audiences:
        raise BrainError(f"unknown audience '{audience}'; brain.yaml audiences: {brain.manifest.audiences or 'none'}")


LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s#]+)(#[^)\s]*)?\)")


def _unlink_hidden(path: Path, bundle: Path) -> None:
    """Links to concepts the audience may not see are removed, text included: a link's text is
    usually the hidden concept's title, which is internal too. `hidden_links` tells approvers."""
    def sub(m: re.Match[str]) -> str:
        target = m.group(2)
        if not target.endswith(".md") or re.match(r"^[a-z][a-z0-9+.-]*:", target):
            return m.group(0)
        base = bundle if target.startswith("/") else path.parent
        return m.group(0) if (base / target.lstrip("/")).exists() else ""

    path.write_text(LINK_RE.sub(sub, path.read_text(encoding="utf-8")), encoding="utf-8")


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
            _unlink_hidden(root / rel, root / "knowledge")
        (root / "knowledge").mkdir(exist_ok=True)
        okf.write_indexes(root / "knowledge")
        yield root, allowed
