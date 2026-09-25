"""Command line entry point.

    jev-agent "Fix the failing add() test" --repo examples/buggy_calc --test-cmd "python -m pytest -q"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .decisions import AuditLog, Decider
from .llm import OpusEngine
from .loop import Agent
from .tools import Workspace


def _approve(label: str) -> bool:
    return input(f"  approve {label}? [y/N] ").strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jev-agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", help="what you want done, in plain language")
    ap.add_argument("--repo", default=".", help="repository root (default: current directory)")
    ap.add_argument("--test-cmd", required=True, help='e.g. "python -m pytest -q" or "npm test"')
    ap.add_argument("--approve", action="store_true", help="ask before every edit and command")
    ap.add_argument("--max-steps", type=int, default=None)
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    if args.max_steps:
        cfg.max_steps = args.max_steps

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"repo not found: {root}", file=sys.stderr)
        return 2

    from typesafe_sdk import TypeSafeClient  # imported late so --help works without keys

    log = AuditLog(root / ".jev_agent" / "audit.jsonl")
    agent = Agent(
        workspace=Workspace(root, cfg.protected_paths),
        engine=OpusEngine(model=cfg.opus_model),
        decider=Decider(TypeSafeClient(model=cfg.jev_model), cfg, log),
        config=cfg,
        approve=_approve if args.approve else None,
    )
    result = agent.run(args.request, args.test_cmd)

    s = result.state
    print("\n" + "=" * 60)
    print(f"status : {result.status}  ({result.reason})")
    print(f"steps  : {s.steps_taken}   files changed: {', '.join(s.files_changed) or 'none'}")
    if s.last_test:
        print(f"tests  : {'passed' if s.last_test.passed else 'failed'}"
              + (f" ({s.last_test.cause})" if s.last_test.cause else ""))
    if s.open_issues:
        print("open   : " + "; ".join(s.open_issues[-3:]))
    if result.summary:
        print("\n" + result.summary)
    print(f"\naudit log: {log.path}")
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
