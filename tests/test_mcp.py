import os
import sys

import anyio
import pytest
from conftest import write
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from brainforge import brain as brain_mod
from brainforge import mcp_server

MILESTONE = """---
type: Milestone
title: Go-live
description: Production go-live of the platform.
---
Go-live is planned for 2027-01-15.
"""
REPORT = """---
type: Report
procedure: {name: weekly-report, version: 1}
generated: {by: brainforge/test, at: "2026-10-28T08:00:00Z"}
---
All on track.
"""


@pytest.fixture
def brains(brain, tmp_path):
    write(brain.knowledge / "milestones" / "go-live.md", MILESTONE)
    write(brain.reports / "weekly" / "2026-10-28.md", REPORT)
    other = brain_mod.init(tmp_path / "other", name="Other", brain_id="client-b/project-2")
    return mcp_server.Brains([brain, other], pull=False)


def test_list_search_read(brains):
    listed = {b["id"]: b for b in mcp_server.list_brains(brains)}
    assert listed["test"]["concepts"] == 1
    assert listed["test"]["latest_reports"] == {"weekly-report": "reports/weekly/2026-10-28.md"}
    assert listed["client-b/project-2"]["concepts"] == 0

    hits = mcp_server.search(brains, "go-live")
    assert hits[0] == {"brain": "test", "path": "knowledge/milestones/go-live.md", "title": "Go-live",
                       "type": "Milestone", "description": "Production go-live of the platform.",
                       "review": "confirmed", "url": hits[0]["url"], "score": hits[0]["score"]}
    assert hits[0]["url"].startswith("obsidian://open?path=") and hits[0]["url"].endswith("go-live.md")
    assert mcp_server.search(brains, "", type="Risk") == []

    assert "2027-01-15" in mcp_server.read(brains, "milestones/go-live", brain="test")
    assert "All on track" in mcp_server.read(brains, "reports/weekly/2026-10-28.md", brain="test")
    assert "okf_version" in mcp_server.read(brains, "knowledge/index.md", brain="test")


@pytest.mark.parametrize("path", ["../brain.yaml", "knowledge/../brain.yaml", "/etc/passwd", "inbox/x.md"])
def test_read_stays_inside_knowledge_and_reports(brains, path):
    with pytest.raises(ValueError):
        mcp_server.read(brains, path, brain="test")


def test_brain_is_required_when_ambiguous(brains):
    with pytest.raises(ValueError, match="available: test, client-b/project-2"):
        mcp_server.read(brains, "knowledge/index.md")


def test_stdio_round_trip(brain):
    """Real protocol over stdio via `brainforge mcp`: stdout must carry nothing but MCP."""
    write(brain.knowledge / "milestones" / "go-live.md", MILESTONE)
    params = StdioServerParameters(
        command=sys.executable, args=["-c", "from brainforge.cli import main; main()", "mcp", str(brain.root)],
        env=dict(os.environ),
    )

    async def session() -> tuple[set[str], str]:
        async with stdio_client(params) as (read, write_), ClientSession(read, write_) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            result = await s.call_tool("read", {"path": "milestones/go-live"})
            return tools, result.content[0].text  # pyright: ignore[reportAttributeAccessIssue]

    tools, text = anyio.run(session)
    assert tools == {"list_brains", "search", "read"}
    assert "2027-01-15" in text
