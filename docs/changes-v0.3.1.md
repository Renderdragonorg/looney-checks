# v0.3.1 — onedir Archives for Fast Startup

Packaging-only release. No CLI, API, or response-shape changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.3.1>
- Previous version: `0.3.0`

---

## 1. Why

The prebuilt binaries were PyInstaller **onefile** executables. A onefile
binary re-extracts its entire bundle to a temp directory on **every** launch,
and macOS re-scans each extracted file. On an Intel macOS 12 machine that made
even `--help` take ~17s, and ~27s once the pipeline was constructed — and much
worse for a downloaded, Gatekeeper-quarantined copy (reported ~90s).

v0.3.1 builds the same app as a **onedir** directory (launcher + `_internal/`)
and ships it as an archive. Warm startup is now sub-second.

| Action | v0.3.0 (onefile) | v0.3.1 (onedir) |
| --- | --- | --- |
| `--help` (warm) | ~17s | ~0.3s |
| Full pipeline startup (warm) | ~27s | ~0.7s |
| First run after extract / reboot | ~18s | ~20s, then fast |
| Archive size | 19 MB (binary) | 19 MB (`.tar.gz`) / 35 MB unpacked |

---

## 2. What changed

- `packaging/music_copyright_checker.spec`: `EXE(...)` is now
  `EXE(exclude_binaries=True)` + `COLLECT(...)`, producing
  `dist/music-copyright-checker/` instead of a single file.
- `.github/workflows/build-binaries.yml`: after the onedir build and smoke
  test, each platform is packaged as `.tar.gz` (Linux/macOS) or `.zip`
  (Windows) and uploaded/released under
  `music-copyright-checker-<version>-<target>.<ext>`.
- `packaging/smoke_test.py`: accepts either the onedir directory or the inner
  executable.
- `README.md` / `docs/binaries.md`: archive download/extract instructions, the
  onedir rationale, and a first-run/quarantine note.
- Version bumped `0.3.0 → 0.3.1` (`pyproject.toml`, `__version__`).

---

## 3. Release assets

| Platform | Asset |
| --- | --- |
| Linux x86_64 | `music-copyright-checker-0.3.1-linux-x86_64.tar.gz` |
| Linux arm64 | `music-copyright-checker-0.3.1-linux-aarch64.tar.gz` |
| macOS (Intel) | `music-copyright-checker-0.3.1-macos-x86_64.tar.gz` |
| macOS (Apple Silicon) | `music-copyright-checker-0.3.1-macos-aarch64.tar.gz` |
| Windows x86_64 | `music-copyright-checker-0.3.1-windows-x86_64.zip` |

```bash
tar -xzf music-copyright-checker-0.3.1-linux-x86_64.tar.gz
cd music-copyright-checker
./music-copyright-checker --help
```

```powershell
Expand-Archive music-copyright-checker-0.3.1-windows-x86_64.zip .
cd music-copyright-checker
.\music-copyright-checker.exe --help
```

Verify with `SHA256SUMS` (`sha256sum -c SHA256SUMS --ignore-missing`).

---

## 4. Upgrading from 0.3.0

- Replace the old single-file binary with the extracted archive directory — the
  launcher lives at `music-copyright-checker/music-copyright-checker`
  (`music-copyright-checker.exe` on Windows).
- Keep the launcher and its `_internal/` directory together when moving the
  install.
- All flags, endpoints, and response shapes are unchanged.

---

## 5. Notes

- The first launch after extracting (or after a reboot) loads files from cold
  storage and can take ~20s; subsequent launches are fast.
- If macOS quarantines the downloaded archive, clear it once:
  `xattr -dr com.apple.quarantine music-copyright-checker`.
- Everything runs natively on its target; Windows arm64 is not shipped.
- Configuration (`OPENROUTER_API_KEY`, `YOUTUBE_API_KEY`) is unchanged — see
  [AI backends](ai-backends.md) and [YouTube source](youtube-source.md).
