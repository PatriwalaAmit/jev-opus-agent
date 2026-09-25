"""Engineering state: a record of what happened, not a transcript of what was said."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TestResult:
    __test__ = False             # keep pytest from collecting this class
    passed: bool
    exit_code: int
    summary: str                 # tail of the output
    cause: str | None = None     # Jev triage label when failing


@dataclass
class EngineeringState:
    request: str
    test_command: str
    plan: str | None = None
    files_inspected: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    last_action: str | None = None
    last_test: TestResult | None = None
    dirty: bool = False          # code changed since the last test run
    rejected_approaches: list[str] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)
    steps_taken: int = 0
    fix_attempts: int = 0
    verify_attempts: int = 0
    flaky_retries_used: int = 0
    empty_edit_steps: int = 0    # consecutive edit steps that changed nothing
    action_history: list[str] = field(default_factory=list)

    def for_jev(self) -> dict[str, Any]:
        """Compact view for decision questions. Only facts that matter for the next call."""
        return {
            "request": self.request,
            "has_plan": self.plan is not None,
            "plan": (self.plan or "")[:1500],
            "files_inspected": self.files_inspected,
            "files_changed": self.files_changed,
            "code_changed_since_last_test": self.dirty,
            "last_action": self.last_action,
            "last_test": None if self.last_test is None else {
                "passed": self.last_test.passed,
                "cause": self.last_test.cause,
                "output_tail": self.last_test.summary[-1500:],
            },
            "open_issues": self.open_issues[-5:],
            "rejected_approaches": self.rejected_approaches[-5:],
            "recent_actions": self.action_history[-8:],
            "fix_attempts": self.fix_attempts,
        }

    def for_llm(self) -> str:
        return json.dumps(self.for_jev(), indent=2)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)
