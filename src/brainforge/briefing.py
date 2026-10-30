"""What needs a person: messages, open rounds, deadlines (overdue, due soon), approvals and, for leads, proposals,
as one briefing.

- Messages are files in the brain repository, `messages/<member>/<id>.md`, so teammates, CI and BrainForge can
  leave them and every clone sees them. Everyone with access to the repository can read them.
- Deadlines come from plugin types with a `deadline` field. An unfinished item past its date is overdue.
- `notify` leaves one message per overdue item and date with each of its approvers, once: the message's path
  is the marker, and git history remembers it after it was dismissed.
- `calendar` renders open deadlines as an iCalendar file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from brainforge import approval, gitops, ingest, okf, plugins, proposals, rounds
from brainforge.brain import Brain, BrainError, TeamMember, slugify

DUE_SOON_DAYS = 3  # ponytail: fixed window; a brain.yaml setting if teams want another one
MESSAGES = "messages"
BRAINFORGE = "BrainForge"


@dataclass
class Message:
    id: str
    rel: str
    sender: str
    at: str
    about: str | None
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "path": self.rel, "from": self.sender, "at": self.at, "about": self.about,
                "text": self.text}


@dataclass
class Due:
    concept: okf.Concept
    id: str
    when: date
    owners: list[TeamMember]

    def as_dict(self) -> dict[str, Any]:
        c = self.concept
        return {"id": self.id, "type": c.meta.get("type"), "title": c.title, "due": self.when.isoformat(),
                "url": approval.url(c.path), "owner": c.meta.get("owner"), "approvers": [m.name for m in self.owners],
                "approval": approval.state(c)}


# --- messages ------------------------------------------------------------------------------------

def folder(m: TeamMember) -> str:
    return f"{MESSAGES}/{slugify(m.name)}"


def send(brain: Brain, to: TeamMember, text: str, sender: str, about: str | None = None,
         stem: str | None = None) -> str:
    """Write a message file and return its path. Refuses what looks like a credential: the repo is shared."""
    text = text.strip()
    if not text:
        raise BrainError("the message is empty")
    if secrets := ingest.find_secrets(text):
        raise BrainError(f"the message looks like it contains a credential ({'; '.join(secrets)}); everyone with "
                         "access to the brain repository could read it")
    now = datetime.now(UTC)
    stem = stem or f"{now:%Y%m%d-%H%M%S}-{slugify(' '.join(text.split()[:6]))[:40]}"
    rel = f"{folder(to)}/{stem}.md"
    meta = {"from": sender, "at": okf.now_iso(now), **({"about": about} if about else {})}
    path = brain.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.render(meta, text + "\n"), encoding="utf-8")
    return rel


def messages(brain: Brain, m: TeamMember) -> list[Message]:
    out = []
    for path in sorted((brain.root / folder(m)).glob("*.md")):
        meta, body = okf.split_frontmatter(path.read_text(encoding="utf-8"))
        meta = meta or {}
        out.append(Message(path.stem, brain.rel(path), str(meta.get("from", "")), str(meta.get("at", "")),
                           meta.get("about"), body.strip()))
    return sorted(out, key=lambda x: x.at, reverse=True)


def dismiss(brain: Brain, m: TeamMember, ids: list[str] | None) -> list[str]:
    """Delete these messages (all with None) and return their paths; git keeps them in its history."""
    found = {x.id: x.rel for x in messages(brain, m)}
    if ids is not None and (unknown := [i for i in ids if i not in found]):
        raise BrainError(f"no message {', '.join(unknown)} for {m.name}")
    rels = list(found.values()) if ids is None else [found[i] for i in ids]
    for rel in rels:
        (brain.root / rel).unlink()
    return rels


# --- deadlines -----------------------------------------------------------------------------------

def deadlines(brain: Brain) -> list[Due]:
    """Unfinished items with a deadline, earliest first. Discarded and retired items don't count."""
    known = plugins.types(brain)
    if not any(t.deadline for t in known.values()):
        return []
    out = []
    for c in okf.read_all(brain.knowledge):
        if approval.state(c) == "discarded" or str(c.meta.get("status", "")).lower() == "deprecated":
            continue
        if when := plugins.deadline(brain, c, known):
            out.append(Due(c, okf.concept_id(brain.knowledge, c.path), when, approval.approvers(brain, c)))
    return sorted(out, key=lambda d: (d.when, d.id))


def notify(brain: Brain, today: date | None = None) -> list[tuple[TeamMember, Due, str]]:
    """Leave a message with each approver of an overdue item, once per item and date. Returns what was sent."""
    today = today or date.today()
    sent = []
    for d in deadlines(brain):
        if d.when >= today:
            break
        for m in d.owners:
            stem = f"overdue-{slugify(d.id)}-{d.when.isoformat()}"
            rel = f"{folder(m)}/{stem}.md"
            if (brain.root / rel).exists() or gitops.git(brain.root, "log", "-1", "--format=%h", "--", rel).strip():
                continue  # sent before, maybe dismissed since
            text = (f"Overdue: {d.concept.meta.get('type')} \"{d.concept.title}\" ({d.id}) was due {d.when}, and no "
                    "document reports it finished. If it is done, or the date moved, say so in a note "
                    "(`brainforge note \"…\"`) or the next minutes.")
            sent.append((m, d, send(brain, m, text, BRAINFORGE, about=d.id, stem=stem)))
    return sent


# --- the briefing --------------------------------------------------------------------------------

def briefing(brain: Brain, who: TeamMember | None, today: date | None = None) -> dict[str, Any]:
    """Messages, open rounds, overdue and soon-due items, and approvals for one person; everyone's when `who`
    is None."""
    today = today or date.today()
    due = [d for d in deadlines(brain) if who is None or who in d.owners]
    pending = approval.pending_for(brain, who)
    return {
        "brain": brain.id,
        "for": who.name if who else None,
        "date": today.isoformat(),
        "messages": [x.as_dict() for x in messages(brain, who)] if who else [],
        "rounds": rounds.waiting(brain, who),
        "proposals": [{"procedure": w["procedure"], "report": w["report"], "waiting": len(w["proposals"])}
                      for w in proposals.waiting(brain)] if who is None or who.lead else [],
        "overdue": [d.as_dict() for d in due if d.when < today],
        "due_soon": [d.as_dict() for d in due if today <= d.when <= today + timedelta(days=DUE_SOON_DAYS)],
        "approvals": len(pending),
        "expired": sum(i.expired for i in pending),
    }


def text(b: dict[str, Any]) -> str:
    """The briefing as short plain text; empty when there is nothing to tell."""
    lines = []
    if b["messages"]:
        lines.append(f"Messages ({len(b['messages'])}):")
        lines += [f"  {_local(m['at'])}  from {m['from']}: {_oneline(m['text'])}  [{m['id']}]"
                  for m in b["messages"]]
    if b["rounds"]:
        lines.append(f"{'Rounds waiting for your update' if b['for'] else 'Open rounds'} ({len(b['rounds'])}):")
        lines += [f"  {r['id']} until {r['closes']}" + (": send it with `brainforge note \"…\"` or tell your agent"
                                                      if b["for"] else f", no update yet from {', '.join(r['missing'])}"
                                                      if r["missing"] else ", everyone has sent one")
                  for r in b["rounds"]]
        if asks := next((r["questions"] for r in b["rounds"] if r.get("questions")), []):
            lines.append("  Questions for your update:")
            lines += [f"    {q['id']}: {q['question']}" for q in asks]
    if b["proposals"]:
        lines.append(f"Proposals waiting for a lead ({sum(p['waiting'] for p in b['proposals'])}):")
        lines += [f"  {p['report']}: {p['waiting']} (brainforge proposals list)" for p in b["proposals"]]
    for key, head in (("overdue", "Overdue"), ("due_soon", f"Due within {DUE_SOON_DAYS} days")):
        if b[key]:
            lines.append(f"{head} ({len(b[key])}):")
            lines += [f"  {d['due']}  {d['id']}  [{d['type']}] {d['title']}"
                      + (f" (owner: {d['owner']})" if d["owner"] and not b["for"] else "") for d in b[key]]
    if b["approvals"]:
        expired = f", {b['expired']} of them past their review date" if b["expired"] else ""
        lines.append(f"Awaiting {'your ' if b['for'] else ''}approval: {b['approvals']} item(s){expired} "
                     "(brainforge pending)")
    return "\n".join(lines)


def _local(at: str) -> str:
    try:
        return f"{datetime.fromisoformat(at).astimezone():%Y-%m-%d %H:%M}"
    except ValueError:
        return at[:16]


def _oneline(s: str, width: int = 160) -> str:
    s = " ".join(s.split())
    return s if len(s) <= width else s[: width - 1] + "…"


def github(brain: Brain, sent: list[tuple[TeamMember, Due, str]], repo_url: str | None = None) -> str:
    """Markdown for the reminder issue: who has a newly overdue item, with an @mention."""
    if not sent:
        return ""
    parts = ["**Overdue, and no document reports it finished:**"]
    members: list[TeamMember] = []
    for m, _, _ in sent:
        if m not in members:
            members.append(m)
    for m in members:
        parts += ["", f"@{m.github}" if m.github else f"{m.name} (no `github` login in brain.yaml)"]
        for _, d, _ in (x for x in sent if x[0] == m):
            rel = brain.rel(d.concept.path)
            title = f"[{d.concept.title}]({repo_url}/blob/HEAD/{rel})" if repo_url else d.concept.title
            parts.append(f"- {title} ({d.concept.meta.get('type')}, due {d.when})")
    return "\n".join(parts)


# --- calendar ------------------------------------------------------------------------------------

def calendar(brain: Brain, who: TeamMember | None) -> str:
    """Open deadlines as all-day events (iCalendar). Stable UIDs, so importing again updates them."""
    def esc(s: str) -> str:
        return re.sub(r"([\\;,])", r"\\\1", s).replace("\n", "\\n")

    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BrainForge//EN", f"X-WR-CALNAME:{esc(brain.manifest.name)}"]
    for d in deadlines(brain):
        if who and who not in d.owners:
            continue
        meta = d.concept.meta
        summary = f"{meta.get('type')}: {d.concept.title}"
        description = f"{d.id} in {brain.manifest.name}" + (f", owner: {meta['owner']}" if meta.get("owner") else "")
        lines += ["BEGIN:VEVENT", f"UID:{d.id}@{brain.id}", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d.when:%Y%m%d}", f"DTEND;VALUE=DATE:{d.when + timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{esc(summary)}", f"DESCRIPTION:{esc(description)}", "END:VEVENT"]
    # ponytail: no 75-octet line folding; calendar apps accept longer lines
    return "\r\n".join([*lines, "END:VCALENDAR"]) + "\r\n"


def find(brain: Brain, name: str | None) -> TeamMember | None:
    """`--for NAME`, else the git identity's team member; None means everyone."""
    return approval.member(brain, name) if name else approval.me(brain)
