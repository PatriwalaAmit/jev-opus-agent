"""The typed decision loop.

    Jev picks the next action -> hard rules may override it -> the loop executes it
    -> state is updated -> repeat until verified, blocked, or out of budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import Config
from .decisions import Decider
from .llm import OpusEngine
from .state import EngineeringState, TestResult
from .tools import EditError, Workspace

Approver = Callable[[str], bool]


@dataclass
class RunResult:
    status: str            # "done" | "needs_human" | "step_limit"
    reason: str
    summary: str
    state: EngineeringState
    diff: str


def enforce_hard_rules(action: str, s: EngineeringState) -> tuple[str, str | None]:
    """Deterministic guard rails. Returns (action, override_reason)."""
    if action == "ask_human":
        return action, None
    if s.plan is None and action != "plan":
        return "plan", "no plan yet"
    if action == "edit" and not s.files_inspected:
        return "search", "cannot edit before inspecting files"
    if action == "verify" and (s.dirty or s.last_test is None or not s.last_test.passed):
        return "test", "verify requires a passing test run on the current code"
    if action == "test" and not s.dirty and s.last_test is not None:
        if s.last_test.passed:
            return "verify", "code unchanged since a passing run"
        return ("edit" if s.files_inspected else "search"), "code unchanged since the failing run"
    return action, None


class Agent:
    def __init__(self, workspace: Workspace, engine: OpusEngine, decider: Decider,
                 config: Config, approve: Approver | None = None, echo: Callable[[str], None] = print):
        self.ws = workspace
        self.llm = engine
        self.decider = decider
        self.cfg = config
        self.approve = approve
        self.echo = echo

    # ---- main loop ---------------------------------------------------------
    def run(self, request: str, test_command: str) -> RunResult:
        s = EngineeringState(request=request, test_command=test_command)
        while s.steps_taken < self.cfg.max_steps:
            decision = self.decider.next_action(s)
            action, override = enforce_hard_rules(decision.value, s)
            note = f" (override: {override})" if override else ""
            conf = f" conf={decision.confidence:.2f}" if decision.confidence is not None else ""
            self.echo(f"[{s.steps_taken + 1:02d}] {action.upper()}{conf}{note}")
            self.decider.log.write("action", step=s.steps_taken, jev=decision.value,
                                   executed=action, override=override, reason=decision.reason)

            if action == "ask_human":
                return self._finish(s, "needs_human", decision.reason or "decision layer asked for a human")

            outcome = self.run_step(action, s)
            s.last_action = action
            s.action_history.append(action)
            s.steps_taken += 1
            if outcome:
                return self._finish(s, *outcome)

        return self._finish(s, "step_limit", f"stopped after {self.cfg.max_steps} steps")

    # ---- steps ---------------------------------------------------------------
    def run_step(self, action: str, s: EngineeringState) -> tuple[str, str] | None:
        handler = {
            "plan": self._plan, "search": self._search, "edit": self._edit,
            "test": self._test, "verify": self._verify,
        }.get(action)
        if handler is None:
            return "needs_human", f"unknown action '{action}'"
        return handler(s)

    def _plan(self, s: EngineeringState) -> None:
        s.plan = self.llm.plan(s.for_llm(), self.ws.list_files())

    def _search(self, s: EngineeringState) -> None:
        tree = self.ws.list_files()
        wanted = self.llm.choose_files(s.for_llm(), tree, self.cfg.max_files_per_search)
        for path in wanted:
            if path in tree and path not in s.files_inspected:
                s.files_inspected.append(path)
        if not wanted:
            s.open_issues.append("search returned no files")

    def _context_files(self, s: EngineeringState) -> dict[str, str]:
        files, budget = {}, self.cfg.max_context_chars
        for path in dict.fromkeys(s.files_changed + s.files_inspected):
            try:
                body = self.ws.read(path)
            except (OSError, EditError):
                continue
            if len(body) > budget:
                break
            files[path], budget = body, budget - len(body)
        return files

    def _edit(self, s: EngineeringState) -> tuple[str, str] | None:
        if s.last_test is not None and not s.last_test.passed:
            s.fix_attempts += 1
            if s.fix_attempts > self.cfg.max_fix_attempts:
                return "needs_human", f"tests still failing after {self.cfg.max_fix_attempts} fix attempts"
        edits = self.llm.propose_edits(s.for_llm(), self._context_files(s))
        applied = 0
        for e in edits:
            label = f"edit {e['path']}: {e.get('reason', '')}".strip()
            if self.approve and not self.approve(label):
                s.rejected_approaches.append(f"human rejected: {label}")
                continue
            try:
                self.ws.apply_edit(e["path"], e["old"], e["new"])
            except EditError as err:
                s.open_issues.append(f"edit failed: {err}")
                continue
            applied += 1
            if e["path"] not in s.files_changed:
                s.files_changed.append(e["path"])
        if applied:
            s.dirty = True
            s.empty_edit_steps = 0
            return None
        s.empty_edit_steps += 1
        s.open_issues.append("no edits were applied in the last edit step")
        if s.empty_edit_steps >= 2:
            return "needs_human", "no progress: two edit steps in a row changed nothing"
        return None

    def _test(self, s: EngineeringState) -> tuple[str, str] | None:
        gate = self.decider.command_check(s.test_command, s)
        if not gate.value:
            return "needs_human", f"test command blocked: {gate.reason}"
        if self.approve and not self.approve(f"run: {s.test_command}"):
            return "needs_human", "human declined to run the test command"

        result = self.ws.run(s.test_command, self.cfg.test_timeout_s)
        s.dirty = False
        if result.exit_code == 0:
            s.last_test = TestResult(True, 0, result.output[-3000:])
            return None

        cause = self.decider.triage(result.output).value
        s.last_test = TestResult(False, result.exit_code, result.output[-3000:], cause)
        self.echo(f"     tests failed, triage: {cause}")

        if cause == "environment":
            return "needs_human", "tests failed because of the environment, not the code"
        if cause == "flaky" and s.flaky_retries_used < self.cfg.flaky_retries:
            s.flaky_retries_used += 1
            s.dirty = True           # allow exactly one re-run without a code change
            return None
        s.open_issues.append(f"tests failing ({cause})")
        return None

    def _verify(self, s: EngineeringState) -> tuple[str, str] | None:
        s.verify_attempts += 1
        score = self.decider.completion(s, self.ws.diff())
        top = self.decider.max_completion_level()
        self.echo(f"     completion score {score.value:.0f}/{top}")
        if score.value is not None and score.value >= top:
            return "done", "request met and verified"
        if s.verify_attempts >= self.cfg.max_verify_attempts:
            return "needs_human", f"verification scored {score.value:.0f}/{top} after {s.verify_attempts} attempts"
        s.open_issues.append(f"verification scored {score.value:.0f}/{top}; work remains against the request")
        # Treat an incomplete result like a failing run so the next step is an edit.
        s.last_test = TestResult(False, 0, "verification: request not fully met", "incomplete")
        s.dirty = False
        return None

    # ---- wrap up -------------------------------------------------------------
    def _finish(self, s: EngineeringState, status: str, reason: str) -> RunResult:
        diff = self.ws.diff()
        summary = ""
        if diff:
            try:
                summary = self.llm.summarize(s.for_llm(), diff)
            except Exception as err:  # summary is nice to have, never fatal
                summary = f"(summary unavailable: {err})"
        self.decider.log.write("finish", status=status, reason=reason, state=s.to_json())
        return RunResult(status, reason, summary, s, diff)
