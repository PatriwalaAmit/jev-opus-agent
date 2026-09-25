import shutil
import sys
from pathlib import Path

import pytest

from jev_agent.config import Config
from jev_agent.decisions import AuditLog, Decider
from jev_agent.loop import Agent, enforce_hard_rules
from jev_agent.state import EngineeringState, TestResult
from jev_agent.tools import EditError, Workspace

from .fakes import FakeJev, FakeOpus, choice

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "buggy_calc"
TEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'

FIX_ADD = {"path": "calc.py", "old": "return a - b", "new": "return a + b", "reason": "add should add"}
FIX_DIV = {"path": "calc.py", "old": "    return a / b",
           "new": '    if b == 0:\n        raise ValueError("cannot divide by zero")\n    return a / b',
           "reason": "raise a clear error on zero"}


@pytest.fixture
def repo(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(EXAMPLE, dst)
    return dst


def make_agent(repo, jev, opus, cfg=None):
    cfg = cfg or Config()
    decider = Decider(jev, cfg, AuditLog(repo / ".jev_agent" / "audit.jsonl"))
    return Agent(Workspace(repo, cfg.protected_paths), opus, decider, cfg, echo=lambda *_: None)


# ---- hard rules ---------------------------------------------------------------

def test_hard_rules_force_plan_then_search():
    s = EngineeringState("x", "t")
    assert enforce_hard_rules("edit", s)[0] == "plan"
    s.plan = "p"
    assert enforce_hard_rules("edit", s)[0] == "search"


def test_hard_rules_block_verify_without_passing_tests():
    s = EngineeringState("x", "t", plan="p", files_inspected=["a.py"], dirty=True)
    assert enforce_hard_rules("verify", s)[0] == "test"


def test_hard_rules_stop_pointless_retest():
    s = EngineeringState("x", "t", plan="p", files_inspected=["a.py"],
                         last_test=TestResult(False, 1, "boom", "real_bug"))
    assert enforce_hard_rules("test", s)[0] == "edit"


def test_command_hard_rule_never_reaches_jev(repo):
    jev = FakeJev()
    decider = Decider(jev, Config(), AuditLog(None))
    d = decider.command_check("rm -rf / && pytest", EngineeringState("x", "t"))
    assert d.value is False and jev.calls == []


def test_low_confidence_escalates_to_human(repo):
    jev = FakeJev(next_action=lambda s: choice("edit", confidence=0.3))
    result = make_agent(repo, jev, FakeOpus([])).run("fix it", TEST_CMD)
    assert result.status == "needs_human" and "low confidence" in result.reason


# ---- end to end -----------------------------------------------------------------

def test_fixes_bugs_over_two_passes_and_verifies(repo):
    opus = FakeOpus([[FIX_ADD], [FIX_DIV]])
    result = make_agent(repo, FakeJev(), opus).run("Make all tests in test_calc.py pass", TEST_CMD)

    assert result.status == "done", result.reason
    assert result.state.last_test.passed
    assert result.state.files_changed == ["calc.py"]
    assert opus.calls.count("propose_edits") == 2        # first fix, then the follow-up
    assert "raise ValueError" in (repo / "calc.py").read_text()
    assert (repo / ".jev_agent" / "audit.jsonl").exists()


def test_environment_failure_stops_instead_of_editing(repo):
    opus = FakeOpus([[FIX_ADD]])
    result = make_agent(repo, FakeJev(cause="environment"), opus).run("fix", TEST_CMD)
    assert result.status == "needs_human" and "environment" in result.reason
    assert opus.calls.count("propose_edits") == 1


DOCSTRING = {"path": "calc.py", "old": "def add(a, b):\n", "new": 'def add(a, b):\n    """Return a + b."""\n',
             "reason": "document add"}


def test_incomplete_verification_goes_back_to_editing(repo):
    opus = FakeOpus([[FIX_ADD, FIX_DIV], [DOCSTRING]])
    cfg = Config(max_verify_attempts=2)
    result = make_agent(repo, FakeJev(completion=2), opus, cfg).run("fix", TEST_CMD)
    assert result.status == "needs_human" and "verification scored" in result.reason
    assert result.state.verify_attempts == 2


def test_blocked_test_command_hands_over(repo):
    result = make_agent(repo, FakeJev(destructive=0.9), FakeOpus([[FIX_ADD]])).run("fix", TEST_CMD)
    assert result.status == "needs_human" and "blocked" in result.reason


# ---- workspace safety -----------------------------------------------------------

def test_workspace_refuses_escape_and_protected_paths(repo):
    ws = Workspace(repo, (".env",))
    with pytest.raises(EditError):
        ws.apply_edit("../outside.py", "", "x")
    with pytest.raises(EditError):
        ws.apply_edit(".env", "", "SECRET=1")


def test_workspace_requires_unique_match(repo):
    ws = Workspace(repo)
    (repo / "dup.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(EditError):
        ws.apply_edit("dup.py", "x = 1", "x = 2")


def test_stops_when_model_makes_no_progress(repo):
    result = make_agent(repo, FakeJev(), FakeOpus([[], []])).run("fix", TEST_CMD)
    assert result.status == "needs_human" and "no progress" in result.reason
