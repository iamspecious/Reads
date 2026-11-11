/* new-entry.js — runs the one-page /new screen: pick a book, write, Post.

   THE BIG IDEA
   The site is just files in a GitHub repo. A new entry is a new file in
   _entries/. A new book is a new file in _logs/. "Posting" means adding
   that file to the repo. Then GitHub Pages rebuilds the site.

   HOW POSTING WORKS
   This page never saves anything itself. It writes the file for you, then
   opens GitHub's own "new file" screen with the name and text filled in.
   You tap "Commit changes" there, using the GitHub login you already have.

   WHY NOT SAVE STRAIGHT FROM THIS PAGE
   That would need a GitHub access key stored in the phone's browser. We
   built it, then took it out on purpose. Browser storage is shared by every
   page on iamspecious.github.io (your portfolio too, with its analytics
   script), so a stored key would be one more thing that could leak.
   With no key, there is nothing secret on this page at all: the worst a
   stranger can do with it is open GitHub's screen, where they have no
   permission to commit. The cost is one extra tap on GitHub per post.

   WHAT THIS PAGE DOES REMEMBER (in this phone's browser only, nothing secret)
   - your unfinished draft, so a closed tab doesn't lose it
   - your last post sent to GitHub, so you can bring it back if the Commit
     didn't go through
   - a new book you just started, so it shows in the list straight away,
     before the site has rebuilt with it

   WHAT THE FILES LOOK LIKE (same as the Bluesky importer makes)
     _entries/2026-11-11-143210-a-court-of-silver-flames.txt
       ---
       date: 2026-11-11 14:32:10 +0100
       log: a-court-of-silver-flames
       chapter: 12
       ---
       The reaction text, exactly as typed.
*/
(function () {
  'use strict';

  var DATA = JSON.parse(document.getElementById('reads-data').textContent);
  var DRAFT_STORE = 'reads-new-draft';
  var SENT_STORE = 'reads-last-sent';
  var PENDING_STORE = 'reads-pending-logs';
  var RESUME_STORE = 'reads-resume-log';
  // A started book that never shows up on the site (the Commit was never
  // tapped) is forgotten after this long, so it doesn't hang around forever.
  var PENDING_MAX_MS = 2 * 24 * 60 * 60 * 1000;
  // Very long links can get cut off by browsers or by GitHub.
  // About 8,000 characters is a safe ceiling.
  var MAX_URL = 8000;

  // Remove the old posting key if an earlier version of this page saved one.
  // The page no longer uses it, and it shouldn't sit in the browser.
  try { localStorage.removeItem('reads-posting-key'); } catch (e) { /* nothing to remove */ }

  var $ = function (id) { return document.getElementById(id); };
  var steps = ['step-pick', 'step-newlog', 'step-write'];
  var current = null;   // the log being written to

  // ─── Small helpers ──────────────────────────────────────────────────────

  // Browser storage can be blocked (private browsing). Every read and write
  // is wrapped so the page still works, just without remembering things.
  function load(key, fallback) {
    try { var v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
  }
  function save(key, value) {
    try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* not saved, still works */ }
  }

  function pad(n) { return String(n).padStart(2, '0'); }
  function ymd(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function hms(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()); }

  // The phone's offset from UTC, like "+0100", so Jekyll puts the post on the right day.
  // Tripped me up: getTimezoneOffset() is backwards (Berlin in winter is -60), so we flip it.
  function offsetFor(d) {
    var m = -d.getTimezoneOffset(), sign = m >= 0 ? '+' : '-';
    m = Math.abs(m);
    return sign + pad(Math.floor(m / 60)) + pad(m % 60);
  }

  // Turn a title into a file name: "A Court of Thorns & Roses!" -> "a-court-of-thorns-roses".
  // Accents are dropped (é -> e) so the web address stays plain.
  function slugify(s) {
    return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
  }

  // Text in the front matter goes in double quotes, JSON style, which is
  // always valid YAML. So a title with ":" or quotes can't break the file.
  function q(s) { return JSON.stringify(String(s)); }

  // Jekyll treats "{{" and "{%" in a file as template code. A post containing
  // them could break the whole site build. So such a post is wrapped in
  // "raw" markers that tell Jekyll to leave it alone. The site removes the
  // markers again when showing it (see _includes/entry-text.html), so what
  // people see is exactly what you typed. Most posts never need this.
  var RAW_OPEN = '{' + '% raw %' + '}', RAW_CLOSE = '{' + '% endraw %' + '}';
  var ENDRAW_RE = /\{%-?\s*endraw/;
  function liquidSafe(text) {
    return (text.indexOf('{{') !== -1 || text.indexOf('{%') !== -1) ? RAW_OPEN + text + RAW_CLOSE : text;
  }

  // Chapter numbers are written bare (chapter: 12); anything else ("Prologue", "7-9") in quotes.
  function chapterValue(raw) {
    raw = raw.trim();
    if (!raw) return '';
    return /^\d+$/.test(raw) ? raw : q(raw);
  }

  // The link that opens GitHub's "new file" screen, already filled in.
  function newFileUrl(folder, filename, content) {
    return 'https://github.com/' + DATA.repo + '/new/' + DATA.branch + '/' + folder +
      '?filename=' + encodeURIComponent(filename) + '&value=' + encodeURIComponent(content);
  }

  function show(id) {
    steps.forEach(function (s) { $(s).hidden = s !== id; });
    window.scrollTo(0, 0);
  }

  // ─── The list of logs ──────────────────────────────────────────────────

  function allLogs() {
    var known = {};
    DATA.logs.forEach(function (l) { known[l.slug] = true; });
    // Keep a remembered new book only until the real site includes it,
    // or until it's clearly never going to (the Commit wasn't tapped).
    var now = Date.now();
    var pending = load(PENDING_STORE, []).filter(function (l) {
      return !known[l.slug] && now - (l.savedAt || 0) < PENDING_MAX_MS;
    });
    save(PENDING_STORE, pending.length ? pending : null);
    return DATA.logs.concat(pending);
  }

  function logButton(l) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'pick';
    // textContent (not innerHTML) so a title can never turn into HTML.
    var t = document.createElement('span'); t.className = 'pick-title'; t.textContent = l.title;
    var s = document.createElement('span'); s.className = 'pick-sub';
    s.textContent = l.creator + ' · ' + l.type +
      (l.pending ? ' · new, waiting for the site to update' : '') +
      (l.count ? ' · ' + l.count + (l.count === 1 ? ' entry' : ' entries') : '') +
      (l.lastChapter ? ' · last: Ch ' + l.lastChapter : '');
    b.appendChild(t); b.appendChild(s);
    b.addEventListener('click', function () { chooseLog(l); });
    return b;
  }

  function renderPick() {
    var logs = allLogs();
    var reading = logs.filter(function (l) { return l.status === 'reading'; })
      .sort(function (a, b) { return (b.started || '').localeCompare(a.started || ''); });
    var others = logs.filter(function (l) { return l.status !== 'reading'; })
      .sort(function (a, b) { return a.title.localeCompare(b.title); });

    $('reading-list').replaceChildren.apply($('reading-list'), reading.map(logButton));
    $('other-list').replaceChildren.apply($('other-list'), others.map(logButton));

    // Nothing on the go? Skip straight to the full list.
    var none = reading.length === 0;
    $('reading-list').hidden = none;
    $('show-others').hidden = none;
    $('others').hidden = !none;
    if (none) $('others').querySelector('.step-q').textContent = 'What are you reading?';
    renderLastSent();
    show('step-pick');
  }

  // ─── "Didn't commit it?" safety net ────────────────────────────────────

  // Posting clears the draft (so the next post starts fresh), but keeps a
  // copy of what was sent for a day. If the Commit on GitHub didn't happen
  // (lost signal, tapped away), one tap brings the text back.
  function renderLastSent() {
    var sent = load(SENT_STORE, null);
    var fresh = sent && Date.now() - sent.at < 24 * 60 * 60 * 1000;
    if (sent && !fresh) save(SENT_STORE, null);
    $('last-sent').hidden = !fresh;
    if (fresh) {
      var d = new Date(sent.at);
      $('last-sent-time').textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
    }
  }

  function restoreLastSent() {
    var sent = load(SENT_STORE, null);
    var l = sent && allLogs().filter(function (x) { return x.slug === sent.log; })[0];
    if (!l) { $('last-sent').hidden = true; return; }
    save(DRAFT_STORE, { log: sent.log, chapter: sent.chapter, text: sent.text });
    save(SENT_STORE, null);
    chooseLog(l);
  }

  // ─── Step: write ───────────────────────────────────────────────────────

  function chooseLog(l) {
    current = l;
    $('w-title').textContent = l.title + ' — ' + l.creator;
    $('w-last').textContent = l.pending ? 'New log. If you tapped Commit for it on GitHub, you’re all set to write the first entry.'
      : l.count ? 'Last entry ' + l.lastDate + (l.lastChapter ? ', Ch ' + l.lastChapter : '') + ': “' + l.lastText + '”'
      : 'No entries yet.';
    // Fill in the chapter you were last on. Usually you're still on it,
    // and if not it's one quick edit.
    var draft = load(DRAFT_STORE, null);
    if (draft && draft.log === l.slug) {
      $('chapter').value = draft.chapter || '';
      $('text').value = draft.text || '';
    } else {
      $('chapter').value = l.lastChapter != null ? String(l.lastChapter) : '';
      $('text').value = '';
    }
    $('when').value = '';
    $('when-label').textContent = 'now';
    // "Mark finished" opens GitHub's edit screen for this log's file.
    // GitHub can't pre-fill an edit, so the link text says what to change.
    $('finish-log').hidden = l.status !== 'reading' || !!l.pending;
    $('finish-log').href = 'https://github.com/' + DATA.repo + '/edit/' + DATA.branch + '/_logs/' + encodeURIComponent(l.slug) + '.md';
    $('post-status').textContent = '';
    updateCount();
    show('step-write');
    $('text').focus();
  }

  function updateCount() {
    $('char-count').textContent = $('text').value.length + ' characters';
    if (current) save(DRAFT_STORE, { log: current.slug, chapter: $('chapter').value, text: $('text').value });
  }

  function buildEntry() {
    var d = $('when').value ? new Date($('when').value) : new Date();
    if (isNaN(d.getTime())) d = new Date();
    // File name: date, time, then the log. The time keeps it unique, and it
    // never changes, which matters because it becomes the share link.
    var filename = ymd(d) + '-' + hms(d).replace(/:/g, '') + '-' + current.slug + '.txt';
    var body = liquidSafe($('text').value.replace(/\r\n?/g, '\n').replace(/\s+$/, ''));
    var content = '---\n' +
      'date: ' + ymd(d) + ' ' + hms(d) + ' ' + offsetFor(d) + '\n' +
      'log: ' + current.slug + '\n' +
      'chapter: ' + chapterValue($('chapter').value) + '\n' +
      '---\n' + body + '\n';
    return { filename: filename, content: content };
  }

  function buildLogFile(l) {
    return '---\n' +
      'title: ' + q(l.title) + '\n' +
      'creator: ' + q(l.creator) + '\n' +
      'type: ' + l.type + '\n' +
      'status: reading\n' +
      'started: ' + l.started + '\n' +
      'finished:\n' +
      'tags: ' + JSON.stringify(l.tags || []) + '\n' +
      (l.thread ? 'bluesky_thread: ' + l.thread + '\n' : '') +
      '---\n';
  }

  // ─── Posting ───────────────────────────────────────────────────────────

  function post(ev) {
    ev.preventDefault();
    if (!$('text').value.trim()) { $('text').focus(); return; }
    // The one thing the "raw" wrapper can't protect: the word that ends it.
    // Nobody types this by accident, so we just ask for it to be removed.
    if (ENDRAW_RE.test($('text').value)) {
      $('post-status').textContent = 'The text contains “{% endraw”, which would break the site. Please remove it.';
      return;
    }
    var entry = buildEntry();
    var url = newFileUrl('_entries', entry.filename, entry.content);
    if (url.length > MAX_URL && !confirm('This is very long and GitHub may cut it off. Open anyway?')) return;

    // Keep a copy for a day in case the Commit doesn't happen, then start
    // the next post fresh.
    save(SENT_STORE, { at: Date.now(), log: current.slug, chapter: $('chapter').value, text: $('text').value });
    save(DRAFT_STORE, null);
    // Same tab, not a new one: phones often block pop-up tabs, and the
    // GitHub app can catch a normal link better.
    window.location.href = url;
  }

  // ─── New log form ──────────────────────────────────────────────────────

  function updateSlugHint() {
    var slug = slugify($('nl-title').value);
    var taken = allLogs().some(function (l) { return l.slug === slug; });
    // The page address is built from the title's plain letters and numbers.
    // A title with none (say, only Japanese characters) can't make one, so we
    // say so instead of silently refusing to go on.
    $('nl-slug').textContent = !$('nl-title').value.trim() ? ''
      : !slug ? 'Add at least one plain letter or number (A–Z, 0–9) to the title, so the log can have a web address.'
      : taken ? 'There’s already a log called “' + slug + '”. Pick it from the list instead, or change the title.'
      : 'Its page will be /logs/' + slug + '/';
    return taken ? '' : slug;
  }

  // Starting a book is two quick Commits: this one makes the book's file,
  // then you come back here and the write screen is already open on it.
  // (GitHub's "new file" screen can only make one file at a time.)
  function submitNewLog(ev) {
    ev.preventDefault();
    var slug = updateSlugHint();
    if (!slug) { $('nl-title').focus(); return; }
    if ($('nl-thread').value.trim() && !threadLink($('nl-thread').value)) {
      $('nl-slug').textContent = 'That Bluesky link doesn’t look right. It should look like https://bsky.app/profile/you.bsky.social/post/3abc…';
      $('nl-thread').focus();
      return;
    }
    var l = {
      slug: slug,
      title: $('nl-title').value.trim(),
      creator: $('nl-creator').value.trim(),
      type: $('nl-type').value,
      status: 'reading',
      started: $('nl-started').value || ymd(new Date()),
      tags: $('nl-tags').value.split(',').map(function (t) { return t.trim(); }).filter(Boolean),
      thread: threadLink($('nl-thread').value),
      count: 0,
      pending: true,
      savedAt: Date.now()
    };
    var pending = load(PENDING_STORE, []).filter(function (x) { return x.slug !== slug; });
    pending.push(l);
    save(PENDING_STORE, pending);
    save(RESUME_STORE, slug);
    window.location.href = newFileUrl('_logs', slug + '.md', buildLogFile(l));
  }

  // Tidy a pasted Bluesky post link, or return '' if it isn't one.
  // Only plain post links are accepted, so nothing odd can end up in the
  // log file (the sync script reads this line). Anything after the post ID,
  // like "?ref=…", is dropped.
  function threadLink(raw) {
    var m = String(raw).trim().match(/^https:\/\/(?:www\.)?bsky\.app\/profile\/([A-Za-z0-9.:_-]+)\/post\/([A-Za-z0-9]+)/);
    return m ? 'https://bsky.app/profile/' + m[1] + '/post/' + m[2] : '';
  }

  // ─── Wiring it all up ──────────────────────────────────────────────────

  $('show-others').addEventListener('click', function () { $('others').hidden = false; $('show-others').hidden = true; });
  $('start-new').addEventListener('click', function () {
    $('nl-started').value = ymd(new Date());
    show('step-newlog');
    $('nl-title').focus();
  });
  document.querySelectorAll('[data-back]').forEach(function (b) { b.addEventListener('click', renderPick); });
  $('newlog-form').addEventListener('submit', submitNewLog);
  $('nl-title').addEventListener('input', updateSlugHint);
  $('entry-form').addEventListener('submit', post);
  $('entry-form').addEventListener('input', updateCount);
  $('when').addEventListener('change', function () {
    $('when-label').textContent = $('when').value ? $('when').value.replace('T', ' ').slice(0, 16) : 'now';
  });
  $('restore-last').addEventListener('click', restoreLastSent);

  // Where to start:
  //  1. just came back from committing a new book -> write screen for it
  //  2. an unfinished draft -> back to it
  //  3. otherwise -> "What are you reading?"
  var logs = allLogs();
  var find = function (slug) { return logs.filter(function (l) { return l.slug === slug; })[0]; };
  var resume = find(load(RESUME_STORE, null));
  save(RESUME_STORE, null);
  var draft = load(DRAFT_STORE, null);
  var draftLog = draft && draft.text && find(draft.log);
  if (resume) chooseLog(resume);
  else if (draftLog) chooseLog(draftLog);
  else renderPick();
})();
