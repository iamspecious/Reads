"""
Tests for scripts/sync_bluesky.py: which logs get synced, and that one
failing thread doesn't stop the others. The importer itself is swapped for
a pretend one, because its own tests already cover talking to Bluesky.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sync_bluesky  # noqa: E402

THREAD = "https://bsky.app/profile/me.test/post/3abc"


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        logs = self.repo / "_logs"
        logs.mkdir()
        def log(slug, status, thread=None):
            extra = f"bluesky_thread: {thread}\n" if thread else ""
            (logs / f"{slug}.md").write_text(f"---\ntitle: T\nstatus: {status}\n{extra}---\n")
        log("reading-with-thread", "reading", THREAD)
        log("quoted-thread", "reading", f'"{THREAD}"')
        log("reading-no-thread", "reading")
        log("finished-with-thread", "finished", THREAD)
        self.calls = []
        self._real = sync_bluesky.import_bluesky.main
        sync_bluesky.import_bluesky.main = lambda argv: self.calls.append(argv)

    def tearDown(self):
        sync_bluesky.import_bluesky.main = self._real
        self.tmp.cleanup()

    def test_only_reading_logs_with_a_thread(self):
        found = sync_bluesky.logs_to_sync(self.repo)
        self.assertEqual(found, [("quoted-thread", THREAD), ("reading-with-thread", THREAD)])

    def test_one_failure_does_not_stop_the_rest(self):
        def flaky(argv):
            self.calls.append(argv)
            if argv[1] == "quoted-thread":
                raise SystemExit("Bluesky said no")
        sync_bluesky.import_bluesky.main = flaky
        code = sync_bluesky.main(["--repo", str(self.repo)])
        self.assertEqual(code, 1)
        self.assertEqual([c[1] for c in self.calls], ["quoted-thread", "reading-with-thread"])

    def test_dry_run_is_passed_on(self):
        sync_bluesky.main(["--repo", str(self.repo), "--dry-run"])
        self.assertTrue(all("--dry-run" in c for c in self.calls))


if __name__ == "__main__":
    unittest.main()
