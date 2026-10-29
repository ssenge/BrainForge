"""Plugins: copied into the brain, they add types, instruction layers, procedures and scripts. Uses the bundled pm plugin."""

import json
from datetime import date, timedelta

import pytest
from conftest import write

from brainforge import agents, approval, cli, gitops, ingest, okf, plugins, procedures
from brainforge import brain as brain_mod
from brainforge.brain import BrainError, instruction_layers


@pytest.fixture
def pm(brain):
    plugins.add(brain, "pm")
    return brain


def test_add_list_and_init(brain, tmp_path, run_cli):
    assert json.loads(run_cli("--brain", brain.root, "--json", "plugin", "list")[1])[0]["status"] == "available"
    code, out, _ = run_cli("--brain", brain.root, "plugin", "add", "pm")
    assert code == 0 and "types: Milestone, Decision, Action Item" in out and "procedures: status-report" in out
    assert json.loads(run_cli("--brain", brain.root, "--json", "plugin", "list")[1])[0]["status"] == "installed"

    code, out, _ = run_cli("init", tmp_path / "proj", "--plugin", "pm")
    assert code == 0 and "with plugin pm" in out
    assert [p.name for p in plugins.installed(brain_mod.load(tmp_path / "proj"))] == ["pm"]

    write(tmp_path / "bad/plugins/other/plugin.yaml", "name: Not A Name\n")
    with pytest.raises(BrainError, match="invalid plugin other"):
        plugins.add(brain, str(tmp_path / "bad/plugins/other"))


def test_add_from_a_git_url(brain, tmp_path):
    repo = tmp_path / "brainforge-extras"  # a repository with a plugin at its root, and one in a subfolder
    write(repo / "plugin.yaml", "name: crm\ndescription: Customers.\n")
    write(repo / "plugins/hr/plugin.yaml", "name: hr\n")
    write(repo / "plugins/hr/procedures/headcount.md", "---\nname: headcount\n---\nCount.\n")
    gitops.git(tmp_path, "init", "-q", str(repo))
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "plugins")

    assert plugins.add(brain, f"file://{repo}").name == "crm"
    assert plugins.add(brain, f"file://{repo}#plugins/hr").files("procedures")[0].name == "headcount.md"
    assert [p.name for p in plugins.installed(brain)] == ["crm", "hr"]
    assert not (brain.root / "plugins/crm/.git").exists()
    with pytest.raises(BrainError, match="name the plugin's folder"):
        plugins.add(brain, f"file://{repo}#plugins")
    with pytest.raises(BrainError, match="cannot clone"):
        plugins.add(brain, f"file://{tmp_path}/missing")


def test_layers_and_procedures(pm):
    labels = [layer.label for layer in instruction_layers(pm, "ingestion")]
    assert labels[2:4] == ["Plugin pm: types", "Plugin pm (plugins/pm/instructions/ingestion.md)"]
    assert labels.index("Plugin pm: types") < labels.index("Brain instructions (instructions.md)")
    types_text = instruction_layers(pm, "ingestion")[2].text
    assert "- `actions/`: `Action Item`. A task someone committed to." in types_text
    assert "  - `state`: one of open, done, cancelled" in types_text
    assert [layer.label for layer in instruction_layers(pm, "query")][2] == "Plugin pm (plugins/pm/instructions/query.md)"

    procs, issues = procedures.load_all(pm)
    assert issues == [] and {p.name for p in procs} == {"weekly-report", "status-report"}
    write(pm.procedures / "status-report.md", "---\nname: status-report\n---\nMine.\n")  # the brain's own wins
    assert next(p for p in procedures.load_all(pm)[0] if p.name == "status-report").path.parent == pm.procedures


def test_field_values_are_checked_and_gaps_shown(pm, tmp_path, monkeypatch, run_cli):
    monkeypatch.setenv("FAKE_AGENT", "bad-field")
    out = ingest.ingest(pm, write(tmp_path / "a.md", "Milestone: Go-live = 2027-02-01\n"), agents.resolve(pm))
    assert out.status == "failed" and "state must be one of planned, achieved, missed, cancelled, not 'someday'" in out.message
    assert not (pm.knowledge / "milestones/go-live.md").exists()

    monkeypatch.delenv("FAKE_AGENT")
    assert ingest.ingest(pm, write(tmp_path / "b.md", "Milestone: Go-live = 2027-02-01\n"), agents.resolve(pm)).status == "ingested"
    item = approval.pending(pm)[0].as_dict(pm)
    assert item["id"] == "milestones/go-live" and item["gaps"] == ["date"]
    assert "missing date" in run_cli("--brain", pm.root, "pending")[1]


def test_plugin_scripts_run_in_the_brain(brain, tmp_path, capfd):
    write(tmp_path / "hello/plugin.yaml", "name: hello\n")
    write(tmp_path / "hello/scripts/greet.py", "import os, sys\nprint(os.getcwd(), *sys.argv[1:])\nsys.exit(3)\n")
    plugins.add(brain, str(tmp_path / "hello"))
    assert cli.run(["--brain", str(brain.root), "plugin", "run", "hello", "greet", "a", "b"]) == 3
    assert capfd.readouterr().out == f"{brain.root} a b\n"
    assert cli.run(["--brain", str(brain.root), "plugin", "run", "hello", "nope"]) == 2


def test_validate_reports_wrong_values_and_groups(pm, run_cli):
    write(pm.knowledge / "topics/a-risk.md", "---\ntype: Risk\ntitle: A risk\nlikelihood: huge\n---\nx\n")
    code, out, _ = run_cli("--brain", pm.root, "brain", "validate")
    assert code == 1 and "likelihood must be one of low, medium, high, not 'huge'" in out and "belongs in risks/" in out


def test_review_period_expires_and_renews(brain, tmp_path, run_cli):
    write(tmp_path / "review/plugin.yaml", "name: review\ntypes:\n  - {type: Milestone, group: milestones, stale_after_days: 10}\n")
    plugins.add(brain, str(tmp_path / "review"))
    gitops.commit(brain.root, "plugin", ["plugins"])
    gitops.git(brain.root, "config", "user.email", "dana@example.com")
    assert ingest.ingest(brain, write(tmp_path / "m.md", "Milestone: Go-live = 2027-02-01\n"), agents.resolve(brain)).status == "ingested"
    go_live = brain.knowledge / "milestones/go-live.md"
    assert okf.read(go_live).meta["stale_after"][:10] == str(date.today() + timedelta(days=10))
    assert run_cli("--brain", brain.root, "approve", "milestones/go-live")[0] == 0

    def expire():
        c = okf.read(go_live)
        c.meta["stale_after"] = "2020-01-01T00:00:00Z"
        okf.write(c)
        gitops.commit(brain.root, "time passes", ["knowledge"])

    expire()
    code, out, _ = run_cli("--brain", brain.root, "pending", "--check")
    assert code == 1 and "! milestones/go-live" in out
    assert run_cli("--brain", brain.root, "approve", "milestones/go-live")[0] == 0  # still true: renewed
    assert okf.read(go_live).meta["stale_after"] > okf.now_iso() and approval.pending(brain) == []

    expire()
    code, out, _ = run_cli("--brain", brain.root, "discard", "milestones/go-live")
    assert code == 0 and "retired (status: deprecated)" in out
    assert okf.read(go_live).meta["status"] == "deprecated" and approval.pending(brain) == []
