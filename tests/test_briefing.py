"""Deadlines, messages, the briefing, overdue notices and the calendar. Uses the bundled pm plugin."""

import json
from datetime import date, timedelta

import pytest
from conftest import commits, write

from brainforge import brain as brain_mod
from brainforge import briefing, gitops, mcp_server, plugins
from brainforge.brain import BrainError

TEAM = """
team:
  - name: Alice Smith
    email: alice@example.com
    github: alice-gh
  - name: Bob Lee
    aliases: [Bob]
    approves: [{type: "*"}]
"""
TODAY = date.today()
LATE, SOON, LATER = TODAY - timedelta(days=8), TODAY + timedelta(days=1), TODAY + timedelta(days=60)


def item(b, rel, type_, title, extra):
    write(b.knowledge / f"{rel}.md", f"---\ntype: {type_}\ntitle: {title}\n{extra}\n---\nx\n")


@pytest.fixture
def pm_team(brain):
    plugins.add(brain, "pm")
    (brain.root / "brain.yaml").write_text((brain.root / "brain.yaml").read_text() + TEAM)
    item(brain, "actions/send-contract", "Action Item", "Send contract", f"owner: Alice Smith\ndue: {LATE}\nstate: open")
    item(brain, "actions/old", "Action Item", "Old", f"owner: Alice Smith\ndue: {LATE}\nstate: done")
    item(brain, "actions/dropped", "Action Item", "Dropped", f"due: {LATE}\napproval: discarded")
    item(brain, "milestones/go-live", "Milestone", "Go-live", f"date: {SOON}\nstate: planned")
    item(brain, "dependencies/vendor-api", "Dependency", "Vendor API", f"due: {LATER}")
    gitops.commit(brain.root, "setup", ["brain.yaml", "plugins", "knowledge"])
    gitops.git(brain.root, "config", "user.email", "alice@example.com")
    return brain_mod.load(brain.root)


def ids(entries):
    return [e["id"] for e in entries]


def test_deadlines_come_from_plugin_types(pm_team):
    alice = briefing.briefing(pm_team, briefing.member(pm_team, "@alice-gh"))
    assert ids(alice["overdue"]) == ["actions/send-contract"] and alice["due_soon"] == []
    everyone = briefing.briefing(pm_team, None)
    assert ids(everyone["due_soon"]) == ["milestones/go-live"] and everyone["due_soon"][0]["approvers"] == ["Bob Lee"]
    assert (f"Overdue (1):\n  {LATE}  actions/send-contract  [Action Item] Send contract (owner: Alice Smith)"
            in briefing.text(everyone))

    types_text = plugins._types_text(plugins.installed(pm_team)[0])
    assert "`due` is the deadline. When a document reports the item finished, set `state: done` or " in types_text
    write(pm_team.root / "plugins/pm/plugin.yaml", "name: pm\ntypes:\n  - {type: X, group: xs, deadline: due}\n")
    with pytest.raises(BrainError, match="deadline 'due' must name a field with format: date"):
        plugins.types(pm_team)


def test_messages_are_sent_shown_and_dismissed(pm_team, run_cli):
    root = pm_team.root
    code, out, _ = run_cli("--brain", root, "message", "send", "Bob", "Please check the vendor risk.",
                           "--about", "risks/vendor")
    assert code == 0 and "everyone with access to this brain repository can read it" in out
    assert commits(root)[0] == "message: to Bob Lee"
    assert run_cli("--brain", root, "message", "send", "Bob", "key " + "ghp_" + "A" * 36)[0] == 2  # shared repo
    assert run_cli("--brain", root, "message", "send", "Nobody", "Hi")[0] == 2

    gitops.git(root, "config", "user.email", "bob@example.com")
    gitops.git(root, "config", "user.name", "Bob Lee")
    out = run_cli("--brain", root, "briefing")[1]
    assert "Messages (1):" in out and "from Alice Smith: Please check the vendor risk." in out
    assert f"Due within 3 days (1):\n  {SOON}  milestones/go-live  [Milestone] Go-live" in out
    [message] = json.loads(run_cli("--brain", root, "--json", "briefing")[1])["messages"]
    assert message["about"] == "risks/vendor"
    assert run_cli("--brain", root, "message", "dismiss", message["id"])[1] == "Dismissed 1 message(s).\n"
    assert "Messages" not in run_cli("--brain", root, "briefing")[1] and gitops.status(root) == []


def test_overdue_items_are_notified_once_per_item_and_date(pm_team, run_cli, monkeypatch):
    root = pm_team.root
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/brain")
    code, out, _ = run_cli("--brain", root, "ci", "remind", "--notify")
    assert code == 0 and out == (
        "**Overdue, and no document reports it finished:**\n\n@alice-gh\n"
        "- [Send contract](https://github.com/acme/brain/blob/HEAD/knowledge/actions/send-contract.md) "
        f"(Action Item, due {LATE})\n")
    assert commits(root)[0] == "notify: 1 overdue item(s)"
    [notice] = briefing.messages(pm_team, briefing.member(pm_team, "Alice Smith"))
    assert notice.sender == "BrainForge" and notice.about == "actions/send-contract" and "brainforge note" in notice.text

    assert run_cli("--brain", root, "ci", "remind", "--notify")[1] == ""
    assert run_cli("--brain", root, "message", "dismiss", "--all")[0] == 0
    assert run_cli("--brain", root, "ci", "remind", "--notify")[1] == ""  # dismissed, but git remembers

    item(pm_team, "actions/send-contract", "Action Item", "Send contract",
         f"owner: Alice Smith\ndue: {LATE + timedelta(days=1)}\nstate: open")
    gitops.commit(root, "moved", ["knowledge"])
    assert "@alice-gh" in run_cli("--brain", root, "ci", "remind", "--notify")[1]  # a new date is a new notice


def test_claude_code_starts_with_the_briefing(pm_team, run_cli, tmp_path):
    out = run_cli("--brain", pm_team.root, "briefing", "--hook")[1]
    assert out.startswith("BrainForge briefing for Alice Smith (brain test). Tell the user") and "Overdue (1)" in out
    item(pm_team, "actions/send-contract", "Action Item", "Send contract", f"owner: Alice Smith\ndue: {LATE}\nstate: done")
    gitops.commit(pm_team.root, "done", ["knowledge"])
    assert run_cli("--brain", pm_team.root, "briefing", "--hook")[1] == ""  # nothing to tell: silent

    settings = pm_team.root / ".claude/settings.json"
    write(settings, json.dumps({"permissions": {"allow": ["Read"]}}))
    for _ in range(2):
        assert run_cli("--brain", pm_team.root, "skills", "install", "claude")[0] == 0
    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": ["Read"]} and len(data["hooks"]["SessionStart"]) == 1
    fresh = json.loads((brain_mod.init(tmp_path / "fresh").root / ".claude/settings.json").read_text())
    assert fresh["hooks"]["SessionStart"][0]["hooks"][0]["command"].startswith("brainforge briefing --hook")


def test_calendar_and_mcp(pm_team, run_cli, tmp_path):
    assert run_cli("--brain", pm_team.root, "calendar", "--all", "-o", tmp_path / "all.ics")[0] == 0
    ics = (tmp_path / "all.ics").read_bytes().decode()
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.count("BEGIN:VEVENT") == 3
    assert ("UID:actions/send-contract@test\r\nDTSTAMP:" in ics and f"DTSTART;VALUE=DATE:{LATE:%Y%m%d}\r\n" in ics
            and "SUMMARY:Action Item: Send contract\r\n" in ics)
    assert run_cli("--brain", pm_team.root, "calendar")[1].count("BEGIN:VEVENT") == 1  # mine

    brains = mcp_server.Brains([pm_team], pull=False)
    assert ids(mcp_server.briefings(brains)[0]["overdue"]) == ["actions/send-contract"]
    sent = mcp_server.send_message(brains, "Alice Smith", "Reminder to myself", about="actions/send-contract")
    assert sent["path"].startswith("messages/alice-smith/") and "can read it" in sent["note"]
    [message] = mcp_server.briefings(brains)[0]["messages"]
    assert mcp_server.dismiss_messages(brains, [message["id"]])["dismissed"] == [sent["path"]]
