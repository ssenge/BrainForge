"""Slice 1: brain skeleton, manifest, discovery, agent selection."""

import pytest
from conftest import commits, write

from brainforge import agents, gitops
from brainforge import brain as brain_mod
from brainforge.brain import BrainError


def test_init_creates_valid_committed_brain(brain, run_cli):
    for d in brain_mod.DIRS:
        assert (brain.root / d).is_dir()
    assert (brain.knowledge / "index.md").read_text().startswith('---\nokf_version: "0.2"\n---')
    assert commits(brain.root) == ["brainforge: initialize brain test"]
    assert gitops.status(brain.root) == []
    code, out, _ = run_cli("--brain", brain.root, "brain", "validate")
    assert code == 0, out


def test_init_refuses_existing_brain(brain):
    with pytest.raises(BrainError, match="already contains"):
        brain_mod.init(brain.root)


def test_find_walks_up_from_subdirectory(brain):
    assert brain_mod.find(brain.knowledge).root == brain.root


def test_invalid_manifest_is_a_config_error(brain, run_cli):
    write(brain.root / "brain.yaml", "schema_version: 1\nid: Not Valid\nname: x\ncolour: blue\n")
    code, _, err = run_cli("--brain", brain.root, "brain", "validate")
    assert code == 2
    assert "id:" in err and "colour" in err


def test_hierarchy_is_reserved_for_phase_b(brain):
    write(brain.root / "brain.yaml", "id: test\nname: T\nparent:\n  repository: git@x:y.git\n")
    with pytest.raises(BrainError, match="Phase B"):
        brain_mod.load(brain.root)


def test_agent_resolution_order_and_allow_list(brain, monkeypatch):
    monkeypatch.delenv("BRAINFORGE_AGENT")
    assert agents.resolve(brain).adapter == "claude"
    monkeypatch.setenv("BRAINFORGE_AGENT", "codex")
    assert agents.resolve(brain).adapter == "codex"
    assert agents.resolve(brain, "claude", "claude-haiku-5-5").model == "claude-haiku-5-5"
    write(brain.root / "brain.yaml", "id: test\nname: T\nagent:\n  adapter: claude\n  allowed: [claude]\n")
    with pytest.raises(BrainError, match="not in agent.allowed"):
        agents.resolve(brain_mod.load(brain.root))


def test_claude_command_isolation_depends_on_auth(brain, monkeypatch, tmp_path):
    cfg = brain_mod.AgentConfig(adapter="claude", model="m")
    cmd = agents.build_command(cfg, brain.root, True, tmp_path / "x")
    assert "--setting-sources" in cmd and "--bare" not in cmd  # subscription login
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cmd = agents.build_command(cfg, brain.root, False, tmp_path / "x")
    assert "--bare" in cmd and "Write" not in cmd[cmd.index("--tools") + 1]
