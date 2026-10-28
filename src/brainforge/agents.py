"""Agent adapters: run a coding-agent CLI headless in the brain root.

BrainForge never handles credentials; each CLI authenticates itself (subscription login or API key).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from brainforge.brain import AgentConfig, Brain, BrainError

WRITE_TOOLS = "Read,Write,Edit,Glob,Grep"
READ_TOOLS = "Read,Glob,Grep"


class AgentError(Exception):
    pass


def resolve(brain: Brain, adapter: str | None = None, model: str | None = None) -> AgentConfig:
    """Flag > environment > brain.yaml. `agent.allowed` limits where this brain's data may go."""
    base = brain.manifest.agent
    adapter = adapter or os.environ.get("BRAINFORGE_AGENT") or base.adapter
    model = model or os.environ.get("BRAINFORGE_MODEL") or (base.model if adapter == base.adapter else None)
    if adapter not in ("claude", "codex", "command"):
        raise BrainError(f"unknown agent adapter '{adapter}' (claude, codex, command)")
    if base.allowed is not None and adapter not in base.allowed:
        raise BrainError(f"agent '{adapter}' is not in agent.allowed {base.allowed} of brain.yaml")
    command = os.environ.get("BRAINFORGE_AGENT_COMMAND") or base.command
    if adapter == "command" and not command:
        raise BrainError("adapter 'command' needs agent.command in brain.yaml or BRAINFORGE_AGENT_COMMAND")
    return AgentConfig(adapter=adapter, model=model, command=command, allowed=base.allowed)  # type: ignore[arg-type]


def actor(cfg: AgentConfig) -> str:
    """OKF actor string for `generated.by`."""
    return f"brainforge/{cfg.model or cfg.adapter}"


def build_command(cfg: AgentConfig, cwd: Path, write: bool, last_message: Path) -> list[str]:
    if cfg.adapter == "claude":
        cmd = ["claude", "-p", "--tools", WRITE_TOOLS if write else READ_TOOLS,
               "--output-format", "text", "--no-session-persistence"]
        if write:
            cmd += ["--permission-mode", "acceptEdits"]
        # --bare isolates from the user's hooks/plugins/CLAUDE.md but only accepts an API key;
        # with a subscription login we fall back to skipping settings files and MCP servers.
        cmd += ["--bare"] if os.environ.get("ANTHROPIC_API_KEY") else ["--setting-sources", "", "--strict-mcp-config"]
    elif cfg.adapter == "codex":
        cmd = ["codex", "exec", "-C", str(cwd), "--sandbox", "workspace-write" if write else "read-only",
               "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "--ephemeral",
               "--output-last-message", str(last_message)]
    else:
        cmd = shlex.split(cfg.command or "")
    if cfg.model and cfg.adapter != "command":
        cmd += ["--model" if cfg.adapter == "claude" else "-m", cfg.model]
    if cfg.adapter == "codex":
        cmd.append("-")  # prompt from stdin
    return cmd


def run(cfg: AgentConfig, prompt: str, cwd: Path, *, write: bool, timeout: int | None = 1800) -> str:
    """Run the agent with the prompt on stdin; return its final message."""
    with tempfile.TemporaryDirectory(prefix="brainforge-") as tmp:
        last = Path(tmp) / "last-message.txt"
        cmd = build_command(cfg, cwd, write, last)
        env = {**os.environ, "BRAINFORGE_MODE": "write" if write else "read"}
        try:
            proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True,
                                  timeout=timeout, env=env)
        except FileNotFoundError:
            raise AgentError(f"agent CLI not found: {cmd[0]} (install it or choose another adapter)") from None
        except subprocess.TimeoutExpired:
            raise AgentError(f"agent timed out after {timeout}s") from None
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-2000:]
            raise AgentError(f"agent exited with {proc.returncode}: {tail}")
        return last.read_text(encoding="utf-8") if last.exists() else proc.stdout
