/* search.js — searches every entry and log, right in the browser.

   How it works, in short:
   1. Download search.json once (built by Jekyll, see search.json).
   2. Split what you typed into words.
   3. Show every item that contains ALL of those words.

   Why no lunr: lunr is a real search library, but it's about 30 KB and
   needs its own index format. For a few hundred short posts, a simple loop
   is instant, and much easier to understand and fix.

   Why "contains" and not "starts with": the portfolio search matches the
   start of words, which suits titles. Reactions are messier ("RHYSAND!!!",
   "#acosf"), so plain "contains" finds more of what you remember.

   Why accents are stripped: so "nesta" finds "Nésta" and the other way round.
*/
(function () {
  'use strict';

  var input = document.getElementById('q');
  var status = document.getElementById('search-status');
  var out = document.getElementById('search-results');
  var items = null;   // the index, once loaded
  var timer = null;

  // Make text easy to compare: lower case, no accents.
  // NFD splits "é" into "e" + an accent mark; the regex then drops the marks.
  function fold(s) {
    return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }

  // Everything we build goes through this, so a "<" in a post can never
  // turn into real HTML on the page.
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  // Wrap each matched word in <mark> so you can see why it matched.
  // We search the folded copy but highlight the original, so letters line up.
  // Tripped me up: folding can change a string's length (some characters
  // split into two). We only highlight when the lengths still match.
  function highlight(text, words) {
    var folded = fold(text);
    if (folded.length !== text.length) return esc(text);
    var marks = new Array(text.length).fill(false);
    words.forEach(function (w) {
      var i = folded.indexOf(w);
      while (i !== -1) {
        for (var j = i; j < i + w.length; j++) marks[j] = true;
        i = folded.indexOf(w, i + w.length);
      }
    });
    var html = '';
    var open = false;
    for (var k = 0; k < text.length; k++) {
      if (marks[k] && !open) { html += '<mark>'; open = true; }
      if (!marks[k] && open) { html += '</mark>'; open = false; }
      html += esc(text[k]);
    }
    return html + (open ? '</mark>' : '');
  }

  function render(q) {
    var words = fold(q).split(/\s+/).filter(Boolean);
    if (!words.length) { out.innerHTML = ''; status.textContent = ''; return; }

    var hits = items.filter(function (it) { return words.every(function (w) { return it._hay.indexOf(w) !== -1; }); });

    // Logs first (they're the "places"), then entries newest first,
    // which is already the order in search.json.
    var logs = hits.filter(function (h) { return h.k === 'l'; });
    var entries = hits.filter(function (h) { return h.k === 'e'; });

    status.textContent = hits.length ? hits.length + (hits.length === 1 ? ' result' : ' results') : 'No matches.';

    // Show at most 100 entries, so a one-letter search can't freeze a phone.
    var html = '';
    logs.forEach(function (l) {
      html += '<a class="result result-log" href="' + esc(l.u) + '">' +
        '<span class="chip chip-quiet">' + esc(l.y) + '</span> ' +
        '<strong>' + highlight(l.g, words) + '</strong> <span class="result-sub">' + highlight(l.a, words) + ' · ' + esc(l.s) + '</span></a>';
    });
    entries.slice(0, 100).forEach(function (e) {
      html += '<a class="result result-entry" href="' + esc(e.u) + '">' +
        '<span class="result-sub">' + esc(e.d) + ' · ' + esc(e.g) + (e.c ? ' · Ch ' + esc(e.c) : '') + '</span>' +
        '<span class="result-text">' + highlight(e.x, words) + '</span></a>';
    });
    out.innerHTML = html;
  }

  // Wait until typing pauses for a moment, and keep ?q= in the address bar
  // so the Back button returns you to your results.
  function onInput() {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var q = input.value.trim();
      history.replaceState(null, '', q ? '?q=' + encodeURIComponent(q) : window.location.pathname);
      if (items) render(q);
    }, 120);
  }

  input.form.addEventListener('submit', function (ev) { ev.preventDefault(); onInput(); });
  input.addEventListener('input', onInput);

  status.textContent = 'Loading…';
  fetch(window.READS_INDEX)
    .then(function (r) { return r.json(); })
    .then(function (data) {
      // Pre-build one folded "haystack" string per item, once, so every
      // keystroke only does cheap indexOf checks.
      items = data.map(function (it) {
        it._hay = fold([it.g, it.a, it.y, it.s, (it.tg || []).join(' '), it.c ? 'ch ' + it.c + ' chapter ' + it.c : '', it.x].join(' '));
        return it;
      });
      status.textContent = '';
      var start = new URLSearchParams(window.location.search).get('q');
      if (start) { input.value = start; render(start); }
    })
    .catch(function () { status.textContent = 'Search could not load. Try reloading the page.'; });
})();
