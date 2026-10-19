# v0.4.1 — RenderDragon resources repo moved

Patch release. No CLI, API, or response-shape changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.4.1>
- Previous version: `0.4.0`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.4.0...v0.4.1>

---

## 1. Why

The RenderDragon resources repository moved from the `Yxmura` GitHub account to
the `Renderdragonorg` organisation. New music links are generated with the
canonical raw URL:

```
https://raw.githubusercontent.com/Renderdragonorg/resources_renderdragon/…
```

The `renderdragon.org/api/music-link` endpoint already accepts the new owner, so
`build_music_link()` must generate URLs in that form and the local allowlist must
match.

## 2. What changed

- `ALLOWLISTED_SOURCE_PREFIXES` in `music_copyright_checker/renderdragon_source.py`
  now uses `raw.githubusercontent.com/Renderdragonorg/resources_renderdragon/`.
- `build_music_link()` continues to rewrite `github.com/.../blob/...` viewer
  URLs to their raw form. The owner is preserved, so resource mappings should
  pass the new `Renderdragonorg` URL; an old `Yxmura` blob URL is rejected by
  the allowlist.
- Tests and `docs/renderdragon-source.md` examples use the new owner.

Existing links generated with the old `Yxmura` owner keep resolving: GitHub
redirects the renamed account, and the RenderDragon endpoint still accepts those
URLs. Only newly built links use the canonical `Renderdragonorg` form.

## 3. Impact

- No breaking changes: request fields, response fields, CLI flags, and server
  endpoints are unchanged.
- Cached metadata keyed by `renderdragon_id` or the raw audio URL is unaffected.
- Version bumped `0.4.0 → 0.4.1` (`pyproject.toml`, `__version__`).

## 4. Verifying

```bash
python -m pytest tests/test_renderdragon.py
```

A live check against the endpoint:

```bash
curl -sS -H 'Accept: application/json' \
  "https://renderdragon.org/api/music-link?name=Song&url=<new-raw-github-url>&id=12"
```
