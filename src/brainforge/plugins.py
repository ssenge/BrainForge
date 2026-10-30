"""Plugins: optional, domain-specific additions to a brain, copied into `plugins/<name>/` of the brain.

A plugin is a folder with `plugin.yaml` and any of:
- `types` in plugin.yaml: concept types with their group and frontmatter fields. They are explained to the
  ingestion agent and checked deterministically after every run; missing expected fields show up as gaps;
  a type with a `deadline` field is overdue once that date passes without one of its `done` values;
- `instructions/<activity>.md`: an instruction layer after the framework defaults, before the brain's own;
- `procedures/*.md`: procedures, run like the brain's own (a brain procedure with the same name wins);
- `scripts/`: commands for `brainforge plugin run`. They never run on their own.

The folder is the installation: it is versioned with the brain, so CI needs nothing extra, and deleting it
removes the plugin. Plugins can't override the framework policy, and agents can't change them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from brainforge import gitops, okf
from brainforge.brain import Brain, BrainError, Layer, blob_hash

NAME_RE = r"^[a-z0-9]+(-[a-z0-9]+)*$"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GIT_URL_RE = re.compile(r"^(https?://|ssh://|git@|file://)")


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""
    values: list[str] = []  # allowed values, compared case-insensitively
    format: Literal["date", "ids"] | None = None  # YYYY-MM-DD, or a list of concept ids (milestones/go-live)
    expected: bool = False  # missing → a gap that approvers see


class TypeDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    group: str = Field(pattern=NAME_RE)
    description: str = ""
    fields: dict[str, FieldDef] = {}
    stale_after_days: int | None = Field(default=None, gt=0)  # review period: OKF `stale_after` on every change
    deadline: str | None = None  # a date field: the item is overdue once it passes...
    done: dict[str, list[str]] = {}  # ...unless one of these fields has one of these values, e.g. {state: [done]}
    baseline: str | None = None  # a date field with the first planned date: how far the deadline slipped
    waits_for: str | None = None  # an ids field: the items this one waits for; a late one puts it at risk

    @model_validator(mode="after")
    def _fields_exist(self) -> TypeDef:
        for key, fmt in (("deadline", "date"), ("baseline", "date"), ("waits_for", "ids")):
            name = getattr(self, key)
            if name and (name not in self.fields or self.fields[name].format != fmt):
                raise ValueError(f"{key} '{name}' must name a field with format: {fmt}")
        return self


class Plugin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    version: str | int = 1
    description: str = ""
    types: list[TypeDef] = []
    path: Path = Field(default=Path(), exclude=True)

    def files(self, sub: str, pattern: str = "*") -> list[Path]:
        return sorted(p for p in (self.path / sub).glob(pattern) if p.is_file() and not p.name.startswith("."))


def _read(path: Path) -> Plugin:
    try:
        data = yaml.safe_load((path / "plugin.yaml").read_text(encoding="utf-8")) or {}
        return Plugin.model_validate({**data, "path": path})
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise BrainError(f"invalid plugin {path.name}: {exc}") from exc


def load(path: Path) -> Plugin:
    plugin = _read(path)
    if plugin.name != path.name:
        raise BrainError(f"plugin folder {path.name} holds plugin '{plugin.name}'; the names must match")
    return plugin


def installed(brain: Brain) -> list[Plugin]:
    root = brain.root / "plugins"
    return [load(d) for d in sorted(root.iterdir()) if (d / "plugin.yaml").is_file()] if root.is_dir() else []


def bundled_dir() -> Path | None:
    """Plugins shipped with BrainForge: inside the package when installed, `plugins/` in a source checkout."""
    here = Path(__file__).resolve()
    return next((d for d in (here.parent / "bundled_plugins", here.parents[2] / "plugins") if d.is_dir()), None)


def available() -> list[Plugin]:
    root = bundled_dir()
    return [load(d) for d in sorted(root.iterdir()) if (d / "plugin.yaml").is_file()] if root else []


def add(brain: Brain, source: str) -> Plugin:
    """Copy a plugin into the brain, replacing an older copy. `source`: a bundled name, a plugin folder, or a git
    URL, with `#<folder>` for a plugin inside a larger repository (`https://github.com/o/r.git#plugins/pm`)."""
    if GIT_URL_RE.match(source):
        url, _, sub = source.partition("#")
        with tempfile.TemporaryDirectory() as tmp:
            try:  # ponytail: default branch only; add `@ref` when someone needs to pin a plugin version
                gitops.git(Path(tmp), "clone", "-q", "--depth", "1", url, "repo")
            except gitops.GitError as exc:
                raise BrainError(f"cannot clone {url}: {exc}") from exc
            folder = Path(tmp, "repo", sub)
            if not (folder / "plugin.yaml").is_file():
                raise BrainError(f"no plugin.yaml in {source}; name the plugin's folder after '#'")
            return _copy(brain, folder)
    folder = Path(source).expanduser()
    if not (folder / "plugin.yaml").is_file():
        folder = next((p.path for p in available() if p.name == source), None)  # type: ignore[assignment]
        if folder is None:
            names = ", ".join(p.name for p in available()) or "none"
            raise BrainError(f"no plugin '{source}': give a bundled name ({names}), a plugin folder or a git URL")
    return _copy(brain, folder)


def _copy(brain: Brain, folder: Path) -> Plugin:
    plugin = _read(folder)  # the source folder may have any name; the copy is named after the plugin
    target = brain.root / "plugins" / plugin.name
    if target.resolve() != folder.resolve():
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(folder, target, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    return load(target)


def run_script(brain: Brain, plugin: str, script: str, args: list[str]) -> int:
    """Run a plugin script in the brain root. Python scripts get BrainForge's interpreter, so they can
    `import brainforge`; anything else must be executable."""
    found = next((p for p in installed(brain) if p.name == plugin), None)
    if found is None:
        raise BrainError(f"plugin '{plugin}' is not installed (brainforge plugin add {plugin})")
    path = next((p for p in found.files("scripts") if script in (p.name, p.stem)), None)
    if path is None:
        names = ", ".join(p.stem for p in found.files("scripts")) or "none"
        raise BrainError(f"plugin '{plugin}' has no script '{script}' (scripts: {names})")
    cmd = [sys.executable, str(path)] if path.suffix == ".py" else [str(path)]
    return subprocess.run([*cmd, *args], cwd=brain.root).returncode


# --- types ------------------------------------------------------------------------------------------

def types(brain: Brain) -> dict[str, TypeDef]:
    found: dict[str, TypeDef] = {}
    for plugin in installed(brain):
        for t in plugin.types:
            if t.type in found:
                raise BrainError(f"type '{t.type}' is defined by two plugins")
            found[t.type] = t
    return found


def gaps(brain: Brain, c: okf.Concept, known: dict[str, TypeDef] | None = None) -> list[str]:
    """Expected fields the concept doesn't have yet."""
    t = (types(brain) if known is None else known).get(str(c.meta.get("type")))
    return [name for name, f in t.fields.items() if f.expected and c.meta.get(name) in (None, "", [])] if t else []


def renew(brain: Brain, c: okf.Concept, known: dict[str, TypeDef] | None = None) -> bool:
    """Set `stale_after` to now + the type's review period. False if the type has none."""
    t = (types(brain) if known is None else known).get(str(c.meta.get("type")))
    if not (t and t.stale_after_days):
        return False
    c.meta["stale_after"] = okf.now_iso(datetime.now(UTC) + timedelta(days=t.stale_after_days))
    return True


def finished(c: okf.Concept, t: TypeDef | None) -> bool:
    """One of the type's `done` values is set."""
    return t is not None and any(str(c.meta.get(k, "")).lower() in [v.lower() for v in vs] for k, vs in t.done.items())


def a_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def deadline(brain: Brain, c: okf.Concept, known: dict[str, TypeDef] | None = None) -> date | None:
    """The date an unfinished item is due, or None: no deadline, no date yet, or done."""
    t = (types(brain) if known is None else known).get(str(c.meta.get("type")))
    if not (t and t.deadline) or finished(c, t):
        return None
    return a_date(c.meta.get(t.deadline))  # missing or not a date: a gap or a check error


def ids(value: object) -> list[str]:
    """Concept ids as written, normalized: `../milestones/go-live.md` and `knowledge/milestones/go-live` are
    `milestones/go-live`."""
    out = []
    for v in value if isinstance(value, list) else [value] if value not in (None, "") else []:
        s = str(v).strip().split("#")[0].removesuffix(".md")
        while s.startswith(("./", "../")):
            s = s.split("/", 1)[1]
        out.append(s.removeprefix("knowledge/"))
    return out


def field_error(name: str, f: FieldDef, value: object) -> str | None:
    if f.values and str(value).lower() not in [v.lower() for v in f.values]:
        return f"{name} must be one of {', '.join(f.values)}, not '{value}'"
    if f.format == "date" and not (isinstance(value, date) or DATE_RE.match(str(value))):
        return f"{name} must be a date (YYYY-MM-DD), not '{value}'"
    if f.format == "ids" and not (isinstance(value, str) or isinstance(value, list)
                                  and all(isinstance(v, str) for v in value)):
        return f"{name} must be a list of concept ids such as milestones/go-live, not '{value}'"
    return None


def check(brain: Brain, path: Path, known: dict[str, TypeDef] | None = None) -> list[okf.Issue]:
    """Field values and groups of plugin types. Errors are what ingestion rolls back on."""
    rel = brain.rel(path)
    try:
        c = okf.read(path)
    except okf.OKFError:
        return []  # reported by OKF validation
    t = (types(brain) if known is None else known).get(str(c.meta.get("type")))
    if t is None:
        return []
    issues = []
    if path.parent.name != t.group:
        issues.append(okf.Issue("warning", rel, f"type '{t.type}' belongs in {t.group}/"))
    for name, f in t.fields.items():
        value = c.meta.get(name)
        if value in (None, "", []):
            continue
        if error := field_error(name, f, value):
            issues.append(okf.Issue("error", rel, error))
        elif f.format == "ids" and (gone := [i for i in ids(value) if not (brain.knowledge / f"{i}.md").is_file()]):
            issues.append(okf.Issue("warning", rel, f"{name}: no such concept: {', '.join(gone)}"))
    return issues


def layers(brain: Brain, activity: str) -> list[Layer]:
    """Instruction layers from plugins: for ingestion, the types they define; then their own instructions."""
    out = []
    for plugin in installed(brain):
        rel = f"plugins/{plugin.name}"
        if activity == "ingestion" and plugin.types:
            text = _types_text(plugin)
            out.append(Layer(f"Plugin {plugin.name}: types", f"{rel}/plugin.yaml@{blob_hash(text)}", text))
        path = plugin.path / "instructions" / f"{activity}.md"
        if path.is_file() and (text := path.read_text(encoding="utf-8")).strip():
            out.append(Layer(f"Plugin {plugin.name} ({rel}/instructions/{activity}.md)",
                             f"{rel}/instructions/{activity}.md@{blob_hash(text)}", text))
    return out


def _types_text(plugin: Plugin) -> str:
    lines = [f"# Types from the {plugin.name} plugin", "",
             "Use these groups and types in addition to the default ones. Set a frontmatter field only when the "
             "document states it, with exactly one of the listed values, or a date as YYYY-MM-DD.", ""]
    for t in plugin.types:
        lines.append(f"- `{t.group}/`: `{t.type}`" + (f". {t.description}" if t.description else ""))
        for name, f in t.fields.items():
            what = [f.description] if f.description else []
            what += [f"one of {', '.join(f.values)}"] if f.values else []
            what += ["a date, YYYY-MM-DD"] if f.format == "date" else []
            what += ["a list of concept ids, e.g. [dependencies/vendor-api]"] if f.format == "ids" else []
            lines.append(f"  - `{name}`" + (f": {'; '.join(what)}" if what else ""))
        if t.deadline:
            done = " or ".join(f"`{k}: {v}`" for k, vs in t.done.items() for v in vs)
            lines.append(f"  - `{t.deadline}` is the deadline. When a document reports the item finished, set "
                         f"{done or 'the matching value'} and cite that document; \"in progress\" is not finished.")
        if t.baseline:
            lines.append(f"  - `{t.baseline}` is the first planned date: set it once, and keep it when "
                         f"`{t.deadline or 'the date'}` moves.")
        if t.waits_for:
            lines.append(f"  - `{t.waits_for}` lists the items this one waits for (\"depends on\", \"needs … "
                         "first\", \"blocked by\"), by concept id; create the item it waits for if it doesn't exist.")
    return "\n".join(lines) + "\n"
