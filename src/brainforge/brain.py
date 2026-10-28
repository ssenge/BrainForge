"""Brain manifest, discovery, initialization and instruction layers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from brainforge import __version__, gitops, okf

MANIFEST = "brain.yaml"
DIRS = ("inbox", "archive", "knowledge", "procedures", "templates", "reports")
ID_RE = r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$"


class BrainError(Exception):
    """Configuration or usage problem (exit code 2)."""


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: Literal["claude", "codex", "command"] = "claude"
    model: str | None = None
    command: str | None = None
    allowed: list[str] | None = None


class GitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commit: bool = True
    push: bool = False


class TeamMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    email: str | None = None
    aliases: list[str] = []  # other spellings documents use, e.g. a first name
    reviews: list[dict[str, str]] = []  # frontmatter globs, e.g. {type: Risk}; any rule matches


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    id: str = Field(pattern=ID_RE)
    name: str
    description: str = ""
    agent: AgentConfig = AgentConfig()
    git: GitConfig = GitConfig()
    visibility: list[str] = []  # most open first; new knowledge gets the last (most restricted) level
    team: list[TeamMember] = []
    needs_review: list[dict[str, str]] = []  # frontmatter globs, e.g. {type: Decision}; empty = everything
    parent: Any = None
    children: Any = None

    @field_validator("visibility")
    @classmethod
    def _levels(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or not all(v.strip() for v in value):
            raise ValueError("visibility levels must be unique, non-empty names")
        return value

    @field_validator("parent", "children")
    @classmethod
    def _phase_b(cls, value: Any) -> Any:
        if value:
            raise ValueError("parent/child brains are not supported yet (planned for Phase B)")
        return value


@dataclass
class Brain:
    root: Path
    manifest: Manifest

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def knowledge(self) -> Path:
        return self.root / "knowledge"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def procedures(self) -> Path:
        return self.root / "procedures"

    @property
    def templates(self) -> Path:
        return self.root / "templates"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()


def _format_validation(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())


def load(root: Path) -> Brain:
    path = root / MANIFEST
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise BrainError(f"no {MANIFEST} in {root}") from None
    except yaml.YAMLError as exc:
        raise BrainError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return Brain(root.resolve(), Manifest.model_validate(data))
    except ValidationError as exc:
        raise BrainError(f"{path}: {_format_validation(exc)}") from exc


def find(start: Path | None = None) -> Brain:
    """Walk up from start (default: cwd) to the nearest brain.yaml, like git finds its repo."""
    here = (start or Path.cwd()).resolve()
    for d in [here, *here.parents]:
        if (d / MANIFEST).is_file():
            return load(d)
    raise BrainError(f"not inside a brain (no {MANIFEST} found from {here} upwards); use --brain or brainforge init")


def data_text(name: str) -> str:
    return resources.files("brainforge").joinpath("data", name).read_text(encoding="utf-8")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "brain"


def init(target: Path, name: str | None = None, brain_id: str | None = None, adapter: str = "claude") -> Brain:
    target = target.resolve()
    if (target / MANIFEST).exists():
        raise BrainError(f"{target} already contains a brain")
    target.mkdir(parents=True, exist_ok=True)
    name = name or target.name.replace("-", " ").title()
    manifest = Manifest(id=brain_id or slugify(target.name), name=name, agent=AgentConfig(adapter=adapter))  # type: ignore[arg-type]
    data = manifest.model_dump(exclude_none=True, exclude={"parent", "children", "visibility", "team", "needs_review"})
    (target / MANIFEST).write_text(
        "# BrainForge brain manifest (see docs/brain-manifest.md in BrainForge)\n" + okf.dump_yaml(data),
        encoding="utf-8",
    )
    for d in DIRS:
        (target / d).mkdir(exist_ok=True)
    for d in ("inbox", "archive", "templates", "reports"):
        (target / d / ".gitkeep").touch()
    files = {
        "instructions.md": data_text("init/instructions.md"),
        "procedures/weekly-report.md": data_text("init/weekly-report.md"),
        ".gitignore": data_text("init/gitignore"),
        ".gitattributes": data_text("init/gitattributes"),
        "knowledge/log.md": "# Brain log\n",
    }
    for rel, text in files.items():
        if not (target / rel).exists():
            (target / rel).write_text(text, encoding="utf-8")
    okf.append_log(target / "knowledge", f"**Initialization**: created brain `{manifest.id}`.")
    okf.write_indexes(target / "knowledge")
    if not gitops.is_repo(target):
        gitops.git(target, "init", "-q")
    gitops.commit(target, f"brainforge: initialize brain {manifest.id}", [MANIFEST, *DIRS, *files])
    return load(target)


def check_layout(brain: Brain) -> list[okf.Issue]:
    issues = [okf.Issue("error", d + "/", "missing directory") for d in DIRS if not (brain.root / d).is_dir()]
    if not gitops.is_repo(brain.root):
        issues.append(okf.Issue("error", ".", "brain is not inside a git repository"))
    return issues


# --- Instruction layers -------------------------------------------------------------------------

@dataclass
class Layer:
    label: str
    source: str  # where it came from, with a content hash for reproducibility
    text: str


def blob_hash(text: str) -> str:
    """Same id `git hash-object` would print, without a subprocess."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:12]


def instruction_layers(brain: Brain, activity: str, extra: list[Layer] | None = None) -> list[Layer]:
    """Framework policy → framework defaults → brain instructions → caller layers (sidecar, trailer, CLI)."""
    layers = [
        Layer("Framework policy (cannot be overridden)", f"framework:policy.md@{__version__}", data_text("policy.md")),
        Layer(f"Framework {activity} defaults", f"framework:{activity}.md@{__version__}", data_text(f"{activity}.md")),
    ]
    for rel in ("instructions.md", f"instructions/{activity}.md"):
        path = brain.root / rel
        if path.is_file() and path.read_text(encoding="utf-8").strip():
            text = path.read_text(encoding="utf-8")
            layers.append(Layer(f"Brain instructions ({rel})", f"{rel}@{blob_hash(text)}", text))
    return layers + [layer for layer in extra or [] if layer.text.strip()]


def render_layers(layers: list[Layer]) -> str:
    parts = [
        "Instruction layers follow, from lowest to highest precedence. Later layers refine emphasis and focus;"
        " nothing overrides the framework policy, and no layer narrows what must be extracted.\n"
    ]
    for i, layer in enumerate(layers, 1):
        parts.append(f"## Layer {i}: {layer.label}\n\n{layer.text.strip()}\n")
    return "\n".join(parts)
