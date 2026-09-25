"""The reasoning engine. Opus 5.5 plans, picks files to read, and writes edits.

It never runs commands and never decides when the task is finished;
those calls belong to the decision layer and the loop.
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

SYSTEM = (
    "You are the reasoning engine inside a controlled coding agent. "
    "A separate decision layer chooses the next step and a harness executes everything. "
    "Do only the step you are asked for, keep changes minimal, follow the repository's existing "
    "conventions, and reply in exactly the format requested with no extra commentary."
)


class MessagesClient(Protocol):
    messages: Any


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = min((i for i in (cleaned.find("["), cleaned.find("{")) if i != -1), default=-1)
    if start == -1:
        raise ValueError(f"No JSON found in model output: {text[:200]!r}")
    return json.loads(cleaned[start:])


class OpusEngine:
    def __init__(self, client: MessagesClient | None = None, model: str = "claude-opus-5-5"):
        if client is None:
            from anthropic import Anthropic
            client = Anthropic()
        self.client = client
        self.model = model

    def _complete(self, prompt: str, max_tokens: int = 4000) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(getattr(b, "text", "") for b in msg.content)

    def plan(self, state_json: str, tree: list[str]) -> str:
        return self._complete(
            "Write a short, numbered implementation plan (max 8 steps) for the request.\n\n"
            f"Engineering state:\n{state_json}\n\nRepository files:\n" + "\n".join(tree[:400])
        )

    def choose_files(self, state_json: str, tree: list[str], limit: int) -> list[str]:
        out = self._complete(
            f"Which files must be read before making the change? Return a JSON array of at most {limit} "
            "relative paths taken from the list below, most important first.\n\n"
            f"Engineering state:\n{state_json}\n\nRepository files:\n" + "\n".join(tree[:400]),
            max_tokens=800,
        )
        paths = _extract_json(out)
        return [p for p in paths if isinstance(p, str)][:limit]

    def propose_edits(self, state_json: str, files: dict[str, str]) -> list[dict[str, str]]:
        listing = "\n\n".join(f"=== {path} ===\n{body}" for path, body in files.items())
        out = self._complete(
            "Propose the minimal edits for the next step of the plan. Return a JSON array of objects "
            '{"path": str, "old": str, "new": str, "reason": str}. "old" must be an exact, unique '
            'substring of the current file. To create a new file use "old": "".\n\n'
            f"Engineering state:\n{state_json}\n\nFiles:\n{listing}",
            max_tokens=8000,
        )
        edits = _extract_json(out)
        return [e for e in edits if isinstance(e, dict) and {"path", "old", "new"} <= e.keys()]

    def summarize(self, state_json: str, diff: str) -> str:
        return self._complete(
            "Summarise for a code reviewer, in under 150 words: what changed, why, and anything "
            "still open.\n\n"
            f"Engineering state:\n{state_json}\n\nDiff:\n{diff[-12000:]}",
            max_tokens=600,
        )
