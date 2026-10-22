/* Stand-alone Friday: the small helpers the Friday page needs (token, escape, confirm box, light or dark). */
(function () {
  var B = { mock: false, store: null, state: "live" };
  B.token = function () {
    var m = document.querySelector('meta[name="bots-token"]');
    return m ? m.getAttribute("content") : "";
  };
  window.Bots = B;
  var U = {};
  U.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  };
  U.cut = function (s, n) {
    s = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s;
  };
  function mark(on) { try { document.documentElement.setAttribute("data-paper", on ? "light" : "dark"); } catch (e) { /* no page */ } }
  U.paper = function () {
    try { var v = localStorage.getItem("donna.theme"); if (v) return v === "light"; } catch (e) { /* private mode */ }
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
  };
  U.setPaper = function (on) {
    mark(!!on);
    try { localStorage.setItem("donna.theme", on ? "light" : "dark"); } catch (e) { /* lasts until the page closes */ }
    window.dispatchEvent(new CustomEvent("bots-theme", { detail: !!on }));
  };
  mark(U.paper());
  U.confirmBox = function (title, lines, yesLabel) {
    return new Promise(function (resolve) {
      var host = document.createElement("div");
      host.className = "cbx-scrim"; host.setAttribute("data-cbx", "scrim");
      var box = document.createElement("div");
      box.className = "cbx"; box.setAttribute("role", "alertdialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-labelledby", "cbx-h");
      box.innerHTML = '<h2 id="cbx-h" tabindex="-1">' + U.esc(title) + "</h2>" + (lines || []).map(function (l) { return "<p>" + U.esc(l) + "</p>"; }).join("") +
        '<div class="cbx-row"><button type="button" class="btn small as-go" data-cbx="yes">' + U.esc(yesLabel || "Yes") + '</button><button type="button" class="btn small" data-cbx="no">Cancel</button></div>';
      host.appendChild(box); document.body.appendChild(host);
      var answered = false;
      function finish(v) { if (answered) return; answered = true; if (window.BotsPopup.isOpen(box)) window.BotsPopup.close(box, true); host.remove(); resolve(v); }
      box.addEventListener("click", function (e) { var b = e.target.closest("[data-cbx]"); if (b) finish(b.getAttribute("data-cbx") === "yes"); });
      window.BotsPopup.open(box, { scrim: host, focus: "#cbx-h", onClose: function () { finish(false); } });
    });
  };
  window.BotsUtil = U;
})();
