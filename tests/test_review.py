"""Review status, reviewer routing, the pending-review scanner, and audience-restricted reports."""

import json

import pytest
from conftest import write

from brainforge import agents, gitops, ingest, okf, procedures, query, review
from brainforge import brain as brain_mod

TEAM = """
visibility: [client, internal]
team:
  - name: Alice Smith
    email: alice@example.com
    aliases: [Ali]
  - name: Bob Lee
    reviews: [{type: Milestone}]
  - name: Carol Lead
    reviews: [{type: "*"}]
"""


@pytest.fixture
def team(brain):
    manifest = brain.root / "brain.yaml"
    manifest.write_text(manifest.read_text() + TEAM)
    gitops.commit(brain.root, "configure team", ["brain.yaml"])
    gitops.git(brain.root, "config", "user.email", "alice@example.com")
    return brain_mod.load(brain.root)


def add(b, tmp_path, text, name="doc.md"):
    outcome = ingest.ingest(b, write(tmp_path / name, text), agents.resolve(b))
    assert outcome.status == "ingested", outcome.message
    return outcome


def meta(b, rel):
    return okf.read(b.knowledge / f"{rel}.md").meta


def test_ingestion_proposes_and_the_agent_cannot_confirm_or_open_up(team, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "self-confirm")
    add(team, tmp_path, "Topic: Quantum\n")
    assert meta(team, "topics/quantum")["review"] == "proposed"
    assert meta(team, "topics/quantum")["visibility"] == "internal"
    source = okf.read(next((team.knowledge / "sources").glob("*.md"))).meta
    assert "review" not in source and source["visibility"] == "internal"


def test_needs_review_limits_what_is_proposed(team, tmp_path, monkeypatch):
    manifest = team.root / "brain.yaml"
    manifest.write_text(manifest.read_text() + "needs_review: [{type: Milestone}]\n")
    gitops.commit(team.root, "only milestones", ["brain.yaml"])
    b = brain_mod.load(team.root)
    monkeypatch.setenv("FAKE_AGENT", "self-confirm")  # the agent still can't set it either way
    add(b, tmp_path, "Topic: Quantum\nMilestone: Go-live = 2026-11-10\n")
    assert "review" not in meta(b, "topics/quantum") and meta(b, "milestones/go-live")["review"] == "proposed"


def test_routing_by_owner_alias_then_rules(team):
    def who(m):
        return [x.name for x in review.reviewers(team, okf.Concept(team.knowledge / "x.md", m))]

    assert who({"type": "Action Item", "owner": "Ali / Bob Lee"}) == ["Alice Smith", "Bob Lee"]
    assert who({"type": "Milestone"}) == ["Bob Lee"]
    assert who({"type": "Topic", "owner": "someone external"}) == ["Carol Lead"]
    assert review.me(team).name == "Alice Smith"


def test_scanner_confirm_and_discard(team, tmp_path, run_cli):
    add(team, tmp_path, "Topic: Quantum\nMilestone: Go-live = 2026-11-10\n")
    code, out, _ = run_cli("--brain", team.root, "review", "status", "--check")
    assert code == 1 and "2 item(s) await review" in out
    assert "Bob Lee" in out and "milestones/go-live" in out and "Alice Smith" in out and "done" in out
    assert run_cli("--brain", team.root, "review", "list")[1].startswith("0 item(s) to review for Alice Smith")
    items = json.loads(run_cli("--brain", team.root, "--json", "review", "list", "--for", "Bob Lee")[1])
    assert [i["id"] for i in items] == ["milestones/go-live"] and items[0]["kind"] == "new"
    assert items[0]["url"].startswith("obsidian://open?path=")

    assert run_cli("--brain", team.root, "review", "confirm", "topics/quantum", "milestones/go-live",
                   "--visibility", "client")[0] == 0
    assert meta(team, "milestones/go-live")["review"] == "confirmed"
    assert meta(team, "milestones/go-live")["visibility"] == "client"
    assert meta(team, "milestones/go-live")["verified"][0]["by"] == "human:alice@example.com"
    assert run_cli("--brain", team.root, "review", "status", "--check")[0] == 0

    # A change to a confirmed item is proposed again; discarding it restores the confirmed version.
    add(team, tmp_path, "Milestone: Go-live = 2026-12-01\n", "update.md")
    items = json.loads(run_cli("--brain", team.root, "--json", "review", "list", "--all")[1])
    assert items[0]["kind"] == "changed" and items[0]["visibility"] == "client"
    code, out, _ = run_cli("--brain", team.root, "review", "discard", "milestones/go-live")
    assert code == 0 and "restored the version confirmed" in out
    go_live = (team.knowledge / "milestones/go-live.md").read_text()
    assert "2026-12-01" not in go_live and "review: confirmed" in go_live

    # A new item becomes a hidden tombstone.
    add(team, tmp_path, "Topic: Noise\n", "noise.md")
    assert run_cli("--brain", team.root, "review", "discard", "topics/noise")[0] == 0
    assert meta(team, "topics/noise")["review"] == "discarded"
    assert [c.path.stem for _, c in okf.search(team.knowledge, "noise", type="Topic")] == []
    assert [c.path.stem for _, c in okf.search(team.knowledge, "noise", type="Topic", review="discarded")] == ["noise"]
    assert gitops.status(team.root) == []
    assert run_cli("--brain", team.root, "brain", "validate")[0] == 0


PROC = "---\nname: client-status\naudience: client\nrequire_review: true\n---\nSummarize for the client.\n"


def test_client_report_waits_for_review_and_sees_only_client_knowledge(team, tmp_path, prompts, monkeypatch):
    write(team.root / "procedures/client-status.md", PROC)
    gitops.commit(team.root, "procedure", ["procedures"])
    add(team, tmp_path, "Topic: Public\nTopic: Secret\nMilestone: Integration testing = 2026-11-17\n")
    proc = procedures.get(team, "client-status")
    with pytest.raises(procedures.ProcedureError, match=r"3 item\(s\) await review: Bob Lee \(1\), Carol Lead \(2\)"):
        procedures.run(team, proc, agents.resolve(team))

    review.confirm(team, ["topics/public"], "client")
    review.confirm(team, ["topics/secret", "milestones/integration-testing"])
    gitops.commit(team.root, "review", ["knowledge"])
    result = procedures.run(team, proc, agents.resolve(team))
    report = (team.root / result["report"]).read_text()
    assert "public.md" in report and "secret" not in report and "integration-testing" not in report
    assert okf.split_frontmatter(report)[0]["audience"] == "client"
    assert "secret" not in prompts()

    monkeypatch.setenv("FAKE_AGENT", "leak")
    with pytest.raises(procedures.ProcedureError, match="may not see: knowledge/topics/secret.md"):
        procedures.run(team, proc, agents.resolve(team), force=True)
    assert gitops.status(team.root) == []

    answer = query.query(team, "Which secret and public topics?", agents.resolve(team), audience="client")
    assert answer["citations"] == ["knowledge/topics/public.md"]


def test_unknown_audience_is_a_validation_error(team):
    write(team.root / "procedures/odd.md", "---\naudience: board\n---\nx\n")
    _, issues = procedures.load_all(team)
    assert any("audience 'board'" in i.message for i in issues)
