"""Check that a highlight's code really appears in the commit's diff (new side only)."""

from __future__ import annotations


def _normalize_lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r", "").split("\n")]
    # Ignore blank lines at the start/end of a snippet.
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def new_side(patch: str) -> list[list[str]]:
    """Each hunk of a unified-diff patch as the lines of the new file version (added + context).

    Removed lines ('-') are excluded: a highlight must show code as it is after the commit.
    """
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for raw in patch.replace("\r", "").split("\n"):
        if raw.startswith("@@"):
            current = []
            hunks.append(current)
        elif current is None or raw.startswith("-") or raw.startswith("\\"):
            continue  # removed line or "\ No newline at end of file"
        elif raw.startswith("+") or raw.startswith(" "):
            current.append(raw[1:].rstrip())
        elif raw == "":
            current.append("")
    return hunks


def check_highlight(file: str, code: str, files: list[dict]) -> str | None:
    """None if the highlight is valid, otherwise a reason (used as feedback to the model)."""
    match = next((f for f in files if f.get("filename") == file), None)
    if match is None:
        names = ", ".join(f.get("filename", "?") for f in files) or "(none)"
        return f"file '{file}' is not changed in this commit (changed files: {names})"
    patch = match.get("patch")
    if not patch:
        return f"file '{file}' has no text diff (binary or too large); pick another file or no highlight"

    snippet = _normalize_lines(code)
    if not snippet:
        return "highlight code is empty"
    width = len(snippet)
    for hunk in new_side(patch):
        for i in range(len(hunk) - width + 1):
            if hunk[i : i + width] == snippet:
                return None
    return (
        f"code for '{file}' does not exactly match a contiguous block of the new version in the diff; "
        "copy lines exactly (without +/space diff markers), from a single hunk, and never removed lines"
    )
