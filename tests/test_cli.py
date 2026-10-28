"""End to end through the CLI (Journey 1 and friends), plus slices 11 and 12."""

import json

from conftest import write

from brainforge import gitops


def test_journey_personal_brain(tmp_path, run_cli, monkeypatch):
    code, out, _ = run_cli("init", tmp_path / "quantum-research")
    assert code == 0
    monkeypatch.chdir(tmp_path / "quantum-research")  # brain discovered from cwd from here on

    doc = write(tmp_path / "qaoa-review.md", "Topic: QAOA\nTopic: Quantum annealing\n")
    assert run_cli("ingest", doc)[0] == 0
    assert run_cli("ingest", doc)[1].startswith("skipped")

    code, out, _ = run_cli("--json", "knowledge", "search", "qaoa")
    assert code == 0 and json.loads(out)[0]["path"] == "knowledge/topics/qaoa.md"
    code, out, _ = run_cli("knowledge", "query", "Which QAOA techniques?")
    assert "knowledge/topics/qaoa.md" in out

    code, out, _ = run_cli("run", "weekly-report")
    assert code == 0 and out.startswith("Wrote reports/weekly/")
    assert run_cli("brain", "validate")[0] == 0
    code, out, _ = run_cli("brain", "show")
    assert "concepts:   3" in out  # 2 topics + 1 source

    gitops.git(tmp_path / "quantum-research", "config", "user.email", "alice@example.com")
    assert run_cli("review", "confirm", "topics/qaoa")[0] == 0
    assert "human:alice@example.com" in (tmp_path / "quantum-research/knowledge/topics/qaoa.md").read_text()


def test_exit_codes(brain, tmp_path, run_cli, monkeypatch):
    assert run_cli("--brain", tmp_path / "nowhere", "brain", "show")[0] == 2
    assert run_cli("--brain", brain.root, "run", "no-such-procedure")[0] == 1
    good = write(tmp_path / "good.md", "Topic: Good\n")
    bad = write(tmp_path / "bad.xyz", "?")
    assert run_cli("--brain", brain.root, "ingest", good, bad)[0] == 3  # partial failure
    assert run_cli("--brain", brain.root, "nonsense")[0] == 2


def test_push_to_remote(brain, tmp_path, run_cli):
    remote = tmp_path / "remote.git"
    gitops.git(tmp_path, "init", "-q", "--bare", str(remote))
    gitops.git(brain.root, "remote", "add", "origin", str(remote))
    gitops.git(brain.root, "push", "-q", "-u", "origin", "HEAD")
    assert run_cli("--brain", brain.root, "ingest", "--push", write(tmp_path / "d.md", "Topic: D\n"))[0] == 0
    assert gitops.git(remote, "log", "-1", "--format=%s").strip() == "ingest: d.md"


def test_skills_install(brain, run_cli):
    assert run_cli("--brain", brain.root, "skills", "install", "claude")[0] == 0
    assert run_cli("--brain", brain.root, "skills", "install", "codex")[0] == 0
    claude = (brain.root / ".claude/skills/brainforge/SKILL.md").read_text()
    assert claude.startswith("---\nname: brainforge\ndescription:")
    assert (brain.root / ".agents/skills/brainforge/SKILL.md").read_text() == claude


def test_obsidian_workspace(brain, tmp_path, run_cli):
    run_cli("--brain", brain.root, "ingest", write(tmp_path / "d.md", "Topic: D\n"))
    (brain.root / ".obsidian").mkdir()
    (brain.root / ".obsidian/app.json").write_text('{"theme": "dark"}')
    assert run_cli("--brain", brain.root, "workspace", "open", "--no-launch")[0] == 0
    app = json.loads((brain.root / ".obsidian/app.json").read_text())
    assert app["theme"] == "dark" and app["useMarkdownLinks"] is True and app["alwaysUpdateLinks"] is False
    graph = json.loads((brain.root / ".obsidian/graph.json").read_text())
    assert graph["search"] == "path:knowledge/ -file:index.md -file:log.md -[review:discarded]"
    assert [g["query"] for g in graph["colorGroups"]] == ['[type:"Source Document"]', '[type:"Topic"]']
    assert gitops.status(brain.root) == []  # .obsidian/ is gitignored
