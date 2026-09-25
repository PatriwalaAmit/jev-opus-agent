"""Watch the loop run end to end without API keys.

Jev and Opus are replaced by scripted fakes; the tests, file edits,
hard rules and audit log are all real.

    python examples/offline_demo.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_agent.config import Config                    # noqa: E402
from jev_agent.decisions import AuditLog, Decider      # noqa: E402
from jev_agent.loop import Agent                       # noqa: E402
from jev_agent.tools import Workspace                  # noqa: E402
from tests.fakes import FakeJev, FakeOpus              # noqa: E402

FIXES = [
    [{"path": "calc.py", "old": "return a - b", "new": "return a + b", "reason": "add should add"}],
    [{"path": "calc.py", "old": "    return a / b",
      "new": '    if b == 0:\n        raise ValueError("cannot divide by zero")\n    return a / b',
      "reason": "raise a clear error on zero"}],
]

with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / "buggy_calc"
    shutil.copytree(ROOT / "examples" / "buggy_calc", repo)
    cfg = Config()
    log = AuditLog(repo / ".jev_agent" / "audit.jsonl")
    agent = Agent(Workspace(repo, cfg.protected_paths), FakeOpus(FIXES),
                  Decider(FakeJev(), cfg, log), cfg)
    result = agent.run("Make all tests in test_calc.py pass",
                       f'"{sys.executable}" -m pytest -q -p no:cacheprovider')
    print(f"\nstatus: {result.status} ({result.reason})\n")
    print(result.diff)
    print(f"{len(log.path.read_text().splitlines())} audit records written")
