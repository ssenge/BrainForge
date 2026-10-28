"""Slices 8, 9, 10: procedures, reports, output formats, scheduling."""

import shutil
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import commits, write

from brainforge import agents, gitops, ingest, okf, procedures, schedule

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def stocked(brain, tmp_path):
    ingest.ingest(brain, write(tmp_path / "plan.md", "Milestone: Integration testing = 2026-11-10\n"),
                  agents.resolve(brain))
    return brain


def run(brain, name="weekly-report", **kw):
    return procedures.run(brain, procedures.get(brain, name), agents.resolve(brain), **kw)


def test_report_is_written_with_metadata_and_committed(stocked):
    result = run(stocked, now=datetime(2026, 10, 30, 9, tzinfo=UTC))
    report = stocked.root / result["report"]
    assert result["report"].startswith("reports/weekly/2026-10-30")
    meta, body = okf.split_frontmatter(report.read_text())
    assert meta["type"] == "Report" and meta["procedure"] == {"name": "weekly-report", "version": 1}
    assert meta["brain"] == "test" and meta["brainforge"]["commit"] == gitops.git(stocked.root, "rev-parse", "HEAD~1").strip()
    assert meta["sources"] == [{"resource": "../../knowledge/milestones/integration-testing.md"}]
    assert "Previous: none" in body
    assert commits(stocked.root)[0] == f"report: weekly-report {result['report'][15:25]}"
    assert gitops.status(stocked.root) == []


def test_second_report_sees_previous_and_only_new_changes(stocked, tmp_path, prompts):
    first = run(stocked, now=datetime(2026, 10, 30, 9, tzinfo=UTC))
    with pytest.raises(procedures.ProcedureError, match="already exists"):
        run(stocked, now=datetime(2026, 10, 30, 10, tzinfo=UTC))
    ingest.ingest(stocked, write(tmp_path / "m.md", "Milestone: Integration testing = 2026-11-17\n"),
                  agents.resolve(stocked))
    second = run(stocked, now=datetime(2026, 11, 6, 9, tzinfo=UTC))
    body = (stocked.root / second["report"]).read_text()
    assert f"Previous: `{first['report']}`" in body
    assert "knowledge/milestones/integration-testing.md" in body.split("## Changed")[1].split("## Knowledge")[0]
    assert second["previous"] == first["report"]


def test_report_agent_touching_other_files_is_rolled_back(stocked, monkeypatch):
    before = gitops.head(stocked.root), gitops.status(stocked.root)
    monkeypatch.setenv("FAKE_AGENT", "stray")
    with pytest.raises(procedures.ProcedureError, match="other than the report"):
        run(stocked)
    assert (gitops.head(stocked.root), gitops.status(stocked.root)) == before


def test_broken_citations_are_recorded(stocked, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT", "badcite")
    result = run(stocked)
    assert result["broken_citations"] == ["../../knowledge/topics/ghost.md"]


def test_procedure_validation(brain):
    write(brain.procedures / "bad.md", "---\nname: bad\nschedule:\n  cron: '99 * * * *'\n  timezone: Mars/Base\n"
                                       "knowledge:\n  descendants:\n    include: true\noutput:\n  format: odt\n---\nx\n")
    _, issues = procedures.load_all(brain)
    message = issues[0].message
    assert "cron" in message and "Mars/Base" in message and "Phase B" in message and "format" in message


@pytest.mark.skipif(not shutil.which("pandoc"), reason="pandoc not installed")
def test_docx_output_uses_pandoc(stocked):
    path = stocked.procedures / "weekly-report.md"
    path.write_text(path.read_text().replace("format: markdown", "format: docx"))
    gitops.commit(stocked.root, "docx", ["procedures"])
    result = run(stocked)
    assert [o.rsplit(".", 1)[1] for o in result["outputs"]] == ["md", "docx"]
    assert (stocked.root / result["outputs"][1]).stat().st_size > 1000


# --- scheduling ----------------------------------------------------------------------------------

def weekly(brain):
    path = brain.procedures / "weekly-report.md"
    text = path.read_text().replace("# schedule:\n#   cron: \"0 8 * * 3\"\n#   timezone: Europe/Berlin",
                                    "schedule:\n  cron: \"0 8 * * 3\"\n  timezone: Europe/Berlin")
    path.write_text(text)
    gitops.commit(brain.root, "schedule", ["procedures"])
    return procedures.get(brain, "weekly-report")


def test_due_logic_handles_dst_and_delays(brain):
    proc = weekly(brain)
    summer_slot = datetime(2026, 7, 1, 8, tzinfo=BERLIN)   # 06:00 UTC
    winter_slot = datetime(2026, 12, 2, 8, tzinfo=BERLIN)  # 07:00 UTC
    assert schedule.last_slot(proc, datetime(2026, 7, 1, 6, 5, tzinfo=UTC)) == summer_slot
    assert schedule.last_slot(proc, datetime(2026, 12, 2, 7, 5, tzinfo=UTC)) == winter_slot
    assert not schedule.is_due(proc, None, datetime(2026, 12, 2, 6, 59, tzinfo=UTC))  # 07:59 Berlin: not yet
    assert schedule.is_due(proc, None, datetime(2026, 12, 2, 9, 40, tzinfo=UTC))  # delayed trigger still runs
    assert not schedule.is_due(proc, datetime(2026, 12, 2, 7, 10, tzinfo=UTC), datetime(2026, 12, 2, 9, tzinfo=UTC))
    assert not schedule.is_due(proc, None, datetime(2026, 12, 7, 9, tzinfo=UTC))  # never run, slot days ago


def test_tick_runs_once_per_slot(stocked):
    weekly(stocked)
    slot = datetime(2026, 12, 2, 7, 3, tzinfo=UTC)
    assert [r["status"] for r in schedule.tick(stocked, now=slot)] == ["ran"]
    assert [r["status"] for r in schedule.tick(stocked, now=slot.replace(minute=40))] == ["not-due"]


def test_github_workflows_are_generated_from_frontmatter(brain):
    weekly(brain)
    text = schedule.write_github_procedures(brain).read_text()
    assert 'cron: "0 8 * * 3"\n      timezone: "Europe/Berlin"  # weekly-report' in text
    assert "brainforge schedule tick --push" in text and "npm install -g @anthropic-ai/claude-code" in text
    ingest_wf = schedule.write_github_ingest(brain, package="brainforge @ git+https://example.com/bf.git").read_text()
    assert 'paths: ["inbox/**"]' in ingest_wf and "brainforge @ git+https://example.com/bf.git" in ingest_wf
    assert "BrainForge-Run:" in ingest_wf and "__" not in ingest_wf


def test_cron_line_calls_tick(brain):
    line = schedule.cron_line(brain)
    assert line.startswith("*/15 * * * * cd ") and "schedule tick" in line and line.endswith(f"# brainforge:{brain.root}")
