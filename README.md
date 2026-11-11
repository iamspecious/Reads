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

Open **https://iamspecious.github.io/reads/new/** (add it to your home screen). It's one page:

1. **What are you reading?** Tap the book. Books marked `reading` are at the top.
2. Not there? Tap **Something else…** and pick another log, or **Start a new book, game or show**.
3. Write your reaction. The chapter is filled in with the last one you logged.
4. Tap **Post**. GitHub opens with the file filled in. Tap **Commit changes**. It's on the site a minute or two later.

You need to be signed in to GitHub in that browser or the GitHub app.

- **New book:** fill in the title, author and type, and tap *Save the new log on GitHub*. Tap **Commit changes**, then come back to `/new`. The write screen is ready for the first entry. That's two quick Commits, only when you start something new.
- **Finished a book?** On the write screen, the *Finished this one?* link opens the book's file on GitHub. Change `status: reading` to `status: finished`, fill in `finished:` with the date, and commit.
- **Lost a post?** If GitHub didn't save it (lost signal, tapped away), `/new` shows *Didn't commit it? Bring it back* for a day after you post.

The page stores no keys or passwords. Saving always happens on GitHub, with your own login.

## Security, in short

- **Only you can change the site.** You're the only person with write access to the repo. Anyone else who taps Post just gets GitHub's "fork this repository" screen, and their changes go nowhere unless you merge them.
- **No keys or passwords anywhere on the site.** Posting always goes through GitHub's own screen, with your own login and two-factor sign-in.
- **`/new` has a strict browser safety rule:** only this site's own scripts can run there, and the page can't send data anywhere by itself.
- **Every change is in the git history** and can be undone.
- **Every push is checked.** The *Check* action tests the importer and validates every log and entry file. A red X on the Actions tab means a file needs fixing, and it says which one.
- **Keep two-factor sign-in on** for your GitHub account. Your account is the only key to the site.

## Bluesky sync

If a book's log has a `bluesky_thread:` line (a link to the first post of your thread), new posts you add to that thread are copied to the site automatically, checked every 30 minutes. Only books marked `status: reading` are synced. Once you mark a book finished, syncing stops.

- **Starting a book on Bluesky:** paste the first post's link in the *Bluesky thread* box when you start the log on `/new`, or add `bluesky_thread: <link>` to the log file.
- **Want it now?** Actions → *Sync Bluesky threads* → **Run workflow**.
- **Posting in both places?** If you post the same thing on Bluesky *and* on `/new`, it will show up twice. Pick one place per post.
- If the Actions tab ever says the timed workflow was paused (GitHub does that after 60 days without any activity), press the button there to turn it back on.

## Import a Bluesky thread

1. On GitHub, go to **Actions → Import Bluesky thread → Run workflow**.
2. Pick the branch (normally `main`).
3. Paste the link to the **first post** of your thread, and type the **log slug** (the file name in `_logs/` without `.md`, e.g. `a-court-of-silver-flames`).
4. Tick **Dry run** if you just want to see the count first.
5. Press **Run workflow**. When it finishes, open the run: the summary shows how many posts were found, how many were imported, and the text of the first and last new post.

What it does: it follows only *your own* replies to yourself, in order, and skips everyone else's. It saves images into `assets/images/entries/` instead of linking to Bluesky. It fills in `chapter` when a post says "Ch 12" or "Chapter 12". It's safe to run again on the same thread: posts that are already imported are skipped, so only new ones get added.

The script is `scripts/import_bluesky.py` (Python, no extra packages). Its tests run on every push (`python -m unittest discover -s tests`).

## Start a new log

The easy way is **Start a new book** on `/new`. To do it by hand, add a file to `_logs/`. The file name becomes the slug and the web address (`/reads/logs/<slug>/`). You can do this on github.com: open `_logs`, tap **Add file → Create new file**, name it e.g. `hades-ii.md`, and paste:

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
