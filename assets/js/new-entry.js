/* new-entry.js — runs the one-page /new screen: pick a book, write, Post.

   THE BIG IDEA
   The site is just files in a GitHub repo. A new entry is a new file in
   _entries/. A new book is a new file in _logs/. "Posting" means adding
   that file to the repo. Then GitHub Pages rebuilds the site.

   HOW IT SAVES WITHOUT OPENING GITHUB
   GitHub has an API: a way for programs to do the same things you'd do on
   github.com. This page uses it to add the file for you. GitHub only lets
   it do that with your permission, which is the "posting key" (a
   fine-grained personal access token) that you paste in once.

   WHERE THE KEY LIVES, AND WHY THAT'S OK
   - Only in this phone's browser storage (localStorage). Never in the repo,
     never on the site, never sent anywhere except api.github.com.
   - It only works on the one repo, and can only change files there
     (Contents: read and write). It can't touch your account, other repos,
     settings, or Actions.
   - The /new page has a strict safety rule (Content-Security-Policy, see
     head.html): only this site's own scripts run, and it can only talk to
     api.github.com. So a key can't be sent off somewhere else.
   - If the phone is lost, delete the key on GitHub (Settings → Developer
     settings → Fine-grained tokens) and it stops working at once.
   Tripped me up: browser storage is shared by every page on
   iamspecious.github.io, which includes your portfolio and its analytics
   script. That script is from a well-known company and has no reason to
   read it, but it's the reason the key is limited to this one repo and
   files only: even in the worst case, the most anyone could do is edit
   this site, and every change is in the git history and can be undone.

   WITHOUT A KEY
   Everything still works the old way: Post opens GitHub's "new file" screen
   with the file filled in, and you tap Commit there.

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
  var API = 'https://api.github.com';
  var KEY_STORE = 'reads-posting-key';
  var DRAFT_STORE = 'reads-new-draft';
  // Logs made on this phone that the site hasn't rebuilt with yet.
  // Why: after you start a new book, it takes a minute or two before the
  // site's own list includes it. Until then we remember it here, so you can
  // keep posting to it straight away.
  var PENDING_STORE = 'reads-pending-logs';

  var $ = function (id) { return document.getElementById(id); };
  var steps = ['step-pick', 'step-newlog', 'step-write', 'step-done'];
  var current = null;      // the log being written to
  var newLog = null;       // set when that log still has to be created

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
  function toLocalInput(d) { return ymd(d) + 'T' + hms(d); }

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

  // The API wants file contents as base64 (text made of safe letters only).
  // Tripped me up: the browser's btoa() only handles plain English letters
  // and breaks on emoji or "é". Turning the text into UTF-8 bytes first fixes it.
  function toBase64(text) {
    var bytes = new TextEncoder().encode(text), bin = '';
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }
  function fromBase64(b64) {
    var bin = atob(b64.replace(/\s/g, '')), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }

  function show(id) {
    steps.forEach(function (s) { $(s).hidden = s !== id; });
    window.scrollTo(0, 0);
  }

  // ─── The list of logs ──────────────────────────────────────────────────

  function allLogs() {
    var known = {};
    DATA.logs.forEach(function (l) { known[l.slug] = true; });
    // Drop pending logs once the real site includes them.
    var pending = load(PENDING_STORE, []).filter(function (l) { return !known[l.slug]; });
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
    s.textContent = l.creator + ' · ' + l.type + (l.count ? ' · ' + l.count + (l.count === 1 ? ' entry' : ' entries') : '') + (l.lastChapter ? ' · last: Ch ' + l.lastChapter : '');
    b.appendChild(t); b.appendChild(s);
    b.addEventListener('click', function () { chooseLog(l, null); });
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
    if (none) $('others').querySelector('.step-q').textContent = "What are you reading?";
    show('step-pick');
  }

  // ─── Step: write ───────────────────────────────────────────────────────

  function chooseLog(l, pendingCreate) {
    current = l;
    newLog = pendingCreate;
    $('w-title').textContent = l.title + ' — ' + l.creator;
    $('w-last').textContent = pendingCreate ? 'New log. This will be the first entry.'
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
    $('finish-log').hidden = !(l.status === 'reading' && !pendingCreate && getKey());
    $('post-status').textContent = '';
    updateCount();
    show('step-write');
    $('text').focus();
  }

  function updateCount() {
    $('char-count').textContent = $('text').value.length + ' characters';
    if (current) save(DRAFT_STORE, { log: current.slug, chapter: $('chapter').value, text: $('text').value });
  }

  // bump: how many seconds to add. Used when a file with that exact
  // time already exists (two posts in the same second), see post().
  function buildEntry(bump) {
    var d = $('when').value ? new Date($('when').value) : new Date();
    if (isNaN(d.getTime())) d = new Date();
    d = new Date(d.getTime() + (bump || 0) * 1000);
    // File name: date, time, then the log. The time keeps it unique, and it
    // never changes, which matters because it becomes the share link.
    var filename = ymd(d) + '-' + hms(d).replace(/:/g, '') + '-' + current.slug + '.txt';
    var body = liquidSafe($('text').value.replace(/\r\n?/g, '\n').replace(/\s+$/, ''));
    var content = '---\n' +
      'date: ' + ymd(d) + ' ' + hms(d) + ' ' + offsetFor(d) + '\n' +
      'log: ' + current.slug + '\n' +
      'chapter: ' + chapterValue($('chapter').value) + '\n' +
      '---\n' + body + '\n';
    return { path: '_entries/' + filename, anchor: 'e-' + filename.replace(/\.txt$/, ''), content: content };
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
      '---\n';
  }

  // ─── Talking to GitHub ─────────────────────────────────────────────────

  function getKey() { return load(KEY_STORE, null); }

  // One call to GitHub's API. Turns GitHub's error codes into plain words.
  function gh(method, path, body) {
    return fetch(API + '/repos/' + DATA.repo + path, {
      method: method,
      headers: {
        'Authorization': 'Bearer ' + getKey(),
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      if (r.ok) return r.json();
      var msg = {
        401: 'GitHub didn’t accept the posting key. It may have expired. Paste a new one below.',
        403: 'The posting key isn’t allowed to change files. It needs Contents: Read and write.',
        404: 'The posting key can’t see the ' + DATA.repo + ' repo. Check its repository access.',
        409: 'GitHub was busy with another change. Try Post again.',
        422: 'That file already exists. Wait a second and try Post again.'
      }[r.status] || ('GitHub said error ' + r.status + '. Try again in a moment.');
      var err = new Error(msg); err.status = r.status; throw err;
    }, function () { throw new Error('Couldn’t reach GitHub. Check your connection and try again.'); });
  }

  // Add one new file. "PUT /contents/<path>" without a "sha" means "create",
  // so it can never overwrite a file that's already there.
  function createFile(path, content, message) {
    return gh('PUT', '/contents/' + path.split('/').map(encodeURIComponent).join('/'), {
      message: message,
      content: toBase64(content),
      branch: DATA.branch
    });
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

    // No key: the old way. GitHub's own screen, you tap Commit.
    if (!getKey()) {
      var url = 'https://github.com/' + DATA.repo + '/new/' + DATA.branch + '/_entries' +
        '?filename=' + encodeURIComponent(entry.path.replace('_entries/', '')) +
        '&value=' + encodeURIComponent(entry.content);
      // Very long links can get cut off; about 8,000 characters is a safe ceiling.
      if (url.length > 8000 && !confirm('This is very long and GitHub may cut it off. Open anyway?')) return;
      window.location.href = url;
      return;
    }

    var btn = $('post-button');
    btn.disabled = true;
    $('post-status').textContent = 'Posting…';

    // A brand-new log is saved first, then the entry. Two small saves.
    // If the log saves but the entry fails, we remember the log already
    // exists, so pressing Post again only retries the entry.
    var first = newLog
      ? createFile('_logs/' + newLog.slug + '.md', buildLogFile(newLog), 'Start log: ' + newLog.title).then(function () {
          var pending = load(PENDING_STORE, []);
          pending.push(current);
          save(PENDING_STORE, pending);
          newLog = null;
        })
      : Promise.resolve();

    // Save the entry. If GitHub says the file name is taken (422: two posts
    // in the same second), try again one second later, up to 3 times.
    // Creating never overwrites, so nothing can be lost this way.
    function saveEntry(bump) {
      entry = buildEntry(bump);
      var ch = $('chapter').value.trim();
      return createFile(entry.path, entry.content, 'New entry: ' + current.title + (ch ? ' · Ch ' + ch : ''))
        .catch(function (err) {
          if (err.status === 422 && bump < 3) return saveEntry(bump + 1);
          throw err;
        });
    }

    first.then(function () { return saveEntry(0); }).then(function () {
      // Remember what was just posted, so "Write another" picks up from here
      // (same chapter, and "last entry" shows this one).
      var ch = $('chapter').value.trim();
      current.count = (current.count || 0) + 1;
      if (ch) current.lastChapter = ch;
      current.lastText = $('text').value.trim().slice(0, 90);
      current.lastDate = 'just now';
      save(DRAFT_STORE, null);
      $('done-link').href = DATA.baseurl + '/logs/' + current.slug + '/#' + entry.anchor;
      show('step-done');
    }).catch(function (err) {
      $('post-status').textContent = err.message;
      if (err.status === 401) openKeyForm();
    }).then(function () { btn.disabled = false; });
  }

  // Mark the current log finished: change "status:" and "finished:" in its file.
  // Updating (not creating) needs the file's current "sha", GitHub's
  // fingerprint of it, so we read the file first. That also means we never
  // overwrite a change made somewhere else in the meantime.
  function finishLog() {
    if (!confirm('Mark “' + current.title + '” as finished today?')) return;
    var path = '/contents/_logs/' + encodeURIComponent(current.slug) + '.md';
    $('post-status').textContent = 'Saving…';
    gh('GET', path + '?ref=' + encodeURIComponent(DATA.branch)).then(function (file) {
      var text = fromBase64(file.content)
        .replace(/^status:.*$/m, 'status: finished')
        .replace(/^finished:.*$/m, 'finished: ' + ymd(new Date()));
      return gh('PUT', path, { message: 'Finish log: ' + current.title, content: toBase64(text), sha: file.sha, branch: DATA.branch });
    }).then(function () {
      current.status = 'finished';
      // Also update the remembered copy, if this log was started on this phone.
      save(PENDING_STORE, load(PENDING_STORE, []).map(function (l) { return l.slug === current.slug ? current : l; }));
      $('finish-log').hidden = true;
      $('post-status').textContent = 'Marked finished. It moves out of “reading” after the next rebuild.';
    }).catch(function (err) { $('post-status').textContent = err.message; });
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

  function submitNewLog(ev) {
    ev.preventDefault();
    var slug = updateSlugHint();
    if (!slug) { $('nl-title').focus(); return; }
    var l = {
      slug: slug,
      title: $('nl-title').value.trim(),
      creator: $('nl-creator').value.trim(),
      type: $('nl-type').value,
      status: 'reading',
      started: $('nl-started').value || ymd(new Date()),
      tags: $('nl-tags').value.split(',').map(function (t) { return t.trim(); }).filter(Boolean),
      count: 0
    };

    // Without a key there's no way to create two files in one go, so the
    // new log is made on GitHub's screen first. After you commit it there,
    // come back and it'll be in the list (once the site rebuilds).
    if (!getKey()) {
      window.location.href = 'https://github.com/' + DATA.repo + '/new/' + DATA.branch + '/_logs' +
        '?filename=' + encodeURIComponent(slug + '.md') + '&value=' + encodeURIComponent(buildLogFile(l));
      return;
    }
    chooseLog(l, l);
  }

  // ─── Posting key box ───────────────────────────────────────────────────

  function renderKeyBox() {
    var has = !!getKey();
    $('key-box').hidden = false;
    $('key-state').textContent = has ? 'Quick posting is on for this phone.' : 'Quick posting is off: Post opens GitHub so you can tap Commit.';
    $('key-toggle').textContent = has ? 'Forget the posting key' : 'Turn on quick posting';
    $('post-button').textContent = has ? 'Post' : 'Open in GitHub to commit';
  }

  function openKeyForm() { $('key-form').hidden = false; $('key-input').focus(); }

  function saveKey(ev) {
    ev.preventDefault();
    var key = $('key-input').value.trim();
    if (!key) return;
    save(KEY_STORE, key);
    $('key-status').textContent = 'Checking…';
    // A quick read of the repo, to check the key works before you rely on it.
    gh('GET', '').then(function () {
      $('key-input').value = '';
      $('key-form').hidden = true;
      $('key-status').textContent = '';
      renderKeyBox();
      if (current) chooseLog(current, newLog);
    }).catch(function (err) {
      save(KEY_STORE, null);
      $('key-status').textContent = err.message;
      renderKeyBox();
    });
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
  $('finish-log').addEventListener('click', finishLog);
  $('write-another').addEventListener('click', function () { chooseLog(current, null); });
  $('key-toggle').addEventListener('click', function () {
    if (getKey()) {
      if (confirm('Forget the posting key on this phone? (To fully switch it off, also delete it on GitHub.)')) { save(KEY_STORE, null); renderKeyBox(); }
    } else { openKeyForm(); }
  });
  $('key-form').addEventListener('submit', saveKey);

  renderKeyBox();

  // If there's an unfinished draft, go straight back to it.
  var draft = load(DRAFT_STORE, null);
  var draftLog = draft && draft.text && allLogs().filter(function (l) { return l.slug === draft.log; })[0];
  if (draftLog) chooseLog(draftLog, null); else renderPick();
})();
