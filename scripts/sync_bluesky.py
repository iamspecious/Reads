#!/usr/bin/env python3
"""
sync_bluesky.py: keep logs up to date with their Bluesky threads.

WHAT IT DOES
 It looks at every log in _logs/. If a log is still being read
 (status: reading) AND has a bluesky_thread link, it runs the importer on
 that thread. The importer skips posts that are already on the site, so
 only new posts get added.

 It runs by itself every 30 minutes on GitHub (see
 .github/workflows/sync-bluesky.yml), so if you keep posting in your Bluesky
 thread, those posts show up on the site without you doing anything.

WHY ONLY "reading" LOGS
 Once you mark a book finished (or dropped), there's nothing more to fetch,
 so it stops asking Bluesky about it. That keeps each run quick and polite
 to Bluesky's servers. To sync a finished book one last time, use the
 "Import Bluesky thread" button instead.

WHY ONE BAD THREAD DOESN'T STOP THE OTHERS
 If one thread fails (deleted post, Bluesky hiccup), the others still sync.
 The run is marked as failed at the end, so you'd see a red X on GitHub,
 but nothing that worked is thrown away.

HOW TO RUN IT BY HAND (not needed normally)
     python scripts/sync_bluesky.py            add new posts
     python scripts/sync_bluesky.py --dry-run  just show what would be added
"""

import argparse
import re
import sys
from pathlib import Path

import import_bluesky

REPO = Path(__file__).resolve().parent.parent


def logs_to_sync(repo):
    """Return (slug, thread link) for every log that's still being read and has a thread."""
    found = []
    for path in sorted((repo / "_logs").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        # Only the front matter (between the first two --- lines) counts.
        parts = text.split("---", 2)
        fm = parts[1] if len(parts) == 3 else ""
        status = re.search(r"^status:\s*(\S+)", fm, re.MULTILINE)
        thread = re.search(r"^bluesky_thread:\s*(\S+)", fm, re.MULTILINE)
        if status and status.group(1) == "reading" and thread:
            # The link may be written with or without quote marks.
            found.append((path.stem, thread.group(1).strip("\"'")))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync every reading log that has a Bluesky thread.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    todo = logs_to_sync(Path(args.repo))
    if not todo:
        print("No logs to sync (none are 'reading' with a bluesky_thread link).")
        return 0

    failed = []
    for slug, url in todo:
        print(f"\n=== {slug} ===")
        cmd = [url, slug, "--repo", args.repo] + (["--dry-run"] if args.dry_run else [])
        try:
            import_bluesky.main(cmd)
        except SystemExit as err:
            # The importer stops with SystemExit when something is wrong.
            # A plain exit (code 0 or None) is fine; anything else is a failure.
            if err.code not in (0, None):
                print(f"Could not sync {slug}: {err.code}")
                failed.append(slug)
        except Exception as err:  # a network error or a surprise in Bluesky's answer
            print(f"Could not sync {slug}: {err!r}")
            failed.append(slug)

    if failed:
        print(f"\nThese logs did not sync: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
