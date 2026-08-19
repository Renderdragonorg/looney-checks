"""Dependency-free tests for opencode self-provisioning (no network)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from music_copyright_checker.bootstrap import _binary_name, ensure_opencode, install_dir, resolve_opencode
from music_copyright_checker.errors import OpenCodeNotInstalledError


class TestResolveOpencode(unittest.TestCase):
    def test_resolves_binary_on_path(self):
        with patch("music_copyright_checker.bootstrap.shutil.which", return_value="/usr/local/bin/opencode"):
            self.assertEqual(resolve_opencode(), "/usr/local/bin/opencode")

    def test_resolves_explicit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "opencode"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            resolved = resolve_opencode(str(binary))
            self.assertIsNotNone(resolved)
            self.assertEqual(Path(resolved).name, "opencode")

    def test_missing_explicit_path_returns_none(self):
        self.assertIsNone(resolve_opencode("/no/such/opencode"))

    def test_falls_back_to_install_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / ".opencode" / "bin"
            install.mkdir(parents=True)
            target = install / _binary_name("opencode")
            target.write_text("#!/bin/sh\n")
            target.chmod(0o755)
            with patch("music_copyright_checker.bootstrap.shutil.which", return_value=None):
                with patch("music_copyright_checker.bootstrap.install_dir", return_value=install):
                    resolved = resolve_opencode()
            self.assertIsNotNone(resolved)
            self.assertTrue(Path(resolved).is_file())

    def test_missing_binary_returns_none(self):
        with patch("music_copyright_checker.bootstrap.shutil.which", return_value=None):
            self.assertIsNone(resolve_opencode("opencode"))

    def test_ensure_raises_without_auto_install(self):
        with patch("music_copyright_checker.bootstrap.resolve_opencode", return_value=None):
            with self.assertRaises(OpenCodeNotInstalledError):
                ensure_opencode(auto_install=False)

    def test_ensure_installs_when_missing(self):
        with patch("music_copyright_checker.bootstrap.resolve_opencode", side_effect=[None, "/tmp/opencode"]):
            with patch("music_copyright_checker.bootstrap.install_opencode", return_value="/tmp/opencode") as install:
                result = ensure_opencode(auto_install=True)
        install.assert_called_once()
        self.assertEqual(result, "/tmp/opencode")

    def test_install_dir_is_home_opencode_bin(self):
        with patch("music_copyright_checker.bootstrap.Path.home", return_value=Path("/home/tester")):
            self.assertEqual(install_dir(), Path("/home/tester") / ".opencode" / "bin")

    def test_binary_name_appends_exe_on_windows(self):
        with patch.object(sys, "platform", "win32"):
            self.assertEqual(_binary_name("opencode"), "opencode.exe")
            self.assertEqual(_binary_name("opencode.exe"), "opencode.exe")
        with patch.object(sys, "platform", "linux"):
            self.assertEqual(_binary_name("opencode"), "opencode")


if __name__ == "__main__":
    unittest.main()