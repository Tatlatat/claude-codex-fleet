from __future__ import annotations
import re
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
    # A `codex` token is allowed ONLY as a deliberate backward-compat artifact. The
    # whole reasonix stack ships together, but a user may have an in-flight session
    # or shell exports under the OLD names, so these are intentionally kept:
    #   1. CLAUDE_CODEX_* env NAMES — the launcher exports them and the gateway/hooks
    #      read them as the fallback arm of a reasonix-first read.
    #   2. CODEX_BIN — the legacy name for the Fleet MCP binary (REASONIX_BIN now).
    #   3. legacy codex-*/deepseek-* agentType acceptance: a `codex-`/`agent(codex-`
    #      token in a whitelist/startswith, kept so an old launcher's lanes still pass.
    #   4. any line a developer explicitly tagged legacy/back-compat/in-flight.
    # Everything else — a codex in a path, a non-env identifier, an MCP name, a
    # user-facing string — is a real leftover and is flagged.
    BACKCOMPAT_MARKERS = ("legacy", "back-compat", "backward-compat", "in-flight", "fallback")
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
            if any(m in low for m in BACKCOMPAT_MARKERS):
                continue
            # Every codex token on the line is an allowed back-compat artifact:
            # a CLAUDE_CODEX_ env name, the CODEX_BIN env, or a codex-/agent(codex-
            # agentType-prefix token (always paired with its reasonix- equivalent).
            tokens = re.findall(r"claude_codex_[a-z0-9_]*|codex_bin|agent\(codex-|codex-|codex", low)
            allowed = all(
                t.startswith(("claude_codex_", "codex_bin", "agent(codex-", "codex-"))
                for t in tokens
            )
            if allowed:
                continue
            offenders.append(f"{p}:{i}: {line.strip()[:80]}")
    assert not offenders, (
        f"{len(offenders)} codex references remain:\n" + "\n".join(offenders))


if __name__ == "__main__":
    test_no_codex_in_filenames()
    test_no_codex_identifiers_outside_fallback()
    print("PASS: no codex leftovers")
