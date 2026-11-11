/* logs-filter.js — makes the Type and Status buttons on /logs/ work.

   Why plain JavaScript and no library: it's about 40 lines of work.
   A library would be bigger than the page itself.

   Why the filters live in the address bar (?type=book&status=reading):
   so a filtered view can be bookmarked or shared, and the Back button
   behaves the way you'd expect.
*/
(function () {
  'use strict';

  var form = document.getElementById('log-filters');
  var count = document.getElementById('log-count');
  var none = document.getElementById('log-none');
  var cards = document.querySelectorAll('.log-card');
  if (!form || !cards.length) return;

  // Read starting filters from the address bar, if any.
  var params = new URLSearchParams(window.location.search);
  ['type', 'status'].forEach(function (name) {
    var wanted = params.get(name) || '';
    var radio = form.querySelector('input[name="' + name + '"][value="' + CSS.escape(wanted) + '"]');
    if (radio) radio.checked = true;
  });

  function apply() {
    var type = form.elements.type.value;
    var status = form.elements.status.value;
    var shown = 0;

    // An empty value means "All", so it matches every card.
    cards.forEach(function (card) {
      var ok = (!type || card.dataset.type === type) &&
               (!status || card.dataset.status === status);
      card.hidden = !ok;
      if (ok) shown++;
    });

    count.textContent = shown + (shown === 1 ? ' log' : ' logs');
    none.hidden = shown !== 0;

    // replaceState changes the address without adding a Back-button step
    // for every tap. Empty filters are left out to keep the link short.
    var next = new URLSearchParams();
    if (type) next.set('type', type);
    if (status) next.set('status', status);
    var qs = next.toString();
    history.replaceState(null, '', qs ? '?' + qs : window.location.pathname);
  }

  form.addEventListener('change', apply);
  form.hidden = false; // only show the buttons once they can actually work
  apply();
})();
