"""Build the test plan page from content.py: python3 gen.py --standalone > brainforge-test-plan.html

Without --standalone it writes the page body for a claude.ai artifact, which adds the document around it and
shares results between testers; the standalone page keeps them in the browser.
"""

import html
import re
import sys

from content import CHAPTERS, OSES, PARTS

TAGS = {"agent": "Uses the AI agent", "optional": "Optional"}
ALL = set(OSES)
BY_ID = {p["id"]: p for p in PARTS}
assert sorted(x for c in CHAPTERS for x in c["parts"]) == sorted(BY_ID), "every part in exactly one chapter"


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*])\*([^*]+)\*(?![*])", r"<em>\1</em>", out)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)


def code_block(code: str) -> str:
    if code.startswith("chat:"):
        return ('<div class="chat"><span class="chat-label">Type this to Claude</span>'
                f'<p class="chat-text">{html.escape(code[5:], quote=False)}</p>'
                '<button type="button" class="copy" aria-label="Copy this text">Copy</button></div>')
    body = html.escape(code, quote=False)
    body = re.sub(r"\{\{(.+?)\}\}", r'<mark class="ph" title="Replace this">\1</mark>', body)
    return ('<div class="code"><pre><code>' + body + '</code></pre>'
            '<button type="button" class="copy" aria-label="Copy these commands">Copy</button></div>')


def shown_on(v) -> set:
    """The computers a step's text or code appears on: a string everywhere, a dict where its value isn't None."""
    if not v:
        return set()
    return ALL if isinstance(v, str) else {o for k, x in v.items() if x for o in k.split()}


def per_os(v, render) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        return render(v)
    return "".join(f'<div class="osv" data-os="{k}">{render(x)}</div>' for k, x in v.items() if x)


def test_html(t: dict) -> str:
    tid = t["id"]
    key = "t" + tid.replace(".", "-")
    tags = "".join(f'<span class="tag tag-{x}">{TAGS[x]}</span>' for x in t["tags"])
    steps = []
    for text, code in t["steps"]:
        on = shown_on(text) | shown_on(code)
        attr = "" if on == ALL else f' class="osv" data-os="{" ".join(sorted(on))}"'
        steps.append(f"<li{attr}>" + per_os(text, lambda x: f"<p>{inline(x)}</p>") + per_os(code, code_block) + "</li>")
    na = "".join(f'<p class="na osv" data-os="{k}">{inline(x)}</p>' for k, x in t["na"].items())
    expect = "".join(f"<li>{inline(x)}</li>" for x in t["expect"])
    verdicts = "".join(
        f'<button type="button" class="v v-{v}" data-v="{v}" aria-pressed="false">{label}</button>'
        for v, label in (("pass", "Pass"), ("fail", "Fail"), ("blocked", "Blocked"), ("skip", "Skip")))
    return f"""
<article class="test" id="{key}" data-id="{tid}" data-status="">
  <header class="test-head">
    <span class="tid">{tid}</span>
    <h4>{inline(t["title"])}</h4>
    <span class="tags">{tags}</span>
  </header>
  <p class="why">{inline(t["why"])}</p>{na}
  <ol class="steps">{"".join(steps)}</ol>
  <section class="expect" aria-label="Expected result"><h5>You should see</h5><ul>{expect}</ul></section>
  <footer class="record">
    <div class="verdicts" role="group" aria-label="Result of test {tid}">{verdicts}</div>
    <label class="note-label" for="note-{key}">Note <span class="hint">what you saw, especially when something differs</span></label>
    <textarea id="note-{key}" class="note" rows="2" placeholder="Paste what the terminal printed, or describe what happened"></textarea>
    <p class="meta" aria-live="polite"></p>
  </footer>
</article>"""


def part_html(p: dict) -> str:
    tests = "".join(test_html(t) for t in p["tests"])
    return f"""
<section class="part" id="{p["id"]}" aria-labelledby="{p["id"]}-h">
  <header class="part-head">
    <p class="eyebrow">Part {p["num"]} · about {p["minutes"]} min · {len(p["tests"])} tests</p>
    <h3 id="{p["id"]}-h">{inline(p["title"])}</h3>
    <p class="part-intro">{inline(p["intro"])}</p>
  </header>
  {tests}
</section>"""


def duration(m: int) -> str:
    return f"{m} min" if m < 60 else f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def chapter_html(i: int, c: dict) -> str:
    return (f'<header class="chapter" id="{c["id"]}"><p class="eyebrow">Chapter {i} of {len(CHAPTERS)}</p>'
            f'<h2>{inline(c["title"])}</h2><p>{inline(c["what"])}</p></header>'
            + "".join(part_html(BY_ID[x]) for x in c["parts"]))


def agenda_html() -> str:
    rows = []
    for i, c in enumerate(CHAPTERS, 1):
        ps = [BY_ID[x] for x in c["parts"]]
        span = f'Part {ps[0]["num"]}' if len(ps) == 1 else f'Parts {ps[0]["num"]}–{ps[-1]["num"]}'
        rows.append(f'<li><a class="ag" href="#{c["id"]}"><span class="ag-num">{i}</span><span class="ag-body">'
                    f'<strong>{inline(c["title"])}</strong><span class="ag-what">{inline(c["what"])}</span></span>'
                    f'<span class="ag-meta">{span} · {duration(sum(p["minutes"] for p in ps))}</span></a></li>')
    return "".join(rows)


def toc_html() -> str:
    out = []
    for i, c in enumerate(CHAPTERS, 1):
        items = "".join(
            f'<li><a href="#{p["id"]}" data-part="{p["id"]}"><span class="toc-num">{p["num"]}</span>'
            f'<span class="toc-title">{html.escape(p["title"])}</span><span class="toc-count" data-count="{p["id"]}">'
            f'0/{len(p["tests"])}</span></a></li>' for p in (BY_ID[x] for x in c["parts"]))
        out.append(f'<li><a class="toc-ch" href="#{c["id"]}">{i} · {html.escape(c["title"])}</a>'
                   f'<ol class="toc-list">{items}</ol></li>')
    return f'<ol class="toc-chapters">{"".join(out)}</ol>'


TOTAL = sum(len(p["tests"]) for p in PARTS)
MINUTES = sum(p["minutes"] for p in PARTS)

PAGE = """<title>BrainForge Test Plan</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
/* Layout: a test protocol. A sticky index with progress on the left, one reading column of test sheets on the right;
   each sheet carries its verdict as a coloured edge. */
:root {
  --paper: #f4f5f1; --surface: #ffffff; --ink: #1a211e; --muted: #59635e; --line: #d8ddd7;
  --accent: #2747a8; --accent-soft: #e5eaf7; --code-bg: #edf0eb; --ph: #ffe39a; --ph-ink: #3d2e00;
  --pass: #1d7a45; --fail: #b42318; --blocked: #9a5a00; --skip: #69707b;
  --pass-soft: #e3f3e9; --fail-soft: #fbe6e3; --blocked-soft: #fbefd9; --skip-soft: #eceef1;
  --f-display: "Bricolage Grotesque", "Avenir Next", "Segoe UI", sans-serif;
  --f-body: "Atkinson Hyperlegible", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --f-mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
  --s-1: 0.25rem; --s-2: 0.5rem; --s-3: 0.75rem; --s-4: 1rem; --s-5: 1.5rem; --s-6: 2.25rem; --s-7: 3.5rem;
  --r: 6px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #101412; --surface: #171c19; --ink: #e3e8e4; --muted: #9aa49e; --line: #2a322e;
  --accent: #93abf5; --accent-soft: #1d2537; --code-bg: #0b0e0c; --ph: #6b5512; --ph-ink: #fff1c7;
  --pass: #52c58d; --fail: #f2796d; --blocked: #e4a843; --skip: #a3aab4;
  --pass-soft: #14291d; --fail-soft: #33171a; --blocked-soft: #2e2412; --skip-soft: #22262b;
  color-scheme: dark;
} }
:root[data-theme="dark"] {
  --paper: #101412; --surface: #171c19; --ink: #e3e8e4; --muted: #9aa49e; --line: #2a322e;
  --accent: #93abf5; --accent-soft: #1d2537; --code-bg: #0b0e0c; --ph: #6b5512; --ph-ink: #fff1c7;
  --pass: #52c58d; --fail: #f2796d; --blocked: #e4a843; --skip: #a3aab4;
  --pass-soft: #14291d; --fail-soft: #33171a; --blocked-soft: #2e2412; --skip-soft: #22262b;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { background: var(--paper); color: var(--ink); font: 16px/1.6 var(--f-body); }
a { color: var(--accent); text-underline-offset: 2px; }
a:focus-visible, button:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
code { font-family: var(--f-mono); font-size: 0.86em; background: var(--code-bg); padding: 0.1em 0.35em; border-radius: 4px; overflow-wrap: anywhere; }
h1, h2, h3, h4 { font-family: var(--f-display); line-height: 1.15; text-wrap: balance; margin: 0; }
/* One page for three computers: body[data-os] hides the variants for the other two */
body[data-os="mac"] .osv:not([data-os~="mac"]), body[data-os="win"] .osv:not([data-os~="win"]),
body[data-os="linux"] .osv:not([data-os~="linux"]) { display: none; }

.shell { display: grid; grid-template-columns: 17rem minmax(0, 1fr); gap: var(--s-6); max-width: 76rem; margin: 0 auto;
  padding-block: var(--s-6) var(--s-7); padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.side { position: sticky; top: calc(env(safe-area-inset-top, 0px) + var(--s-4)); align-self: start; max-height: calc(100vh - 2rem); overflow-y: auto; display: grid; gap: var(--s-5); }
main { min-width: 0; display: grid; gap: var(--s-7); }

/* index */
.brand { display: grid; gap: var(--s-1); }
.brand .kicker { font: 500 0.72rem/1 var(--f-mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.brand h1 { font-size: 1.6rem; }
.progress { display: grid; gap: var(--s-2); }
.bar { display: flex; height: 10px; border-radius: 99px; overflow: hidden; background: var(--line); }
.bar span { display: block; height: 100%; transition: width 0.3s; }
.bar .b-pass { background: var(--pass); } .bar .b-fail { background: var(--fail); }
.bar .b-blocked { background: var(--blocked); } .bar .b-skip { background: var(--skip); }
.counts { display: flex; flex-wrap: wrap; gap: var(--s-1) var(--s-3); font-size: 0.85rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.counts b { color: var(--ink); }
.dot { display: inline-block; width: 0.6em; height: 0.6em; border-radius: 50%; margin-right: 0.3em; vertical-align: 0.05em; }
.dot.pass { background: var(--pass); } .dot.fail { background: var(--fail); } .dot.blocked { background: var(--blocked); } .dot.skip { background: var(--skip); }
.os-pick { display: grid; gap: var(--s-1); }
.os-pick .lbl { font-size: 0.8rem; font-weight: 700; color: var(--muted); }
.seg { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--line); border-radius: var(--r); overflow: hidden; }
.seg button { font: 700 0.85rem/1 var(--f-body); padding: 0.6rem 0.3rem; border: 0; background: var(--surface); color: var(--ink); cursor: pointer; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button[aria-pressed="true"] { background: var(--accent); color: var(--surface); }
.toc-chapters { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s-3); }
.toc-ch { display: block; padding: 0 0.5rem var(--s-1); font: 500 0.7rem/1.3 var(--f-mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); text-decoration: none; }
.toc-ch:hover { color: var(--accent); }
.toc-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
.toc-list a { display: grid; grid-template-columns: 2rem 1fr auto; gap: var(--s-2); align-items: baseline; padding: 0.35rem 0.5rem; border-radius: var(--r); color: var(--ink); text-decoration: none; font-size: 0.92rem; line-height: 1.3; }
.toc-list a:hover { background: var(--accent-soft); }
.toc-num { font-family: var(--f-mono); font-size: 0.78rem; color: var(--muted); }
.toc-count { font-family: var(--f-mono); font-size: 0.75rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.toc-list a.has-fail .toc-count { color: var(--fail); font-weight: 500; }
.toc-list a.done .toc-count { color: var(--pass); }
.tools { display: grid; gap: var(--s-2); }
.btn { font: 700 0.88rem/1 var(--f-body); padding: 0.6rem 0.8rem; border-radius: var(--r); border: 1px solid var(--line); background: var(--surface); color: var(--ink); cursor: pointer; text-align: left; }
.btn:hover { border-color: var(--accent); }
.switch { display: flex; gap: var(--s-2); align-items: center; font-size: 0.88rem; color: var(--muted); cursor: pointer; }
.switch input { accent-color: var(--accent); width: 1rem; height: 1rem; }
.saved { font-size: 0.8rem; color: var(--muted); line-height: 1.4; border-left: 3px solid var(--line); padding-left: var(--s-2); }
.saved.shared { border-color: var(--pass); }
.saved.local { border-color: var(--blocked); }

/* intro */
.lede { display: grid; gap: var(--s-4); max-width: 44rem; }
.lede .kicker { font: 500 0.75rem/1 var(--f-mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
.lede h2 { font-size: clamp(1.9rem, 4vw, 2.6rem); }
.lede p { margin: 0; font-size: 1.05rem; }
.facts { display: flex; flex-wrap: wrap; gap: var(--s-2) var(--s-5); padding: 0; margin: 0; list-style: none; font-size: 0.92rem; color: var(--muted); }
.facts b { color: var(--ink); font-variant-numeric: tabular-nums; }
.howto { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: var(--s-4); }
.box { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r); padding: var(--s-4) var(--s-5); min-width: 0; }
.box h3 { font-size: 1.05rem; margin-bottom: var(--s-2); }
.box ul, .box ol { margin: 0; padding-left: 1.2rem; display: grid; gap: var(--s-1); font-size: 0.95rem; }
.box.warn { border-left: 4px solid var(--blocked); }
details.box summary { cursor: pointer; font: 700 1.05rem/1.3 var(--f-display); }
details.box[open] summary { margin-bottom: var(--s-3); }
dl.gloss { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: var(--s-2) var(--s-4); font-size: 0.95rem; }
dl.gloss dt { font-weight: 700; } dl.gloss dd { margin: 0; }

/* agenda */
.agenda { display: grid; gap: var(--s-4); }
.agenda h2 { font-size: 1.75rem; }
.agenda h3 { font-size: 1.05rem; }
.agenda > p { margin: 0; max-width: 44rem; }
.flow { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--s-3) var(--s-5); }
.flow li { flex: 1 1 8.5rem; position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r); padding: var(--s-2) var(--s-3); font-weight: 700; font-size: 0.95rem; line-height: 1.3; }
.flow li small { display: block; margin-top: 2px; font-weight: 400; font-size: 0.82rem; color: var(--muted); }
.flow li.hub { border-color: var(--accent); background: var(--accent-soft); }
.flow li:not(:last-child)::after { content: "→"; position: absolute; right: -1.15rem; top: 50%; transform: translateY(-50%); color: var(--muted); font-weight: 400; }
.ag-list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s-2); }
.ag { display: grid; grid-template-columns: 2.2rem minmax(0, 1fr) auto; gap: var(--s-3); align-items: baseline; padding: var(--s-3) var(--s-4); background: var(--surface); border: 1px solid var(--line); border-radius: var(--r); color: var(--ink); text-decoration: none; }
.ag:hover { border-color: var(--accent); }
.ag-num { font: 700 1.3rem/1 var(--f-display); color: var(--accent); }
.ag-body { display: grid; gap: 2px; min-width: 0; }
.ag-body strong { font-family: var(--f-display); font-size: 1.1rem; }
.ag-what { color: var(--muted); font-size: 0.95rem; }
.ag-meta { font: 500 0.78rem/1.3 var(--f-mono); color: var(--muted); white-space: nowrap; }

/* chapters and parts */
.chapter { display: grid; gap: var(--s-2); max-width: 44rem; scroll-margin-top: var(--s-4); padding-top: var(--s-5); }
.chapter .eyebrow { color: var(--accent); }
.chapter h2 { font-size: clamp(1.9rem, 4vw, 2.5rem); }
.chapter p:last-child { margin: 0; font-size: 1.05rem; }
.part { display: grid; gap: var(--s-5); scroll-margin-top: var(--s-4); }
.part-head { display: grid; gap: var(--s-2); max-width: 44rem; border-top: 2px solid var(--ink); padding-top: var(--s-4); }
.eyebrow { margin: 0; font: 500 0.75rem/1 var(--f-mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.part-head h3 { font-size: 1.6rem; }
.part-intro { margin: 0; color: var(--muted); }

/* a test sheet */
.test { background: var(--surface); border: 1px solid var(--line); border-left: 5px solid var(--line); border-radius: var(--r); padding: var(--s-5); display: grid; gap: var(--s-4); scroll-margin-top: var(--s-4); }
.test[data-status="pass"] { border-left-color: var(--pass); }
.test[data-status="fail"] { border-left-color: var(--fail); }
.test[data-status="blocked"] { border-left-color: var(--blocked); }
.test[data-status="skip"] { border-left-color: var(--skip); }
.test-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--s-2) var(--s-3); }
.tid { font: 500 0.85rem/1 var(--f-mono); color: var(--accent); background: var(--accent-soft); padding: 0.3rem 0.45rem; border-radius: 4px; font-variant-numeric: tabular-nums; }
.test-head h4 { font-size: 1.2rem; flex: 1 1 14rem; }
.na { margin: 0; padding: var(--s-2) var(--s-3); border-radius: var(--r); background: var(--skip-soft); }
.tags { display: flex; gap: var(--s-1); flex-wrap: wrap; }
.tag { font-size: 0.72rem; font-weight: 700; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 99px; border: 1px solid var(--line); color: var(--muted); }
.tag-agent { color: var(--accent); border-color: var(--accent); }
.why { margin: 0; color: var(--muted); }
.steps { margin: 0; padding-left: 1.4rem; display: grid; gap: var(--s-3); }
.steps li { min-width: 0; padding-left: var(--s-1); }
.steps li::marker { font-family: var(--f-mono); font-size: 0.85rem; color: var(--muted); }
.steps p { margin: 0 0 var(--s-2); }
.code { position: relative; background: var(--code-bg); border: 1px solid var(--line); border-radius: var(--r); min-width: 0; }
.code pre { margin: 0; padding: var(--s-3) 5rem var(--s-3) var(--s-4); overflow-x: auto; font: 0.84rem/1.55 var(--f-mono); }
.code pre code { background: none; padding: 0; font-size: inherit; overflow-wrap: normal; white-space: pre; }
mark.ph { background: var(--ph); color: var(--ph-ink); border-radius: 3px; padding: 0 0.2em; }
.copy { position: absolute; top: var(--s-2); right: var(--s-2); font: 700 0.78rem/1 var(--f-body); padding: 0.45rem 0.6rem; border-radius: 4px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); cursor: pointer; }
.copy:hover { border-color: var(--accent); color: var(--accent); }
.copy.done { color: var(--pass); border-color: var(--pass); }
.chat { position: relative; border: 1px dashed var(--accent); border-radius: var(--r); padding: var(--s-3) 5rem var(--s-3) var(--s-4); background: var(--accent-soft); }
.chat-label { display: block; font: 500 0.7rem/1 var(--f-mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent); margin-bottom: var(--s-1); }
.chat-text { margin: 0 !important; font-weight: 700; }
.expect { background: var(--paper); border-radius: var(--r); padding: var(--s-3) var(--s-4); }
.expect h5 { margin: 0 0 var(--s-1); font: 500 0.72rem/1.2 var(--f-mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.expect ul { margin: 0; padding-left: 1.2rem; display: grid; gap: var(--s-1); }
.record { display: grid; gap: var(--s-2); border-top: 1px solid var(--line); padding-top: var(--s-4); }
.verdicts { display: flex; flex-wrap: wrap; gap: var(--s-2); }
.v { font: 700 0.9rem/1 var(--f-body); padding: 0.6rem 1rem; border-radius: var(--r); border: 1.5px solid var(--line); background: var(--surface); color: var(--ink); cursor: pointer; min-width: 5.5rem; }
.v:hover { border-color: var(--muted); }
.v-pass[aria-pressed="true"] { background: var(--pass-soft); border-color: var(--pass); color: var(--pass); }
.v-fail[aria-pressed="true"] { background: var(--fail-soft); border-color: var(--fail); color: var(--fail); }
.v-blocked[aria-pressed="true"] { background: var(--blocked-soft); border-color: var(--blocked); color: var(--blocked); }
.v-skip[aria-pressed="true"] { background: var(--skip-soft); border-color: var(--skip); color: var(--skip); }
.note-label { font-size: 0.85rem; font-weight: 700; margin-top: var(--s-1); }
.note-label .hint { font-weight: 400; color: var(--muted); }
.note { width: 100%; resize: vertical; font: 0.95rem/1.5 var(--f-body); padding: var(--s-2) var(--s-3); border: 1px solid var(--line); border-radius: var(--r); background: var(--paper); color: var(--ink); }
.test[data-status="fail"] .note:placeholder-shown { border-color: var(--fail); }
.meta { margin: 0; font-size: 0.8rem; color: var(--muted); min-height: 1.2em; }
body.hide-done .test[data-status="pass"], body.hide-done .test[data-status="skip"] { display: none; }

.out { position: fixed; inset: auto max(16px, env(safe-area-inset-right, 0px)) calc(env(safe-area-inset-bottom, 0px) + 16px) max(16px, env(safe-area-inset-left, 0px)); max-width: 40rem; margin-left: auto; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r); padding: var(--s-4); box-shadow: 0 8px 30px rgb(0 0 0 / 0.18); display: grid; gap: var(--s-2); z-index: 20; }
.out textarea { width: 100%; height: 14rem; font: 0.8rem/1.4 var(--f-mono); background: var(--code-bg); color: var(--ink); border: 1px solid var(--line); border-radius: 4px; padding: var(--s-2); }
.toast { position: fixed; left: 50%; transform: translateX(-50%); bottom: calc(env(safe-area-inset-bottom, 0px) + 16px); background: var(--ink); color: var(--paper); padding: 0.6rem 1rem; border-radius: var(--r); font-size: 0.9rem; z-index: 30; }

@media (max-width: 900px) {
  .shell { grid-template-columns: minmax(0, 1fr); gap: var(--s-5); }
  .side { position: static; max-height: none; overflow: visible; }
  .toc-list { grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); }
  .test { padding: var(--s-4); }
}
@media (max-width: 600px) {
  .ag { grid-template-columns: 2rem minmax(0, 1fr); }
  .ag-meta { grid-column: 2; white-space: normal; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; scroll-behavior: auto !important; } }
</style>

<div class="shell">
  <aside class="side" aria-label="Progress and parts">
    <div class="brand"><span class="kicker">Test protocol</span><h1>BrainForge Test Plan</h1></div>
    <div class="os-pick"><span class="lbl" id="os-lbl">Your computer: the steps follow it</span>
      <div class="seg" role="group" aria-labelledby="os-lbl"><button type="button" data-os="mac" aria-pressed="false">Mac</button><button type="button" data-os="win" aria-pressed="false">Windows</button><button type="button" data-os="linux" aria-pressed="false">Linux</button></div></div>
    <div class="progress">
      <div class="bar" role="img" aria-label="Progress"><span class="b-pass"></span><span class="b-fail"></span><span class="b-blocked"></span><span class="b-skip"></span></div>
      <div class="counts"><span><b data-sum="done">0</b>/__TOTAL__ tested</span><span><i class="dot pass"></i><b data-sum="pass">0</b> pass</span><span><i class="dot fail"></i><b data-sum="fail">0</b> fail</span><span><i class="dot blocked"></i><b data-sum="blocked">0</b> blocked</span><span><i class="dot skip"></i><b data-sum="skip">0</b> skipped</span></div>
    </div>
    <nav aria-label="Chapters and parts">__TOC__</nav>
    <div class="tools">
      <button type="button" class="btn" id="copy-results">Copy results as text</button>
      <button type="button" class="btn" id="next-open">Go to the next open test</button>
      <label class="switch"><input type="checkbox" id="hide-done"> Hide passed and skipped tests</label>
      <p class="saved local" id="saved">Results are saved in this browser for now.</p>
    </div>
  </aside>

  <main>
    <section class="lede" aria-labelledby="lede-h">
      <span class="kicker">For testers · no prior knowledge needed</span>
      <h2 id="lede-h">Try every part of BrainForge on a made-up project</h2>
      <p>BrainForge turns project documents, such as minutes, call transcripts and Word files, into a knowledge base in a Git repository, using an AI agent. People approve what the agent found. On top of that it writes reports, collects status updates, finds what needs attention and proposes what to do. You will try all of it on the relaunch of the ACME website, a project that doesn't exist: on the command line, by talking to Claude, in Obsidian, and on GitHub.</p>
      <p>The page works for a <strong>Mac</strong>, a <strong>Windows</strong> PC and a <strong>Linux</strong> computer. Pick yours under <strong>Your computer</strong> in the index; the steps change with it.</p>
      <ul class="facts"><li><b>__TOTAL__</b> tests in <b>__PARTS__</b> parts</li><li>about <b>__HOURS__</b> hours, in as many sittings as you like</li><li>a GitHub account and a Claude Pro or Max subscription</li></ul>
    </section>

    <section class="agenda" aria-labelledby="agenda-h">
      <h2 id="agenda-h">Agenda</h2>
      <h3>How it fits together</h3>
      <ol class="flow">
        <li>Documents<small>minutes, call transcripts, Word files, notes</small></li>
        <li>AI agent<small>reads them and records each fact with its source</small></li>
        <li class="hub">The brain<small>a Git repository of linked notes</small></li>
        <li>People approve<small>each their own items</small></li>
        <li>Reports, signals, proposals<small>what needs whom, and what to do next</small></li>
      </ol>
      <p>You reach the brain through <strong>the command line</strong> (chapter 2), through <strong>AI assistants</strong> such as Claude Code and Claude Desktop, with the BrainForge skill or its MCP server (chapter 3), in <strong>Obsidian</strong> (chapter 3), and through <strong>GitHub</strong>, where it runs by itself (chapter 4).</p>
      <h3>What you will do</h3>
      <ol class="ag-list">__AGENDA__</ol>
    </section>

    <section class="howto" aria-label="How to use this page">
      <div class="box">
        <h3>How to test</h3>
        <ol>
          <li>Work through the parts in order: each builds on the one before.</li>
          <li>Do the steps, then compare with <strong>You should see</strong>.</li>
          <li>Mark <strong>Pass</strong>, <strong>Fail</strong>, <strong>Blocked</strong> (you couldn't do it, for example because an earlier test failed) or <strong>Skip</strong> (an optional test you left out).</li>
          <li>When something differs, mark Fail and paste what the terminal printed into the note. That is the most useful thing you can give us.</li>
        </ol>
      </div>
      <div class="box">
        <h3>The terminal in one minute</h3>
        <ul>
          <li>Click <strong>Copy</strong> on a grey block, click into the terminal, paste with <span class="osv" data-os="mac"><strong>⌘V</strong></span><span class="osv" data-os="linux"><strong>Ctrl+Shift+V</strong></span><span class="osv" data-os="win"><strong>right-click</strong> or <strong>Ctrl+Shift+V</strong></span>, then press Enter. One block at a time; wait for the prompt (a line ending in <code>$</code> or <code>%</code>) to come back.</li>
          <li class="osv" data-os="win">Every command goes into the <strong>Ubuntu</strong> window (WSL), not into PowerShell, unless a step says otherwise.</li>
          <li><mark class="ph">Highlighted</mark> words are placeholders: replace them before pressing Enter. Move in the line with the arrow keys.</li>
          <li>The AI agent takes one to three minutes per document. To stop a command, press <strong>Ctrl+C</strong>.</li>
          <li>A blue dashed box is not a command: type or paste it into Claude.</li>
          <li>Most blocks start with <code>cd ~/bf-test/acme-website</code>, so they also work in a new terminal window.</li>
        </ul>
      </div>
      <div class="box warn">
        <h3>Known issues</h3>
        <ul>
          <li>On GitHub, reply to an approval issue one comment at a time and wait for BrainForge's answer. Replies sent within seconds of each other can get lost.</li>
          <li>GitHub writes each report once per day. Test 13.8 shows how to make room when you already wrote it on your computer today.</li>
          <li>Claude Desktop doesn't exist for Linux: there, skip 0.9 and 11.6.</li>
          <li>The agent's results vary from run to run. Judge whether they are right and useful, not whether they match the examples word for word.</li>
        </ul>
      </div>
      <details class="box">
        <summary>Words you will meet</summary>
        <dl class="gloss">
          <dt>Terminal, WSL</dt><dd>The window where you type commands. On Windows, BrainForge runs in WSL: Linux (Ubuntu) built into Windows.</dd>
          <dt>Brain</dt><dd>A folder, also a Git repository, with everything BrainForge knows about one project.</dd>
          <dt>Claude Code, Claude Desktop</dt><dd>Claude as a terminal program and as a desktop app. BrainForge uses Claude Code as its AI agent, and you can talk to the brain through both.</dd>
          <dt>Skill</dt><dd>Instructions that teach Claude Code how to work with BrainForge for you.</dd>
          <dt>MCP server</dt><dd>MCP connects AI apps to tools. <code>brainforge mcp</code> lets Claude Desktop, Claude Code and other AI apps search the brain and, if you allow it, approve and send messages for you.</dd>
          <dt>Obsidian, vault</dt><dd>A notes app. It opens the brain folder as a vault: notes with clickable links and a graph.</dd>
          <dt>Git, commit</dt><dd>Git records every change as a commit with who and when, so all of it can be traced and undone. GitHub hosts Git repositories online.</dd>
          <dt>Ingest</dt><dd>Give a document to BrainForge: the AI agent records what it states, with a citation.</dd>
          <dt>Item, concept</dt><dd>One piece of knowledge in one file: a milestone, an action, a risk, a person.</dd>
          <dt>Pending</dt><dd>What the agent wrote waits until a person approves or discards it.</dd>
          <dt>Procedure, report</dt><dd>A recipe for a report, and the report it writes.</dd>
          <dt>Round</dt><dd>A status meeting without a meeting: people send updates while it is open; a report closes it.</dd>
          <dt>Signal</dt><dd>Something that needs attention, found by code: overdue, at risk, slipping, no owner.</dd>
          <dt>Proposal, lead</dt><dd>What the steering report suggests doing. A lead approves it and BrainForge carries it out.</dd>
          <dt>Project model</dt><dd>The phases and steps a project follows, such as sales → delivery → closure.</dd>
          <dt>Workflow</dt><dd>An automatic job on GitHub (GitHub Actions), for example when a document is pushed.</dd>
        </dl>
      </details>
    </section>

    __CHAPTERS_HTML__
  </main>
</div>

<script>
(() => {
  const LOCAL = "bf-testplan-v2", PREFS = "bf-testplan-prefs";
  const OSNAME = { mac: "Mac", win: "Windows", linux: "Linux" };
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(PREFS) || "{}") || {}; } catch {}
  const savePrefs = () => { try { localStorage.setItem(PREFS, JSON.stringify(prefs)); } catch {} };
  function guessOs() {
    const p = ((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent || "").toLowerCase();
    return p.includes("win") ? "win" : (p.includes("linux") || p.includes("x11")) ? "linux" : "mac";
  }
  function setOs(os) {
    document.body.dataset.os = os;
    document.querySelectorAll(".seg button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.os === os)));
  }
  setOs(OSNAME[prefs.os] ? prefs.os : guessOs());
  document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
    prefs.os = b.dataset.os; savePrefs(); setOs(b.dataset.os);
  }));
  const tests = [...document.querySelectorAll(".test")];
  const byId = Object.fromEntries(tests.map(el => [el.dataset.id, el]));
  const parts = [...document.querySelectorAll(".part")];
  const state = {};
  const names = {};
  let mode = "local", db = null, me = null, people = null;
  const docId = id => "t" + id.replace(/\\./g, "-");
  const idFromDoc = d => d.slice(1).replace(/-/g, ".");

  function readLocal() { try { return JSON.parse(localStorage.getItem(LOCAL) || "{}"); } catch { return {}; } }
  function writeLocal() { try { localStorage.setItem(LOCAL, JSON.stringify(state)); } catch {} }

  function toast(text) {
    const t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); t.textContent = text;
    document.body.append(t); setTimeout(() => t.remove(), 2600);
  }
  function setMode(next, text) {
    mode = next;
    const el = document.getElementById("saved");
    el.className = "saved " + (next === "shared" ? "shared" : "local");
    el.textContent = text || (next === "shared" ? "Results are saved for everyone who opens this page."
                                                : "Results are saved in this browser only.");
  }

  const LABEL = { pass: "Pass", fail: "Fail", blocked: "Blocked", skip: "Skipped" };
  function when(iso) {
    try { return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
    catch { return ""; }
  }
  function renderTest(id) {
    const el = byId[id]; if (!el) return;
    const s = state[id] || {};
    el.dataset.status = s.status || "";
    el.querySelectorAll(".v").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.v === s.status)));
    const note = el.querySelector(".note");
    if (document.activeElement !== note && note.value !== (s.note || "")) note.value = s.note || "";
    const who = (s.by && names[s.by] ? " by " + names[s.by] : "") + (s.os && OSNAME[s.os] ? " on " + OSNAME[s.os] : "");
    el.querySelector(".meta").textContent = s.status ? LABEL[s.status] + who + (s.at ? " · " + when(s.at) : "")
                                           : (s.note ? "Note saved" + who + (s.at ? " · " + when(s.at) : "") : "");
  }
  function renderSummary() {
    const c = { pass: 0, fail: 0, blocked: 0, skip: 0 };
    tests.forEach(el => { const s = state[el.dataset.id]?.status; if (s in c) c[s]++; });
    const done = c.pass + c.fail + c.blocked + c.skip;
    document.querySelector('[data-sum="done"]').textContent = done;
    for (const k in c) {
      document.querySelector(`[data-sum="${k}"]`).textContent = c[k];
      document.querySelector(".b-" + k).style.width = (100 * c[k] / tests.length) + "%";
    }
    parts.forEach(p => {
      const ts = [...p.querySelectorAll(".test")];
      const st = ts.map(t => state[t.dataset.id]?.status).filter(Boolean);
      const link = document.querySelector(`.toc-list a[data-part="${p.id}"]`);
      document.querySelector(`[data-count="${p.id}"]`).textContent = st.length + "/" + ts.length;
      link.classList.toggle("has-fail", st.includes("fail"));
      link.classList.toggle("done", st.length === ts.length && !st.includes("fail"));
    });
  }
  function renderAll() { tests.forEach(el => renderTest(el.dataset.id)); renderSummary(); }

  const queues = {};
  function queue(id, fn) { const p = (queues[id] || Promise.resolve()).then(fn, fn); queues[id] = p.catch(() => {}); return p; }
  async function save(id, patch) {
    const next = { ...(state[id] || {}), ...patch, by: me || null, os: document.body.dataset.os || null, at: new Date().toISOString() };
    if (!next.status) next.status = null;
    state[id] = next; renderTest(id); renderSummary();
    if (mode === "shared" && db) {
      try { await queue(id, () => db.doc("results/" + docId(id)).set(next)); }
      catch (e) {
        setMode("local", "You can open this page but not save to it, so your results stay in this browser. Ask the page's owner for edit access.");
        writeLocal();
      }
    } else writeLocal();
  }

  tests.forEach(el => {
    const id = el.dataset.id;
    el.querySelectorAll(".v").forEach(b => b.addEventListener("click", () => {
      const cur = state[id]?.status;
      save(id, { status: cur === b.dataset.v ? null : b.dataset.v });
    }));
    const note = el.querySelector(".note");
    let timer = null;
    const flush = () => { clearTimeout(timer); timer = null; if ((state[id]?.note || "") !== note.value) save(id, { note: note.value }); };
    note.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(flush, 800); });
    note.addEventListener("blur", flush);
  });

  function copyText(text, button) {
    const ok = () => { if (button) { button.textContent = "Copied"; button.classList.add("done");
                       setTimeout(() => { button.textContent = "Copy"; button.classList.remove("done"); }, 1600); } };
    try {
      return navigator.clipboard.writeText(text).then(ok, () => fallback(text));
    } catch { fallback(text); }
  }
  function fallback(text) {
    const box = document.createElement("div"); box.className = "out";
    box.innerHTML = '<strong>Copy this text, then close</strong><textarea readonly></textarea><button type="button" class="btn">Close</button>';
    const ta = box.querySelector("textarea"); ta.value = text;
    box.querySelector("button").addEventListener("click", () => box.remove());
    document.body.append(box); ta.focus(); ta.select();
  }
  document.querySelectorAll(".copy").forEach(b => b.addEventListener("click", () => {
    const src = b.parentElement.querySelector("code, .chat-text");
    copyText(src.textContent, b);
  }));

  document.getElementById("copy-results").addEventListener("click", () => {
    const lines = ["BrainForge test results, " + new Date().toLocaleDateString() + ", computer: " + OSNAME[document.body.dataset.os]];
    const c = { pass: 0, fail: 0, blocked: 0, skip: 0 };
    tests.forEach(el => { const s = state[el.dataset.id]?.status; if (s in c) c[s]++; });
    lines.push(`Tested ${c.pass + c.fail + c.blocked + c.skip}/${tests.length}: ${c.pass} pass, ${c.fail} fail, ${c.blocked} blocked, ${c.skip} skipped`, "");
    parts.forEach(p => {
      lines.push(p.querySelector(".part-head h3").textContent.trim().toUpperCase());
      p.querySelectorAll(".test").forEach(el => {
        const s = state[el.dataset.id] || {};
        const on = s.status && s.os && OSNAME[s.os] ? " (" + OSNAME[s.os] + ")" : "";
        lines.push(`${el.dataset.id} ${el.querySelector(".test-head h4").textContent.trim()}: ${s.status ? LABEL[s.status] + on : "not tested"}`);
        if (s.note) lines.push("    Note: " + s.note.replace(/\\n/g, "\\n    "));
      });
      lines.push("");
    });
    copyText(lines.join("\\n"), null);
    toast("Results copied");
  });

  document.getElementById("next-open").addEventListener("click", () => {
    const el = tests.find(t => !state[t.dataset.id]?.status);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "start" }); el.querySelector(".v").focus({ preventScroll: true }); }
    else toast("Every test has a result");
  });

  const hide = document.getElementById("hide-done");
  hide.checked = prefs.hideDone === true;
  document.body.classList.toggle("hide-done", hide.checked);
  hide.addEventListener("change", () => {
    document.body.classList.toggle("hide-done", hide.checked);
    prefs.hideDone = hide.checked; savePrefs();
  });

  async function resolveNames() {
    if (!people) return;
    const ids = [...new Set(Object.values(state).map(s => s.by).filter(Boolean))];
    if (!ids.length) return;
    try {
      const ps = await people.profiles(ids);
      ids.forEach(i => { names[i] = (ps[i] && ps[i].name) || ""; });
      tests.forEach(el => renderTest(el.dataset.id));
    } catch {}
  }

  Object.assign(state, readLocal());
  renderAll();

  async function connect() {
    if (!window.claude || typeof window.claude.use !== "function") return;
    const [store, user] = await Promise.all([window.claude.use("db"), window.claude.use("user")]);
    people = user;
    if (user) { try { me = await user.id(); } catch {} }
    if (!store) return;
    let canWrite = null;
    if (user) { try { canWrite = await user.can("data.write"); } catch {} }
    db = store;
    let first = true;
    db.collection("results").onSnapshot(snap => {
      for (const k of Object.keys(state)) delete state[k];
      snap.docs.forEach(d => { const v = d.data(); if (v) state[idFromDoc(d.id)] = v; });
      if (first) {
        first = false;
        setMode(canWrite === false ? "local" : "shared", canWrite === false
          ? "You can see everyone's results here, but not save your own. Ask the page's owner for edit access." : null);
      }
      renderAll(); resolveNames();
    }, () => { db = null; Object.assign(state, readLocal()); renderAll();
               setMode("local", "The shared results can't be reached right now, so yours are saved in this browser."); });
  }
  connect();
})();
</script>
"""


def main() -> None:
    out = (PAGE.replace("__TOC__", toc_html()).replace("__AGENDA__", agenda_html())
           .replace("__CHAPTERS_HTML__", "".join(chapter_html(i, c) for i, c in enumerate(CHAPTERS, 1)))
           .replace("__TOTAL__", str(TOTAL)).replace("__PARTS__", str(len(PARTS)))
           .replace("__HOURS__", f"{round(MINUTES / 60)}"))
    if "--standalone" in sys.argv[1:]:
        out = ('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
               '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + out)
    sys.stdout.write(out)


if __name__ == "__main__":
    main()
