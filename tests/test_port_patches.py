"""Regression checks for overlapping cached port patches."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("patch_recipe", ROOT / "Backend/ports/downhill/build.py")
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


class PortPatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="PS2 patch upgrade ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "Cached port with spaces"
        self.source.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Local patch test")
        self.git("config", "user.email", "patch-test@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/skuza-oss/downhill-revived.git")
        self.file = self.source / "main.c"
        self.file.write_text("int main(void) { return 0; }\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic baseline")
        self.profile = {"upstream": "skuza-oss/downhill-revived", "revision": self.git("rev-parse", "HEAD").strip()}
        self.patches = []
        for number in range(1, 8):
            self.file.write_text(f"int main(void) {{ return {number}; }}\n")
            if number == 4:
                (self.source / "launcher.m").write_text("/* native launcher fixture */\n")
                self.git("add", "--intent-to-add", "launcher.m")
            if number == 5:
                (self.source / "launcher.m").write_text("/* catalog launcher fixture */\n")
            path = self.root / f"step-{number}.patch"
            path.write_text(self.git("diff"))
            self.patches.append(path)
            self.git("add", ".")
        self.expected = self.file.read_bytes()
        self.git("reset", "--hard", "--quiet", "HEAD")
        (self.source / "launcher.m").unlink(missing_ok=True)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.source, check=True, text=True,
                              capture_output=True).stdout

    def prepare(self):
        with patch.object(recipe, "PORT_PATCHES", tuple(self.patches)), contextlib.redirect_stdout(io.StringIO()):
            recipe.prepare_port_source(self.source, self.profile)

    def test_updates_previous_port_and_reuses_completed_upgrade(self):
        for path in self.patches[:4]:
            self.git("apply", str(path))
        note = self.source / "local-note.txt"
        note.write_text("Preserve this staged user file.\n")
        self.git("add", "local-note.txt")
        index_before = (self.source / ".git/index").read_bytes()
        self.prepare()
        self.prepare()
        self.assertEqual(self.file.read_bytes(), self.expected)
        self.assertEqual((self.source / "launcher.m").read_text(), "/* catalog launcher fixture */\n")
        self.assertEqual((self.source / ".git/index").read_bytes(), index_before)
        self.assertEqual(note.read_text(), "Preserve this staged user file.\n")

    def test_prepares_clean_source_and_resumes_partial_upgrades(self):
        for completed in (0, 1, 2, 3, 6):
            with self.subTest(completed=completed):
                self.git("reset", "--hard", "--quiet", "HEAD")
                (self.source / "launcher.m").unlink(missing_ok=True)
                for path in self.patches[:completed]:
                    self.git("apply", str(path))
                self.prepare()
                self.assertEqual(self.file.read_bytes(), self.expected)

    def test_refuses_unexpected_edit_without_overwriting_it(self):
        for path in self.patches[:3]:
            self.git("apply", str(path))
        edited = self.file.read_bytes() + b"/* user's separate edit */\n"
        self.file.write_bytes(edited)
        with self.assertRaisesRegex(recipe.BuildError, "modified"):
            self.prepare()
        self.assertEqual(self.file.read_bytes(), edited)
        self.assertFalse((self.source / "launcher.m").exists())


if __name__ == "__main__":
    unittest.main()
