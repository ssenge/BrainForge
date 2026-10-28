"""MCP server: lets a desktop agent (Claude Desktop, …) search and read brains.

The calling agent does the reasoning. BrainForge only serves the knowledge, deterministically:
no nested agent run, no writes except `git pull --ff-only` to pick up what CI ingested.
With `approvals=True` (opt-in), it also records the user's own approve/discard decisions.
"""

from __future__ import annotations

import time
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from brainforge import __version__, approval, gitops, okf, procedures
from brainforge.brain import Brain, data_text, instruction_layers

PULL_EVERY = 300  # seconds between `git pull` per brain
READABLE = ("knowledge", "reports")
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
DECIDE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
UNDO = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)

GUIDE = """\
You are connected to BrainForge brains: Git-versioned knowledge bases in Open Knowledge Format (Markdown + YAML).
Use `list_brains` to pick the brain (match the user's wording to its id/name) and to get its instructions,
`search` to find concepts, and `read` to open them. Start with `knowledge/index.md` when browsing.
For status questions, read the latest report (from `list_brains`) and the milestone, risk, decision and action concepts.
Search results carry a `url` that opens the concept in Obsidian: cite concepts as markdown links to it, e.g. [Go-live](url).
`approval: pending` means no person has approved the concept yet: say so when an answer rests on it.

"""

APPROVALS_GUIDE = """\
Approvals are enabled: you record the user's own decisions on knowledge awaiting approval.
1. Call `pending`. Show the items as one compact table, nothing more: | # | Item | What changed | Source |,
   with Item as [title](url). For a changed item, summarize its `changes` diff in a few words.
2. Ask about up to four items at a time, one short question each, and wait for the user's explicit answer:
   Approve, one "Approve for <audience>" per entry in the item's `wider` list, Discard, or Not now.
3. Only then call `approve` or `discard` with the `ref` values exactly as `pending` returned them.
   Never decide on your own or for someone else. If a decision fails because the item changed,
   show the new version before asking again.

"""


class Brains:
    def __init__(self, brains: list[Brain], pull: bool = True):
        self.by_id = {b.id: b for b in brains}
        self.pull = pull
        self.pulled: dict[str, float] = {}
        self.sync_errors: dict[str, str] = {}

    def get(self, brain_id: str | None) -> Brain:
        if brain_id is None and len(self.by_id) == 1:
            brain_id = next(iter(self.by_id))
        if brain_id not in self.by_id:
            raise ValueError(f"unknown brain {brain_id!r}; available: {', '.join(self.by_id)}")
        b = self.by_id[brain_id]
        self.refresh(b)
        return b

    def all(self) -> list[Brain]:
        return [self.get(i) for i in self.by_id]

    def refresh(self, b: Brain) -> None:
        if not self.pull or time.monotonic() - self.pulled.get(b.id, -PULL_EVERY) < PULL_EVERY:
            return
        self.pulled[b.id] = time.monotonic()
        try:
            gitops.pull(b.root)
            self.sync_errors.pop(b.id, None)
        except gitops.GitError as exc:  # stale but usable: report it, keep serving
            self.sync_errors[b.id] = str(exc).strip()


def list_brains(brains: Brains) -> list[dict[str, Any]]:
    out = []
    for b in brains.all():
        procs, _ = procedures.load_all(b)
        latest = {p.name: b.rel(found[-1][1]) for p in procs if (found := procedures.reports(b, p))}
        local = [layer.text.strip() for layer in instruction_layers(b, "query")[2:]]
        out.append({
            "id": b.id,
            "name": b.manifest.name,
            "description": b.manifest.description,
            "concepts": len(okf.concept_files(b.knowledge)),
            "latest_reports": latest,
            "instructions": "\n\n".join(local),
            "audiences": b.manifest.audiences,
            "commit": gitops.head(b.root),
            "sync_error": brains.sync_errors.get(b.id),
        })
    return out


def search(brains: Brains, query: str = "", brain: str | None = None, type: str | None = None,
           tag: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    targets = [brains.get(brain)] if brain else brains.all()
    hits = [
        (score, b, c)
        for b in targets
        for score, c in okf.search(b.knowledge, query or None, type=type, tag=tag)
    ]
    hits.sort(key=lambda h: -h[0])
    return [
        {"brain": b.id, "path": b.rel(c.path), "url": approval.url(c.path), "title": c.title, "type": c.meta.get("type"),
         "description": c.description, "approval": approval.state(c), "score": score}
        for score, b, c in hits[:limit]
    ]


def read(brains: Brains, path: str, brain: str | None = None) -> str:
    b = brains.get(brain)
    rel = path.strip().lstrip("/")
    if not rel.startswith(tuple(f"{d}/" for d in READABLE)):
        rel = f"knowledge/{rel}"
    if not rel.endswith(".md"):
        rel += ".md"
    target = (b.root / rel).resolve()
    if not any(target.is_relative_to((b.root / d).resolve()) for d in READABLE):
        raise ValueError(f"only files under {' and '.join(READABLE)}/ can be read")
    if not target.is_file():
        raise ValueError(f"not found: {rel} (use search, or read knowledge/index.md)")
    return target.read_text(encoding="utf-8")


def pending(brains: Brains, brain: str | None = None, scope: str = "mine") -> list[dict[str, Any]]:
    b = brains.get(brain)
    if scope not in ("mine", "all"):
        raise ValueError("scope must be 'mine' or 'all'")
    if brains.pull and (warning := approval.sync(b)):
        brains.sync_errors[b.id] = warning
    who = None
    if scope == "mine" and b.manifest.team and not (who := approval.me(b)):
        raise ValueError(f"your git identity ({gitops.user(b.root) or 'not set'}) is not on this brain's team; "
                         "use scope='all'")
    items = []
    for i in approval.pending_for(b, who):
        d = i.as_dict(b)
        d["ref"] = f"{d['id']}@{d['version']}"
        if d["since"]:  # ponytail: truncated, an item diff is small unless the agent rewrote everything
            d["changes"] = gitops.git(b.root, "diff", d["since"], "--", d["path"])[:6000]
        items.append(d)
    return items


def decide(brains: Brains, verb: str, items: list[str], brain: str | None = None,
           audience: str | None = None) -> dict[str, Any]:
    """Approve or discard as the local git user; pushed when the brain has a remote."""
    b = brains.get(brain)
    if unpinned := [i for i in items if "@" not in i]:
        raise ValueError(f"pass the `ref` values from `pending` (id@version), not: {', '.join(unpinned)}")
    return approval.decide(b, verb, items, audience=audience, commit=b.manifest.git.commit,
                           push=b.manifest.git.commit and gitops.has_upstream(b.root))


def build(roots: list[Brain], pull: bool = True, approvals: bool = False) -> MCPServer:
    brains = Brains(roots, pull)
    guide = GUIDE + (APPROVALS_GUIDE if approvals else "")
    server = MCPServer("brainforge", version=__version__, instructions=guide + data_text("query.md"))

    @server.tool(name="list_brains", annotations=READ_ONLY)
    def list_brains_tool() -> list[dict[str, Any]]:
        """List the available brains: id, name, concept count, latest report per procedure and brain-specific instructions."""
        return list_brains(brains)

    @server.tool(name="search", annotations=READ_ONLY)
    def search_tool(query: str = "", brain: str | None = None, type: str | None = None,
                    tag: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Search concepts by words (empty query lists all), optionally in one brain and filtered by OKF type
        (e.g. Milestone, Risk, Decision, Action Item, Source Document) or tag. Returns paths for `read`."""
        return search(brains, query, brain, type, tag, limit)

    @server.tool(name="read", annotations=READ_ONLY)
    def read_tool(path: str, brain: str | None = None) -> str:
        """Read a concept or report as Markdown, e.g. `knowledge/milestones/go-live.md`,
        `milestones/go-live`, `knowledge/index.md` or a report path from `list_brains`."""
        return read(brains, path, brain)

    if not approvals:
        return server

    @server.tool(name="pending", annotations=READ_ONLY)
    def pending_tool(brain: str | None = None, scope: str = "mine") -> list[dict[str, Any]]:
        """Knowledge awaiting approval: the user's own items (scope 'mine') or everyone's ('all').
        Each item has a `ref` for approve/discard, a `url` to link it, and for a changed item a `changes` diff."""
        return pending(brains, brain, scope)

    @server.tool(name="approve", annotations=DECIDE)
    def approve_tool(items: list[str], brain: str | None = None, audience: str | None = None) -> dict[str, Any]:
        """Record the user's approval. Call only after the user explicitly approved each item.
        `items`: `ref` values from `pending`. `audience`: also set who may see them (from `list_brains`)."""
        return decide(brains, "approve", items, brain, audience)

    @server.tool(name="discard", annotations=UNDO)
    def discard_tool(items: list[str], brain: str | None = None) -> dict[str, Any]:
        """Record the user's decision to discard: a change to approved knowledge is undone, a new item is
        hidden. Call only after the user explicitly said so. `items`: `ref` values from `pending`."""
        return decide(brains, "discard", items, brain)

    return server


def serve(roots: list[Brain], pull: bool = True, approvals: bool = False) -> None:
    build(roots, pull, approvals).run("stdio")
