"""Agent skills (Claude Code, Codex) and the Obsidian view."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from brainforge import okf
from brainforge.brain import Brain, BrainError, data_text

# Claude Code: .claude/skills/<name>/SKILL.md; Codex: .agents/skills/<name>/SKILL.md (repo or home).
SKILL_DIRS = {"claude": ".claude/skills", "codex": ".agents/skills"}


def install_skill(agent: str, base: Path) -> Path:
    if agent not in SKILL_DIRS:
        raise BrainError(f"unknown agent '{agent}' (claude, codex)")
    path = base / SKILL_DIRS[agent] / "brainforge" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data_text("skills/SKILL.md"), encoding="utf-8")
    return path


# Okabe-Ito palette: distinguishable for colour-blind readers.
PALETTE = [0xE69F00, 0x56B4E9, 0x009E73, 0xF0E442, 0x0072B2, 0xD55E00, 0xCC79A7, 0x999999]


def _merge_json(path: Path, update: dict) -> None:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.write_text(json.dumps({**data, **update}, indent=2) + "\n", encoding="utf-8")


def obsidian_config(brain: Brain) -> Path:
    """Write the (gitignored) .obsidian/ settings: colour by OKF type, hide machinery, keep links as written."""
    vault = brain.root / ".obsidian"
    vault.mkdir(exist_ok=True)
    types = sorted({str(c.meta.get("type")) for c in okf.read_all(brain.knowledge)})
    groups = [{"query": f'[type:"{t}"]', "color": {"a": 1, "rgb": PALETTE[i % len(PALETTE)]}}
              for i, t in enumerate(types)]
    _merge_json(vault / "graph.json", {"colorGroups": groups})
    _merge_json(vault / "app.json", {
        "userIgnoreFilters": ["archive/", ".brainforge/", ".github/"],
        "useMarkdownLinks": True,  # OKF uses standard markdown links, not [[wikilinks]]
        "newLinkFormat": "relative",
        "alwaysUpdateLinks": False,  # concept paths are identities; never rewrite links on move
    })
    return vault


def open_obsidian(brain: Brain) -> str:
    url = f"obsidian://open?path={quote(str(brain.root))}"
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if shutil.which(opener):
        subprocess.run([opener, url], capture_output=True)
    return url
