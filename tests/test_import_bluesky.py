"""
Tests for scripts/import_bluesky.py, using a pretend Bluesky.

Why a pretend Bluesky: the real one changes, can be slow, and could be
down when the tests run. Here we build a small fake thread in memory and
swap the script's network calls for ones that read from it. That way the
tests check OUR rules, every time, in under a second:
  - only my own replies are followed, other people's are skipped
  - when the thread looks cut off, the script fetches again from the last post
  - a second run imports nothing new
  - "Ch 12" / "Chapter 12" fill in the chapter, other text leaves it blank
  - images are saved into the repo, not linked
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_bluesky as imp  # noqa: E402  (has to come after the path line above)

ME = "did:plc:me"
STRANGER = "did:plc:someoneelse"


def post(n, did=ME, text=None, image=False):
    """Make one fake post, numbered so the tests can tell them apart."""
    p = {
        "uri": f"at://{did}/app.bsky.feed.post/rk{n:03d}",
        "author": {"did": did, "handle": "me.test"},
        "record": {"text": text if text is not None else f"post {n}", "createdAt": f"2026-11-10T{10 + n // 60:02d}:{n % 60:02d}:00.000Z"},
    }
    if image:
        p["embed"] = {"$type": "app.bsky.embed.images#view",
                      "images": [{"fullsize": f"https://cdn.test/img{n}", "alt": f"picture {n}"}]}
    return p


class FakeBluesky:
    """Pretends to be the API. It only ever sends `depth` levels at a time,
    like the real one, so the "fetch again" rule actually gets used."""

    def __init__(self, chain, extra_replies, depth):
        self.chain = chain                  # my posts, in order
        self.extra = extra_replies          # {index: [other replies under that post]}
        self.depth = depth
        self.calls = 0

    def node(self, i, levels_left):
        n = {"$type": "app.bsky.feed.defs#threadViewPost", "post": self.chain[i]}
        if levels_left == 0:
            return n  # cut off: no "replies" key at all, just like the real API
        replies = [{"$type": "app.bsky.feed.defs#threadViewPost", "post": r} for r in self.extra.get(i, [])]
        if i + 1 < len(self.chain):
            replies.append(self.node(i + 1, levels_left - 1))
        n["replies"] = replies
        return n

    def xrpc(self, method, **params):
        self.calls += 1
        if method == "com.atproto.identity.resolveHandle":
            return {"did": ME}
        uri = params["uri"]
        i = next(k for k, p in enumerate(self.chain) if p["uri"] == uri)
        return {"thread": self.node(i, self.depth)}


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "_logs").mkdir()
        (self.repo / "_entries").mkdir()
        (self.repo / "_logs" / "test-book.md").write_text("---\ntitle: Test\n---\n")
        (self.repo / "_config.yml").write_text("timezone: Europe/Berlin\n")

        texts = {1: "Starting! Ch 1 already hurts", 4: "Chapter 12: oh no", 6: "chapters 20-22 in one go",
                 7: "much 12 nothing", 8: "#ACOSF line one\nline two"}
        # 15 posts in my chain, deeper than the fake depth of 4.
        self.chain = [post(n, text=texts.get(n), image=(n == 3)) for n in range(15)]
        extra = {
            2: [post(100, did=STRANGER, text="a stranger's reply")],
            # A fork: a second, later self-reply under post 5. It must NOT be followed.
            5: [dict(post(200, text="late side branch"), record={"text": "late side branch", "createdAt": "2026-11-11T00:00:00.000Z"})],
        }
        self.fake = FakeBluesky(self.chain, extra, depth=4)
        self._real_xrpc, self._real_get = imp.xrpc, imp.http_get
        imp.xrpc = self.fake.xrpc
        imp.http_get = lambda url, want_json=True, tries=5: (b"\xff\xd8fakejpeg", "image/jpeg")

    def tearDown(self):
        imp.xrpc, imp.http_get = self._real_xrpc, self._real_get
        self.tmp.cleanup()

    def run_import(self):
        return imp.main(["https://bsky.app/profile/me.test/post/rk000", "test-book", "--repo", str(self.repo)])

    def entries(self):
        return sorted((self.repo / "_entries").glob("*.txt"))

    def test_imports_whole_chain_past_the_depth_limit(self):
        self.run_import()
        files = self.entries()
        self.assertEqual(len(files), 15)
        self.assertGreater(self.fake.calls, 2, "should have fetched again after the cut-off")
        all_text = "".join(f.read_text() for f in files)
        self.assertNotIn("stranger", all_text)
        self.assertNotIn("late side branch", all_text)

    def test_rerun_skips_everything(self):
        self.run_import()
        before = [f.read_text() for f in self.entries()]
        self.run_import()
        self.assertEqual([f.read_text() for f in self.entries()], before)

    def test_new_posts_in_a_growing_thread_are_added(self):
        full = list(self.chain)
        self.fake.chain = full[:10]
        self.run_import()
        self.assertEqual(len(self.entries()), 10)
        self.fake.chain = full
        self.run_import()
        self.assertEqual(len(self.entries()), 15)

    def test_chapter_detection(self):
        self.assertEqual(imp.find_chapter("Ch 12 is wild"), 12)
        self.assertEqual(imp.find_chapter("ch.7"), 7)
        self.assertEqual(imp.find_chapter("CHAPTER 30!!"), 30)
        self.assertEqual(imp.find_chapter("Chapters 12-14"), "12-14")
        self.assertEqual(imp.find_chapter("Chapter 7 - 9 : interesting"), "7-9")
        self.assertEqual(imp.find_chapter("ch 7 to 9"), "7-9")
        self.assertEqual(imp.find_chapter("Chapter 7 – 9"), "7-9")
        self.assertEqual(imp.find_chapter("Chapter 12 - 3 favourite lines"), 12)
        self.assertIsNone(imp.find_chapter("much 12 nothing"))
        self.assertIsNone(imp.find_chapter("no chapter number here"))

    def test_file_contents(self):
        self.run_import()
        by_rkey = {f.stem.rsplit("-", 1)[-1]: f.read_text() for f in self.entries()}
        # Times are moved into the site's time zone (Berlin is UTC+1 in November).
        self.assertIn("date: 2026-11-10 11:04:00 +0100", by_rkey["rk004"])
        self.assertIn("chapter: 12\n", by_rkey["rk004"])
        self.assertIn("chapter:\n", by_rkey["rk002"])
        self.assertIn("source_url: https://bsky.app/profile/me.test/post/rk008", by_rkey["rk008"])
        # The text is kept exactly, hashtags and line breaks included.
        self.assertTrue(by_rkey["rk008"].endswith("---\n#ACOSF line one\nline two\n"))
        # Images are saved into the repo and listed in the front matter.
        self.assertIn('  - src: "/assets/images/entries/rk003-1.jpg"', by_rkey["rk003"])
        self.assertTrue((self.repo / "assets/images/entries/rk003-1.jpg").exists())
        # No comments in generated files.
        for text in by_rkey.values():
            self.assertNotIn("#", text.split("---")[1])

    def test_template_code_is_wrapped_and_endraw_is_skipped(self):
        # Post 9 contains "{{", post 10 contains the one thing we can't wrap.
        self.chain[9]["record"]["text"] = "Feyre {{ is }} here {% wow"
        self.chain[10]["record"]["text"] = "sneaky {% endraw %} text"
        self.run_import()
        by_rkey = {f.stem.rsplit("-", 1)[-1]: f.read_text() for f in self.entries()}
        self.assertTrue(by_rkey["rk009"].endswith("---\n" + imp.RAW_OPEN + "Feyre {{ is }} here {% wow" + imp.RAW_CLOSE + "\n"))
        self.assertNotIn("rk010", by_rkey)
        self.assertEqual(len(by_rkey), 14)

    def test_range_is_written_quoted(self):
        self.chain[4]["record"]["text"] = "Chapter 7 - 9 : wow"
        self.run_import()
        f = next(f for f in self.entries() if f.stem.endswith("rk004"))
        self.assertIn('chapter: "7-9"\n', f.read_text())

    def test_unknown_log_is_refused(self):
        with self.assertRaises(SystemExit):
            imp.main(["https://bsky.app/profile/me.test/post/rk000", "nope", "--repo", str(self.repo)])


if __name__ == "__main__":
    unittest.main()
