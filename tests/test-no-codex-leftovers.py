from __future__ import annotations
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files that LEGITIMATELY still mention codex: this guard test (the pattern strings),
# historical docs/specs/plans, patches notes, and the backward-compat fallback
# (CLAUDE_CODEX_ as the SECOND arg of env_first). Everything else must be codex-free.
ALLOW_SUBSTR = ("tests/test-no-codex-leftovers.py", "/docs/", "/patches/", "README")


def shipped_files():
    # Scan only git-TRACKED files — that is exactly the set that gets published.
    # This auto-excludes scratch (the SDD ledger, .bak, runtime/*.jsonl) without an
    # ever-growing allow-list, so the guard measures the real publish surface.
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True, check=True).stdout.splitlines()
    for rel in tracked:
        p = ROOT / rel
        if not p.is_file():
            continue
        if "/runtime/" in rel or rel.startswith("runtime/"):
            continue
        if any(a in str(p) or a.lstrip("/") in rel for a in ALLOW_SUBSTR):
            continue
        if p.suffix in (".py", ".sh", ".json", ".md", "") or p.name == "claude-reasonix":
            yield p


def test_no_codex_in_filenames():
    bad = [str(p) for p in shipped_files() if "codex" in p.name.lower()]
    assert not bad, f"files still named codex: {bad}"


def test_no_codex_identifiers_outside_fallback():
    # Allow `CLAUDE_CODEX_` ONLY as a backward-compat fallback (a later env_first arg
    # or a getenv fallback). Flag any other codex / CLAUDE_CODEX_ token.
    offenders = []
    for p in shipped_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if "codex" not in low:
                continue
            if "claude_codex_" in low and ("env_first(" in low or "fallback" in low or "getenv" in low):
                continue
            offenders.append(f"{p}:{i}: {line.strip()[:80]}")
    assert not offenders, "codex references remain:\n" + "\n".join(offenders)


if __name__ == "__main__":
    test_no_codex_in_filenames()
    test_no_codex_identifiers_outside_fallback()
    print("PASS: no codex leftovers")
