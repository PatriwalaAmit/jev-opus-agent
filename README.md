# JEV OPUS AGENT

A small, readable reference implementation of a coding agent with two kinds of thinking:

- **Opus 5.5** (System Two) plans, chooses which files to read, writes edits and summarises the result.
- **Jev** (System One) makes the small, typed decisions in between: what to do next, whether a command is safe, why tests failed, and whether the request is really done.
- **Plain code** owns the engineering state, enforces hard rules, and is the only thing that touches files or runs processes.

It accompanies the *AI Architecture & Engineering* newsletter edition "Wiring Jev and Opus 5.5 into a Coding Agent".

## How the loop works

```
            ┌──────────── EngineeringState ────────────┐
            │ plan, files inspected/changed, last test, │
            │ open issues, rejected approaches, counts  │
            └───────────────────┬───────────────────────┘
                                ▼
   Jev: next action (Choice + confidence) ── low confidence ──► ask_human
                                ▼
   Hard rules in code (no edit before search, no verify without a passing run, ...)
                                ▼
   plan / search / edit  ──► Opus 5.5
   test                  ──► command gate (hard deny list → Jev Nouls) → run → Jev triage
   verify                ──► Jev completion Score against the original request
                                ▼
                 done  |  needs_human  |  step_limit
```

Every Jev answer and every executed action is appended to `.jev_agent/audit.jsonl` in the target repo. That file is your evidence for reviewers and your data for tuning thresholds.

## Quick start

### API keys

Copy `.env.example` to `.env` and fill in real keys:

```text
ANTHROPIC_API_KEY=...
TYPESAFE_API_KEY=...
```

`jev-agent` does **not** load `.env` by itself — export those variables into your shell before running (see below). A `401` from `api.typesafe.ai` means `TYPESAFE_API_KEY` is missing or invalid in that shell.

### macOS / Linux

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 1. See the loop without any API keys (scripted fakes, real tests and edits)
python examples/offline_demo.py

# 2. Run the test suite
pytest

# 3. Run it for real — load keys, then copy the example and run
set -a && source .env && set +a
cp -r examples/buggy_calc /tmp/buggy_calc
jev-agent "Make all tests in test_calc.py pass" \
  --repo /tmp/buggy_calc \
  --test-cmd "python -m pytest -q" \
  --approve
```

### Windows (PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 1. See the loop without any API keys (scripted fakes, real tests and edits)
python examples\offline_demo.py

# 2. Run the test suite
pytest

# 3. Run it for real — load keys from .env into this shell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
    Set-Item -Path "env:$($matches[1].Trim())" -Value $matches[2].Trim().Trim('"')
  }
}

Copy-Item -Recurse examples\buggy_calc $env:TEMP\buggy_calc -Force
jev-agent "Make all tests in test_calc.py pass" `
  --repo "$env:TEMP\buggy_calc" `
  --test-cmd "python -m pytest -q" `
  --approve
```

If `jev-agent` is not found after install, use the venv executable:

```powershell
.\venv\Scripts\jev-agent.exe "Make all tests in test_calc.py pass" `
  --repo "$env:TEMP\buggy_calc" `
  --test-cmd "python -m pytest -q" `
  --approve
```

`--approve` asks you before every edit and command. Leave it on until you trust your thresholds.

The agent edits files in place, so point it at a copy or a clean git branch.

## Project layout

| File | Responsibility |
|---|---|
| `jev_agent/config.py` | Every threshold, budget and hard rule in one place |
| `jev_agent/state.py` | Engineering state: what happened, not what was said |
| `jev_agent/decisions.py` | The four Jev questions and the audit log |
| `jev_agent/llm.py` | Opus 5.5 prompts for plan, file selection, edits, summary |
| `jev_agent/tools.py` | Sandboxed workspace: reads, exact-match edits, diffs, commands |
| `jev_agent/loop.py` | The typed decision loop and hard-rule overrides |
| `jev_agent/cli.py` | Command line entry point |
| `tests/` | Offline tests with fake Jev and Opus clients |

## The four decision points

| Question | Jev type | What the code does with it |
|---|---|---|
| What next? | `Choice` over plan/search/edit/test/verify/ask_human | Below `min_action_confidence` → hand over to a human |
| Is this command safe? | two `Noul`s: destructive, in scope | Hard deny list runs first; both thresholds must pass |
| Why did tests fail? | `Choice` over real_bug/test_is_wrong/environment/flaky | Environment → stop; flaky → one retry; otherwise back to Opus |
| Is it done? | `Score` on four levels | Only the top level ends the run as `done` |

## Guard rails that never depend on a probability

- Commands containing anything in `Config.never_run` are refused before Jev is asked.
- Paths in `Config.protected_paths` (`.git/`, `.env`, `migrations/`) can't be edited.
- Edits must match exactly once; paths can't escape the repo root.
- No edit before files are inspected, no verify without a passing run on current code.
- The run stops on repeated failing fixes, repeated empty edits, repeated incomplete verification, or the step limit.

## Tuning

The defaults (`0.6`, `0.2`, `0.7`) are starting points. Run with `--approve`, compare what you approved against what Jev said in `audit.jsonl`, and adjust `config.py` from that data.

Opus 5.5 does not run arbitrary commands in this version; the only command executed is your `--test-cmd`, and it still goes through the gate. If you add more tools (installs, linters, migrations), route each one through `Decider.command_check` and give it its own hard rules.

## Requirements

Python 3.10+, `anthropic`, `typesafe-sdk` (tested against 0.7.1). Model names can be overridden with `OPUS_MODEL` and `JEV_MODEL`.
