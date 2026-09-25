"""All tunable thresholds live here, so they're easy to find and adjust from your own logs."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    opus_model: str = "claude-opus-5-5"
    jev_model: str = "jev-latest"

    max_steps: int = 25
    max_fix_attempts: int = 4       # edit->test cycles before we hand over to a human
    max_verify_attempts: int = 2
    flaky_retries: int = 1
    test_timeout_s: int = 300

    # Jev thresholds: starting points, tune them from .jev_agent/audit.jsonl
    min_action_confidence: float = 0.6
    max_destructive: float = 0.2
    min_in_scope: float = 0.7

    # Hard rules. These never depend on a probability.
    never_run: tuple[str, ...] = (
        "rm -rf", "git push", "git reset --hard", "drop table", "drop database",
        "mkfs", "shutdown", "curl | bash", "curl | sh", "wget | sh", ":(){",
    )
    protected_paths: tuple[str, ...] = (".git/", ".env", "migrations/")

    # Context budgets
    max_files_per_search: int = 8
    max_context_chars: int = 60_000

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls()
        cfg.opus_model = os.getenv("OPUS_MODEL", cfg.opus_model)
        cfg.jev_model = os.getenv("JEV_MODEL", cfg.jev_model)
        return cfg
