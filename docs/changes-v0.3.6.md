# v0.3.6 — Graceful summary truncation

Patch release. No CLI, API, or response-shape changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.3.6>
- Previous version: `0.3.5`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.3.5...v0.3.6>

---

## 1. Why

Every length-capped AI string was cut with a blunt `value[:limit]`. When a model
overran the 300-character summary cap, the text was sliced mid-word with no
ellipsis, so a finished-looking answer just stopped:

```text
... unauthorized lyrics upload by channel "HyperTunes" (licensedContent=false).
Both sync + master clearance required f
```

---

## 2. The fix

`music_copyright_checker/ai_researcher.py` now trims capped strings on a word
boundary and marks the cut with an ellipsis:

```python
def _clip(value: str, limit: int) -> str:
    """Trim to ``limit`` characters without splitting a word, adding an ellipsis."""
    if len(value) <= limit:
        return value
    clipped = value[: max(0, limit - 1)].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return f"{clipped}\u2026"
```

`_text()` routes through `_clip()`, so the summary, match notes, source
`supports`, caveats, and warnings all degrade on a whole word.

The summary cap is raised from **300 to 600** characters (prompt hint and output
rule updated to match), and `RESEARCH_PROMPT_VERSION` is bumped `2 → 3`.

---

## 3. Impact

- No field is cut mid-word; an over-long value ends on a complete word followed
  by `…`.
- Summaries may now run up to 600 characters.
- The prompt-version bump re-keys the research cache once, so previously cached
  tracks re-run research the next time they are checked.

---

## 4. Also in this release

- `tests/test_basic.py`: regressions for word-boundary truncation and for a
  short summary being returned untouched.

## 5. Upgrade notes

- Version bumped `0.3.5 → 0.3.6` (`pyproject.toml`, `__version__`).
- No configuration changes. Cache entries re-run once (prompt version bump).
