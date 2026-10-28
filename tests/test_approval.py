"""Approval status, approver routing, the pending scanner, and audience-restricted reports."""

import io
import json
import re

import pytest
from conftest import write

from brainforge import agents, approval, cli, gitops, ingest, okf, procedures, query
from brainforge import brain as brain_mod

TEAM = """
audiences: [client, internal]
team:
  - name: Alice Smith
    email: alice@example.com
    aliases: [Ali]
  - name: Bob Lee
    approves: [{type: Milestone}]
  - name: Carol Lead
    approves: [{type: "*"}]
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


def test_ingestion_marks_pending_and_the_agent_cannot_approve_or_widen(team, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "self-approve")
    add(team, tmp_path, "Topic: Quantum\n")
    assert meta(team, "topics/quantum")["approval"] == "pending"
    assert meta(team, "topics/quantum")["audience"] == "internal"
    source = okf.read(next((team.knowledge / "sources").glob("*.md"))).meta
    assert "approval" not in source and source["audience"] == "internal"


def test_needs_approval_limits_what_is_pending(team, tmp_path, monkeypatch):
    manifest = team.root / "brain.yaml"
    manifest.write_text(manifest.read_text() + "needs_approval: [{type: Milestone}]\n")
    gitops.commit(team.root, "only milestones", ["brain.yaml"])
    b = brain_mod.load(team.root)
    monkeypatch.setenv("FAKE_AGENT", "self-approve")  # the agent still can't set it either way
    add(b, tmp_path, "Topic: Quantum\nMilestone: Go-live = 2026-11-10\n")
    assert "approval" not in meta(b, "topics/quantum") and meta(b, "milestones/go-live")["approval"] == "pending"


def test_routing_by_owner_alias_then_rules(team):
    def who(m):
        return [x.name for x in approval.approvers(team, okf.Concept(team.knowledge / "x.md", m))]

    assert who({"type": "Action Item", "owner": "Ali / Bob Lee"}) == ["Alice Smith", "Bob Lee"]
    assert who({"type": "Milestone"}) == ["Bob Lee"]
    assert who({"type": "Topic", "owner": "someone external"}) == ["Carol Lead"]
    assert approval.me(team).name == "Alice Smith"


def test_pending_approve_and_discard(team, tmp_path, run_cli):
    add(team, tmp_path, "Topic: Quantum\nMilestone: Go-live = 2026-11-10\n")
    code, out, _ = run_cli("--brain", team.root, "pending", "--team", "--check")
    assert code == 1 and "2 item(s) await approval" in out
    assert "Bob Lee" in out and "milestones/go-live" in out and "Alice Smith" in out and "done" in out
    assert run_cli("--brain", team.root, "pending")[1].startswith("0 item(s) awaiting approval by Alice Smith")
    items = json.loads(run_cli("--brain", team.root, "--json", "pending", "--for", "Bob Lee")[1])
    assert [i["id"] for i in items] == ["milestones/go-live"] and items[0]["kind"] == "new"
    assert items[0]["url"].startswith("obsidian://open?path=")

    assert run_cli("--brain", team.root, "approve", "topics/quantum", "milestones/go-live",
                   "--audience", "client")[0] == 0
    assert meta(team, "milestones/go-live")["approval"] == "approved"
    assert meta(team, "milestones/go-live")["audience"] == "client"
    assert meta(team, "milestones/go-live")["verified"][0]["by"] == "human:alice@example.com"
    assert run_cli("--brain", team.root, "pending", "--team", "--check")[0] == 0

    # A change to an approved item is pending again; discarding it restores the approved version.
    add(team, tmp_path, "Milestone: Go-live = 2026-12-01\n", "update.md")
    items = json.loads(run_cli("--brain", team.root, "--json", "pending", "--all")[1])
    assert items[0]["kind"] == "changed" and items[0]["audience"] == "client"
    code, out, _ = run_cli("--brain", team.root, "discard", "milestones/go-live")
    assert code == 0 and "restored the version approved" in out
    go_live = (team.knowledge / "milestones/go-live.md").read_text()
    assert "2026-12-01" not in go_live and "approval: approved" in go_live

    # A new item becomes a hidden tombstone.
    add(team, tmp_path, "Topic: Noise\n", "noise.md")
    assert run_cli("--brain", team.root, "discard", "topics/noise")[0] == 0
    assert meta(team, "topics/noise")["approval"] == "discarded"
    assert [c.path.stem for _, c in okf.search(team.knowledge, "noise", type="Topic")] == []
    assert [c.path.stem for _, c in okf.search(team.knowledge, "noise", type="Topic", approval="discarded")] == ["noise"]
    assert gitops.status(team.root) == []
    assert run_cli("--brain", team.root, "brain", "validate")[0] == 0


PROC = "---\nname: client-status\naudience: client\nrequire_approval: true\n---\nSummarize for the client.\n"


def test_client_report_waits_for_approval_and_sees_only_client_knowledge(team, tmp_path, prompts, monkeypatch):
    write(team.root / "procedures/client-status.md", PROC)
    gitops.commit(team.root, "procedure", ["procedures"])
    add(team, tmp_path, "Topic: Public\nTopic: Secret\nMilestone: Integration testing = 2026-11-17\n")
    proc = procedures.get(team, "client-status")
    with pytest.raises(procedures.ProcedureError, match=r"3 item\(s\) await approval: Bob Lee \(1\), Carol Lead \(2\)"):
        procedures.run(team, proc, agents.resolve(team))

    approval.approve(team, ["topics/public"], "client")
    approval.approve(team, ["topics/secret", "milestones/integration-testing"])
    gitops.commit(team.root, "approve", ["knowledge"])
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


def test_audience_view_unlinks_what_the_audience_may_not_see(team, tmp_path):
    add(team, tmp_path, "Topic: Public\nTopic: Secret\n")
    public = okf.read(team.knowledge / "topics/public.md")
    public.body += "\n\nRelated: [Secret](../topics/secret.md#risk), [Itself](public.md), [Web](https://x.org/a.md)."
    okf.write(public)
    approval.approve(team, ["topics/public"], "client")
    approval.approve(team, ["topics/secret"])
    with approval.view(team, "client") as (root, _):
        assert "Related: Secret, [Itself](public.md), [Web](https://x.org/a.md)." in (
            root / "knowledge/topics/public.md").read_text()


def test_unknown_audience_is_a_validation_error(team):
    write(team.root / "procedures/odd.md", "---\naudience: board\n---\nx\n")
    _, issues = procedures.load_all(team)
    assert any("audience 'board'" in i.message for i in issues)


def share(b, tmp_path):
    """Give the brain a remote and return a teammate's clone of it."""
    remote, branch = tmp_path / "remote.git", gitops.git(b.root, "branch", "--show-current").strip()
    gitops.git(tmp_path, "init", "-q", "--bare", "-b", branch, str(remote))
    gitops.git(b.root, "remote", "add", "origin", str(remote))
    gitops.git(b.root, "push", "-q", "-u", "origin", "HEAD")
    gitops.git(tmp_path, "clone", "-q", str(remote), "teammate")
    return tmp_path / "teammate"


def test_decisions_pull_push_and_stop_on_unseen_changes(team, tmp_path, run_cli):
    add(team, tmp_path, "Topic: Quantum\nMilestone: Go-live = 2026-11-10\n")
    mate = share(team, tmp_path)
    gitops.git(mate, "config", "user.name", "Carol Lead")
    version = json.loads(run_cli("--brain", team.root, "--json", "pending", "--all")[1])[1]["version"]

    # Alice decides offline, Carol pushes first; Alice's push rebases onto it, since they touched different files.
    assert run_cli("--brain", team.root, "approve", f"milestones/go-live@{version}")[0] == 0
    assert run_cli("--brain", mate, "approve", "topics/quantum", "--push")[0] == 0
    cli.publish(team, None)
    gitops.pull(mate)
    assert meta(brain_mod.load(mate), "milestones/go-live")["approval"] == "approved"
    assert meta(team, "topics/quantum")["approval"] == "approved"

    # The teammate's clone changes the item; Alice decides on the version she saw and is stopped.
    seen = approval.version(team.root, "knowledge/milestones/go-live.md")
    add(brain_mod.load(mate), tmp_path, "Milestone: Go-live = 2027-02-01\n", "moved.md")
    gitops.push(mate)
    code, _, err = run_cli("--brain", team.root, "approve", f"milestones/go-live@{seen[:7]}", "--push")
    assert code == 1 and "milestones/go-live changed since you looked, so nothing was decided" in err
    assert meta(team, "milestones/go-live")["approval"] == "pending" and gitops.status(team.root) == []

    # Same file changed on both sides between pull and push: the decision is undone, not merged.
    assert run_cli("--brain", mate, "approve", "milestones/go-live", "--push")[0] == 0
    start = gitops.head(team.root)
    approval.discard(team, ["milestones/go-live"])
    gitops.commit(team.root, "discard", ["knowledge"])
    with pytest.raises(gitops.Stale, match="undone"):
        cli.publish(team, start)
    assert meta(team, "milestones/go-live")["approval"] == "approved" and gitops.status(team.root) == []


def test_github_comment_route(team, tmp_path, run_cli, monkeypatch):
    manifest = team.root / "brain.yaml"
    manifest.write_text(manifest.read_text().replace("aliases: [Ali]", "aliases: [Ali]\n    github: alice-gh")
                        .replace("name: Bob Lee", "name: Bob Lee\n    github: bob-gh"))
    gitops.commit(team.root, "github logins", ["brain.yaml"])
    add(team, tmp_path, "Topic: Quantum\nMilestone: Go-live = 2026-11-10\n")
    share(team, tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/brain")

    code, out, _ = run_cli("--brain", team.root, "ci", "remind")
    assert code == 0 and out.startswith("**2 item(s) await approval.**")
    assert "@bob-gh\n- [Go-live](https://github.com/acme/brain/blob/" in out and "Carol Lead (no `github` login" in out
    command = re.search(r"`(approve milestones/go-live@\w+)`", out).group(1)

    def comment(author, text):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        return run_cli("--brain", team.root, "ci", "decide", "--author", author)

    code, out, _ = comment("Bob-GH", f"Looks right.\n{command}\n> approve topics/quantum\n")
    assert code == 0 and "- approved milestones/go-live (" in out and out.endswith("Nothing else waits for you.\n")
    remote = tmp_path / "remote.git"
    assert gitops.git(remote, "log", "-1", "--format=%an|%s").strip() == "Bob Lee|approve: milestones/go-live"
    assert meta(team, "milestones/go-live")["verified"][-1]["by"] == "human:bob-gh@users.noreply.github.com"
    assert meta(team, "topics/quantum")["approval"] == "pending"  # quoted lines don't count

    assert comment("mallory", "approve topics/quantum")[0] == 1
    code, out, _ = comment("alice-gh", "approve topics/quantum@0000000\ndiscard topics/nothing")
    assert code == 1 and "topics/quantum changed since you looked" in out and "no such concept: topics/nothing" in out
    assert comment("alice-gh", "thanks, all good")[1] == "" and gitops.status(team.root) == []

    assert run_cli("--brain", team.root, "ci", "install", "--remind", "0 6 * * 1")[0] == 0
    workflow = (team.root / ".github/workflows/brainforge-approvals.yml").read_text()
    assert 'cron: "0 6 * * 1"' in workflow and "brainforge ci decide" in workflow
