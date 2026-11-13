"""
Checks every log and entry file in the repo, on every push.

Why: entries now arrive from three places (the /new page, the Bluesky
importer, and editing on github.com by hand). This is one safety net for all
of them. If something is off, the "Check" run on GitHub goes red and says
which file and why, instead of a post quietly going missing from the site.

What counts as "off":
  - an entry whose log doesn't exist (it would never show on any log page)
  - a missing or unreadable date, or one without a time zone
  - template code ("{{" or "{%") that isn't wrapped in raw markers, which
    could break the whole site build
  - a log with a type or status the site doesn't know about
  - template code inside a template's own comments (this broke the build once)

Python can't read YAML (the front matter format) without an extra library,
so this reads the few simple fields by hand. The files are written by our own
tools in a fixed shape, which is what makes that safe.
"""

import re
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW_OPEN, RAW_CLOSE = "{" + "% raw %" + "}", "{" + "% endraw %" + "}"
TYPES = {"book", "game", "show"}
STATUSES = {"reading", "finished", "dropped"}


def split(path):
    """Return (front matter fields, body) for one file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{path.name}: doesn't start with a --- front matter line")
    try:
        fm, body = text[4:].split("\n---\n", 1)
    except ValueError:
        raise AssertionError(f"{path.name}: front matter is never closed with ---")
    fields = {}
    for line in fm.splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields, body


class ContentTests(unittest.TestCase):
    def logs(self):
        return {p.stem: split(p)[0] for p in (REPO / "_logs").glob("*.md")}

    def test_logs_are_valid(self):
        for slug, f in self.logs().items():
            with self.subTest(log=slug):
                self.assertRegex(slug, r"^[a-z0-9]+(-[a-z0-9]+)*$", "file name must be lowercase-with-dashes")
                self.assertTrue(f.get("title"), "needs a title")
                self.assertTrue(f.get("creator"), "needs a creator")
                self.assertIn(f.get("type"), TYPES)
                self.assertIn(f.get("status"), STATUSES)
                self.assertRegex(f.get("started", ""), r"^\d{4}-\d{2}-\d{2}$", "started must be YYYY-MM-DD")
                if f.get("bluesky_thread"):
                    self.assertRegex(f["bluesky_thread"].strip('"'), r"^https://bsky\.app/profile/[A-Za-z0-9.:_-]+/post/[A-Za-z0-9]+$",
                                     "bluesky_thread must be a link to a Bluesky post")
                if f.get("status") == "finished":
                    self.assertRegex(f.get("finished", ""), r"^\d{4}-\d{2}-\d{2}$", "finished logs need a finished date")

    def test_entries_are_valid(self):
        logs = self.logs()
        files = sorted((REPO / "_entries").glob("*.txt")) + sorted((REPO / "_entries").glob("*.md"))
        for path in files:
            with self.subTest(entry=path.name):
                f, body = split(path)
                self.assertIn(f.get("log"), logs, "log must match a file in _logs/")
                # e.g. 2026-11-10 21:14:03 +0100. The offset matters: without it
                # a late-night post can land on the wrong day.
                datetime.strptime(f.get("date", ""), "%Y-%m-%d %H:%M:%S %z")
                ch = f.get("chapter", "")
                self.assertRegex(ch, r'^$|^\d+$|^".*"$', "chapter must be blank, a number, or quoted text")
                if f.get("source_url"):
                    self.assertRegex(f["source_url"], r"^https://bsky\.app/profile/[^/]+/post/[A-Za-z0-9]+$")
                text = body.strip()
                self.assertTrue(text, "entry text is empty")
                if "{{" in text or "{%" in text:
                    self.assertTrue(text.startswith(RAW_OPEN) and text.endswith(RAW_CLOSE),
                                    "contains template code; it must be wrapped in raw markers")
                    inner = text[len(RAW_OPEN):-len(RAW_CLOSE)]
                    self.assertNotRegex(inner, r"\{%-?\s*endraw", "contains an endraw marker inside")
                # Every picture, GIF and video an entry lists must be in the repo.
                for src in re.findall(r'src:\s*"([^"]+)"', path.read_text(encoding="utf-8")):
                    self.assertTrue((REPO / src.lstrip("/")).exists(), f"media file {src} is missing from the repo")

    def test_no_template_code_in_template_comments(self):
        pattern = re.compile(r"\{%-?\s*comment\s*-?%\}(.*?)\{%-?\s*endcomment\s*-?%\}", re.S)
        files = [p for p in REPO.rglob("*") if p.suffix in (".html", ".xml", ".json")
                 and "_site" not in p.parts and ".git" not in p.parts]
        for path in files:
            for m in pattern.finditer(path.read_text(encoding="utf-8")):
                with self.subTest(file=str(path.relative_to(REPO))):
                    self.assertNotIn("{{", m.group(1))
                    self.assertNotIn("{%", m.group(1))


if __name__ == "__main__":
    unittest.main()
