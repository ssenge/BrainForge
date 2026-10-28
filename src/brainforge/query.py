"""Question answering over the knowledge bundle, read-only."""

from __future__ import annotations

import re

from brainforge import agents, gitops, review
from brainforge.brain import AgentConfig, Brain, Layer, blob_hash, instruction_layers, render_layers

CITATION_RE = re.compile(r"\((?:\./)?(knowledge/[^)\s#]+\.md)(?:#[^)]*)?\)|`(knowledge/[^`\s]+\.md)`")


def query(brain: Brain, question: str, cfg: AgentConfig, *, instruction: str | None = None,
          audience: str | None = None) -> dict:
    extra = [Layer("Invocation (--instruction)", f"cli@{blob_hash(instruction)}", instruction)] if instruction else []
    layers = instruction_layers(brain, "query", extra)
    prompt = f"""You are the BrainForge query agent. Your working directory is the root of a brain repository.

{render_layers(layers)}

# Question

{question}
"""
    if audience:  # answer from a copy that holds only what this audience may see
        with review.view(brain, audience) as (root, allowed):
            answer = agents.run(cfg, prompt, root, write=False).strip()
    else:
        allowed = None
        before = gitops.status(brain.root)
        answer = agents.run(cfg, prompt, brain.root, write=False).strip()
        if gitops.status(brain.root) != before:  # read-only tools should make this impossible; git tells us
            gitops.rollback(brain.root, {p for c, p in before if c == "??"})
            raise agents.AgentError("query agent modified files; changes were reverted")
    cited = sorted({a or b for a, b in CITATION_RE.findall(answer)})
    missing = [c for c in cited if not (brain.root / c).is_file() or (allowed is not None and c not in allowed)]
    return {"question": question, "answer": answer, "citations": cited, "unknown_citations": missing}
