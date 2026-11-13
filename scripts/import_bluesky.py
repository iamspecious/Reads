#!/usr/bin/env python3
"""
import_bluesky.py: copy a Bluesky thread into the site, one entry per post.

WHAT IT DOES, STEP BY STEP
 1. You give it a post link (https://bsky.app/profile/<you>/post/<id>)
    and a log slug (the log's file name, like "a-court-of-silver-flames").
 2. It turns your handle (booksbyspec.bsky.social) into your DID.
    A DID is your permanent account ID. Handles can change; DIDs don't.
 3. It asks Bluesky's public API for the thread (app.bsky.feed.getPostThread).
    No login is needed, because the posts are public.
 4. It walks down the thread, only stepping into replies that YOU wrote
    to yourself. Other people's replies are skipped.
 5. For each post, it downloads any pictures, GIFs and videos into this
    repo and writes one file in _entries/ with the original time and the
    link to the post.
 6. It skips posts that are already in _entries/, so you can run it again
    on a thread that is still growing and only the new posts get added.
    (One exception: if an older entry is missing a GIF, picture or video
    that its post has, the media is added to it. Its text is never changed.)

HOW TO RUN IT
 It is meant to run on GitHub, through the "Import Bluesky thread" action
 (.github/workflows/import-bluesky.yml). By hand it would be:
     python scripts/import_bluesky.py <post link> <log slug>
 Add --dry-run to see what would happen without writing anything.

WHY ONLY PYTHON'S BUILT-IN TOOLS
 No "pip install" step means nothing extra to download, nothing to keep
 up to date, and nothing that can break when a library changes.

WHY ENTRIES ARE .txt FILES AND NOT .md
 Jekyll runs .md files through Markdown. On Bluesky, "#ACOSF" is a hashtag,
 but Markdown turns a line starting with "#" into a big heading, and it also
 joins lines you meant to keep apart. A .txt file is left alone, so the post
 shows up exactly as you wrote it. The templates escape it and keep the
 line breaks (see _includes/entry-card.html).

THE FILES THIS SCRIPT WRITES HAVE NO COMMENTS ON PURPOSE
 Entry files and images are data, not code. Every note about them lives
 here, in the script, so the data stays clean.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Bluesky's public "AppView" server. It answers read-only questions with no
# login. (bsky.social is the login server; we don't need it.)
API = "https://public.api.bsky.app/xrpc"

# The biggest "depth" getPostThread accepts. The default is only 6, which
# means a long thread gets cut off after 6 replies. 1000 is the max the API
# allows, so we ask for all of it.
# Tripped me up: even at depth 1000, very long chains can come back shorter
# than you'd expect. That's why walk_thread() also re-fetches from the last
# post it reached (see "fetch again" below), instead of trusting one call.
MAX_DEPTH = 1000

# A name for our requests, so Bluesky can see what kind of tool is calling.
# It doesn't say who you are, on purpose.
USER_AGENT = "reads-bluesky-importer/1.0 (+static site importer)"

# Spots "Ch 12", "Ch. 12", "ch12", "Chapter 12", "Chap 3", and ranges like
# "Chapter 7 - 9", "Chapters 12-14", "Ch 7 to 9", "Ch 7–9".
# \b means "start of a word", so "much 12" does NOT count as "ch 12".
# A range is kept as a range ("7-9"), so the badge says what the post covers.
# Tripped me up: at first only the first number was kept, so a post about
# chapters 7 to 9 was labelled as just chapter 7.
CHAPTER_RE = re.compile(
    r"\bch(?:apters?|aps?|pts?)?\.?\s*#?\s*(\d{1,4})\b(?:\s*(?:-|–|—|to|through|thru)\s*(\d{1,4})\b)?",
    re.IGNORECASE)

# Jekyll treats "{{" and "{%" in a file as template code, which could break
# the whole site build. Posts containing them get wrapped in "raw" markers,
# which tell Jekyll to leave the text alone. The site strips the markers again
# when it shows the text (_includes/entry-text.html), so readers see the post
# exactly as written. The markers are built from pieces so this script's own
# text never looks like one.
RAW_OPEN, RAW_CLOSE = "{" + "% raw %" + "}", "{" + "% endraw %" + "}"
ENDRAW_RE = re.compile(r"\{%-?\s*endraw")

# Pulls the handle (or DID) and the post ID out of a bsky.app link.
POST_URL_RE = re.compile(r"^https?://(?:www\.)?bsky\.app/profile/([^/]+)/post/([A-Za-z0-9]+)/?(?:[?#].*)?$")

# The last part of any Bluesky post link. Used to spot posts already imported.
RKEY_IN_URL_RE = re.compile(r"/post/([A-Za-z0-9]+)")

# Image types we know how to name. Bluesky's CDN almost always sends JPEG.
EXT_FOR_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
VIDEO_EXT_FOR_TYPE = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}

# GIFs picked from Bluesky's GIF button come from a GIF service. Bluesky
# doesn't attach them as images: it stores a LINK to the .gif on that
# service's server.
# Tripped me up, twice: the first version only looked for attached images,
# so every GIF from the picker was left out. The second only trusted Tenor,
# but Bluesky's GIF button had moved to Klipy (static.klipy.com), so the GIFs
# were still left out. Found by printing what Bluesky actually stored for
# the posts. If GIFs stop appearing again, check the service hasn't changed.
# Only these hosts are trusted for GIF links, so a random link card can't
# make the script download something from anywhere on the internet.
GIF_HOSTS = {"static.klipy.com", "media.tenor.com"}

# A GIF bigger than this is left on Bluesky (the entry keeps its link to it),
# so one huge GIF can't bloat the repo.
MAX_GIF_BYTES = 15 * 1024 * 1024

# Videos (and GIFs uploaded from a phone, which Bluesky turns into short
# videos) are copied too, up to this size. GitHub refuses files over 100 MB
# and big files slow the repo down, so anything bigger is left on Bluesky
# and the entry keeps its "Bluesky" link to it.
MAX_VIDEO_BYTES = 30 * 1024 * 1024


# ─── Talking to Bluesky ────────────────────────────────────────────────────

def http_get(url, want_json=True, tries=5):
    """Fetch a URL. Try again a few times if Bluesky is busy.

    Why retry: a public API can say "slow down" (error 429) or have a short
    hiccup (errors 500 and up). Waiting a moment and asking again usually
    works. Each wait is twice as long as the last one (1s, 2s, 4s...), so we
    back off politely instead of hammering the server.
    Other errors (like 400 "bad request") won't fix themselves, so we stop.
    """
    delay = 1.0
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                if want_json:
                    return json.loads(body)
                return body, resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as err:
            if err.code == 429 or err.code >= 500:
                if attempt == tries:
                    raise
                print(f"  Bluesky said {err.code}, waiting {delay:.0f}s and trying again…")
            else:
                detail = err.read().decode("utf-8", "replace")[:300]
                raise SystemExit(f"Bluesky refused the request ({err.code}): {detail}\nURL: {url}")
        except urllib.error.URLError as err:
            if attempt == tries:
                raise SystemExit(f"Could not reach Bluesky: {err.reason}\nURL: {url}")
            print(f"  Network hiccup ({err.reason}), waiting {delay:.0f}s and trying again…")
        time.sleep(delay)
        delay *= 2
    raise SystemExit("Gave up after several tries.")


def xrpc(method, **params):
    """Call one Bluesky API method. "xrpc" is just Bluesky's name for its web API."""
    return http_get(f"{API}/{method}?{urllib.parse.urlencode(params)}")


def resolve_did(handle_or_did):
    """Turn a handle into a DID. If we were given a DID already, keep it."""
    if handle_or_did.startswith("did:"):
        return handle_or_did
    return xrpc("com.atproto.identity.resolveHandle", handle=handle_or_did)["did"]


def fetch_thread(at_uri):
    """Get one post and the replies under it, as deep as Bluesky allows.

    parentHeight=0: we don't want the posts ABOVE this one, only below.
    """
    data = xrpc("app.bsky.feed.getPostThread", uri=at_uri, depth=MAX_DEPTH, parentHeight=0)
    node = data.get("thread", {})
    if node.get("$type") != "app.bsky.feed.defs#threadViewPost":
        # notFoundPost or blockedPost: deleted, private, or blocked.
        raise SystemExit(f"Bluesky couldn't show this post ({node.get('$type')}): {at_uri}")
    return node


# ─── Walking the thread ────────────────────────────────────────────────────

def next_self_reply(node, my_did):
    """Find the reply to this post that I wrote myself. Return None if there isn't one.

    Replies can be real posts, or placeholders for deleted/blocked posts.
    We only keep real posts (threadViewPost) whose author is me.

    If I somehow replied to my own post twice (a fork in the thread), we
    follow the EARLIEST reply. That's the most likely "next" post, and it
    keeps the order stable every time the script runs. We say so in the
    output so a fork is never skipped silently.
    """
    mine = [
        r for r in node.get("replies") or []
        if r.get("$type") == "app.bsky.feed.defs#threadViewPost"
        and r["post"]["author"]["did"] == my_did
    ]
    if not mine:
        return None
    mine.sort(key=lambda r: r["post"]["record"].get("createdAt", ""))
    if len(mine) > 1:
        skipped = ", ".join(r["post"]["uri"].rsplit("/", 1)[-1] for r in mine[1:])
        print(f"  Note: you replied to yourself more than once here. Following the earliest; other branch(es) not followed: {skipped}")
    return mine[0]


def walk_thread(start_uri, my_did):
    """Collect my posts in order, from the start post to the end of my chain.

    "Fetch again" rule: when we reach a post with no self-reply in what
    Bluesky sent back, that might be the real end, or it might be where
    Bluesky stopped sending (the depth cut-off). We can't always tell, so
    we simply ask again starting FROM that last post. If that fresh answer
    still has no self-reply, it really is the end.
    Tripped me up: a cut-off post looks almost the same as a real last post.
    The only hint is that "replies" is missing instead of an empty list, and
    that hint isn't reliable enough to trust alone.
    """
    posts = []
    seen = set()
    node = fetch_thread(start_uri)
    refetched_from = None
    calls = 1

    while True:
        uri = node["post"]["uri"]
        if uri not in seen:
            posts.append(node["post"])
            seen.add(uri)

        child = next_self_reply(node, my_did)
        if child is not None:
            node = child
            continue

        if refetched_from == uri:
            break  # we already asked again from here: this is the real end
        print(f"  Reached post {len(posts)} with no more replies loaded. Fetching again from there to make sure…")
        node = fetch_thread(uri)
        refetched_from = uri
        calls += 1
        if calls > 500:
            # Safety net so a strange API answer can never loop forever.
            raise SystemExit("Stopped after 500 fetches. Something looks wrong with this thread.")

    print(f"  Walked the thread with {calls} API call(s).")
    return posts


# ─── Reading what's already in the repo ────────────────────────────────────

def site_timezone(repo):
    """Read "timezone:" from _config.yml so entries use the same zone as the site.

    We read the one line by hand, because Python can't read YAML (the
    config file format) without installing an extra library.
    """
    text = (repo / "_config.yml").read_text(encoding="utf-8")
    m = re.search(r"^timezone:\s*([^\s#]+)", text, re.MULTILINE)
    return ZoneInfo(m.group(1) if m else "UTC")


def already_imported(entries_dir):
    """Return {post ID: entry file} for every entry that came from Bluesky.

    We match on the post ID at the end of source_url, not the whole link.
    Why: the link contains your handle. If you ever change your handle, the
    same post gets a different link, and matching the full link would import
    everything a second time. The post ID never changes.
    """
    ids = {}
    for f in entries_dir.glob("*.*"):
        if f.suffix not in (".txt", ".md"):
            continue
        for line in f.read_text(encoding="utf-8").splitlines()[1:40]:
            if line.strip() == "---":
                break  # end of front matter
            if line.startswith("source_url:"):
                m = RKEY_IN_URL_RE.search(line)
                if m:
                    ids[m.group(1)] = f
    return ids


# ─── Making one entry ──────────────────────────────────────────────────────

def find_chapter(text):
    """Return the chapter mentioned in the text: 12, or "7-9" for a range, or None."""
    m = CHAPTER_RE.search(text)
    if not m:
        return None
    start, end = m.group(1), m.group(2)
    if end and int(end) > int(start):
        return f"{int(start)}-{int(end)}"
    return int(start)


def post_media(post, did):
    """List the pictures, GIFs and videos in a post, in order.

    Each item is a dict: kind ("image" or "video"), url (where to download
    it), alt (its description), and gif (True if it should play like a GIF:
    on a silent loop).

    Media can sit in three kinds of "embed" (Bluesky's word for what's
    attached under a post):
      - images: plain pictures, up to four.
      - external: a link card. A GIF from the GIF button is one of these,
        pointing at a .gif on Tenor (see GIF_HOSTS).
      - video: an uploaded video, or an uploaded GIF that Bluesky turned
        into a video. Bluesky only hands out a streaming playlist for these,
        so we fetch the original file from your account's server instead
        (see video_url).
    A post that quotes another post AND has media wraps it in
    "recordWithMedia", so we unwrap that first.
    """
    embed = post.get("embed") or {}
    kind = embed.get("$type", "")
    if kind == "app.bsky.embed.recordWithMedia#view":
        embed = embed.get("media") or {}
        kind = embed.get("$type", "")

    if kind == "app.bsky.embed.images#view":
        return [{"kind": "image", "url": img["fullsize"], "alt": img.get("alt", ""), "gif": False}
                for img in embed.get("images", [])]

    if kind == "app.bsky.embed.external#view":
        ext = embed.get("external") or {}
        uri = ext.get("uri", "")
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme == "https" and parsed.hostname in GIF_HOSTS and parsed.path.lower().endswith(".gif"):
            # The GIF picker writes a description like "ALT: Barbie's
            # disgusted look". It's the best alt text there is; the "ALT:"
            # label in front is dropped because screen readers don't need it.
            alt = ext.get("description") or ext.get("title") or ""
            alt = re.sub(r"^\s*alt:\s*", "", alt, flags=re.IGNORECASE)
            # Klipy keeps an MP4 copy of every GIF right next to it, named
            # by the "mp4=" code in the link. It looks the same and is far
            # smaller (one GIF was 20 MB, its MP4 0.5 MB), so we take that
            # and show it as a silent looping video. Bluesky does the same.
            # Tripped me up: the MP4 is NOT the GIF's name with .mp4 on the
            # end; it's named by the mp4= code. Checked by asking Klipy for
            # each candidate address.
            mp4 = urllib.parse.parse_qs(parsed.query).get("mp4", [""])[0]
            if parsed.hostname == "static.klipy.com" and re.fullmatch(r"[A-Za-z0-9]{4,64}", mp4):
                folder = parsed.path.rsplit("/", 1)[0]
                return [{"kind": "video", "url": f"https://static.klipy.com{folder}/{mp4}.mp4", "alt": alt, "gif": True}]
            return [{"kind": "image", "url": uri, "alt": alt, "gif": True}]
        return []  # an ordinary link card: nothing to copy

    if kind == "app.bsky.embed.video#view":
        cid = embed.get("cid")
        if not cid:
            return []
        # "presentation: gif" is how Bluesky marks an uploaded GIF.
        is_gif = embed.get("presentation") == "gif"
        return [{"kind": "video", "url": video_url(did, cid), "alt": embed.get("alt", ""), "gif": is_gif}]

    return []


_pds_cache = {}


def video_url(did, cid):
    """Where to download a video's original file.

    Every Bluesky account lives on a "PDS" (personal data server). It keeps
    the original file of everything you upload. We look up which server
    yours is (from your DID document, the public record of your account)
    and ask it for the file by its ID ("cid").
    """
    if did not in _pds_cache:
        if did.startswith("did:plc:"):
            doc = http_get(f"https://plc.directory/{did}")
        elif did.startswith("did:web:"):
            doc = http_get(f"https://{did[len('did:web:'):]}/.well-known/did.json")
        else:
            raise SystemExit(f"Don't know how to look up the account server for {did}")
        pds = next((s["serviceEndpoint"] for s in doc.get("service", []) if s.get("id", "").endswith("#atproto_pds")), None)
        if not pds or not pds.startswith("https://"):
            raise SystemExit(f"Couldn't find the account server for {did}")
        _pds_cache[did] = pds.rstrip("/")
    query = urllib.parse.urlencode({"did": did, "cid": cid})
    return f"{_pds_cache[did]}/xrpc/com.atproto.sync.getBlob?{query}"


def download_media(item, dest_stem):
    """Save one picture, GIF or video into the repo. Returns the saved path, or None if it was skipped.

    Why download instead of linking: if a post is deleted, or Bluesky or
    Tenor move their files, a linked picture would break. A copy in the
    repo stays. If the file is already there from an earlier run, we keep
    it and skip the download.
    """
    types = VIDEO_EXT_FOR_TYPE if item["kind"] == "video" else EXT_FOR_TYPE
    for ext in types.values():
        existing = dest_stem.with_suffix(ext)
        if existing.exists():
            return existing
    body, ctype = http_get(item["url"], want_json=False)
    ctype = ctype.split(";")[0].strip().lower()
    # Account servers sometimes send videos as a generic "file" type, so for
    # videos we also check the file's own first bytes: every MP4 has "ftyp" there.
    if item["kind"] == "video" and ctype not in types and body[4:8] == b"ftyp":
        ctype = "video/mp4"
    # Only save real images/videos. If the server sends back anything else
    # (an error page, say), we stop rather than save it under the wrong name.
    ext = types.get(ctype)
    if not ext:
        raise SystemExit(f"Expected {item['kind']} but got '{ctype}' from {item['url']}. Nothing was written for this post; run the import again later.")
    if item["gif"] and item["kind"] == "image" and len(body) > MAX_GIF_BYTES:
        print(f"  Note: a GIF in post {dest_stem.name} is {len(body) // (1024 * 1024)} MB, over the {MAX_GIF_BYTES // (1024 * 1024)} MB limit. It stays on Bluesky; the entry links to it.")
        return None
    if item["kind"] == "video" and len(body) > MAX_VIDEO_BYTES:
        print(f"  Note: a video in post {dest_stem.name} is {len(body) // (1024 * 1024)} MB, over the {MAX_VIDEO_BYTES // (1024 * 1024)} MB limit. It stays on Bluesky; the entry links to it.")
        return None
    path = dest_stem.with_suffix(ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def save_media(post, did, rkey, repo, dry_run):
    """Download a post's media. Returns (images, videos) as lists of front-matter dicts.

    Everything goes in assets/images/entries/, videos included, so the
    site's media lives in one folder (and the workflows only add one).
    Files are named <post ID>-<number>, so each post's files stay together.
    """
    images, videos = [], []
    for i, item in enumerate(post_media(post, did), start=1):
        dest = repo / "assets" / "images" / "entries" / f"{rkey}-{i}"
        if dry_run:
            src = f"/assets/images/entries/{rkey}-{i}" + (".mp4" if item["kind"] == "video" else ".gif" if item["gif"] else ".jpg")
        else:
            saved = download_media(item, dest)
            if saved is None:
                continue
            src = "/" + saved.relative_to(repo).as_posix()
        if item["kind"] == "video":
            videos.append({"src": src, "alt": item["alt"], "gif": item["gif"]})
        else:
            images.append({"src": src, "alt": item["alt"]})
    return images, videos


def media_lines(images, videos):
    """The front-matter lines that list a post's media."""
    lines = []
    if images:
        lines.append("images:")
        for m in images:
            lines.append(f"  - src: {yaml_str(m['src'])}")
            lines.append(f"    alt: {yaml_str(m['alt'])}")
    if videos:
        lines.append("videos:")
        for m in videos:
            lines.append(f"  - src: {yaml_str(m['src'])}")
            lines.append(f"    alt: {yaml_str(m['alt'])}")
            lines.append(f"    gif: {'true' if m['gif'] else 'false'}")
    return lines


def add_missing_media(post, did, path, repo, dry_run):
    """Give an already-imported entry the media it was missing. Returns True if it changed.

    Why: entries imported before GIFs and videos were supported have no
    media listed. When a sync sees one of those posts again, it adds the
    media. Only the front matter changes: the text is never touched, and an
    entry that already lists any media is left completely alone.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return False
    fm, body = text[4:].split("\n---\n", 1)
    if re.search(r"^(images|videos):", fm, re.MULTILINE):
        return False
    if not post_media(post, did):
        return False
    rkey = post["uri"].rsplit("/", 1)[-1]
    images, videos = save_media(post, did, rkey, repo, dry_run)
    if not images and not videos:
        return False
    new = "---\n" + fm + "\n" + "\n".join(media_lines(images, videos)) + "\n---\n" + body
    if not dry_run:
        path.write_text(new, encoding="utf-8")
    print(f"  Added {len(images) + len(videos)} picture(s)/video(s) to existing entry {path.name}")
    return True


def yaml_str(s):
    """Write text so the front matter can't be broken by it.

    Front matter is YAML. Text with a ":" or a quote mark in it can confuse
    YAML. A JSON-style string in double quotes is always valid YAML, so we
    use that. ensure_ascii=False keeps emoji as emoji instead of codes.
    """
    return json.dumps(s, ensure_ascii=False)


def write_entry(post, log_slug, handle, did, tz, repo, dry_run):
    """Write one _entries/ file for one post. Returns a short summary dict."""
    record = post["record"]
    text = record.get("text", "")
    rkey = post["uri"].rsplit("/", 1)[-1]
    # The post ID becomes part of file names. Bluesky's IDs are plain letters
    # and numbers; anything else (like "../") is refused, so data from the
    # internet can never make the script write outside its own folders.
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", rkey):
        raise SystemExit(f"Unexpected post ID {rkey!r}. Stopping without writing it.")

    # The original time the post was made. Bluesky stores it in UTC ("Z").
    # We turn it into site time, so a post made late at night shows the
    # date it was for you, not the date in London.
    # Tripped me up: older Pythons can't read the trailing "Z", so it's swapped
    # for "+00:00", which means the same thing.
    created = datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00")).astimezone(tz)

    # File name: date, time, then the post ID. Sorted by name = sorted by time,
    # and the post ID makes it unique and stable forever (it's also the share link).
    stem = f"{created:%Y-%m-%d-%H%M%S}-{rkey}"
    source_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
    chapter = find_chapter(text)

    # The words are never changed. A post with "{{" or "{%" is only wrapped
    # in raw markers (see RAW_OPEN above) so it can't break the build.
    # The one thing the wrapper can't hold is the word that ends it, so a post
    # containing it is skipped and reported, instead of breaking the site.
    if ENDRAW_RE.search(text):
        print(f"  Skipped post {rkey}: it contains the text '{{% endraw', which would break the site build. Add it by hand without that part.")
        return None
    body = f"{RAW_OPEN}{text}{RAW_CLOSE}" if ("{{" in text or "{%" in text) else text

    images, videos = save_media(post, did, rkey, repo, dry_run)

    # Build the file. Only the fields the site uses, in the same order the
    # /new page writes them, so both kinds of entry look the same.
    lines = [
        "---",
        f"date: {created:%Y-%m-%d %H:%M:%S %z}",
        f"log: {log_slug}",
        f"chapter: {'' if chapter is None else yaml_str(chapter) if isinstance(chapter, str) else chapter}".rstrip(),
        f"source_url: {source_url}",
    ]
    lines += media_lines(images, videos)
    lines.append("---")
    # The text goes in exactly as posted (only raw-wrapped when needed, see
    # above). One newline is added at the very end because files should end
    # with one; the site trims it when showing.
    content = "\n".join(lines) + "\n" + body + "\n"

    path = repo / "_entries" / f"{stem}.txt"
    if not dry_run:
        path.write_text(content, encoding="utf-8")
    return {"file": path.name, "text": text, "chapter": chapter, "images": len(images) + len(videos), "time": created}


# ─── Putting it together ───────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a Bluesky self-thread into a log.")
    parser.add_argument("post_url", help="Link to the first post, e.g. https://bsky.app/profile/you.bsky.social/post/3abc…")
    parser.add_argument("log_slug", help="The log's file name without .md, e.g. a-court-of-silver-flames")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen, write nothing")
    # The repo folder defaults to the one above scripts/, so it works from anywhere.
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    entries_dir = repo / "_entries"

    # Check the log exists first. A typo in the slug would otherwise create
    # entries that point at nothing, and they'd never show up on a log page.
    if not (repo / "_logs" / f"{args.log_slug}.md").exists():
        known = ", ".join(sorted(p.stem for p in (repo / "_logs").glob("*.md"))) or "(none yet)"
        raise SystemExit(f"No log called '{args.log_slug}'. Make _logs/{args.log_slug}.md first. Logs that exist: {known}")

    m = POST_URL_RE.match(args.post_url.strip())
    if not m:
        raise SystemExit("That doesn't look like a Bluesky post link. It should look like https://bsky.app/profile/<handle>/post/<id>")
    handle, rkey = m.groups()

    print(f"Looking up {handle}…")
    did = resolve_did(handle)
    # An "at://" link is Bluesky's internal address for a post: account + post ID.
    start_uri = f"at://{did}/app.bsky.feed.post/{rkey}"

    print("Fetching the thread…")
    posts = walk_thread(start_uri, did)
    print(f"Found {len(posts)} post(s) by you in this thread.")

    # The start post must be yours too, or we'd import someone else's words.
    if posts and posts[0]["author"]["did"] != did:
        raise SystemExit("The first post isn't by the account in the link. Stopping.")

    done = already_imported(entries_dir)
    tz = site_timezone(repo)
    entries_dir.mkdir(exist_ok=True)

    written, skipped, topped_up = [], 0, 0
    for post in posts:
        existing = done.get(post["uri"].rsplit("/", 1)[-1])
        if existing:
            skipped += 1
            if add_missing_media(post, did, existing, repo, args.dry_run):
                topped_up += 1
            continue
        result = write_entry(post, args.log_slug, handle, did, tz, repo, args.dry_run)
        if result:
            written.append(result)

    report(posts, written, skipped, args, topped_up)
    return 0


def report(posts, written, skipped, args, topped_up=0):
    """Print what happened, and also put it on the GitHub Actions run page.

    GITHUB_STEP_SUMMARY is a file GitHub gives each run. Whatever we write
    to it shows up as a neat summary on the run's page, so you can check the
    count and the first/last post on your phone without opening the logs.
    """
    verb = "Would import" if args.dry_run else "Imported"
    lines = [
        f"## Bluesky import → `{args.log_slug}`",
        "",
        f"- Posts found in thread (yours only): **{len(posts)}**",
        f"- {verb}: **{len(written)}**",
        f"- Already imported, skipped: **{skipped}**",
        f"- With a chapter detected: **{sum(1 for w in written if w['chapter'] is not None)}**",
        f"- Pictures, GIFs and videos downloaded: **{sum(w['images'] for w in written)}**",
        f"- Older entries given their missing GIFs/pictures: **{topped_up}**",
    ]
    if written:
        first, last = written[0], written[-1]
        lines += [
            "",
            f"### First new post ({first['time']:%Y-%m-%d %H:%M})",
            "",
            *("> " + l for l in first["text"].splitlines() or [""]),
            "",
            f"### Last new post ({last['time']:%Y-%m-%d %H:%M})",
            "",
            *("> " + l for l in last["text"].splitlines() or [""]),
        ]
    out = "\n".join(lines)
    print("\n" + out)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(out + "\n")


if __name__ == "__main__":
    sys.exit(main())
