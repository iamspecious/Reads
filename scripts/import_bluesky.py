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
 5. For each post, it downloads any images into this repo and writes one
    file in _entries/ with the original time and the link to the post.
 6. It skips posts that are already in _entries/, so you can run it again
    on a thread that is still growing and only the new posts get added.

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

# Spots "Ch 12", "Ch. 12", "ch12", "Chapter 12", "Chapters 12-14", "Chap 3".
# \b means "start of a word", so "much 12" does NOT count as "ch 12".
# Only the first number is kept: "Chapters 12-14" becomes 12.
CHAPTER_RE = re.compile(r"\bch(?:apters?|aps?|pts?)?\.?\s*#?\s*(\d{1,4})\b", re.IGNORECASE)

# Pulls the handle (or DID) and the post ID out of a bsky.app link.
POST_URL_RE = re.compile(r"^https?://(?:www\.)?bsky\.app/profile/([^/]+)/post/([A-Za-z0-9]+)/?(?:[?#].*)?$")

# The last part of any Bluesky post link. Used to spot posts already imported.
RKEY_IN_URL_RE = re.compile(r"/post/([A-Za-z0-9]+)")

# Image types we know how to name. Bluesky's CDN almost always sends JPEG.
EXT_FOR_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


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
    """Return the post IDs of every entry that came from Bluesky.

    We match on the post ID at the end of source_url, not the whole link.
    Why: the link contains your handle. If you ever change your handle, the
    same post gets a different link, and matching the full link would import
    everything a second time. The post ID never changes.
    """
    ids = set()
    for f in entries_dir.glob("*.*"):
        if f.suffix not in (".txt", ".md"):
            continue
        for line in f.read_text(encoding="utf-8").splitlines()[1:40]:
            if line.strip() == "---":
                break  # end of front matter
            if line.startswith("source_url:"):
                m = RKEY_IN_URL_RE.search(line)
                if m:
                    ids.add(m.group(1))
    return ids


# ─── Making one entry ──────────────────────────────────────────────────────

def find_chapter(text):
    """Return the chapter number mentioned in the text, or None."""
    m = CHAPTER_RE.search(text)
    return int(m.group(1)) if m else None


def post_images(post):
    """List the images attached to a post, as (full-size link, alt text).

    Images can sit in two places: a plain image post, or a post that quotes
    another post AND has images ("recordWithMedia"). We check both.
    Videos are not downloaded (they're big, and the site has no player);
    we just mention them in the output so you know.
    """
    embed = post.get("embed") or {}
    kind = embed.get("$type", "")
    if kind == "app.bsky.embed.recordWithMedia#view":
        embed = embed.get("media") or {}
        kind = embed.get("$type", "")
    if kind == "app.bsky.embed.images#view":
        return [(img["fullsize"], img.get("alt", "")) for img in embed.get("images", [])]
    if kind == "app.bsky.embed.video#view":
        print(f"  Note: post {post['uri'].rsplit('/', 1)[-1]} has a video. Videos aren't copied; the entry links to the original post.")
    return []


def download_image(url, dest_stem):
    """Save one image into the repo. Returns the saved file's path.

    Why download instead of linking: if a post is deleted, or Bluesky moves
    its images, a hotlinked picture would break. A copy in the repo stays.
    If the file is already there from an earlier run, we keep it and skip
    the download.
    """
    for ext in EXT_FOR_TYPE.values():
        existing = dest_stem.with_suffix(ext)
        if existing.exists():
            return existing
    body, ctype = http_get(url, want_json=False)
    ext = EXT_FOR_TYPE.get(ctype.split(";")[0].strip(), ".jpg")
    path = dest_stem.with_suffix(ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def yaml_str(s):
    """Write text so the front matter can't be broken by it.

    Front matter is YAML. Text with a ":" or a quote mark in it can confuse
    YAML. A JSON-style string in double quotes is always valid YAML, so we
    use that. ensure_ascii=False keeps emoji as emoji instead of codes.
    """
    return json.dumps(s, ensure_ascii=False)


def write_entry(post, log_slug, handle, tz, repo, dry_run):
    """Write one _entries/ file for one post. Returns a short summary dict."""
    record = post["record"]
    text = record.get("text", "")
    rkey = post["uri"].rsplit("/", 1)[-1]

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

    # Liquid (Jekyll's template language) reads "{{" and "{%" as commands,
    # even inside .txt files. We don't change your words, so we just warn.
    if "{{" in text or "{%" in text:
        print(f"  Warning: post {rkey} contains '{{{{' or '{{%'. Jekyll may treat it as template code. Check that entry after the build.")

    images = []
    for i, (url, alt) in enumerate(post_images(post), start=1):
        dest = repo / "assets" / "images" / "entries" / f"{rkey}-{i}"
        if dry_run:
            images.append((f"/assets/images/entries/{rkey}-{i}.jpg", alt))
            continue
        saved = download_image(url, dest)
        images.append(("/" + saved.relative_to(repo).as_posix(), alt))

    # Build the file. Only the fields the site uses, in the same order the
    # /new page writes them, so both kinds of entry look the same.
    lines = [
        "---",
        f"date: {created:%Y-%m-%d %H:%M:%S %z}",
        f"log: {log_slug}",
        f"chapter: {chapter if chapter is not None else ''}".rstrip(),
        f"source_url: {source_url}",
    ]
    if images:
        lines.append("images:")
        for src, alt in images:
            lines.append(f"  - src: {yaml_str(src)}")
            lines.append(f"    alt: {yaml_str(alt)}")
    lines.append("---")
    # The text goes in exactly as posted. One newline is added at the very
    # end because files should end with one; the site trims it when showing.
    content = "\n".join(lines) + "\n" + text + "\n"

    path = repo / "_entries" / f"{stem}.txt"
    if not dry_run:
        path.write_text(content, encoding="utf-8")
    return {"file": path.name, "text": text, "chapter": chapter, "images": len(images), "time": created}


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

    written, skipped = [], 0
    for post in posts:
        if post["uri"].rsplit("/", 1)[-1] in done:
            skipped += 1
            continue
        written.append(write_entry(post, args.log_slug, handle, tz, repo, args.dry_run))

    report(posts, written, skipped, args)
    return 0


def report(posts, written, skipped, args):
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
        f"- Images downloaded: **{sum(w['images'] for w in written)}**",
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
