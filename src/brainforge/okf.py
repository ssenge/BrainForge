"""Open Knowledge Format (OKF) v0.2 bundles: read, write, validate, index, log, search.

Spec: https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md
BrainForge profile on top of it: docs/knowledge-profile.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

OKF_VERSION = "0.2"
RESERVED = {"index.md", "log.md"}
STATUSES = {"draft", "stable", "deprecated"}
APPROVAL_STATES = ("pending", "approved", "discarded")  # BrainForge profile, see docs/knowledge-profile.md

GROUP_DESCRIPTIONS = {
    "sources": "Ingested source documents and their provenance.",
    "entities": "People, organizations, systems and other named things.",
    "topics": "Subject-matter concepts.",
}

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:", re.MULTILINE)
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_HEADING_RE = re.compile(r"^## \d{4}-\d{2}-\d{2}$")


class OKFError(Exception):
    pass


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps as strings so they round-trip unchanged."""


_Loader.yaml_implicit_resolvers = {
    key: [r for r in resolvers if r[0] != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)  # noqa: S506 - SafeLoader subclass


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)


def now_iso(at: datetime | None = None) -> str:
    return (at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Issue:
    level: str  # "error" | "warning"
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.path}: {self.message}"


@dataclass
class Concept:
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or self.path.stem.replace("-", " ").capitalize())

    @property
    def description(self) -> str:
        return str(self.meta.get("description") or "")

    def source_ids(self) -> list[str]:
        return [str(s.get("id")) for s in self.sources() if s.get("id")]

    def sources(self) -> list[dict[str, Any]]:
        sources = self.meta.get("sources") or []
        return [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    if not text.startswith("---\n") and text.strip() != "---":
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        raise OKFError("frontmatter is not closed with '---'")
    try:
        meta = load_yaml(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise OKFError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise OKFError("frontmatter must be a YAML mapping")
    rest = text[end + 4 :]
    return meta, rest.lstrip("\n")


def read(path: Path) -> Concept:
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if meta is None:
        raise OKFError("missing YAML frontmatter")
    return Concept(path, meta, body)


def render(meta: dict[str, Any], body: str) -> str:
    return f"---\n{dump_yaml(meta)}---\n\n{body.strip()}\n"


def write(concept: Concept) -> None:
    concept.path.parent.mkdir(parents=True, exist_ok=True)
    concept.path.write_text(render(concept.meta, concept.body), encoding="utf-8")


def concept_files(bundle: Path) -> list[Path]:
    return sorted(
        p
        for p in bundle.rglob("*.md")
        if p.name not in RESERVED and not any(part.startswith(".") for part in p.relative_to(bundle).parts)
    )


def read_all(bundle: Path) -> list[Concept]:
    """Every parseable concept; unparseable files are reported by validate_bundle, not here."""
    concepts = []
    for p in concept_files(bundle):
        try:
            concepts.append(read(p))
        except (OKFError, UnicodeDecodeError):
            continue
    return concepts


def concept_id(bundle: Path, path: Path) -> str:
    return path.relative_to(bundle).with_suffix("").as_posix()


def links(concept: Concept, bundle: Path) -> list[Path]:
    """Resolved targets of every local markdown link in the body (absolute or relative form)."""
    targets = []
    for raw in LINK_RE.findall(concept.body):
        target = raw.split("#", 1)[0]
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue
        base = bundle if target.startswith("/") else concept.path.parent
        targets.append((base / target.lstrip("/")).resolve())
    return targets


def resolve_resource(concept: Concept, bundle: Path, resource: str) -> Path | None:
    if re.match(r"^[a-z][a-z0-9+.-]*:", resource):
        return None
    base = bundle if resource.startswith("/") else concept.path.parent
    return (base / resource.lstrip("/")).resolve()


def _is_iso(value: Any) -> bool:
    try:
        return datetime.fromisoformat(str(value)).tzinfo is not None
    except ValueError:
        return False


def validate_concept(path: Path, bundle: Path) -> list[Issue]:
    rel = path.relative_to(bundle).as_posix()
    try:
        c = read(path)
    except (OKFError, UnicodeDecodeError) as exc:
        return [Issue("error", rel, str(exc))]
    issues: list[Issue] = []

    def err(msg: str) -> None:
        issues.append(Issue("error", rel, msg))

    def warn(msg: str) -> None:
        issues.append(Issue("warning", rel, msg))

    if not isinstance(c.meta.get("type"), str) or not c.meta["type"].strip():
        err("missing required 'type'")
    if c.meta.get("status", "stable") not in STATUSES:
        err(f"status must be one of {sorted(STATUSES)}")
    if c.meta.get("approval", "approved") not in APPROVAL_STATES:
        err(f"approval must be one of {list(APPROVAL_STATES)}")

    generated = c.meta.get("generated")
    if generated is not None:
        if not isinstance(generated, dict) or not generated.get("by"):
            err("'generated' needs 'by'")
        elif "at" in generated and not _is_iso(generated["at"]):
            err("'generated.at' must be an ISO 8601 datetime with offset")
    verified = c.meta.get("verified")
    for v in [verified] if isinstance(verified, dict) else verified or []:
        if not isinstance(v, dict) or not v.get("by") or not _is_iso(v.get("at")):
            err("each 'verified' entry needs 'by' and an ISO 8601 'at'")
    if "stale_after" in c.meta and not _is_iso(c.meta["stale_after"]):
        err("'stale_after' must be an ISO 8601 datetime with offset")

    raw_sources = c.meta.get("sources")
    if raw_sources is not None and not isinstance(raw_sources, list):
        err("'sources' must be a list")
    ids: list[str] = []
    for s in c.sources():
        if not s.get("resource"):
            err("every 'sources' entry needs 'resource'")
            continue
        if s.get("id"):
            ids.append(str(s["id"]))
        target = resolve_resource(c, bundle, str(s["resource"]))
        if target is not None and not target.exists() and "/" in str(s["resource"]):
            warn(f"source resource not found: {s['resource']}")
    if len(ids) != len(set(ids)):
        err("duplicate 'sources' ids")
    for label in sorted(set(FOOTNOTE_REF_RE.findall(c.body)) | set(FOOTNOTE_DEF_RE.findall(c.body))):
        if label not in ids:
            err(f"footnote [^{label}] has no matching 'sources' id")

    parts = path.relative_to(bundle).parts
    if len(parts) != 2:
        warn("BrainForge profile expects knowledge/<group>/<slug>.md")
    if not SLUG_RE.match(path.stem):
        warn("slug should be kebab-case")
    for target in links(c, bundle):
        if target.suffix == ".md" and not target.exists():
            warn(f"broken link: {target.relative_to(bundle.resolve()) if target.is_relative_to(bundle.resolve()) else target}")
    return issues


def validate_bundle(bundle: Path) -> list[Issue]:
    issues: list[Issue] = []
    for index in sorted(bundle.rglob("index.md")):
        rel = index.relative_to(bundle).as_posix()
        try:
            meta, _ = split_frontmatter(index.read_text(encoding="utf-8"))
        except OKFError as exc:
            issues.append(Issue("error", rel, str(exc)))
            continue
        if meta is not None and (index.parent != bundle or set(meta) - {"okf_version"}):
            issues.append(Issue("error", rel, "index.md may only carry 'okf_version' frontmatter, at the root"))
    for log in sorted(bundle.rglob("log.md")):
        _, body = split_frontmatter(log.read_text(encoding="utf-8"))
        for line in body.splitlines():
            if line.startswith("## ") and not DATE_HEADING_RE.match(line.strip()):
                issues.append(Issue("error", log.relative_to(bundle).as_posix(), f"log heading not ISO date: {line}"))
    for path in concept_files(bundle):
        issues.extend(validate_concept(path, bundle))
    return issues


def graph_issues(bundle: Path) -> list[Issue]:
    """Knowledge-graph health, as warnings: concepts nothing links to or from (source pages, which link to
    everything they produced, don't count), and concepts sharing a title, likely the same thing twice."""
    concepts = [c for c in read_all(bundle)
                if c.path.parent.name != "sources" and c.meta.get("approval") != "discarded"]
    paths = {c.path.resolve() for c in concepts}
    linked: set[Path] = set()
    for c in concepts:
        if targets := {t for t in links(c, bundle) if t in paths} - {c.path.resolve()}:
            linked |= targets | {c.path.resolve()}
    issues = [Issue("warning", c.path.relative_to(bundle).as_posix(), "isolated: no links to or from other concepts")
              for c in concepts if c.path.resolve() not in linked]
    by_title: dict[str, list[Concept]] = {}
    for c in concepts:
        by_title.setdefault(" ".join(c.title.lower().split()), []).append(c)
    for same in (group for group in by_title.values() if len(group) > 1):
        for c in same:
            others = ", ".join(o.path.relative_to(bundle).as_posix() for o in same if o is not c)
            issues.append(Issue("warning", c.path.relative_to(bundle).as_posix(),
                                f"same title as {others}: possibly the same thing twice"))
    return issues


def write_indexes(bundle: Path) -> list[Path]:
    """Regenerate every index.md deterministically. Returns the files that changed."""
    changed = []
    dirs = sorted({bundle} | {p.parent for p in concept_files(bundle)} | {d for d in bundle.iterdir() if d.is_dir() and not d.name.startswith(".")})
    for d in dirs:
        lines: list[str] = []
        subdirs = sorted(s for s in d.iterdir() if s.is_dir() and not s.name.startswith("."))
        if subdirs:
            lines.append("# Subdirectories\n")
            for s in subdirs:
                count = len(concept_files(s))
                desc = GROUP_DESCRIPTIONS.get(s.name, f"{count} concepts.")
                lines.append(f"* [{s.name}]({s.name}/index.md) - {desc}")
            lines.append("")
        concepts = []
        for p in sorted(d.glob("*.md")):
            if p.name in RESERVED:
                continue
            try:
                c = read(p)
            except OKFError:
                continue
            concepts.append(c)
        if concepts:
            lines.append(f"# {d.name.capitalize() if d != bundle else 'Concepts'}\n")
            for c in sorted(concepts, key=lambda c: (c.title.lower(), c.path.name)):
                desc = f" - {c.description}" if c.description else ""
                lines.append(f"* [{c.title}]({c.path.name}){desc}")
            lines.append("")
        if not lines:
            lines = ["# Contents\n", "No concepts yet.", ""]
        text = "\n".join(lines)
        if d == bundle:
            text = f"---\nokf_version: \"{OKF_VERSION}\"\n---\n\n{text}"
        index = d / "index.md"
        if not index.exists() or index.read_text(encoding="utf-8") != text:
            index.write_text(text, encoding="utf-8")
            changed.append(index)
    return changed


def append_log(bundle: Path, entry: str, date: str | None = None) -> None:
    """Add an entry under today's ISO date heading (newest first)."""
    date = date or datetime.now(UTC).date().isoformat()
    log = bundle / "log.md"
    text = log.read_text(encoding="utf-8") if log.exists() else "# Brain log\n"
    lines = text.rstrip("\n").splitlines()
    heading = f"## {date}"
    if heading in lines:
        at = lines.index(heading) + 1
        lines[at:at] = [f"* {entry}"]
    else:
        first = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        lines[first:first] = [heading, f"* {entry}", ""]
        if first > 0 and lines[first - 1].strip():
            lines.insert(first, "")
    log.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def search(
    bundle: Path,
    text: str | None = None,
    *,
    type: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    approval: str | None = None,
    links_to: str | None = None,
    linked_from: str | None = None,
) -> list[tuple[int, Concept]]:
    """Deterministic search. Returns (score, concept), best first. Discarded concepts only on request."""
    concepts = read_all(bundle)

    def target(ref: str) -> Path:
        ref = ref.removeprefix("knowledge/").removesuffix(".md")
        return (bundle / f"{ref}.md").resolve()

    allowed: set[Path] | None = None
    if linked_from:
        src = target(linked_from)
        allowed = set(links(read(src), bundle)) if src.exists() else set()
    terms = [t.lower() for t in (text or "").split()]
    results = []
    for c in concepts:
        if type and str(c.meta.get("type", "")).lower() != type.lower():
            continue
        if tag and tag not in (c.meta.get("tags") or []):
            continue
        if status and c.meta.get("status", "stable") != status:
            continue
        state = c.meta.get("approval") or "approved"
        if (approval and state != approval) or (not approval and state == "discarded"):
            continue
        if links_to and target(links_to) not in links(c, bundle):
            continue
        if allowed is not None and c.path.resolve() not in allowed:
            continue
        score = 1
        if terms:
            head = f"{concept_id(bundle, c.path)} {c.title} {c.description}".lower()
            full = f"{head} {c.body}".lower()
            if not all(t in full for t in terms):
                continue
            score = sum(3 * head.count(t) + full.count(t) for t in terms)
        results.append((score, c))
    results.sort(key=lambda r: (-r[0], concept_id(bundle, r[1].path)))
    return results
