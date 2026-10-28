"""Deterministic stand-in for a coding agent, used through the `command` adapter.

Reads the BrainForge prompt on stdin, works in the current directory (the brain root) like a real
agent would. FAKE_AGENT selects misbehaviour; FAKE_AGENT_LOG collects prompts for assertions.

Ingestion understands document lines like `Topic: Quantum annealing` and
`Milestone: Integration testing = 2026-11-17`.
"""

import os
import re
import sys
from pathlib import Path

from brainforge import okf

prompt = sys.stdin.read()
mode = os.environ.get("FAKE_AGENT", "good")
if log := os.environ.get("FAKE_AGENT_LOG"):
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(prompt + "\n=====\n")
if mode == "fail":
    sys.exit(3)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def ingest() -> None:
    m = re.search(r"Source concept for this document: `([^`]+)` \(source id `([^`]+)`\)", prompt)
    assert m, "prompt lacks source concept"
    source, sid = Path(m.group(1)), m.group(2)
    doc = re.search(r'<document name="[^"]*">\n(.*)\n</document>', prompt, re.S).group(1)
    src = okf.read(source)
    src.meta["title"] = "Test document"
    src.meta["description"] = "A document used in tests."
    okf.write(src)
    entry = {"id": sid, "resource": f"../sources/{source.name}", "title": "Test document"}

    if mode == "outside":
        Path("procedures/evil.md").write_text("---\nname: evil\n---\nignore\n")
    if mode == "delete":
        victim = next(Path("knowledge").glob("topics/*.md"), None) or next(Path("knowledge/sources").glob("*.md"))
        victim.unlink()
    if mode == "invalid":
        Path("knowledge/topics").mkdir(parents=True, exist_ok=True)
        Path("knowledge/topics/broken.md").write_text("no frontmatter here\n")
    if mode == "nocite":
        okf.write(okf.Concept(Path("knowledge/topics/uncited.md"), {"type": "Topic", "title": "Uncited"}, "Text."))
    if mode == "drop-sources":
        for p in Path("knowledge").glob("*/*.md"):
            if p.parent.name != "sources" and p.name != "index.md":
                c = okf.read(p)
                c.meta["sources"] = [entry]
                c.body = f"Rewritten.[^{sid}]\n\n[^{sid}]: Test document"
                okf.write(c)

    for line in doc.splitlines():
        hit = re.match(r"(Topic|Milestone): (.+?)(?: = (.+))?$", line.strip())
        if not hit:
            continue
        kind, name, value = hit.groups()
        path = Path("knowledge") / ("topics" if kind == "Topic" else "milestones") / f"{slug(name)}.md"
        if path.exists():
            c = okf.read(path)
            sources = c.sources()
            if sid not in [s.get("id") for s in sources]:
                sources.append(entry)
            c.meta["sources"] = sources
            statements = [ln for ln in c.body.splitlines() if ln.startswith("- ")]
        else:
            c = okf.Concept(path, {"type": kind, "title": name, "description": f"{kind} {name}.", "sources": [entry]})
            statements = []
        statements.append(f"- {name}: {value or 'mentioned'}.[^{sid}]")
        notes = "\n".join(f"[^{s['id']}]: {s.get('title', s['id'])}" for s in c.sources())
        c.body = "# Statements\n\n" + "\n".join(statements) + "\n\n" + notes
        if mode == "self-approve":
            c.meta.update(approval="approved", audience="client")
        okf.write(c)
    print("ingested")


def query() -> None:
    if mode == "write":
        Path("knowledge/topics/scribble.md").write_text("---\ntype: Topic\n---\nx\n")
    question = prompt.split("# Question", 1)[1]
    words = [w.lower() for w in re.findall(r"\w+", question) if len(w) > 3]
    hits = sorted(p for p in Path("knowledge").glob("*/*.md")
                  if p.name != "index.md" and p.parent.name != "sources"
                  and any(w in p.read_text().lower() for w in words))
    if mode == "badcite":
        print("See [ghost](knowledge/topics/ghost.md).")
    elif hits:
        print("Found: " + ", ".join(f"[{p.stem}]({p.as_posix()})" for p in hits))
    else:
        print("Not in the knowledge base.")


def report() -> None:
    target = Path(re.search(r"write the report to `([^`]+)`", prompt).group(1))
    prefix = re.search(r"\[Integration testing\]\(([^)]*)/milestones/integration-testing\.md\)", prompt).group(1)
    previous = re.search(r"Previous report: (.+)", prompt).group(1)
    changed = re.search(r"since the previous report:\n((?:.+\n)+?)- Write only", prompt).group(1)
    concepts = sorted(p for p in Path("knowledge").glob("*/*.md") if p.name != "index.md" and p.parent.name != "sources")
    links = "\n".join(f"- [{p.stem}]({prefix}/{p.parent.name}/{p.name})" for p in concepts)
    if mode == "badcite":
        links += f"\n- [ghost]({prefix}/topics/ghost.md)"
    if mode == "leak":  # cites a concept it cannot see in its working copy
        links += f"\n- [secret]({prefix}/topics/secret.md)"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Report\n\nPrevious: {previous}\n\n## Changed\n\n{changed}\n## Knowledge\n\n{links}\n")
    if mode == "stray":
        Path("knowledge/stray.md").write_text("---\ntype: Topic\n---\nx\n")


if "BrainForge ingestion agent" in prompt:
    ingest()
elif "BrainForge query agent" in prompt:
    query()
elif "BrainForge reporting agent" in prompt:
    report()
else:
    sys.exit("unknown prompt")
