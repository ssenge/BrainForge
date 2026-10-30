"""Proposals: a procedure suggests interventions, a lead approves them, BrainForge carries them out.

- A procedure with `proposes: true` ends its report with a ```proposals block (YAML). Each proposal is one of a
  closed set: `message` a team member, `set` a field of an item, `create` an item of a plugin type. Code checks
  each against the brain; one that doesn't pass is left out, with the reason in the report's frontmatter.
- Nothing happens until a lead (`lead: true` in the brain.yaml team) approves; any lead can, so no single person
  holds things up. Approving carries the proposal out as a commit by that lead; discarding drops it. Nobody
  outside the team is ever messaged.
- Only the latest report of a procedure can be decided: an older one saw an older brain. A `set` also stops
  when the field changed since it was proposed.
- The state is the report's frontmatter (`proposals`, each with its `state`); git keeps the history.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from brainforge import approval, briefing, gitops, ingest, okf, plan, plugins, procedures, rounds
from brainforge.brain import Brain, BrainError, TeamMember, slugify

MAX = 7
BLOCK_RE = re.compile(r"^```[ \t]*(?:yaml[ \t]+)?proposals[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
START, END = "<!-- brainforge-proposals -->", "<!-- /brainforge-proposals -->"
LABEL = "brainforge-steering"
MARKER_RE = re.compile(r"<!-- brainforge-proposals: (\S+) -->")
LINE_RE = re.compile(r"^[ \t]*`?[ \t]*(approve|discard)[ \t]+(\d+(?:[ \t,]+\d+)*)[ \t]*(?::[ \t]*(.+?))?`?[ \t]*$",
                     re.I | re.M)


def leads(brain: Brain) -> list[TeamMember]:
    return [m for m in brain.manifest.team if m.lead]


def prompt(brain: Brain) -> str:
    """What the agent may propose, and how to write it."""
    def field(name: str, f: plugins.FieldDef) -> str:
        return name + (f" ({'|'.join(f.values)})" if f.values else f" ({f.format})" if f.format else "")

    known = plugins.types(brain).values()
    team = ", ".join(m.name + (" (lead)" if m.lead else "") for m in brain.manifest.team)
    types = "\n".join(f"  - {t.type} (`{t.group}/`): {', '.join(field(n, f) for n, f in t.fields.items())}"
                      for t in known)
    return f"""
# Proposals

End the report with your proposals for the leads: a fenced code block opened with ```proposals that holds a YAML
list, most important first, at most {MAX}. Each entry is one of:

    - do: message     # a question, a chase, or an escalation to a lead
      to: <team member>
      about: <concept id it concerns, optional>
      text: "<the message in plain words; it goes out in the name of the lead who approves it>"
      why: "<the facts behind it, in one sentence>"
    - do: set         # one field of an item, e.g. a new date or owner
      item: <concept id>
      field: <a field of its type>
      value: <the new value>
      why: "<...>"
    - do: create      # a new item, e.g. a risk, or a decision someone needs to take
      type: <a type below>
      title: "<title>"
      fields: {{<field>: <value>}}
      text: "<one or two sentences>"
      why: "<...>"

- The team: {team}. Messages go to team members only; anything for people outside the team is a draft in the
  report, not a proposal. The leads read these proposals: for something a lead should decide, propose the
  change itself (`set`, `create`) rather than a message to a lead.
- Put text in double quotes, as above: a colon in an unquoted value ("Agenda: …") breaks the YAML.
- Concept ids are paths under `knowledge/` without `.md`, e.g. `milestones/go-live`.
- Types and their fields:
{types}
- Nothing happens until a lead approves. Don't repeat proposals of the previous report that were approved or
  discarded; its `proposals` frontmatter says which.
"""


# --- checking ------------------------------------------------------------------------------------

def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _plain(value: Any) -> Any:
    """A frontmatter value as it compares: dates as text, ids as normalized lists."""
    return value if isinstance(value, list) or value is None else str(value)


def _concept(brain: Brain, ref: Any) -> tuple[str, okf.Concept]:
    [cid] = plugins.ids(ref) or [""]
    path = brain.knowledge / f"{cid}.md"
    if not cid or not path.is_file():
        raise BrainError(f"no such concept: {ref}")
    return cid, okf.read(path)


def _value(brain: Brain, name: str, f: plugins.FieldDef, value: Any) -> Any:
    if error := plugins.field_error(name, f, value) or plan.check_value(brain, name, value):
        raise BrainError(error)
    if f.values:
        return next(v for v in f.values if v.lower() == str(value).lower())
    if f.format == "ids":
        found = plugins.ids(value)
        for cid in found:
            _concept(brain, cid)
        return found
    return str(value)


def check(brain: Brain, p: Any) -> dict[str, Any]:
    """A proposal as BrainForge carries it out, or BrainError saying why it can't be."""
    if not isinstance(p, dict):
        raise BrainError("not a mapping")
    known, do, why = plugins.types(brain), p.get("do"), _text(p.get("why"))
    if not why:
        raise BrainError("no `why`")
    if do == "message":
        to, text = approval.member(brain, _text(p.get("to"))), _text(p.get("text"))
        if not text:
            raise BrainError("no `text`")
        if secrets := ingest.find_secrets(text):
            raise BrainError(f"the text looks like it contains a credential ({'; '.join(secrets)})")
        about = _concept(brain, p["about"])[0] if p.get("about") else None
        return {"do": "message", "to": to.name, **({"about": about} if about else {}), "text": text, "why": why}
    if do == "set":
        cid, c = _concept(brain, p.get("item"))
        t, name = known.get(str(c.meta.get("type"))), _text(p.get("field"))
        if t is None or name not in t.fields:
            raise BrainError(f"{cid} has no field '{name}'" + (f" (fields: {', '.join(t.fields)})" if t else ""))
        value = _value(brain, name, t.fields[name], p.get("value"))
        if _plain(c.meta.get(name)) == value:
            raise BrainError(f"{cid} already has {name}: {value}")
        return {"do": "set", "item": cid, "field": name, "from": _plain(c.meta.get(name)), "value": value, "why": why}
    if do == "create":
        t, title = known.get(_text(p.get("type"))), _text(p.get("title"))
        if t is None:
            raise BrainError(f"unknown type '{p.get('type')}' (types: {', '.join(known)})")
        fields = p.get("fields") or {}
        if not title or not isinstance(fields, dict):
            raise BrainError("a new item needs a `title`, and `fields` as a mapping")
        if unknown := [k for k in fields if k not in t.fields]:
            raise BrainError(f"{t.type} has no field {', '.join(unknown)}")
        cid = f"{t.group}/{slugify(title)}"
        if (brain.knowledge / f"{cid}.md").exists():
            raise BrainError(f"{cid} exists already")
        values = {k: _value(brain, k, t.fields[k], v) for k, v in fields.items() if v not in (None, "")}
        return {"do": "create", "item": cid, "type": t.type, "title": title, "fields": values,
                "text": _text(p.get("text")), "why": why}
    raise BrainError(f"unknown action '{do}': message, set or create")


def extract(brain: Brain, body: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Take the agent's proposals out of the report: (body without them, checked proposals, rejected ones)."""
    blocks = list(BLOCK_RE.finditer(body))
    if not blocks:
        return body, [], []
    last = blocks[-1]  # its place keeps the list, under the agent's own heading if it wrote one
    head = BLOCK_RE.sub("", body[:last.start()])
    if not re.search(r"^#+[^\n]*proposals[^\n]*\s*\Z", head, re.I | re.M):
        head = head.rstrip() + "\n\n## Proposals\n\n"
    body = head + f"{START}\n{END}" + BLOCK_RE.sub("", body[last.end():])
    rejected: list[dict[str, Any]] = []
    try:
        raw = okf.load_yaml(blocks[-1].group(1))
    except yaml.YAMLError as exc:
        raw, rejected = _entries(blocks[-1].group(1), exc)
    raw = raw.get("proposals") if isinstance(raw, dict) else raw
    if not isinstance(raw, list):
        return body, [], [{"error": "not a YAML list"}]
    valid: list[dict[str, Any]] = []
    rejected += [{"proposal": p, "error": f"more than {MAX}"} for p in raw[MAX:]]
    for p in raw[:MAX]:
        try:
            valid.append({"n": len(valid) + 1, **check(brain, p), "state": "proposed"})
        except BrainError as exc:
            rejected.append({"proposal": p, "error": str(exc)})
    return body, valid, rejected


def _entries(text: str, exc: yaml.YAMLError) -> tuple[list[Any], list[dict[str, Any]]]:
    """The list entry by entry, when the whole isn't valid YAML: an entry that isn't costs only itself."""
    first = re.search(r"(?m)^( *)- ", text)
    if not first:
        return [], [{"error": f"not YAML: {exc}"}]
    good: list[Any] = []
    bad: list[dict[str, Any]] = []
    for part in re.split(rf"(?m)^(?={first.group(1)}- )", text)[1:]:
        try:
            x = okf.load_yaml(textwrap.dedent(part))
            good += x if isinstance(x, list) else [x]
        except yaml.YAMLError as e:
            bad.append({"proposal": part.strip(), "error": f"not YAML: {e}"})
    return good, bad


# --- views ---------------------------------------------------------------------------------------

def _show(value: Any) -> str:
    return ", ".join(map(str, value)) if isinstance(value, list) else "(empty)" if value in (None, "") else str(value)


def lines(brain: Brain, props: list[dict[str, Any]], link: Callable[[str], str]) -> list[str]:
    """The proposals as a numbered markdown list; `link(concept id)` gives a link target."""
    def item(cid: str) -> str:
        path = brain.knowledge / f"{cid}.md"
        return f"[{okf.read(path).title if path.is_file() else cid}]({link(cid)})"

    out = []
    for p in props:
        if p["do"] == "message":
            what = (f"**Message to {p['to']}**" + (f" about {item(p['about'])}" if p.get("about") else "")
                    + f": \"{' '.join(p['text'].split())}\"")
        elif p["do"] == "set":
            what = f"**Set** `{p['field']}` of {item(p['item'])}: {_show(p['from'])} → {_show(p['value'])}"
        else:
            fields = ", ".join(f"{k}: {_show(v)}" for k, v in p["fields"].items())
            what = (f"**New {p['type']}** \"{p['title']}\"" + (f" ({fields})" if fields else "")
                    + (f": {p['text']}" if p.get("text") else ""))
        done = "" if p["state"] == "proposed" else f" ({p['state']} by {p['by']}, {p['at'][:10]})"
        out += [f"{p['n']}. {what}{done}  ", f"   Why: {p['why']}"]
    return out or ["No proposals."]


def block(brain: Brain, report: Path, props: list[dict[str, Any]]) -> str:
    def link(cid: str) -> str:
        return os.path.relpath(brain.knowledge / f"{cid}.md", report.parent)
    return "\n".join([START, *lines(brain, props, link), END])


def with_block(brain: Brain, report: Path, body: str, props: list[dict[str, Any]]) -> str:
    """The report body with its proposals list, added or brought up to date."""
    if START in body and END in body:
        head, _, rest = body.partition(START)
        return head + block(brain, report, props) + rest.partition(END)[2]
    return body.rstrip() + "\n\n## Proposals\n\n" + block(brain, report, props) + "\n"


# --- deciding ------------------------------------------------------------------------------------

def latest(brain: Brain, target: str | None = None) -> tuple[procedures.Procedure, Path]:
    """The report whose proposals can be decided: the latest of the procedure named, or of the only one that
    proposes. A report path is accepted only if it is still the latest."""
    procs = [p for p in procedures.load_all(brain)[0] if p.proposes]
    path = brain.root / target if target and target.endswith(".md") else None
    named = [p for p in procs if target in (None, p.name) or (path and path.parent == brain.root / p.directory)]
    if len(named) != 1:
        names = ", ".join(p.name for p in procs) or "none (set `proposes: true` on a procedure)"
        raise BrainError(f"name the procedure whose proposals to decide; procedures that propose: {names}")
    found = procedures.reports(brain, named[0])
    if not found:
        raise BrainError(f"{named[0].name} has no report yet")
    if path and path.resolve() != found[-1][1].resolve():
        raise BrainError(f"{target} was superseded by {brain.rel(found[-1][1])}; decide on that one")
    return named[0], found[-1][1]


def waiting(brain: Brain) -> list[dict[str, Any]]:
    """Per procedure that proposes: its latest report and the proposals still waiting for a lead."""
    out = []
    for proc in procedures.load_all(brain)[0]:
        if proc.proposes and (found := procedures.reports(brain, proc)):
            meta, _ = okf.split_frontmatter(found[-1][1].read_text(encoding="utf-8"))
            props = [p for p in (meta or {}).get("proposals") or [] if p.get("state") == "proposed"]
            if props:
                out.append({"procedure": proc.name, "report": brain.rel(found[-1][1]), "proposals": props})
    return out


def parse(text: str) -> list[tuple[str, list[int], str | None]]:
    """(verb, numbers, edit) per `approve 1 3` / `discard 2` / `approve 2: <new text>` line."""
    return [(verb.lower(), [int(n) for n in re.split(r"[ \t,]+", nums.strip())], edit or None)
            for verb, nums, edit in LINE_RE.findall(text)]


def _history(body: str, line: str) -> str:
    rows = body.rstrip("\n").split("\n")
    if "# History" in rows:
        at = rows.index("# History") + 1
        end = next((k for k in range(at, len(rows)) if rows[k].startswith("# ") or okf.FOOTNOTE_DEF_RE.match(rows[k])),
                   len(rows))
        while end > at and not rows[end - 1].strip():
            end -= 1
        rows[end:end] = ([""] if end == at else []) + [f"- {line}"]
    else:
        at = next((k for k, r in enumerate(rows) if okf.FOOTNOTE_DEF_RE.match(r)), len(rows))
        rows[at:at] = ["", "# History", "", f"- {line}", ""]
    return "\n".join(rows) + "\n"


def _carry_out(brain: Brain, p: dict[str, Any], who: TeamMember, where: str) -> list[str]:
    """Do what the proposal says, as `who`; returns the paths it changed."""
    if p["do"] == "set":  # first: a field that changed since the proposal is a stop, not "already set"
        path = brain.knowledge / f"{p['item']}.md"
        if path.is_file() and _plain(okf.read(path).meta.get(p["field"])) != p.get("from"):
            raise BrainError(f"{p['item']} changed since the proposal: {p['field']} is now "
                             f"{_show(okf.read(path).meta.get(p['field']))}")
    checked = check(brain, p)
    if p["do"] == "message":
        rel = briefing.send(brain, approval.member(brain, checked["to"]), checked["text"], who.name, checked.get("about"))
        return [rel]
    today = datetime.now(UTC).date().isoformat()
    path = brain.knowledge / f"{checked['item']}.md"
    stamp = {"by": f"human:{who.email or who.name}", "at": okf.now_iso()}
    if p["do"] == "set":
        c = okf.read(path)
        c.meta[checked["field"]] = checked["value"]
        c.body = _history(c.body, f"{today}: {checked['field']} {_show(p['from'])} → {_show(checked['value'])}, "
                                  f"decided by {who.name} ({where}).")
        # ponytail: `# Current state` may still state the old value until the next document updates it
        if approval.state(c) == "approved":  # a pending item stays pending: its approvers haven't seen it yet
            verified = c.meta.get("verified") or []
            c.meta["verified"] = [*([verified] if isinstance(verified, dict) else verified), stamp]
        okf.write(c)
        entry = f"**Steering**: {who.name} set `{checked['field']}` of [{c.title}]({checked['item']}.md) ({where})."
    else:
        levels = brain.manifest.audiences
        meta = {"type": checked["type"], "title": checked["title"], **checked["fields"],
                **({"audience": levels[-1]} if levels else {}), "verified": [stamp]}
        body = f"# Current state\n\n{checked['text'] or checked['title']}\n"
        c = okf.Concept(path, meta, _history(body, f"{today}: created, decided by {who.name} ({where})."))
        plugins.renew(brain, c)
        okf.write(c)
        entry = f"**Steering**: {who.name} created [{c.title}]({checked['item']}.md) ({where})."
    okf.append_log(brain.knowledge, entry)
    return [brain.rel(path), "knowledge/log.md", *(brain.rel(i) for i in okf.write_indexes(brain.knowledge))]


def decide(brain: Brain, verb: str, numbers: list[int], who: TeamMember, *, target: str | None = None,
           edit: str | None = None, author: str | None = None, push: bool = False) -> dict[str, Any]:
    """Approve (carry out) or discard proposals as a lead, commit as them, push. A push that loses a race on the
    same files pulls and decides again on top: proposals already decided are skipped."""
    if verb not in ("approve", "discard"):
        raise BrainError(f"unknown decision '{verb}'")
    if not who.lead:
        raise BrainError(f"{who.name} is not a lead; leads (lead: true in brain.yaml) decide on proposals: "
                         f"{', '.join(m.name for m in leads(brain)) or 'none yet'}")
    if edit is not None and (verb != "approve" or len(numbers) != 1):
        raise BrainError("a change goes with one approved proposal: `approve 2: <new text, value or title>`")
    for attempt in range(3):
        if push:
            gitops.pull(brain.root)
        start, baseline = gitops.head(brain.root), gitops.untracked(brain.root)
        try:
            result = _decide(brain, verb, numbers, who, target, edit)
            sha = (gitops.commit(brain.root, f"steer: {verb} {', '.join(map(str, numbers))} of {result['title']}\n\n"
                                 + "\n".join(f"- {line}" for line in result["lines"]), result["paths"], author)
                   if result["paths"] else None)
        except Exception:
            gitops.rollback(brain.root, baseline)
            raise
        try:
            if push and sha:
                gitops.publish(brain.root, start)
            return {**result, "commit": sha}
        except gitops.Stale:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def _decide(brain: Brain, verb: str, numbers: list[int], who: TeamMember, target: str | None,
            edit: str | None) -> dict[str, Any]:
    proc, report = latest(brain, target)
    meta, body = okf.split_frontmatter(report.read_text(encoding="utf-8"))
    meta = meta or {}
    props = meta.get("proposals") or []
    title = f"{proc.name} {report.stem}"
    out: list[str] = []
    paths: list[str] = []
    sent: list[dict[str, Any]] = []
    failed = decided = 0
    state = "approved" if verb == "approve" else "discarded"
    for n in numbers:
        p = next((x for x in props if x.get("n") == n), None)
        if p is None or p.get("state") != "proposed":
            out.append(f"{n}: " + (f"already {p['state']} by {p.get('by')}" if p else f"no proposal {n} in {title}"))
            failed += p is None
            continue
        try:
            todo = dict(p)
            if edit:  # the lead's own words replace the agent's
                todo[{"message": "text", "set": "value", "create": "title"}[p["do"]]] = edit
                todo = {**check(brain, todo), **({"from": p.get("from")} if p["do"] == "set" else {})}
            if verb == "approve":
                paths += _carry_out(brain, todo, who, f"proposal {n} of {title}")
        except BrainError as exc:
            failed += 1
            out.append(f"{n}: not carried out: {exc}")
            continue
        new = {**todo, "n": n, "state": state, "by": who.name, "at": okf.now_iso()}
        p.clear()
        p.update(new)
        decided += 1
        if verb == "approve" and p["do"] == "message":
            sent.append({"to": p["to"], "text": p["text"]})
        out.append(f"{n}: {state}" + (f" ({p['do']} {p.get('to') or p.get('item')})" if verb == "approve" else ""))
    if decided:
        meta["proposals"] = props
        report.write_text(okf.render(meta, with_block(brain, report, body, props)), encoding="utf-8")
        paths = list(dict.fromkeys([brain.rel(report), *paths]))
    return {"report": brain.rel(report), "title": title, "lines": out, "paths": paths, "sent": sent, "failed": failed}


# --- GitHub --------------------------------------------------------------------------------------

def issue_body(brain: Brain, report_rel: str, props: list[dict[str, Any]], repo_url: str | None) -> str:
    def link(rel: str) -> str:
        return f"{repo_url}/blob/HEAD/{rel}" if repo_url else rel

    who = ", ".join(f"@{m.github}" if m.github else m.name for m in leads(brain))
    return "\n".join([
        f"<!-- brainforge-proposals: {report_rel} -->",
        f"**Proposals for the leads** ({who}) from [{report_rel}]({link(report_rel)}). One of you decides.", "",
        "Reply with one line per decision: `approve 1 3`, `discard 2`, or `approve 2: <new text, date or title>` "
        "to change one before it is carried out. Nothing happens until a lead approves; a message goes out in the "
        "name of the lead who approved it.", "",
        *lines(brain, props, lambda cid: link(f"knowledge/{cid}.md"))])


def sync_issues(brain: Brain, repo_url: str | None = None) -> list[str]:
    """One open issue per report with proposals waiting; closed once all are decided or a newer report came."""
    if not any(p.proposes for p in procedures.load_all(brain)[0]):
        return []
    want = {w["report"]: w for w in waiting(brain)}
    listed = json.loads(rounds.gh(brain, "issue", "list", "--label", LABEL, "--state", "open", "--json", "number,body",
                            "--limit", "100"))
    found = {m.group(1): i["number"] for i in listed if (m := MARKER_RE.search(i.get("body") or ""))}
    done = []
    for rel, w in want.items():
        if rel not in found:
            rounds.gh(brain, "label", "create", LABEL, "--color", "D93F0B", "--force")
            rounds.gh(brain, "issue", "create", "--title", f"Proposals: {w['procedure']} {Path(rel).stem} "
                f"({len(w['proposals'])} waiting)", "--label", LABEL,
                "--body", issue_body(brain, rel, w["proposals"], repo_url))
            done.append(f"opened {rel}")
    for rel, number in found.items():
        if rel not in want:
            rounds.gh(brain, "issue", "close", str(number), "--comment", "Every proposal here is decided, or a newer report "
                "replaced them.")
            done.append(f"closed {rel}")
    return done
