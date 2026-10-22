/* One popup rule for the whole Office (NT-O4). Every modal, drawer, popover, menu and confirm box uses it.
   - It closes on Esc and on a click outside. A modal (it has a scrim) closes on the click itself and swallows it,
     so the click never lands on the button behind. A popover closes on pointer down.
   - Focus goes in when it opens, stays inside while it is a modal, and goes back to the opener when it closes.
   - A modal locks the page scroll behind it.
   - One top level popup at a time: opening another closes the first. A nested popup (option parent) stays on top of its parent.
   - One stacking order: the helper sets the z-index (depth 0 starts at 1000).
   Use composedPath, so a click inside a shadow root counts as inside.
   Public: BotsPopup.open(el, opts), .close(el, restoreFocus), .closeAll(), .isOpen(el), .top()
   opts: hide (default true: the helper hides the popup on close; false when the caller animates it away), opener, restore() (puts the focus back instead of the opener), onEsc(e) (return true when it used the Esc), scrim, modal (true when a scrim is given), trap, parent, focus (element or selector), esc (default true),
         outside (default true), ignore (elements that are not "outside"), onClose(), onOutside() (return false to stay open). */
(function () {
  var stack = [], locks = 0, savedOverflow = "";
  var FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

  /* The focused element, also when it sits inside a shadow root (the Office scene). */
  function deepActive() { var a = document.activeElement; while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement; return a; }
  function find(el) { for (var i = 0; i < stack.length; i++) if (stack[i].el === el) return stack[i]; return null; }
  function inside(e, node) {
    if (!node) return false;
    var path = e.composedPath ? e.composedPath() : [];
    if (path.length) return path.indexOf(node) >= 0;
    return node.contains(e.target);
  }
  function shown(n) { return !!n && n.isConnected && !n.hidden && (n.offsetWidth || n.offsetHeight || n.getClientRects().length); }
  function focusables(el) { return Array.prototype.slice.call(el.querySelectorAll(FOCUSABLE)).filter(shown); }
  function lock() { if (locks++ === 0) { savedOverflow = document.documentElement.style.overflow; document.documentElement.style.overflow = "hidden"; } }
  function unlock() { if (locks > 0 && --locks === 0) document.documentElement.style.overflow = savedOverflow; }

  function giveFocusBack(p) {
    if (p.opts.restore) { try { p.opts.restore(); return; } catch (e) { /* use the opener */ } }
    var o = p.opener;
    if (o && !o.isConnected && p.fk) o = document.querySelector('[data-fk="' + (window.CSS && CSS.escape ? CSS.escape(p.fk) : p.fk) + '"]');
    if (o && o.isConnected && shown(o) && o.focus) { try { o.focus({ preventScroll: true }); return; } catch (e) { /* fall through */ } }
    var h = document.querySelector("main,[data-tab]:not([hidden])");
    if (h && !h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
    if (h && h.focus) h.focus({ preventScroll: true });
  }

  function open(el, o) {
    if (!el) return null;
    o = o || {};
    var p = find(el);
    if (p) { p.opts = o; return p; }
    if (!o.parent) stack.slice().forEach(function (q) { if (!q.opts.parent) close(q.el, false); });
    var opener = o.opener || deepActive();
    if (opener === document.body) opener = null;
    p = { el: el, opts: o, opener: opener, fk: opener && opener.getAttribute ? opener.getAttribute("data-fk") : null, modal: !!(o.modal || o.scrim) };
    stack.push(p);
    var depth = stack.length - 1;
    if (o.scrim) { o.scrim.hidden = false; o.scrim.style.zIndex = String(1000 + depth * 10); }
    el.hidden = false;
    el.style.zIndex = String(1001 + depth * 10);
    if (p.modal) {
      lock();
      /* A drawer starts under the top bar, so the bar (Chat, Menu, Live) stays usable. */
      var bar = document.querySelector("header.top"), bh = bar ? Math.round(bar.getBoundingClientRect().bottom) : 0;
      document.documentElement.style.setProperty("--topbar-h", Math.max(0, Math.min(bh, 160)) + "px");
    }
    var f = o.focus && (typeof o.focus === "string" ? el.querySelector(o.focus) : o.focus);
    if (!f) f = (o.trap !== false && focusables(el)[0]) || null;
    if (!f) { if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1"); f = el; }
    try { f.focus({ preventScroll: true }); } catch (e) { /* ok */ }
    return p;
  }

  function close(el, restore) {
    var p = find(el);
    if (!p) return false;
    /* Close the nested popups first. */
    stack.slice().forEach(function (q) { if (q !== p && q.opts.parent === el) close(q.el, false); });
    stack.splice(stack.indexOf(p), 1);
    if (p.modal) unlock();
    if (p.opts.scrim) { p.opts.scrim.hidden = true; p.opts.scrim.style.zIndex = ""; }
    el.style.zIndex = "";
    if (p.opts.hide !== false) el.hidden = true;
    if (p.opts.onClose) { try { p.opts.onClose(); } catch (e) { /* ok */ } }
    if (restore !== false) giveFocusBack(p);
    return true;
  }
  function closeAll() { stack.slice().reverse().forEach(function (p) { close(p.el, false); }); }
  function top() { return stack.length ? stack[stack.length - 1] : null; }

  document.addEventListener("keydown", function (e) {
    var p = top();
    if (!p) return;
    if (e.key === "Escape" && p.opts.esc !== false) {
      if (p.opts.onEsc && p.opts.onEsc(e) === true) { e.preventDefault(); e.stopImmediatePropagation(); return; }   /* the popup used the Esc itself */
      /* Let a select, a date box or an open <details> in the popup keep its own Esc only when it has not been handled yet. */
      e.preventDefault(); e.stopImmediatePropagation();
      close(p.el, true);
      return;
    }
    if (e.key === "Tab" && (p.modal || p.opts.trap)) {
      var f = focusables(p.el), a = document.activeElement;
      if (!f.length) { e.preventDefault(); return; }
      var first = f[0], last = f[f.length - 1], within = p.el.contains(a) || (a && a.getRootNode && a.getRootNode().host && p.el.contains(a.getRootNode().host));
      if (e.shiftKey && (a === first || a === p.el)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
      else if (!within) { e.preventDefault(); first.focus(); }
    }
  }, true);

  function outsideOf(p, e) {
    if (inside(e, p.el)) return false;
    if (p.opener && inside(e, p.opener)) return false;           /* the opener toggles it itself */
    var ig = p.opts.ignore || [];
    for (var i = 0; i < ig.length; i++) if (inside(e, ig[i])) return false;
    for (var k = 0; k < stack.length; k++) if (stack[k] !== p && stack[k].opts.parent === p.el && inside(e, stack[k].el)) return false;
    return true;
  }
  function handleOutside(e, phase) {
    var p = top();
    if (!p || p.opts.outside === false) return;
    if ((phase === "click") !== p.modal) return;                /* modal: on click (swallowed); popover: on pointer down */
    if (!outsideOf(p, e)) return;
    if (p.opts.onOutside && p.opts.onOutside(e) === false) return;
    if (p.modal) { e.preventDefault(); e.stopPropagation(); }
    close(p.el, !!p.modal || !e.target || !e.target.closest || !e.target.closest("button,a,input,textarea,select,[tabindex]"));
  }
  document.addEventListener("pointerdown", function (e) { handleOutside(e, "down"); }, true);
  document.addEventListener("click", function (e) { handleOutside(e, "click"); }, true);

  window.BotsPopup = { deepActive: deepActive, open: open, close: close, closeAll: closeAll, isOpen: function (el) { return !!find(el); }, top: top, focusables: focusables };
})();
