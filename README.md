# Reads

Live reading and playing logs: short reactions, chapter by chapter, as they happen.
Live site: **https://iamspecious.github.io/reads/**

- **Home** shows the newest entries from every log.
- **Each log** (book, game or show) has its own page, oldest entry first, so it reads like the journey.
- Tap an entry's **timestamp** to get a link to that one reaction.
- **All logs** can be filtered by type and status. **Search** looks through everything.
- RSS feed at `/reads/feed.xml`. No tracking, no cookies.

It's a plain Jekyll site built by GitHub Pages. No server, no paid services, no tokens stored anywhere.

## Post from your phone

1. Open **https://iamspecious.github.io/reads/new/** (add it to your home screen).
2. Pick the log, type the chapter if you like, write your reaction.
3. Tap **Open in GitHub to commit**. GitHub opens with the file already filled in.
4. Tap **Commit changes**. The site updates in a minute or two.

You need to be logged in to GitHub in that browser (or the GitHub app). Your draft stays on the phone until you tap *Clear draft*, in case GitHub doesn't open.

## Import a Bluesky thread

1. On GitHub, go to **Actions → Import Bluesky thread → Run workflow**.
2. Pick the branch (normally `main`).
3. Paste the link to the **first post** of your thread, and type the **log slug** (the file name in `_logs/` without `.md`, e.g. `a-court-of-silver-flames`).
4. Tick **Dry run** if you just want to see the count first.
5. Press **Run workflow**. When it finishes, open the run: the summary shows how many posts were found, how many were imported, and the text of the first and last new post.

What it does: it follows only *your own* replies to yourself, in order, and skips everyone else's. It saves images into `assets/images/entries/` instead of linking to Bluesky. It fills in `chapter` when a post says "Ch 12" or "Chapter 12". It's safe to run again on the same thread: posts that are already imported are skipped, so only new ones get added.

The script is `scripts/import_bluesky.py` (Python, no extra packages). Its tests run on every push (`python -m unittest discover -s tests`).

## Start a new log

Add a file to `_logs/`. The file name becomes the slug and the web address (`/reads/logs/<slug>/`). You can do this on github.com: open `_logs`, tap **Add file → Create new file**, name it e.g. `hades-ii.md`, and paste:

```yaml
---
title: Hades II
creator: Supergiant Games
type: game          # book, game or show
status: reading     # reading, finished or dropped
started: 2026-11-20
finished:
tags: [roguelike]
---
```

Anything you write under the second `---` shows as a note at the top of the log page. When you finish, set `status: finished` and fill in `finished:`.

## Entry files

Entries live in `_entries/`, one `.txt` file each. They're `.txt` so the text shows exactly as written (Markdown would turn `#hashtags` into headings).

```yaml
---
date: 2026-11-10 21:14:03 +0100
log: a-court-of-silver-flames
chapter: 12
source_url: https://bsky.app/profile/booksbyspec.bsky.social/post/3m…
---
The reaction text, unchanged.
```

`chapter` and `source_url` can be blank. Imported posts with pictures also get an `images:` list.

## Turning the site on

Settings → Pages → *Deploy from a branch* → `main`, folder `/ (root)`.
