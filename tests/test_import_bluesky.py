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

    chain_extra_branch = None  # optional (index in chain, [posts]) for a second, deep branch of mine

    def branch_node(self, posts, j, levels_left):
        n = {"$type": "app.bsky.feed.defs#threadViewPost", "post": posts[j]}
        if levels_left == 0:
            return n
        n["replies"] = [self.branch_node(posts, j + 1, levels_left - 1)] if j + 1 < len(posts) else []
        return n

    def node(self, i, levels_left):
        n = {"$type": "app.bsky.feed.defs#threadViewPost", "post": self.chain[i]}
        if levels_left == 0:
            return n  # cut off: no "replies" key at all, just like the real API
        replies = [{"$type": "app.bsky.feed.defs#threadViewPost", "post": r} for r in self.extra.get(i, [])]
        if self.chain_extra_branch and self.chain_extra_branch[0] == i:
            replies.append(self.branch_node(self.chain_extra_branch[1], 0, levels_left - 1))
        if i + 1 < len(self.chain):
            replies.append(self.node(i + 1, levels_left - 1))
        n["replies"] = replies
        return n

    def xrpc(self, method, **params):
        self.calls += 1
        if method == "com.atproto.identity.resolveHandle":
            return {"did": ME}
        uri = params["uri"]
        for k, p in enumerate(self.chain):
            if p["uri"] == uri:
                return {"thread": self.node(k, self.depth)}
        if self.chain_extra_branch:
            posts = self.chain_extra_branch[1]
            for j, p in enumerate(posts):
                if p["uri"] == uri:
                    return {"thread": self.branch_node(posts, j, self.depth)}
        # The side-branch posts from setUp have no replies of their own.
        for kids in self.extra.values():
            for p in kids:
                if p["uri"] == uri:
                    return {"thread": {"$type": "app.bsky.feed.defs#threadViewPost", "post": p, "replies": []}}
        raise AssertionError(f"unknown uri {uri}")


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
        imp._pds_cache.clear()
        self.downloads = []

        def fake_get(url, want_json=True, tries=5):
            # Pretend to be every server the importer talks to besides the API.
            self.downloads.append(url)
            if url.startswith("https://plc.directory/"):
                return {"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.test"}]}
            if url.startswith("https://static.klipy.com/") and url.endswith(".mp4"):
                return b"\x00\x00\x00\x18ftypmp42fakeklipy", "video/mp4"
            if url.startswith(("https://media.tenor.com/", "https://static.klipy.com/")):
                return b"GIF89afakegif", "image/gif"
            if url.startswith("https://pds.test/xrpc/com.atproto.sync.getBlob"):
                # Real servers often send videos as a generic file type.
                return b"\x00\x00\x00\x18ftypmp42fakevideo", "application/octet-stream"
            return b"\xff\xd8fakejpeg", "image/jpeg"
        imp.http_get = fake_get

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
        self.assertEqual(len(files), 16)  # 15 in the main chain + my side branch
        self.assertGreater(self.fake.calls, 2, "should have fetched again after the cut-off")
        all_text = "".join(f.read_text() for f in files)
        self.assertNotIn("stranger", all_text)

    def test_every_branch_of_my_replies_is_followed(self):
        # The side branch under post 5 is mine, so it's included too.
        self.run_import()
        all_text = "".join(f.read_text() for f in self.entries())
        self.assertIn("late side branch", all_text)
        self.assertEqual(len(self.entries()), 16)

    def test_new_posts_on_a_later_branch_are_found(self):
        # Like the real thread: I replied to an older post again and carried
        # on from there. The new chain hangs off post 3 and is 8 posts long,
        # deeper than the fake depth of 4, so "fetch again" is needed too.
        branch = [post(300 + n, text=f"branch post {n}") for n in range(8)]
        for n, p in enumerate(branch):
            p["record"]["createdAt"] = f"2026-11-12T0{n}:00:00.000Z"
        self.fake.chain_extra_branch = (3, branch)
        self.run_import()
        all_text = "".join(f.read_text() for f in self.entries())
        for n in range(8):
            self.assertIn(f"branch post {n}", all_text)

    def test_rerun_skips_everything(self):
        self.run_import()
        before = [f.read_text() for f in self.entries()]
        self.run_import()
        self.assertEqual([f.read_text() for f in self.entries()], before)

    def test_new_posts_in_a_growing_thread_are_added(self):
        full = list(self.chain)
        self.fake.chain = full[:10]
        self.run_import()
        self.assertEqual(len(self.entries()), 11)  # 10 in the chain + my side branch under post 5
        self.fake.chain = full
        self.run_import()
        self.assertEqual(len(self.entries()), 16)

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
        self.assertEqual(imp.find_chapter("31: kiss of life I guess?"), 31)
        self.assertEqual(imp.find_chapter("34-37: wow"), "34-37")
        self.assertIsNone(imp.find_chapter("10 reasons Nesta is insufferable"))
        self.assertIsNone(imp.find_chapter("It was 3:00 when I finished"))
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
        self.assertEqual(len(by_rkey), 15)

    def test_range_is_written_quoted(self):
        self.chain[4]["record"]["text"] = "Chapter 7 - 9 : wow"
        self.run_import()
        f = next(f for f in self.entries() if f.stem.endswith("rk004"))
        self.assertIn('chapter: "7-9"\n', f.read_text())

    def entry(self, rkey):
        return next(f for f in self.entries() if f.stem.endswith(rkey)).read_text()

    def test_gif_from_the_gif_button(self):
        # A Tenor GIF arrives as a link card, not an image.
        self.chain[5]["embed"] = {"$type": "app.bsky.embed.external#view", "external": {
            "uri": "https://media.tenor.com/abc/nesta-eyeroll.gif?hh=200&ww=300",
            "title": "nesta eyeroll", "description": "Alt: woman rolling her eyes"}}
        self.run_import()
        text = self.entry("rk005")
        self.assertIn('  - src: "/assets/images/entries/rk005-1.gif"', text)
        self.assertIn('    alt: "woman rolling her eyes"', text)
        self.assertTrue((self.repo / "assets/images/entries/rk005-1.gif").exists())

    def test_gif_from_klipy_like_the_real_thread(self):
        # Copied from what Bluesky actually stored for the Chapter 33 post.
        self.chain[5]["embed"] = {"$type": "app.bsky.embed.external#view", "external": {
            "uri": "https://static.klipy.com/ii/935d7ab9d8c6202580a668421940ec81/b2/ae/377VY4PU.gif?hh=455&ww=498&mp4=2NCQAYdW&webm=6BxOUMBNXH6HuOD",
            "title": "Barbie Doll's Surprised Disgusted Look",
            "description": "ALT: Barbie Doll's Surprised Disgusted Look"}}
        self.run_import()
        text = self.entry("rk005")
        # Saved as Klipy's small MP4 copy, played like a GIF.
        self.assertIn('videos:\n  - src: "/assets/images/entries/rk005-1.mp4"\n    alt: "Barbie Doll\'s Surprised Disgusted Look"\n    gif: true', text)
        self.assertIn("https://static.klipy.com/ii/935d7ab9d8c6202580a668421940ec81/b2/ae/2NCQAYdW.mp4", self.downloads)
        self.assertFalse(any(u.endswith("377VY4PU.gif?hh=455&ww=498&mp4=2NCQAYdW&webm=6BxOUMBNXH6HuOD") for u in self.downloads))

    def test_ordinary_link_cards_are_not_downloaded(self):
        self.chain[5]["embed"] = {"$type": "app.bsky.embed.external#view", "external": {
            "uri": "https://example.com/evil.gif", "title": "x", "description": ""}}
        self.run_import()
        self.assertNotIn("images:", self.entry("rk005"))
        self.assertFalse(any("example.com" in u for u in self.downloads))

    def test_uploaded_gif_becomes_a_looping_video(self):
        self.chain[6]["embed"] = {"$type": "app.bsky.embed.video#view", "cid": "bafyvideo",
                                  "presentation": "gif", "alt": "dancing"}
        self.run_import()
        text = self.entry("rk006")
        self.assertIn('videos:\n  - src: "/assets/images/entries/rk006-1.mp4"\n    alt: "dancing"\n    gif: true', text)
        self.assertIn("https://pds.test/xrpc/com.atproto.sync.getBlob?did=did%3Aplc%3Ame&cid=bafyvideo", self.downloads)

    def test_older_entries_get_their_missing_gif(self):
        self.run_import()
        before = self.entry("rk007")
        self.assertNotIn("images:", before)
        # Same post, seen again by a later sync, now that GIFs are understood.
        self.chain[7]["embed"] = {"$type": "app.bsky.embed.external#view", "external": {
            "uri": "https://media.tenor.com/x/y.gif", "title": "t", "description": "d"}}
        self.run_import()
        after = self.entry("rk007")
        self.assertIn('images:\n  - src: "/assets/images/entries/rk007-1.gif"', after)
        # The text below the front matter is untouched.
        self.assertEqual(before.split("\n---\n", 1)[1], after.split("\n---\n", 1)[1])
        # And a third run changes nothing.
        self.run_import()
        self.assertEqual(self.entry("rk007"), after)
        self.assertEqual(len(self.entries()), 16)

    def test_unknown_log_is_refused(self):
        with self.assertRaises(SystemExit):
            imp.main(["https://bsky.app/profile/me.test/post/rk000", "nope", "--repo", str(self.repo)])


if __name__ == "__main__":
    unittest.main()
