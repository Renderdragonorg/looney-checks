"""Tests for the stdlib-only .env loader."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music_copyright_checker.env import load_env_file, parse_env_file


class TestParseEnvFile(unittest.TestCase):
    def test_comments_blanks_and_quotes(self):
        text = "\n".join(
            [
                "# a comment",
                "",
                "OPENROUTER_API_KEY=sk-or-abc",
                'QUOTED="value with spaces"',
                "SINGLE='single'",
                "export EXPORTED=yes",
                "NO_EQUALS_LINE",
            ]
        )
        values = parse_env_file(text)
        self.assertEqual(values["OPENROUTER_API_KEY"], "sk-or-abc")
        self.assertEqual(values["QUOTED"], "value with spaces")
        self.assertEqual(values["SINGLE"], "single")
        self.assertEqual(values["EXPORTED"], "yes")
        self.assertNotIn("NO_EQUALS_LINE", values)


class TestLoadEnvFile(unittest.TestCase):
    def test_loads_without_overriding_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("MCC_TEST_NEW_VAR=from-file\nMCC_TEST_KEEP=file\n", encoding="utf-8")
            os.environ["MCC_TEST_KEEP"] = "from-env"
            try:
                loaded = load_env_file(paths=[path])
                self.assertEqual(loaded, ["MCC_TEST_NEW_VAR"])
                self.assertEqual(os.environ["MCC_TEST_NEW_VAR"], "from-file")
                self.assertEqual(os.environ["MCC_TEST_KEEP"], "from-env")
            finally:
                os.environ.pop("MCC_TEST_KEEP", None)
                os.environ.pop("MCC_TEST_NEW_VAR", None)

    def test_override_sets_even_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("MCC_TEST_OVERRIDE=from-file\n", encoding="utf-8")
            os.environ["MCC_TEST_OVERRIDE"] = "from-env"
            try:
                load_env_file(paths=[path], override=True)
                self.assertEqual(os.environ["MCC_TEST_OVERRIDE"], "from-file")
            finally:
                os.environ.pop("MCC_TEST_OVERRIDE", None)

    def test_missing_file_is_ignored(self):
        self.assertEqual(load_env_file(paths=[Path("/nope/.env")]), [])


if __name__ == "__main__":
    unittest.main()
