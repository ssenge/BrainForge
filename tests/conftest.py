import sys
from pathlib import Path

import pytest

from brainforge import brain as brain_mod
from brainforge import cli, gitops

FAKE_AGENT = Path(__file__).parent / "fake_agent.py"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Hermetic git and agent: no user git config, the fake agent instead of a real CLI."""
    for key, value in {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "BRAINFORGE_AGENT": "command",
        "BRAINFORGE_AGENT_COMMAND": f"{sys.executable} {FAKE_AGENT}",
        "FAKE_AGENT_LOG": str(tmp_path / "prompts.log"),
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("BRAINFORGE_MODEL", "BRAINFORGE_BRAIN", "FAKE_AGENT", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def brain(tmp_path):
    return brain_mod.init(tmp_path / "brain", name="Test Brain", brain_id="test")


@pytest.fixture
def prompts(tmp_path):
    log = tmp_path / "prompts.log"
    return lambda: log.read_text(encoding="utf-8") if log.exists() else ""


@pytest.fixture
def run_cli(capsys):
    def run(*args: str) -> tuple[int, str, str]:
        code = cli.run([str(a) for a in args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return run


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commits(root: Path) -> list[str]:
    return gitops.git(root, "log", "--format=%s").splitlines()


def make_pdf(path: Path, text: str) -> Path:
    """A minimal one-page PDF with real extractable text, no PDF library needed."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(out)
    return path
