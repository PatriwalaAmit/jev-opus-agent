"""The decision layer. Every question here is small, typed and logged.

Jev decides; it never executes. The loop and the Workspace do the executing,
and hard rules in code always run before (and override) any probability.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from typesafe_sdk import Choice, Noul, Score

from .config import Config
from .state import EngineeringState

ACTIONS: dict[str, str] = {
    "plan": "There is no plan yet, or the facts show the current plan no longer fits",
    "search": "The agent must read code, config or docs before it can change anything safely",
    "edit": "The agent knows which files to change and how, or tests failed because of a real bug",
    "test": "Code has changed since the last test run",
    "verify": "The latest tests pass; check the result against the original request",
    "ask_human": "The task is blocked, ambiguous, looping, or risky enough that a person should decide",
}

FAILURE_CAUSES: dict[str, str] = {
    "real_bug": "The code under test behaves incorrectly",
    "test_is_wrong": "The test's expectation is outdated or incorrect for the requested change",
    "environment": "Missing dependency, service, config, file or environment variable",
    "flaky": "Timing, network or ordering issue unrelated to the code change",
}

COMPLETION_LEVELS: list[str] = [
    "Not addressed",
    "Partially addressed, key parts of the request are missing",
    "Addressed but unverified, or with open issues",
    "Fully addressed and verified by passing tests",
]


class SystemOneClient(Protocol):
    def system_one(self, state: Any, questions: Any, **kwargs: Any) -> Any: ...


@dataclass
class Decision:
    value: Any
    confidence: float | None = None
    probabilities: dict | None = None
    reason: str = ""


class AuditLog:
    """Append-only JSONL of every decision. This is your evidence and your tuning data."""

    def __init__(self, path: Path | None):
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **data: Any) -> None:
        if not self.path:
            return
        record = {"ts": time.time(), "kind": kind, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")


class Decider:
    def __init__(self, client: SystemOneClient, config: Config, log: AuditLog):
        self.client = client
        self.cfg = config
        self.log = log

    def _ask(self, name: str, state: Any, questions: dict) -> dict:
        resp = self.client.system_one(state=state, questions=questions, model=self.cfg.jev_model)
        answers = resp.answers
        self.log.write(
            "jev",
            question=name,
            answers={k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in answers.items()},
        )
        return answers

    # 1. What should happen next?
    def next_action(self, s: EngineeringState) -> Decision:
        a = self._ask("next_action", s.for_jev(), {
            "action": Choice(instructions="What should the coding agent do next?", criteria=ACTIONS),
        })["action"]
        if a.confidence is not None and a.confidence < self.cfg.min_action_confidence:
            return Decision("ask_human", a.confidence, a.probabilities,
                            f"low confidence ({a.confidence:.2f}) for '{a.choice}'")
        return Decision(a.choice, a.confidence, a.probabilities)

    # 2. Is this command safe to run? Hard rules first, then Jev for the grey area.
    def command_check(self, cmd: str, s: EngineeringState) -> Decision:
        lowered = " ".join(cmd.lower().split())
        for bad in self.cfg.never_run:
            if bad in lowered:
                return Decision(False, reason=f"blocked by hard rule: '{bad}'")
        ans = self._ask("command_check", {**s.for_jev(), "proposed_command": cmd}, {
            "destructive": Noul(instructions="The proposed command could delete data, rewrite git history, "
                                             "publish anything, or change shared infrastructure"),
            "in_scope": Noul(instructions="The proposed command is needed for the current task"),
        })
        destructive, in_scope = ans["destructive"].noul, ans["in_scope"].noul
        ok = destructive < self.cfg.max_destructive and in_scope > self.cfg.min_in_scope
        return Decision(ok, reason=f"destructive={destructive:.2f} in_scope={in_scope:.2f}")

    # 3. Why did the tests fail? Decides whether the big model needs to be involved at all.
    def triage(self, test_output: str) -> Decision:
        a = self._ask("triage", test_output[-6000:], {
            "cause": Choice(instructions="What most likely caused this test failure?", criteria=FAILURE_CAUSES),
        })["cause"]
        return Decision(a.choice, a.confidence, a.probabilities)

    # 4. Is the request actually done? Scored against the request, not just the test output.
    def completion(self, s: EngineeringState, diff: str) -> Decision:
        a = self._ask("completion", {**s.for_jev(), "changes": diff[-8000:]}, {
            "done": Score(instructions="How completely do the changes satisfy the original request?",
                          criteria=COMPLETION_LEVELS),
        })["done"]
        return Decision(a.score, a.confidence, a.probabilities)

    @staticmethod
    def max_completion_level() -> int:
        return len(COMPLETION_LEVELS) - 1
