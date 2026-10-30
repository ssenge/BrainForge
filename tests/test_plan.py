"""Project models: the phases and steps a project goes through, where it stands, and what steering proposes next."""

import json
from datetime import date, timedelta

import pytest
from conftest import write
from test_steering import TEAM, steer

from brainforge import approval, briefing, gitops, okf, plan, plugins, proposals, signals
from brainforge import brain as brain_mod
from brainforge.brain import BrainError

TODAY = date.today()
TYPES = {"actions": "Action Item", "milestones": "Milestone", "projects": "Project"}
PLAN_KINDS = {"phase", "next step", "step late", "out of order", "phase complete"}


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def item(b, cid, **meta):
    okf.write(okf.Concept(b.knowledge / f"{cid}.md", {"type": TYPES[cid.split("/")[0]], "title": cid.split("/")[1],
                                                      **meta}, "# Current state\n\nAs written.\n"))
    gitops.commit(b.root, f"item {cid}", ["knowledge"])


def found(b, today=TODAY):
    return {(s.kind, s.id): s for s in signals.compute(b, today) if s.kind in PLAN_KINDS}


@pytest.fixture
def planned(brain):
    """A client project in sales, following pm's demo model; qualification is done."""
    (brain.root / "brain.yaml").write_text((brain.root / "brain.yaml").read_text() + "model: pm/client-project\n"
                                           + TEAM)
    plugins.add(brain, "pm")
    gitops.commit(brain.root, "team, pm, model", ["brain.yaml", "plugins"])
    b = brain_mod.load(brain.root)
    item(b, "projects/website", owner="Alice Smith", phase="sales")
    item(b, "actions/qualify", owner="Bob Lee", due=day(-3), state="done", step="sales/qualification")
    return b


def test_where_the_project_stands_and_what_comes_next(planned, run_cli):
    b = planned
    pos = plan.position(b)
    assert (pos.phase.name, pos.since, pos.project.title) == ("sales", TODAY, "website")
    assert set(found(b)) == {("next step", "sales/proposal"), ("next step", "sales/contract")}
    nxt = found(b)["next step", "sales/proposal"]
    assert nxt.question == 'Sales has started. Who takes "Proposal sent", and by when?'
    assert [m.name for m in nxt.owners] == ["Alice Smith"]  # no item yet: the leads

    item(b, "milestones/proposal", owner="Bob Lee", date=day(-1), state="achieved", step="sales/proposal")
    item(b, "milestones/contract", owner="Bob Lee", date=day(5), state="planned", step="sales/contract")
    assert found(b) == {}
    code, out, _ = run_cli("--brain", b.root, "plan")
    assert code == 0 and f"website · pm/client-project v1 · phase sales since {TODAY}" in out
    assert f"sales     qualification ✓ · proposal ✓ · contract open (Bob Lee, due {day(5)})" in out
    assert "delivery  not started" in out

    item(b, "projects/website", owner="Alice Smith", phase="delivery")  # moved on before the contract was signed
    s = found(b)
    assert [m.name for m in s["out of order", "sales/contract"].owners] == ["Bob Lee"]
    assert {i for k, i in s if k == "next step"} == {"delivery/kickoff", "delivery/requirements",
                                                    "delivery/status-reporting", "delivery/acceptance"}
    assert s["next step", "delivery/kickoff"].detail == (f"phase delivery since {TODAY}: no item for delivery/kickoff "
                                                         f"yet, planned by {day(10)}")

    item(b, "milestones/contract", owner="Bob Lee", date=day(5), state="achieved", step="sales/contract")
    item(b, "milestones/kickoff", owner="Bob Lee", date=day(12), state="planned", step="delivery/kickoff")
    item(b, "actions/invoice", owner="Bob Lee", due=day(40), state="open", step="closure/invoice")
    s = found(b)
    assert ("out of order", "sales/contract") not in s and ("next step", "delivery/kickoff") not in s
    assert s["out of order", "closure/invoice"].question == "Has the project moved on to closure?"
    late = found(b, TODAY + timedelta(days=20))["step late", "delivery/kickoff"]
    assert late.detail == f"planned within 10 days of the phase start, by {day(10)}, and not done"

    for cid, step in (("kickoff", "kickoff"), ("requirements", "requirements"), ("acceptance", "acceptance")):
        item(b, f"milestones/{cid}", owner="Bob Lee", date=day(1), state="achieved", step=f"delivery/{step}")
    done = found(b)["phase complete", "projects/website"]  # status reporting is optional
    assert done.detail == "every required step of delivery is done; next: closure"
    assert done.question == "Move the project on to closure?"

    data = json.loads(run_cli("--brain", b.root, "--json", "plan")[1])
    assert data["phase"] == "delivery" and data["phases"][1]["complete"]
    assert data["phases"][1]["steps"][2] == {"id": "delivery/status-reporting", "title": "Regular status reporting set "
                                             "up", "optional": True, "state": "not started", "items": []}
    text = briefing.text(briefing.briefing(b, None))
    assert f"Phase delivery since {TODAY}. Open steps: Regular status reporting set up (not started)." in text


def test_no_phase_no_project(planned):
    b = planned
    item(b, "projects/website", owner="Alice Smith")
    [s] = found(b).values()
    assert (s.kind, s.id, s.question) == ("phase", "projects/website", "Which phase of pm/client-project is the "
                                          "project in?")
    (b.knowledge / "projects/website.md").unlink()
    assert [i.message for i in plan.check(b)] == ["the brain follows a project model but has no project item to "
                                                  "hold its `phase`"]
    assert found(b)["phase", "pm/client-project"].owners[0].name == "Alice Smith"


def test_models_resolve_and_are_checked(planned):
    b = planned
    assert plan.load(b).path == b.root / "plugins/pm/models/client-project.md"
    b.manifest.model = "client-project"
    assert plan.load(b).phases[0].name == "sales"
    write(b.root / "models/client-project.md", "---\nphases:\n  - name: build\n    steps: [{name: code}]\n---\nOurs.\n")
    assert plan.load(b).path == b.root / "models/client-project.md"  # the brain's own copy wins

    write(b.root / "models/client-project.md", "---\nphases:\n  - name: build\n    steps: [{name: a}, {name: a}]\n---\n")
    with pytest.raises(BrainError, match="names must be unique: build/a"):
        plan.load(b)
    write(b.root / "models/client-project.md", "---\nphases:\n  - name: build\n    steps: [{name: a, type: Epic}]\n---\n")
    with pytest.raises(BrainError, match="unknown type Epic"):
        plan.load(b)
    assert plan.check(b)[0].level == "error"
    b.manifest.model = "pm/nothing"
    with pytest.raises(BrainError, match="no models/nothing.md in the brain or plugin pm"):
        plan.load(b)

    b.manifest.model = "pm/client-project"
    (b.root / "models/client-project.md").unlink()
    item(b, "actions/party", owner="Bob Lee", due=day(3), state="open", step="delivery/party")
    assert [i.message for i in plan.check(b)] == ["step 'delivery/party' is not in model pm/client-project"]


def test_a_model_from_another_repository(brain, tmp_path, run_cli):
    """A company keeps its method in its own repository; a brain adds it and follows it."""
    repo = tmp_path / "acme-method"
    write(repo / "plugin.yaml", "name: acme\ndescription: The Acme project method.\n")
    write(repo / "models/fixed-price.md", "---\ndescription: Fixed price.\nphases:\n  - name: offer\n    steps:\n"
          "      - {name: estimate, title: Estimate signed off}\n  - name: build\n    steps: [{name: sprint-zero}]\n"
          "---\n\n## offer/estimate\n\nTwo people estimate independently.\n")
    gitops.git(tmp_path, "init", "-q", str(repo))
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "method")

    code, out, _ = run_cli("--brain", brain.root, "plugin", "add", f"file://{repo}")
    assert code == 0 and "models: acme/fixed-price" in out
    (brain.root / "brain.yaml").write_text((brain.root / "brain.yaml").read_text() + "model: acme/fixed-price\n")
    b = brain_mod.load(brain.root)
    assert plan.load(b).step_ids() == ["offer/estimate", "build/sprint-zero"]
    assert "Two people estimate independently." in plan.prompt(b)
    assert plan.position(b).project is None  # no plugin type holds a phase yet: add pm, or a type of your own


def test_steering_knows_the_model(planned, monkeypatch, prompts):
    b = planned
    props = [
        {"do": "set", "item": "projects/website", "field": "phase", "value": "delivery", "why": "Contract signed."},
        {"do": "create", "type": "Milestone", "title": "Kickoff", "why": "Delivery starts.",
         "fields": {"owner": "Bob Lee", "date": day(7), "state": "planned", "step": "delivery/kickoff"}},
        {"do": "set", "item": "projects/website", "field": "phase", "value": "launch", "why": "Not a phase."},
        {"do": "create", "type": "Action Item", "title": "Party", "fields": {"step": "delivery/party"}, "why": "No."},
    ]
    result = steer(b, monkeypatch, props)
    assert (result["proposals"], result["rejected_proposals"]) == (2, 2)
    meta = okf.split_frontmatter((b.root / result["report"]).read_text())[0]
    errors = [r["error"] for r in meta["brainforge"]["rejected_proposals"]]
    assert "phase must be one of sales, delivery, closure, not 'launch'" in errors[0]
    assert "step must be one of sales/qualification" in errors[1]
    sent = prompts()
    assert "# Project model" in sent and "- sales/proposal \"Proposal sent\", item type Milestone: no item yet" in sent
    assert "Agenda: goals and scope" in sent and 'next step: sales/proposal "Proposal sent"' in sent

    alice = approval.member(b, "Alice Smith")
    proposals.decide(b, "approve", [1, 2], alice)
    pos = plan.position(b)
    assert pos.phase.name == "delivery" and pos.since == TODAY
    assert [okf.concept_id(b.knowledge, c.path) for c in pos.of(pos.phase)[0].items] == ["milestones/kickoff"]


def test_agents_learn_the_model(planned):
    def layer(activity):
        return next(x.text for x in brain_mod.instruction_layers(planned, activity)
                    if x.label.startswith("Project model"))

    assert "- `delivery/kickoff`: Kickoff meeting" in layer("ingestion") and "set `step:" in layer("ingestion")
    assert f"phase sales since {TODAY}" in layer("query")
