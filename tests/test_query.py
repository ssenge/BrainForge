"""Slice 4: read-only question answering."""

import pytest
from conftest import write

from brainforge import agents, gitops, ingest, query


@pytest.fixture
def stocked(brain, tmp_path):
    ingest.ingest(brain, write(tmp_path / "paper.md", "Topic: Quantum annealing\n"), agents.resolve(brain))
    return brain


def test_answer_cites_existing_concepts(stocked):
    result = query.query(stocked, "What about annealing?", agents.resolve(stocked))
    assert result["citations"] == ["knowledge/topics/quantum-annealing.md"]
    assert result["unknown_citations"] == []


def test_missing_knowledge_is_said_out_loud(stocked):
    result = query.query(stocked, "Which photonics vendors?", agents.resolve(stocked))
    assert "Not in the knowledge base" in result["answer"] and result["citations"] == []


def test_invented_citations_are_flagged(stocked, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "badcite")
    result = query.query(stocked, "anything", agents.resolve(stocked))
    assert result["unknown_citations"] == ["knowledge/topics/ghost.md"]


def test_query_that_writes_is_reverted(stocked, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "write")
    with pytest.raises(agents.AgentError, match="modified files"):
        query.query(stocked, "annealing", agents.resolve(stocked))
    assert gitops.status(stocked.root) == []


def test_cli_query_json(stocked, run_cli):
    import json

    code, out, _ = run_cli("--brain", stocked.root, "--json", "knowledge", "query", "annealing?")
    assert code == 0 and json.loads(out)["citations"] == ["knowledge/topics/quantum-annealing.md"]
