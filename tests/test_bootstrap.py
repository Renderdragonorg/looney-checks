"""Dependency-free tests for opencode self-provisioning (no network)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from music_copyright_checker.bootstrap import (
    _binary_name,
    ensure_opencode,
    ensure_ssl_certs,
    install_dir,
    resolve_opencode,
)
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


class TestEnsureSslCerts(unittest.TestCase):
    def test_sets_ssl_cert_file_from_certifi(self):
        with tempfile.TemporaryDirectory() as tmp:
            cacert = Path(tmp) / "cacert.pem"
            cacert.write_text("dummy CA bundle\n")
            with patch.dict(os.environ, clear=True):
                with patch("certifi.where", return_value=str(cacert)):
                    ensure_ssl_certs()
                self.assertEqual(os.environ.get("SSL_CERT_FILE"), str(cacert))

    def test_respects_existing_ssl_cert_file(self):
        with patch.dict(os.environ, {"SSL_CERT_FILE": "/custom/certs.pem"}, clear=True):
            with patch("certifi.where", side_effect=AssertionError("should not be called")):
                ensure_ssl_certs()
            self.assertEqual(os.environ.get("SSL_CERT_FILE"), "/custom/certs.pem")

    def test_respects_existing_ssl_cert_dir(self):
        with patch.dict(os.environ, {"SSL_CERT_DIR": "/custom/certs"}, clear=True):
            with patch("certifi.where", side_effect=AssertionError("should not be called")):
                ensure_ssl_certs()
        self.assertNotIn("SSL_CERT_FILE", os.environ)

    def test_skips_when_certifi_unavailable(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "certifi":
                raise ImportError("no certifi")
            return real_import(name, *args, **kwargs)

        with patch.dict(os.environ, clear=True):
            with patch("builtins.__import__", side_effect=fake_import):
                ensure_ssl_certs()
        self.assertNotIn("SSL_CERT_FILE", os.environ)


if __name__ == "__main__":
    unittest.main()