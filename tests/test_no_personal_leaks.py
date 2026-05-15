"""Block personal-info leaks before they hit main.

The repo is public on GitHub, so anything tracked is visible to anyone. This
test fails on PR if a tracked file contains a forbidden personal identifier
(Windows username, hardcoded user-home paths, etc).

Add new patterns to FORBIDDEN_PATTERNS when you spot a new sensitivity.
Patterns that legitimately need to appear in a file (e.g. this test file
itself, where they're defined) go in ALLOWED_FILES.

Discovered by the security audit in PR #105: the Windows username had
leaked into 7 tracked files across multiple PRs. The forward-fix scrubbed
those + this test prevents reintroduction. The git history was rewritten
separately to remove the pattern from old commits.
"""
import subprocess
from pathlib import Path


# Patterns that must NOT appear in any tracked file. Case-sensitive.
# When adding here, also consider whether existing matches need cleanup.
#
# Patterns are constructed at runtime via string concatenation so the
# literal sensitive value does NOT appear as a Python string constant
# in source. This makes the file safe to pass through future
# `git filter-repo --replace-text` runs (which would otherwise rewrite
# any literal match in this file too -- the bug PR #107 fixes).
FORBIDDEN_PATTERNS = [
    # Windows username — replace any occurrence with %USERPROFILE%
    # (PowerShell), $HOME (Bash), or a doc placeholder.
    "NI" + "NOH",
    # Local-LLM workflow tooling. The maintainer's local AI infra is
    # personal config, not project documentation. Don't reintroduce
    # references to the runtime or specific model names in tracked files.
    # If a contributor PR mentions these by name, ask them to genericize
    # ("local OSS provider" / "local model") before merge.
    "olla" + "ma",       # runtime name (lowercase form)
    "Olla" + "ma",       # runtime name (capitalized form)
    "qwen" + "3",        # Qwen 3.x model family
    "gemm" + "a4",       # Gemma 4 model family
    "nemo" + "tron",     # Nemotron model family (lowercase)
    "Nemo" + "tron",     # Nemotron model family (capitalized)
]

# Tracked files allowed to contain the forbidden patterns. The test file
# itself defines the patterns so it has to match itself — add the path
# here. New entries should be rare; prefer scrubbing over allowlisting.
ALLOWED_FILES = {
    "tests/test_no_personal_leaks.py",
    # Installer-script tests assert these patterns are ABSENT from the
    # installers, which requires the literal strings in the test source.
    "tests/test_installer_scripts.py",
}


REPO_ROOT = Path(__file__).resolve().parent.parent


def _tracked_files():
    """Return paths of all git-tracked files in the repo."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line]


def test_no_forbidden_patterns_in_tracked_files():
    """Every tracked file must be free of FORBIDDEN_PATTERNS unless
    explicitly listed in ALLOWED_FILES. Leak detected → assertion error
    with the file + pattern so it's obvious how to fix."""
    leaks = []
    for path in _tracked_files():
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        if rel in ALLOWED_FILES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            # Binary or unreadable — skip rather than fail. The patterns
            # we care about are textual.
            continue
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in content:
                leaks.append(f"  {rel}: contains forbidden pattern '{pattern}'")

    if leaks:
        leaks_str = "\n".join(leaks)
        raise AssertionError(
            "Personal-info leak detected in tracked file(s):\n"
            f"{leaks_str}\n\n"
            "Replace the offending text with a placeholder "
            "(e.g. %USERPROFILE% / $HOME / <USERNAME>) and re-run.\n"
            "If a legitimate match is needed in a doc, add the file to "
            "ALLOWED_FILES in tests/test_no_personal_leaks.py."
        )
