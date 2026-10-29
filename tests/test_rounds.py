"""Rounds: asynchronous status meetings. Updates in a window, routed to their sender, closed by a report."""

import io
import json
import os
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import commits, write
from test_approval import share

from brainforge import agents, approval, briefing, gitops, ingest, mcp_server, okf, procedures, rounds, schedule
from brainforge import brain as brain_mod

BERLIN = ZoneInfo("Europe/Berlin")
TEAM = """
team:
  - name: Alice Smith
    email: alice@example.com
    github: alice-gh
  - name: Bob Lee
    aliases: [Bob]
    approves: [{type: "*"}]
"""
# The operational round runs Monday 08:00 to Tuesday 18:00, the management round Tuesday 08:00 to Wednesday 23:59.
OPS = """---
description: Operational status for the client.
schedule: {cron: "0 18 * * 2", timezone: Europe/Berlin}
round: {opens: "0 8 * * 1"}
---
Summarize the updates.
"""
MGMT = """---
schedule: {cron: "59 23 * * 3", timezone: Europe/Berlin}
round: {opens: "0 8 * * 2", from: [Bob]}
---
Summarize for management.
"""
MON, TUE, THU = (datetime(2026, 11, d, 10, tzinfo=BERLIN) for d in (2, 3, 5))


@pytest.fixture
def team(brain):
    (brain.root / "brain.yaml").write_text((brain.root / "brain.yaml").read_text() + TEAM)
    write(brain.procedures / "ops.md", OPS)
    write(brain.procedures / "mgmt.md", MGMT)
    gitops.commit(brain.root, "rounds", ["brain.yaml", "procedures"])
    gitops.git(brain.root, "config", "user.email", "alice@example.com")
    return brain_mod.load(brain.root)


def send(b, tmp_path, who, text, at, round_id=None):
    doc = write(tmp_path / f"update-{len(list(tmp_path.glob('update-*')))}.md", text)
    outcome = ingest.ingest(b, doc, agents.resolve(b), sender=who, sent=at, round_id=round_id)
    assert outcome.status == "ingested", outcome.message
    return outcome


def source(b, outcome):
    return okf.read(b.root / outcome.source).meta["brainforge"]


def test_rounds_overlap_and_updates_count_for_the_open_ones(team, tmp_path):
    assert [(r.id, r.open_at(MON)) for r in rounds.all_latest(team, MON)] == [("mgmt/2026-10-27", False),
                                                                           ("ops/2026-11-02", True)]
    assert [r.id for r in rounds.all_latest(team, TUE) if r.open_at(TUE)] == ["mgmt/2026-11-03", "ops/2026-11-02"]
    assert not any(r.open_at(THU) for r in rounds.all_latest(team, THU))
    opening = datetime(2026, 11, 2, 8, tzinfo=BERLIN)  # a round opening right now is open
    assert [r.id for r in rounds.all_latest(team, opening) if r.open_at(opening)] == ["ops/2026-11-02"]

    alice = send(team, tmp_path, "Alice Smith", "Milestone: Go-live = 2026-12-01\n", MON)
    assert source(team, alice)["from"] == "Alice Smith" and source(team, alice)["rounds"] == ["ops/2026-11-02"]
    assert "reports/rounds/ops/2026-11-02.md" in gitops.git(team.root, "show", "--name-only", "--format=", "HEAD")
    bob = send(team, tmp_path, "Bob Lee", "Topic: Budget\n", TUE)
    assert source(team, bob)["rounds"] == ["mgmt/2026-11-03", "ops/2026-11-02"]  # in both, both open
    reply = send(team, tmp_path, "Bob Lee", "Topic: Hiring\n", TUE, round_id="mgmt/2026-11-03")
    assert source(team, reply)["rounds"] == ["mgmt/2026-11-03"]  # a reply on a round's issue counts there only
    minutes = ingest.ingest(team, write(tmp_path / "minutes.md", "Topic: Minutes\n"), agents.resolve(team),
                            sender="Alice Smith", sent=MON)
    assert source(team, minutes)["rounds"] == ["ops/2026-11-02"]  # counts for the round, routed as usual
    late = send(team, tmp_path, "Alice Smith", "Topic: Late\n", THU)
    assert "rounds" not in source(team, late)

    ops = rounds.latest(team, procedures.get(team, "ops"), TUE)
    assert [u.sender for u in rounds.updates(team, ops)] == ["Alice Smith", "Alice Smith", "Bob Lee"]
    page = (team.root / ops.page).read_text()
    assert ("| Alice Smith | Mon 02 Nov 10:00 | [Test document](../../../knowledge/sources/update-0-" in page
            and "): A document used in tests. |" in page)

    # The sender approves what the brain took from their own round update; other documents follow the usual rules.
    items = {i.rel: [m.name for m in i.approvers] for i in approval.pending(team)}
    assert items["knowledge/milestones/go-live.md"] == ["Alice Smith"]
    assert items["knowledge/topics/minutes.md"] == items["knowledge/topics/late.md"] == ["Bob Lee"]


def test_the_report_closes_the_round_and_the_page_links_it(team, tmp_path, prompts):
    send(team, tmp_path, "Alice Smith", "Milestone: Integration testing = 2026-11-17\n", MON)
    results = schedule.tick(team, now=datetime(2026, 11, 3, 18, 5, tzinfo=BERLIN))
    [report] = [r for r in results if r["status"] == "ran"]
    assert report["procedure"] == "ops"
    meta = okf.split_frontmatter((team.root / report["report"]).read_text())[0]
    assert meta["round"]["id"] == "ops/2026-11-02" and meta["round"]["missing"] == ["Bob Lee"]
    assert meta["round"]["updates"][0].startswith("knowledge/sources/update-0-")
    assert "No update from Bob Lee. That is fine" in prompts() and "# Round" in prompts()

    page = (team.root / "reports/rounds/ops/2026-11-02.md").read_text()
    assert "Report: [reports/ops/2026-11-03.md](../../ops/2026-11-03.md)" in page and "| Bob Lee | | no update |" in page
    assert "reports/rounds/ops/2026-11-02.md" in gitops.git(team.root, "show", "--name-only", "--format=", "HEAD~1")
    assert commits(team.root)[0] == "rounds: 1 page(s)" and gitops.status(team.root) == []  # mgmt's opened
    assert [r for r in schedule.tick(team, now=datetime(2026, 11, 3, 18, 20, tzinfo=BERLIN))
            if r["status"] != "not-due"] == []


def round_now(b, keep=False):
    """A round open right now (it opened a minute ago and closes on New Year's Day); without `keep`, the only one."""
    if not keep:
        for name in ("ops", "mgmt"):
            (b.procedures / f"{name}.md").unlink()
    write(b.procedures / "now.md", '---\nschedule: {cron: "0 0 1 1 *"}\nround: {opens: "* * * * *"}\n---\nx\n')
    gitops.commit(b.root, "a round open now", ["procedures"])
    return next(r.id for r in rounds.all_latest(b) if r.proc.name == "now")


def test_briefing_cli_and_validation(team, run_cli):
    round_now(team)
    alice = approval.member(team, "alice-gh")
    [waiting] = briefing.briefing(team, alice)["rounds"]
    assert waiting["id"].startswith("now/") and waiting["missing"] == ["Alice Smith", "Bob Lee"]
    out = run_cli("--brain", team.root, "briefing")[1]
    assert "Rounds waiting for your update (1):\n  now/" in out and "send it with `brainforge note" in out
    assert run_cli("--brain", team.root, "note", "Topic: Status\nAll on track.")[0] == 0
    assert briefing.briefing(team, alice)["rounds"] == []
    out = run_cli("--brain", team.root, "rounds")[1]
    assert "now/" in out and "  Alice Smith  " in out and "not yet: Bob Lee" in out

    write(team.procedures / "bad.md", '---\nround: {opens: "0 8 * * 1", from: [Nobody]}\n---\nx\n')
    issues = [i.message for i in procedures.load_all(team)[1]]
    assert "a round needs a schedule: its report closes the round" in issues
    assert any(m.startswith("round: no single team member 'Nobody'") for m in issues)


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A `gh` that logs its arguments and lists the issues in FAKE_GH_ISSUES."""
    bin_dir = tmp_path / "bin"
    write(bin_dir / "gh", f"#!{sys.executable}\nimport json, os, sys\n"
          "open(os.environ['FAKE_GH_LOG'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
          "if sys.argv[1:3] == ['issue', 'list']:\n    print(os.environ.get('FAKE_GH_ISSUES', '[]'))\n").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))

    def calls():
        log = tmp_path / "gh.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return calls


def test_round_issues_open_and_close(team, tmp_path, fake_gh, monkeypatch):
    assert rounds.sync_issues(team, "https://github.com/acme/brain", TUE) == ["opened mgmt/2026-11-03",
                                                                               "opened ops/2026-11-02"]
    bodies = [c[c.index("--body") + 1] for c in fake_gh() if c[:2] == ["issue", "create"]]
    create, body = [c for c in fake_gh() if c[:2] == ["issue", "create"]][1], bodies[1]
    assert create[create.index("--title") + 1] == "Round ops/2026-11-02: updates until Tue 03 Nov 18:00"
    assert body.startswith("<!-- brainforge-round: ops/2026-11-02 -->") and "\n@alice-gh\n- no open items" in body
    assert "Bob Lee (no `github` login in brain.yaml)" in body

    monkeypatch.setenv("FAKE_GH_ISSUES", json.dumps([{"number": 6, "body": bodies[0]}, {"number": 7, "body": body}]))
    wed = datetime(2026, 11, 4, 10, tzinfo=BERLIN)
    assert rounds.sync_issues(team, None, wed) == []  # closed, but the report is still to come
    schedule.tick(team, now=datetime(2026, 11, 3, 18, 5, tzinfo=BERLIN))
    assert rounds.sync_issues(team, "https://github.com/acme/brain", wed) == ["closed ops/2026-11-02"]
    assert fake_gh()[-1] == ["issue", "close", "7", "--comment", "The round has closed. Report: [reports/ops/2026-11-03"
                             ".md](https://github.com/acme/brain/blob/HEAD/reports/ops/2026-11-03.md)"]

    assert run_install(team) == [".github/workflows/brainforge-procedures.yml", ".github/workflows/brainforge-rounds.yml"]
    text = (team.root / ".github/workflows/brainforge-procedures.yml").read_text()
    assert 'cron: "0 8 * * 1"\n      timezone: "Europe/Berlin"  # ops: round opens' in text
    rounds_wf = (team.root / ".github/workflows/brainforge-rounds.yml").read_text()
    assert "brainforge ci rounds" in rounds_wf and "brainforge ci reply" in rounds_wf and "__" not in rounds_wf


def run_install(b):
    return [b.rel(p) for p in (schedule.write_github_procedures(b), schedule.write_github_rounds(b)) if p]


def test_a_reply_on_the_round_issue_is_the_senders_update(team, tmp_path, run_cli, monkeypatch):
    rid = round_now(team, keep=True)
    share(team, tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/brain")

    def reply(author, text, round_id=rid):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        return run_cli("--brain", team.root, "ci", "reply", "--author", author, "--round", round_id)

    code, out, _ = reply("alice-gh", "Milestone: Go-live = 2026-12-01\nThe vendor signed.")
    assert code == 0 and out.startswith("Thanks, @alice-gh. Your update is in.")
    command = next(line for line in out.splitlines() if "approve milestones/go-live@" in line)
    assert gitops.git(tmp_path / "remote.git", "log", "-1", "--format=%s").strip().startswith("ingest: update-")
    code, out, _ = reply("alice-gh", command.split("`")[1])
    assert code == 0 and "- approved milestones/go-live (" in out
    assert reply("mallory", "All fine.")[0] == 1


def test_a_note_through_mcp_lands_in_the_inbox_as_the_users(team, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "alice@example.com")  # the commit is the user's, like in their clone
    brains = mcp_server.Brains([team], pull=False)
    sent = mcp_server.add_note(brains, "Topic: Vendor\nThe vendor signed.")
    assert sent["path"].startswith("inbox/note-") and commits(team.root)[0] == "inbox: note by Alice Smith"
    with pytest.raises(ValueError, match="credential"):
        mcp_server.add_note(brains, "token " + "ghp_" + "A" * 36)
    [outcome], _ = ingest.process_inbox(team, agents.resolve(team))
    meta = okf.read(team.root / outcome.source).meta["brainforge"]
    assert meta["from"] == "Alice Smith" and datetime.fromisoformat(meta["sent"]) <= datetime.now(UTC)
