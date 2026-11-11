/* new-entry.js — turns the /new form into a pre-filled GitHub "new file" link.

   The link looks like:
   https://github.com/iamspecious/reads/new/main/_entries?filename=NAME&value=TEXT
   GitHub opens its file editor with NAME and TEXT already typed in.

   The file it makes looks exactly like the ones the Bluesky importer makes,
   so the site can't tell them apart:
     ---
     date: 2026-11-11 14:32:10 +0100
     log: a-court-of-silver-flames
     chapter: 12
     ---
     The reaction text, exactly as typed.
*/
(function () {
  'use strict';

  var form = document.getElementById('new-entry');
  if (!form) return;
  var logEl = form.elements.log;
  var chapterEl = form.elements.chapter;
  var textEl = form.elements.text;
  var whenEl = form.elements.when;
  var previewEl = document.getElementById('preview');
  var countEl = document.getElementById('char-count');
  var DRAFT_KEY = 'reads-new-draft';

  // Put a leading zero on numbers below 10: 7 -> "07".
  function pad(n) { return String(n).padStart(2, '0'); }

  // The value a datetime-local box wants: "2026-11-11T14:32:10", local time.
  function toLocalInput(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  // The phone's offset from UTC, written like "+0100".
  // Why we include it: without it, Jekyll would guess the time zone, and a
  // late-night post could land on the wrong day.
  // Tripped me up: getTimezoneOffset() is backwards. Berlin in winter gives
  // -60, not +60, so we flip the sign.
  function offsetFor(d) {
    var mins = -d.getTimezoneOffset();
    var sign = mins >= 0 ? '+' : '-';
    mins = Math.abs(mins);
    return sign + pad(Math.floor(mins / 60)) + pad(mins % 60);
  }

  function chosenDate() {
    // datetime-local values are read as local time, which is what we want.
    var d = whenEl.value ? new Date(whenEl.value) : new Date();
    return isNaN(d.getTime()) ? new Date() : d;
  }

  // Chapter numbers are written bare (chapter: 12). Anything else, like
  // "Prologue" or "Act 2", goes in quotes so YAML (the front matter format)
  // reads it as plain text and a ":" in it can't break the file.
  function chapterValue(raw) {
    raw = raw.trim();
    if (!raw) return '';
    return /^\d+$/.test(raw) ? raw : JSON.stringify(raw);
  }

  function build() {
    var d = chosenDate();
    var day = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    var time = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    // File name: date, time, then the log. The time makes it unique, and the
    // name never changes, which matters because it becomes the entry's
    // share link (#e-<file name>).
    var filename = day + '-' + time.replace(/:/g, '') + '-' + logEl.value + '.txt';
    // The text is left exactly as typed, except Windows-style line endings
    // become plain ones, so every file in the repo matches.
    var body = textEl.value.replace(/\r\n?/g, '\n').replace(/\s+$/, '');
    var content = '---\n' +
      'date: ' + day + ' ' + time + ' ' + offsetFor(d) + '\n' +
      'log: ' + logEl.value + '\n' +
      'chapter: ' + chapterValue(chapterEl.value) + '\n' +
      '---\n' + body + '\n';
    return { filename: filename, content: content };
  }

  function refresh() {
    var f = build();
    previewEl.textContent = '_entries/' + f.filename + '\n\n' + f.content;
    countEl.textContent = textEl.value.length + ' characters';
    saveDraft();
  }

  // localStorage keeps the draft on this phone only. It's wrapped in try/catch
  // because private browsing can block it, and the form must still work then.
  function saveDraft() {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ log: logEl.value, chapter: chapterEl.value, text: textEl.value }));
    } catch (e) { /* no storage, no draft. That's fine. */ }
  }
  function loadDraft() {
    try {
      var d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
      if (!d) return;
      if (d.log && logEl.querySelector('option[value="' + CSS.escape(d.log) + '"]')) logEl.value = d.log;
      chapterEl.value = d.chapter || '';
      textEl.value = d.text || '';
    } catch (e) { /* ignore a broken or blocked draft */ }
  }

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    if (!textEl.value.trim()) { textEl.focus(); return; }
    var f = build();
    var url = 'https://github.com/' + form.dataset.repo + '/new/' + form.dataset.branch + '/_entries' +
      '?filename=' + encodeURIComponent(f.filename) +
      '&value=' + encodeURIComponent(f.content);
    // Tripped me up: very long links can be cut off by browsers or by GitHub.
    // About 8,000 characters is a safe ceiling. A reaction that long is rare,
    // but we warn instead of silently losing the end of it.
    if (url.length > 8000 && !confirm('This is very long and GitHub may cut it off. Open anyway?')) return;
    // Same tab, not a new one: phones often block pop-up tabs, and the
    // GitHub app can catch a normal link better.
    window.location.href = url;
  });

  document.getElementById('clear-draft').addEventListener('click', function () {
    try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* nothing to clear */ }
    chapterEl.value = '';
    textEl.value = '';
    whenEl.value = toLocalInput(new Date());
    refresh();
  });

  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  loadDraft();
  whenEl.value = toLocalInput(new Date());
  refresh();
})();
