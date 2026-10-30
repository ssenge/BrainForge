"""Steering: signals found by code, proposals a lead approves, questions for the round."""

import io
import json
from datetime import date, timedelta

import pytest
from conftest import commits, write
from test_approval import share
from test_rounds import fake_gh  # noqa: F401 - fixture

from brainforge import (
    agents,
    approval,
    briefing,
    gitops,
    okf,
    plugins,
    procedures,
    proposals,
    rounds,
    schedule,
    signals,
)
from brainforge import brain as brain_mod
from brainforge.brain import BrainError

TODAY = date.today()
TEAM = """
team:
  - name: Alice Smith
    email: alice@example.com
    github: alice-gh
    lead: true
  - name: Bob Lee
    aliases: [Bob]
    approves: [{type: "*"}]
"""


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def item(b, cid, commit=True, **meta):
    kind = {"actions": "Action Item", "milestones": "Milestone", "risks": "Risk"}[cid.split("/")[0]]
    okf.write(okf.Concept(b.knowledge / f"{cid}.md", {"type": kind, "title": cid.split("/")[1].replace("-", " "),
                                                      **meta}, "# Current state\n\nAs planned.\n"))
    if commit:
        gitops.commit(b.root, f"item {cid}", ["knowledge"])


@pytest.fixture
def project(brain):
    """Bob's test plan is overdue; integration testing waits for it and slipped twice; go-live waits for that."""
    (brain.root / "brain.yaml").write_text((brain.root / "brain.yaml").read_text() + TEAM)
    plugins.add(brain, "pm")
    gitops.commit(brain.root, "team, pm", ["brain.yaml", "plugins"])
    gitops.git(brain.root, "config", "user.email", "alice@example.com")
    b = brain_mod.load(brain.root)
    item(b, "actions/test-plan", owner="Bob Lee", due=day(-6), state="open")
    for when in (day(2), day(5), day(9)):  # moved later twice
        item(b, "milestones/integration-testing", owner="Alice Smith", date=when, depends_on=["actions/test-plan"])
    item(b, "milestones/go-live", owner="Alice Smith", date=day(30), depends_on=["milestones/integration-testing"])
    item(b, "actions/budget", state="open")  # nobody owns it, no date
    item(b, "actions/done-thing", owner="Bob Lee", due=day(-20), state="done")
    return b


def test_signals(project):
    found = {(s.kind, s.id): s for s in signals.compute(project)}
    assert set(found) == {("overdue", "actions/test-plan"), ("at risk", "milestones/integration-testing"),
                          ("at risk", "milestones/go-live"), ("slipping", "milestones/integration-testing"),
                          ("gap", "actions/budget")}
    assert found["at risk", "milestones/integration-testing"].detail.startswith("waits for actions/test-plan")
    assert "which is at risk" in found["at risk", "milestones/go-live"].detail
    assert found["slipping", "milestones/integration-testing"].detail == f"moved 2 time(s), from {day(2)} to {day(9)}"
    assert found["gap", "actions/budget"].detail == "no owner, due"
    assert [m.name for m in found["overdue", "actions/test-plan"].owners] == ["Bob Lee"]
    assert [m.name for m in found["gap", "actions/budget"].owners] == ["Bob Lee"]  # by his approval rule


def test_round_questions_for_each_person(project):
    write(project.procedures / "now.md", '---\nschedule: {cron: "0 0 1 1 *"}\nround: {opens: "* * * * *"}\n---\nx\n')
    gitops.commit(project.root, "a round open now", ["procedures"])
    bob = approval.member(project, "Bob")
    [r] = briefing.briefing(project, bob)["rounds"]
    assert [q["id"] for q in r["questions"]] == ["actions/test-plan", "actions/budget"]
    assert "It was due" in briefing.text(briefing.briefing(project, bob))
    [rnd] = [x for x in rounds.all_latest(project) if x.proc.name == "now"]
    body = rounds.issue_body(project, rnd, None)
    assert f"(overdue): It was due {day(-6)}. Is it done, or what is the new date?" in body


PROPOSED = [
    {"do": "message", "to": "Bob", "about": "actions/test-plan", "text": "The test plan is overdue. New date?",
     "why": "Overdue by six days and it holds up integration testing."},
    {"do": "set", "item": "milestones/integration-testing", "field": "date", "value": day(16),
     "why": "Bob said in his update it needs another week."},
    {"do": "create", "type": "Risk", "title": "Vendor delay", "fields": {"owner": "Bob Lee", "impact": "high"},
     "text": "The vendor API may come late.", "why": "Two dates moved."},
    {"do": "message", "to": "Mallory", "text": "Hi", "why": "Outside the team."},
    {"do": "set", "item": "milestones/go-live", "field": "colour", "value": "red", "why": "No such field."},
    {"do": "delete", "item": "milestones/go-live", "why": "Not an action BrainForge knows."},
]


def steer(b, monkeypatch, props=PROPOSED):
    monkeypatch.setenv("FAKE_PROPOSALS", json.dumps(props))
    return procedures.run(b, procedures.get(b, "steering"), agents.resolve(b))


def test_steering_report_and_a_lead_decides(project, monkeypatch, prompts):
    result = steer(project, monkeypatch)
    assert (result["proposals"], result["rejected_proposals"]) == (3, 3)
    assert f"overdue: actions/test-plan \"test plan\" (Action Item, owner: Bob Lee): due {day(-6)}" in prompts()
    report = project.root / result["report"]
    meta, body = okf.split_frontmatter(report.read_text())
    assert [p["do"] for p in meta["proposals"]] == ["message", "set", "create"]
    assert meta["proposals"][1]["from"] == day(9)
    assert "no single team member 'Mallory'" in meta["brainforge"]["rejected_proposals"][0]["error"]
    assert "```proposals" not in body and "1. **Message to Bob Lee** about [test plan](../../knowledge/actions/" in body

    alice, bob = approval.member(project, "Alice Smith"), approval.member(project, "Bob")
    assert briefing.briefing(project, alice)["proposals"][0]["waiting"] == 3
    assert briefing.briefing(project, bob)["proposals"] == []
    with pytest.raises(BrainError, match="not a lead"):
        proposals.decide(project, "approve", [1], bob)

    item(project, "milestones/integration-testing", owner="Alice Smith", date=day(12),
         depends_on=["actions/test-plan"])  # someone moved it meanwhile
    stale = proposals.decide(project, "approve", [2], alice)
    assert stale["failed"] == 1 and "changed since the proposal: date is now" in stale["lines"][0]

    done = proposals.decide(project, "approve", [1], alice)
    [msg] = briefing.messages(project, bob)
    assert msg.sender == "Alice Smith" and msg.about == "actions/test-plan" and "New date?" in msg.text
    assert done["lines"] == ["1: approved (message Bob Lee)"] and commits(project.root)[0].startswith("steer: approve 1")

    proposals.decide(project, "approve", [3], alice, edit="Vendor API late")
    risk = okf.read(project.knowledge / "risks/vendor-api-late.md")
    assert risk.meta["impact"] == "high" and risk.meta["verified"][0]["by"] == "human:alice@example.com"
    assert "decided by Alice Smith (proposal 3 of steering" in risk.body and approval.state(risk) == "approved"
    assert "Vendor API late" in (project.knowledge / "risks/index.md").read_text()

    proposals.decide(project, "discard", [2], alice)
    assert proposals.decide(project, "approve", [2], alice)["lines"] == ["2: already discarded by Alice Smith"]
    assert proposals.waiting(project) == []
    assert "(approved by Alice Smith" in okf.split_frontmatter(report.read_text())[1]
    assert gitops.status(project.root) == []


def test_set_carries_out_and_validation(project, monkeypatch):
    steer(project, monkeypatch)
    alice = approval.member(project, "Alice Smith")
    proposals.decide(project, "approve", [2], alice)
    c = okf.read(project.knowledge / "milestones/integration-testing.md")
    assert c.meta["date"] == day(16) and f"date {day(9)} → {day(16)}, decided by Alice Smith" in c.body

    project.manifest.team[0].lead = False
    with pytest.raises(procedures.ProcedureError, match="set `lead: true`"):
        steer(project, monkeypatch)
    write(project.procedures / "steering.md", "---\nproposes: true\naudience: client\nschedule: {cron: \"0 8 * * 1\"}\n"
          "---\nx\n")
    issues = [i.message for i in procedures.load_all(project)[1]]
    assert any("set `lead: true`" in m for m in issues) and any("drop `audience`" in m for m in issues)
    assert proposals.parse("approve 1 3\n> approve 9\ndiscard 2\n`approve 4: Next Friday`") == [
        ("approve", [1, 3], None), ("discard", [2], None), ("approve", [4], "Next Friday")]


def test_proposal_issues_and_github_replies(project, tmp_path, monkeypatch, run_cli, fake_gh):  # noqa: F811
    result = steer(project, monkeypatch)
    share(project, tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/brain")
    assert proposals.sync_issues(project, "https://github.com/acme/brain") == [f"opened {result['report']}"]
    create = next(c for c in fake_gh() if c[:2] == ["issue", "create"])
    body = create[create.index("--body") + 1]
    assert body.startswith(f"<!-- brainforge-proposals: {result['report']} -->") and "(@alice-gh)" in body
    assert "](https://github.com/acme/brain/blob/HEAD/knowledge/actions/test-plan.md)" in body

    def reply(author, text):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        return run_cli("--brain", project.root, "ci", "decide", "--author", author, "--proposals", result["report"])

    code, out, _ = reply("alice-gh", "Thanks!\napprove 1\ndiscard 2 3")
    assert code == 0 and "- 1: approved (message Bob Lee)" in out and "  - to Bob Lee: The test plan" in out
    assert "All proposals decided." in out
    assert gitops.git(tmp_path / "remote.git", "log", "-1", "--format=%an %s").startswith("Alice Smith steer: discard")
    assert reply("bob-gh", "approve 1")[0] == 1  # not on the team by that login

    monkeypatch.setenv("FAKE_GH_ISSUES", json.dumps([{"number": 3, "body": body}]))
    assert proposals.sync_issues(project, None) == [f"closed {result['report']}"]

    workflow = schedule.write_github_steering(project)
    assert workflow and "brainforge ci decide --author" in workflow.read_text() and "__" not in workflow.read_text()
