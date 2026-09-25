"""The only place that touches the filesystem or runs processes."""
from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".jev_agent", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build"}


class EditError(Exception):
    pass


@dataclass
class CommandResult:
    exit_code: int
    output: str


class Workspace:
    def __init__(self, root: Path, protected_paths: tuple[str, ...] = ()):
        self.root = root.resolve()
        self.protected = protected_paths
        self._originals: dict[str, str | None] = {}

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise EditError(f"path escapes workspace: {rel}")
        return p

    def list_files(self, limit: int = 2000) -> list[str]:
        out: list[str] = []
        for p in sorted(self.root.rglob("*")):
            if any(part in SKIP_DIRS for part in p.relative_to(self.root).parts):
                continue
            if p.is_file():
                out.append(p.relative_to(self.root).as_posix())
                if len(out) >= limit:
                    break
        return out

    def read(self, rel: str) -> str:
        return self._resolve(rel).read_text(encoding="utf-8", errors="replace")

    def is_protected(self, rel: str) -> bool:
        norm = rel[2:] if rel.startswith("./") else rel
        return any(norm == p.rstrip("/") or norm.startswith(p) for p in self.protected)

    def apply_edit(self, rel: str, old: str, new: str) -> None:
        if self.is_protected(rel):
            raise EditError(f"{rel} is protected; a human must change it")
        path = self._resolve(rel)
        if old == "":
            if path.exists():
                raise EditError(f"{rel} already exists; refusing to overwrite with an empty 'old'")
            self._originals.setdefault(rel, None)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, encoding="utf-8")
            return
        if not path.exists():
            raise EditError(f"{rel} does not exist")
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            raise EditError(f"'old' text found {count} times in {rel}; it must match exactly once")
        self._originals.setdefault(rel, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def diff(self) -> str:
        chunks = []
        for rel, before in self._originals.items():
            after = self.read(rel) if self._resolve(rel).exists() else ""
            chunks.extend(difflib.unified_diff(
                (before or "").splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}",
            ))
        return "".join(chunks)

    def run(self, cmd: str, timeout_s: int) -> CommandResult:
        try:
            proc = subprocess.run(cmd, shell=True, cwd=self.root, capture_output=True,
                                  text=True, timeout=timeout_s)
            return CommandResult(proc.returncode, (proc.stdout + proc.stderr)[-20000:])
        except subprocess.TimeoutExpired as e:
            parts = [x.decode(errors="replace") if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr)]
            out = "".join(parts)
            return CommandResult(124, f"TIMEOUT after {timeout_s}s\n{out[-5000:]}")
