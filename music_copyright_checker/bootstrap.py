"""Self-provisioning for the `opencode` CLI dependency.

The packaged binaries intentionally do not bundle the `opencode` agent — it
is a separate ~60 MB distribution that updates on its own. Instead, the first
run locates an existing install and, when missing, downloads it using the
official installer:

* Linux / macOS — ``curl -fsSL https://opencode.ai/install | bash``
  (the same direct install command from the opencode docs)
* Windows — direct download of the official release artifact
  (``opencode-windows-x64.zip`` / ``opencode-windows-arm64.zip`` from the
  GitHub release feed) extracted with the standard library

Binaries land in ``~/.opencode/bin`` (the same location the official
installer uses), so a manual ``opencode`` install is always reused first.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .errors import OpenCodeNotInstalledError

OPENCODE_INSTALL_URL = "https://opencode.ai/install"
OPENCODE_RELEASES_URL = "https://github.com/anomalyco/opencode/releases/latest/download"

# opencode.ai sits behind Cloudflare and 403s urllib's default
# ``Python-urllib/3.x`` User-Agent from datacenter IPs; send a browser-like
# one for both the installer script fetch and the Windows release download.
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; music-copyright-checker/0.2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def install_dir() -> Path:
    """Return the directory the official installer writes to."""
    return Path.home() / ".opencode" / "bin"


def ensure_ssl_certs() -> None:
    """Make sure Python can verify HTTPS certificates when running frozen.

    python.org Python builds on macOS do not read the system keychain: the
    bundled OpenSSL looks for its CA store in a path inside the framework
    that only exists when python.org is actually installed. Inside a
    PyInstaller one-file binary that path is gone, so every HTTPS call fails
    with ``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``
    unless the trust store is provided explicitly.

    certifi is bundled with the binary, so expose its CA bundle through the
    ``SSL_CERT_FILE`` environment variable, which the stdlib ``ssl`` module,
    urllib3 and requests all honor when creating default verification
    contexts.
    """
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return
    try:
        import certifi
    except ImportError:
        return
    cafile = certifi.where()
    if os.path.isfile(cafile):
        os.environ["SSL_CERT_FILE"] = cafile


def _binary_name(binary: str) -> str:
    if sys.platform == "win32" and not Path(binary).suffix:
        return binary + ".exe"
    return binary


def resolve_opencode(binary: str = "opencode") -> Optional[str]:
    """Return a usable path to the opencode binary, or None if absent.

    Checks, in order: an explicit path, the ``PATH``, and the official
    ``~/.opencode/bin`` install location.
    """
    candidate = Path(binary)
    if os.path.isabs(binary) or os.sep in binary or (os.altsep and os.altsep in binary):
        if candidate.is_file():
            return str(candidate.resolve())
        return None
    found = shutil.which(binary)
    if found:
        return found
    local = install_dir() / _binary_name(binary)
    if local.is_file():
        return str(local)
    return None


def _stream_output(process: subprocess.Popen) -> None:
    """Forward a subprocess's stdout/stderr to ours so installs stay visible.

    Everything is sent to *our* stderr: the CLI result (the JSON document)
    is written to stdout and must not be polluted by installer banners,
    progress bars, or download output.
    """
    assert process.stdout is not None
    assert process.stderr is not None
    for line in process.stdout:
        print(line, end="", file=sys.stderr)
        sys.stderr.flush()
    for line in process.stderr:
        print(line, end="", file=sys.stderr)
        sys.stderr.flush()


def _install_with_official_script(version: Optional[str] = None) -> str:
    """Run the official cross-platform installer on Linux/macOS.

    ``--no-modify-path`` keeps it from editing shell rc files; callers that
    need opencode on PATH still get ``~/.opencode/bin`` from ``resolve_``.
    """
    script = urllib.request.urlopen(urllib.request.Request(OPENCODE_INSTALL_URL, headers=_HTTP_HEADERS), timeout=60).read()
    process = subprocess.Popen(
        ["bash", "-s", "--", "--no-modify-path"] + ([ "--version", version] if version else []),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(script.decode("utf-8", errors="replace"))
    process.stdin.close()
    _stream_output(process)
    return_code = process.wait()
    if return_code != 0:
        raise OpenCodeNotInstalledError(
            f"The opencode installer (curl -fsSL {OPENCODE_INSTALL_URL}) exited with code {return_code}."
        )
    return _require_installed()


def _windows_asset() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    return f"opencode-windows-{arch}.zip"


def _install_on_windows(version: Optional[str] = None) -> str:
    """Download the official opencode release zip for Windows and extract it."""
    target = install_dir()
    target.mkdir(parents=True, exist_ok=True)
    url = (
        f"https://github.com/anomalyco/opencode/releases/download/v{version}/{_windows_asset()}"
        if version
        else f"{OPENCODE_RELEASES_URL}/{_windows_asset()}"
    )
    with tempfile.TemporaryDirectory(prefix="opencode-download-") as tmp:
        archive = Path(tmp) / _windows_asset()
        print(f"Downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(urllib.request.Request(url, headers=_HTTP_HEADERS), timeout=120) as response:
            with open(archive, "wb") as out:
                shutil.copyfileobj(response, out)
        with zipfile.ZipFile(archive) as zf:
            member = next(
                (name for name in zf.namelist() if Path(name).name in {"opencode", "opencode.exe"}),
                None,
            )
            if member is None:
                raise OpenCodeNotInstalledError(
                    f"The opencode release archive did not contain an opencode binary: {archive.name}"
                )
            destination = target / "opencode.exe"
            with zf.open(member) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)
    return str(destination)


def install_opencode(version: Optional[str] = None) -> str:
    """Download and install the opencode CLI for the current platform.

    Returns the path to the freshly installed binary.
    """
    if sys.platform == "win32":
        return _install_on_windows(version=version)
    return _install_with_official_script(version=version)


def _require_installed() -> str:
    path = resolve_opencode()
    if path is None:
        raise OpenCodeNotInstalledError(
            "The opencode installer ran but no binary could be found on PATH "
            f"or in {install_dir()}."
        )
    return path


def ensure_opencode(binary: str = "opencode", *, auto_install: bool = False, version: Optional[str] = None) -> str:
    """Resolve the opencode binary, installing it when missing.

    ``auto_install=False`` (the library default) raises
    :class:`OpenCodeNotInstalledError` when opencode is absent, so embedded
    callers never trigger a surprise download. The CLI, server, and packaged
    binaries enable it by default and report what they are doing.
    """
    resolved = resolve_opencode(binary)
    if resolved is not None:
        return resolved
    if not auto_install:
        raise OpenCodeNotInstalledError(
            f"opencode binary not found: {binary!r}. Install it with "
            f"`curl -fsSL {OPENCODE_INSTALL_URL} | bash`, or pass a path with "
            "--opencode-binary."
        )
    print(
        f"opencode CLI not found. Downloading it now (curl -fsSL {OPENCODE_INSTALL_URL} | bash)...",
        file=sys.stderr,
    )
    return install_opencode(version=version)
