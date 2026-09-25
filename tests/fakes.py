"""Offline fakes so the loop can be tested without API keys."""
from __future__ import annotations

from typing import Any, Callable

from typesafe_sdk import SystemOneResponse


def response(answers: dict[str, dict]) -> SystemOneResponse:
    return SystemOneResponse.model_validate(
        {"model": "fake-jev", "usage": {"input_tokens": 0, "output_tokens": 0}, "answers": answers})


def choice(value: str, confidence: float = 0.9) -> dict:
    return {"type": "choice", "choice": value, "confidence": confidence, "probabilities": {value: confidence}}


def noul(value: float) -> dict:
    return {"type": "noul", "noul": value}


def score(value: int, levels: int = 4, confidence: float = 0.9) -> dict:
    return {"type": "score", "score": float(value), "confidence": confidence,
            "legend": {i: f"level {i}" for i in range(levels)},
            "probabilities": {i: (1.0 if i == value else 0.0) for i in range(levels)}}


def sensible_next_action(state: dict) -> dict:
    """A reasonable policy, standing in for Jev's judgement in tests."""
    if not state["has_plan"]:
        return choice("plan")
    if not state["files_inspected"]:
        return choice("search")
    if state["code_changed_since_last_test"]:
        return choice("test")
    last = state["last_test"]
    if last and last["passed"]:
        return choice("verify")
    return choice("edit")


class FakeJev:
    def __init__(self, next_action: Callable[[dict], dict] = sensible_next_action,
                 destructive: float = 0.01, in_scope: float = 0.95,
                 cause: str = "real_bug", completion: int = 3):
        self.next_action = next_action
        self.destructive, self.in_scope = destructive, in_scope
        self.cause, self.completion = cause, completion
        self.calls: list[str] = []

    def system_one(self, state: Any, questions: dict, **_: Any) -> SystemOneResponse:
        keys = set(questions)
        self.calls.append(",".join(sorted(keys)))
        if keys == {"action"}:
            return response({"action": self.next_action(state)})
        if keys == {"destructive", "in_scope"}:
            return response({"destructive": noul(self.destructive), "in_scope": noul(self.in_scope)})
        if keys == {"cause"}:
            return response({"cause": choice(self.cause)})
        if keys == {"done"}:
            return response({"done": score(self.completion)})
        raise AssertionError(f"unexpected questions {keys}")


class FakeOpus:
    def __init__(self, edit_batches: list[list[dict]], files: list[str] | None = None):
        self.edit_batches = list(edit_batches)
        self.files = files or ["calc.py", "test_calc.py"]
        self.calls: list[str] = []

    def plan(self, state_json: str, tree: list[str]) -> str:
        self.calls.append("plan")
        return "1. Read calc.py and its tests\n2. Fix the failing behaviour\n3. Run tests"

    def choose_files(self, state_json: str, tree: list[str], limit: int) -> list[str]:
        self.calls.append("choose_files")
        return self.files[:limit]

    def propose_edits(self, state_json: str, files: dict[str, str]) -> list[dict]:
        self.calls.append("propose_edits")
        return self.edit_batches.pop(0) if self.edit_batches else []

    def summarize(self, state_json: str, diff: str) -> str:
        return "fake summary"
