/* @ds-bundle: {"format":4,"namespace":"Etrati","components":[]} */
/* Etrati — preview helpers. The app itself is Qt (PySide6); these helpers only draw
   stand-in icons for the HTML previews. In the app, icons are Segoe Fluent Icons glyphs. */
(function () {
  var P = {
    open: '<path d="M3 7.5V18a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1h-8l-2-2.5H4a1 1 0 0 0-1 1z"/>',
    save: '<path d="M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    print: '<path d="M7 9V4h10v5M7 17H5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2"/><path d="M7 14h10v6H7z"/>',
    undo: '<path d="M9 7 5 11l4 4"/><path d="M5 11h9a5 5 0 0 1 0 10h-2"/>',
    redo: '<path d="m15 7 4 4-4 4"/><path d="M19 11h-9a5 5 0 0 0 0 10h2"/>',
    search: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/>',
    zoomin: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5M8 10.5h5M10.5 8v5"/>',
    zoomout: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5M8 10.5h5"/>',
    sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M9 4.5v15"/>',
    select: '<path d="M6 4l12 7-5.5 1.5L10 18z"/>',
    highlight: '<path d="m14 5 5 5-7 7H7v-5z"/><path d="M4 21h9"/>',
    text: '<path d="M5 6V5h14v1M12 5v14M9 19h6"/>',
    ink: '<path d="M4 18c3-6 5-9 7-9s1 6 3 6 3-4 6-6"/>',
    rect: '<rect x="4.5" y="6.5" width="15" height="11" rx="1"/>',
    arrow: '<path d="M5 19 19 5M10 5h9v9"/>',
    note: '<path d="M5 5h14v10l-4 4H5z"/><path d="M15 19v-4h4"/>',
    stamp: '<path d="M9 13V9a3 3 0 1 1 6 0v4"/><path d="M5 13h14v4H5zM7 20h10"/>',
    sign: '<path d="M3 17c3 0 4-8 6-8s0 7 3 7 2-4 4-4 2 3 5 3"/><path d="M3 21h18"/>',
    close: '<path d="m7 7 10 10M17 7 7 17"/>',
    left: '<path d="m14 6-6 6 6 6"/>',
    right: '<path d="m10 6 6 6-6 6"/>',
    down: '<path d="m6 10 6 6 6-6"/>',
    page: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    merge: '<path d="M6 3h7v6H6zM11 15h7v6h-7zM9.5 9v3.5a2 2 0 0 0 2 2H14"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    rotate: '<path d="M19 12a7 7 0 1 1-2.05-4.95"/><path d="M19 4v4h-4"/>',
    trash: '<path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13"/>',
    check: '<path d="m5 12 4.5 4.5L19 7"/>',
    warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v5M12 17.5v.5"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8v.5"/>',
    error: '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>',
    ai: '<path d="M12 3c.6 4.5 2.5 6.4 7 7-4.5.6-6.4 2.5-7 7-.6-4.5-2.5-6.4-7-7 4.5-.6 6.4-2.5 7-7z"/>',
    scan: '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M7 12h10"/>',
    fitw: '<path d="M4 5v14M20 5v14M8 12h8M10 9.5 7.5 12l2.5 2.5M14 9.5l2.5 2.5-2.5 2.5"/>',
    fitp: '<path d="M7 3h10v18H7z"/><path d="M10 8h4M10 12h4"/>',
    two: '<path d="M3.5 5h7.5v14H3.5zM13 5h7.5v14H13z"/>',
    underline: '<path d="M7 4v7a5 5 0 0 0 10 0V4M5 20h14"/>',
    strike: '<path d="M16 7a4 3 0 0 0-8 0c0 4 8 2.5 8 6.5a4 3 0 0 1-8 .5M4 12h16"/>',
    ellipse: '<ellipse cx="12" cy="12" rx="8.5" ry="6"/>',
    line: '<path d="M5 19 19 5"/>',
    image: '<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><circle cx="9" cy="10" r="1.6"/><path d="m4 18 5-5 4 4 2.5-2.5L20 18"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    redact: '<rect x="4" y="8" width="16" height="8" rx="1"/><path d="M7 12h10" stroke-width="3"/>',
    field: '<rect x="3.5" y="7" width="17" height="10" rx="1.5"/><path d="M7 10v4"/>',
    pages: '<rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/>',
    up: '<path d="m6 14 6-6 6 6"/>',
    key: '<circle cx="8" cy="15" r="3.5"/><path d="m10.5 12.5 8-8M16 7l2 2"/>',
    more: '<path d="M6 12h.01M12 12h.01M18 12h.01"/>',
    color: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16z"/>'
  };
  function svg(name, size) {
    var s = size || 16;
    return '<svg class="et-ico" width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (P[name] || '') + '</svg>';
  }
  function icons(root) {
    var els = (root || document).querySelectorAll('[data-i]');
    for (var k = 0; k < els.length; k++) {
      var el = els[k];
      el.innerHTML = svg(el.getAttribute('data-i'), +el.getAttribute('data-s') || 16) + el.innerHTML;
    }
  }
  window.Etrati = window.Etrati || {};
  window.Etrati.svg = svg;
  window.Etrati.icons = icons;
  window.Etrati.iconNames = Object.keys(P);
})();
