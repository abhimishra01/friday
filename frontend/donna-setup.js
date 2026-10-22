/* First-run page for stand-alone Friday. Six steps. Each step saves on its own, so closing the window loses nothing.
   Every text from the server goes through U.esc. */
(function () {
  var U = window.BotsUtil, B = window.Bots;
  var S = { claude: null, cv: null, settings: null, profile: null, status: null, blocklist: "", busy: "", note: {} };
  function $(k) { return document.querySelector('[data-su="' + k + '"]'); }
  function E(s) { return U.esc(s); }
  function lines(v) { return String(v || "").split("\n").map(function (x) { return x.trim(); }).filter(Boolean); }
  function say(t) { $("say").textContent = t; }
  function fail(e) { var el = $("err"); el.hidden = !e; el.textContent = e ? (e.message || String(e)) : ""; }

  function call(method, path, body) {
    var opt = { method: method, headers: { "X-Bots-Token": B.token(), Accept: "application/json" }, cache: "no-store" };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.detail || "Friday answered " + r.status + "."); e.status = r.status; throw e; }
        return j;
      });
    });
  }
  /* The person pressed the button on this page, so the second step of a two-step action is taken here. */
  function act(method, path, body) {
    return call(method, path, body).then(function (j) {
      if (j && j.status === "needs_confirmation") return call(method, path, Object.assign({}, body || {}, { confirm_token: j.confirm_token }));
      return j;
    });
  }
  function load() {
    return Promise.all([call("GET", "/api/donna"), call("GET", "/api/donna/cv"), call("GET", "/api/donna/settings"), call("GET", "/api/donna/profile"), call("GET", "/api/donna/blocklist")]).then(function (r) {
      S.status = r[0]; S.cv = r[1]; S.settings = r[2]; S.profile = r[3]; S.blocklist = (r[4] && r[4].text) || "";
    });
  }
  function run(label, fn) {
    if (S.busy) return;
    S.busy = label; fail(null); draw();
    return fn().then(function () { S.busy = ""; return load(); }, function (e) { S.busy = ""; fail(e); return load(); }).then(draw, draw);
  }

  function done() {
    var st = S.status || {}, cv = S.cv || {}, se = S.settings || {}, pr = S.profile || {};
    return {
      claude: !!(S.claude && S.claude.ok), gmail: !!(st.login && st.login.configured), cv: !!(cv.cv && cv.ticked > 0),
      about: !!(se.home && se.home.country && (se.titles || []).length), tone: !!pr.approved, go: !!(st.login && !st.paused && pr.approved)
    };
  }
  function step(n, key, title, ok, body) {
    return '<li class="su-step' + (ok ? " done" : "") + '" data-step="' + key + '"><h2><span class="n" aria-hidden="true">' + (ok ? "✓" : n) + "</span>" + E(title) + (ok ? ' <span class="su-ok">done</span>' : "") + "</h2>" + body + "</li>";
  }
  function btn(k, label, extra) { return '<button type="button" class="dn-go su-go" data-act="' + k + '"' + (S.busy ? " disabled" : "") + (extra || "") + ">" + E(S.busy === k ? "Working…" : label) + "</button>"; }

  function draw() {
    if (!S.status) return;
    var d = done(), se = S.settings || {}, home = se.home || {}, cv = S.cv || {}, pr = S.profile || {}, tzGuess = "";
    try { tzGuess = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* old browser */ }
    var h = "";
    h += step(1, "claude", "Check that Claude works", d.claude,
      "<p>Friday uses your own Claude Code to read job posts and write drafts. It must be installed and signed in. Install help: https://docs.claude.com/en/docs/claude-code</p>" +
      (S.claude ? '<p class="' + (S.claude.ok ? "su-ok" : "su-bad") + '">' + E(S.claude.message) + "</p>" : "") + btn("claude", "Check Claude"));
    h += step(2, "gmail", "Connect your Gmail", d.gmail,
      '<p>Friday sends from your own Gmail, and only after you approve each email. Google needs an app password for this. Make one at <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">myaccount.google.com/apppasswords</a>. You need 2-step verification on. Name it Friday. Google shows 16 letters. Copy them here.</p>' +
      (d.gmail ? "<p>Connected as <b>" + E(S.status.login.address) + "</b>.</p>" : "") +
      '<form data-form="gmail" autocomplete="off"><label>Your Gmail address<input type="email" name="address" required></label><label>App password (16 letters)<input type="password" name="app_password" required autocomplete="off"></label>' +
      '<button type="submit" class="dn-go su-go"' + (S.busy ? " disabled" : "") + ">" + (S.busy === "gmail" ? "Working…" : "Save and test the login") + "</button></form>" +
      (S.note.gmail ? '<p class="' + (S.note.gmail.ok ? "su-ok" : "su-bad") + '">' + E(S.note.gmail.text) + "</p>" : "") +
      "<p>The password is saved only in your private settings folder on this computer. It is never shown again.</p>");
    h += step(3, "cv", "Add your CV", d.cv,
      "<p>Choose your CV as a PDF (5 MB at most). Friday reads the facts from it. She writes only facts you tick. If the PDF is a scan, Friday will ask you to paste the text on the main page.</p>" +
      '<label>CV (PDF)<input type="file" accept="application/pdf,.pdf" data-file="cv"></label>' +
      (cv.cv ? "<p>Your CV: <b>" + E(cv.cv.filename || "saved") + "</b>. Tick the facts Friday may use.</p>" : "") +
      ((cv.facts || []).length ? '<ul class="facts">' + cv.facts.map(function (f) { return '<li><label><input type="checkbox" data-tick="' + f.id + '"' + (f.ticked ? " checked" : "") + "> <span>" + E(U.cut(f.text, 160)) + "</span></label></li>"; }).join("") + "</ul>" : ""));
    h += step(4, "about", "About you and the jobs you want", d.about,
      '<form data-form="about" autocomplete="off"><div class="row"><label>Your country<input type="text" name="country" maxlength="80" placeholder="Brazil" value="' + E(home.country || "") + '" required></label>' +
      '<label>Your time zone<input type="text" name="tz" maxlength="60" placeholder="America/Sao_Paulo" value="' + E(home.tz || tzGuess) + '" required></label></div>' +
      '<div class="row"><label>Country code (two letters, optional)<input type="text" name="cc" maxlength="2" placeholder="br" value="' + E(home.country_code || "") + '"></label>' +
      '<label>Work type<select name="work_type">' + [["", "No preference"], ["remote", "Remote"], ["hybrid", "Hybrid"], ["onsite", "On-site"], ["any", "Any"]].map(function (o) { return '<option value="' + o[0] + '"' + (se.work_type === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></label></div>" +
      '<label>Your cities, and other names for your country (one per line)<textarea rows="3" name="places">' + E((home.places || []).join("\n")) + "</textarea></label>" +
      '<label>Job titles you want. Friday keeps a job when its title holds one of these words (one per line)<textarea rows="4" name="titles" required>' + E((se.titles || []).join("\n")) + "</textarea></label>" +
      '<label>Places to search by hand (one per line, such as Remote or a city)<textarea rows="2" name="locations">' + E((se.locations || []).join("\n")) + "</textarea></label>" +
      '<div class="row"><label>Lowest pay you would take (optional)<input type="text" name="salary_floor" maxlength="60" value="' + E(se.salary_floor || "") + '"></label>' +
      '<label>How you end emails (for example: Thanks, and your name)<input type="text" name="sign_off" maxlength="200" value="' + E(se.sign_off || "") + '" required></label></div>' +
      '<label>Companies Friday never writes to, such as your current employer (one rule a line: a domain like example.com, or name: Example Ltd)<textarea rows="3" name="blocklist">' + E(S.blocklist) + "</textarea></label>" +
      '<button type="submit" class="dn-go su-go"' + (S.busy ? " disabled" : "") + ">" + (S.busy === "about" ? "Saving…" : "Save") + "</button></form>");
    var groups = (pr.groups || []);
    h += step(5, "tone", "Teach Friday how you write", d.tone,
      "<p>Friday can read the job emails you sent before, to learn your style. She only reads. She changes nothing in your mailbox. Then you keep or remove each item she learned and approve the result.</p>" +
      (S.note.tone ? '<p class="' + (S.note.tone.ok ? "su-ok" : "su-bad") + '">' + E(S.note.tone.text) + "</p>" : "") +
      btn("learn", pr.state && pr.state !== "none" ? "Read my sent mail again" : "Read my sent mail", d.gmail ? "" : " disabled") +
      (groups.length ? groups.map(function (g) { return "<h3>" + E(g.title) + '</h3><ul class="facts">' + g.items.map(function (i) { return '<li><label><input type="checkbox" data-keep="' + E(i.id) + '"' + (i.keep ? " checked" : "") + "> <span>" + E(U.cut(i.text, 160)) + "</span></label></li>"; }).join("") + "</ul>"; }).join("") + btn("approve", "Use this profile") : ""));
    h += step(6, "go", "Switch Friday on", d.go,
      "<p>Friday starts switched off. When you switch her on, she fetches jobs once a day and writes drafts. Every email waits for you. You can pause her at any time.</p>" +
      btn("go", "Switch Friday on", d.gmail && d.cv && d.about && d.tone ? "" : " disabled") + (d.go ? ' <a class="dn-go su-go" href="/">Open Friday</a>' : ""));
    var el = $("steps");
    var active = document.activeElement, key = active && active.name ? active.name : null;
    var keep = {};
    Array.prototype.forEach.call(el.querySelectorAll("form"), function (f) { var k = f.getAttribute("data-form"); Array.prototype.forEach.call(f.elements, function (x) { if (x.name && x.type !== "password" && x.value !== x.defaultValue) { (keep[k] = keep[k] || {})[x.name] = x.value; } }); });
    el.innerHTML = h;
    Object.keys(keep).forEach(function (k) { var f = el.querySelector('[data-form="' + k + '"]'); if (f) Object.keys(keep[k]).forEach(function (n) { if (f.elements[n]) f.elements[n].value = keep[k][n]; }); });
    if (key) { var back = el.querySelector('[name="' + key + '"]'); if (back) back.focus(); }
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    var a = b.getAttribute("data-act");
    if (a === "claude") run("claude", function () { return call("POST", "/api/shell/claude-check").then(function (j) { S.claude = j; say(j.message); }); });
    if (a === "learn") run("learn", function () { return call("POST", "/api/donna/profile/learn").then(function (j) { S.note.tone = j.ok === false ? { ok: false, text: j.error || j.message || "Friday could not read your sent mail." } : { ok: true, text: "Done. Check the list below." }; }); });
    if (a === "approve") run("approve", function () { return act("POST", "/api/donna/profile/approve", {}).then(function () { S.note.tone = { ok: true, text: "Profile approved." }; }); });
    if (a === "go") run("go", function () { return act("POST", "/api/donna/pause", { paused: false }).then(function () { say("Friday is on."); }); });
  });
  document.addEventListener("submit", function (e) {
    var f = e.target.closest("[data-form]");
    if (!f) return;
    e.preventDefault();
    var d = new FormData(f), kind = f.getAttribute("data-form");
    if (kind === "gmail") run("gmail", function () {
      return act("PUT", "/api/donna/connect", { address: d.get("address"), app_password: d.get("app_password") }).then(function () { return call("POST", "/api/donna/test"); }).then(function (t) {
        var ok = t && t.imap === "ok" && t.smtp === "ok";
        S.note.gmail = { ok: ok, text: ok ? "Saved. Gmail answered. Nothing was sent." : "Saved, but Gmail did not accept the login. " + [t && t.imap !== "ok" ? t.imap : "", t && t.smtp !== "ok" ? t.smtp : ""].filter(Boolean).join(" ") };
      });
    });
    if (kind === "about") run("about", function () {
      var body = { home: { country: String(d.get("country") || "").trim(), tz: String(d.get("tz") || "").trim(), country_code: String(d.get("cc") || "").trim(), places: lines(d.get("places")) },
                   titles: lines(d.get("titles")), locations: lines(d.get("locations")), salary_floor: String(d.get("salary_floor") || "").trim(), work_type: String(d.get("work_type") || ""), sign_off: String(d.get("sign_off") || "").trim() };
      return call("PUT", "/api/donna/settings", body).then(function () { return act("PUT", "/api/donna/blocklist", { text: String(d.get("blocklist") || "") }); }).then(function () { say("Saved."); });
    });
  });
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t.matches("[data-tick]")) return void run("tick", function () { return call("PATCH", "/api/donna/cv/facts/" + t.getAttribute("data-tick"), { ticked: t.checked }); });
    if (t.matches("[data-keep]")) return void run("keep", function () { return call("POST", "/api/donna/profile/items/" + encodeURIComponent(t.getAttribute("data-keep")), { keep: t.checked }); });
    if (t.matches("[data-file=cv]")) {
      var f = t.files && t.files[0];
      if (!f) return;
      if (f.size > 5 * 1024 * 1024) return void fail(new Error("The CV is larger than 5 MB. Save a smaller PDF and try again."));
      var rd = new FileReader();
      rd.onload = function () { var data = String(rd.result).split(",")[1] || ""; run("cv", function () { return call("POST", "/api/donna/cv/upload", { filename: f.name, data_base64: data }); }); };
      rd.readAsDataURL(f);
    }
  });
  load().then(draw, function (e) { fail(e); });
})();
