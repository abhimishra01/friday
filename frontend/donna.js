/* Friday tab (DJ09). Friday reads job boards and writes job applications from her own Gmail. Every email waits here for the owner.
   Data: /api/donna/* (page token on every call). Seven actions need two steps: the engine answers with a proposal and a one-time
   token, the owner confirms in a popup on this page, and the call repeats with the token. The app password is sent once, never shown.
   Every text from outside goes through BotsUtil.esc; links are http or https only. In sample mode (?mock=1) the data comes from
   donna-sample.js and nothing leaves the page. */
(function () {
  var U = window.BotsUtil, B = window.Bots;
  var SECTIONS = [["today", "Today"], ["companies", "Companies"], ["wait", "Waiting for you"], ["forms", "Apply by form"], ["hand", "Search by hand"], ["jobs", "Jobs found"], ["profile", "Profile review"], ["cv", "CV facts"], ["boards", "Boards"], ["sheets", "Sheets"], ["settings", "Settings"]];
  var S = { section: "today", status: null, outbox: { waiting: [], recent: [] }, cards: { forms: [], search: [] }, jobs: [], profile: null, cv: null, dataset: null, cboard: null, command: null, heavyAt: 0, span: "today", cstep: "", ctab: "all", cgrade: "", cpage: 0, cfilter: "all", cq: "", cstatus: "", ccountry: "", csort: "needs", copen: 0, cdetail: null, sheets: { sheets: [], tracked: [], blocked_today: [], fields: {} },
            boards: [], settings: null, events: [], blocklist: "", notice: "", needok: {}, off: false, err: "", loaded: false, busy: {}, undo: {}, edit: {}, fresh: {}, maybe: false, jobsAll: false,
            boardFilter: "active", lastLoad: 0, paper: false, bound: false, started: false, timer: null, onChange: null, shown: false, note: "", sample: null };

  function $(k) { return document.querySelector('[data-dn="' + k + '"]'); }
  function E(s) { return U.esc(s); }
  function clip(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n) + "…" : s; }   /* keeps line breaks, unlike U.cut */
  function say(t) { var el = $("say"); if (el) el.textContent = t; }
  function safeUrl(u) { u = String(u || ""); return /^https?:\/\//i.test(u) ? u : ""; }
  function link(u, label, cls) { var s = safeUrl(u); return s ? '<a class="' + (cls || "dn-link") + '" href="' + E(s) + '" target="_blank" rel="noopener noreferrer">' + label + "</a>" : ""; }
  function put(el, html) {
    if (!el || el.__html === html) return;
    el.__html = html;
    var a = document.activeElement, key = null, sel = null;
    if (a && el.contains(a)) { key = a.getAttribute("data-fk"); if (a.selectionStart != null) sel = [a.selectionStart, a.selectionEnd]; }
    el.innerHTML = html;
    if (key) { var b = el.querySelector('[data-fk="' + CSS.escape(key) + '"]'); if (b) { b.focus({ preventScroll: true }); if (sel && b.setSelectionRange) try { b.setSelectionRange(sel[0], sel[1]); } catch (e) { /* not a text field */ } } }
  }
  function when(ts) {
    if (!ts) return "never";
    var d = new Date(ts * 1000), now = new Date();
    if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  }

  /* ---------- calls ---------- */
  function call(method, path, body) {
    return call1(method, path, body).then(function (j) {
      if (j && j.status === "needs_confirmation") {
        return U.confirmBox("Please confirm", [j.description, "Nothing happens until you press Yes."], "Yes, do it").then(function (yes) {
          if (!yes) { var e = new Error("Cancelled. Nothing was changed."); e.status = 0; e.cancelled = true; throw e; }
          return call1(method, path, Object.assign({}, body || {}, { confirm_token: j.confirm_token }));
        });
      }
      return j;
    });
  }
  function call1(method, path, body) {
    if (B.mock) return mockCall(method, path, body);
    var opt = { method: method, headers: { "X-Bots-Token": B.token(), Accept: "application/json" }, cache: "no-store" };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch("/api/donna" + path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.detail || "The data engine answered " + r.status + "."); e.status = r.status; throw e; }
        return j;
      });
    });
  }
  /* Sample mode: GET reads the made-up data; an action changes only this page. */
  function mockCall(method, path, body) {
    var D = S.sample, m;
    if ((m = path.match(/^\/board\/(\d+)(\/status|\/note|\/dismiss)?$/))) {
      var co = D.board.companies.filter(function (x) { return String(x.id) === m[1]; })[0];
      if (!co) return Promise.resolve({});
      if (method === "GET") return Promise.resolve(JSON.parse(JSON.stringify(Object.assign({ roles: [], drafts: [], sent: [], replies: [], insights: {}, log: [{ ts: Date.now() / 1000 - 3600, status: co.status, reason: co.reason, by: "donna" }], labels: {} }, co, D.board.detail || {}))));
      if (m[2] === "/status") { co.status = body.status === "auto" ? co.status : body.status; co.hand = body.status === "auto" ? "" : body.status; co.label = (D.board.statuses.filter(function (x) { return x.id === co.status; })[0] || {}).label || co.status; co.unconfirmed = false; co.reason = "You set this status."; if (body.note != null) co.note = body.note; }
      if (m[2] === "/note") co.note = body.note;
      if (m[2] === "/dismiss") { co.unconfirmed = false; co.status = "reply"; co.label = "Reply received"; co.tone = "amber"; }
      return Promise.resolve({ ok: true });
    }
    if (method === "GET") {
      var map = { "": D.status, "/outbox": D.outbox, "/cards": D.cards, "/jobs": D.jobs, "/profile": D.profile, "/cv": D.cv, "/dataset": D.dataset, "/boards": D.boards, "/settings": D.settings, "/events": D.events, "/blocklist": { text: D.blocklist || "" }, "/board": D.board, "/command": D.command, "/digest": D.digest || { applied: 0, followups: 0, replies: 0, interviews: 0, top: [], learning: { enough: false, resolved: 0, need: 30 }, outcomes: { by: { source: [] } }, connections: 0 }, "/sheets": D.sheets || { sheets: [], tracked: [], blocked_today: [], fields: {} } };
      return Promise.resolve(JSON.parse(JSON.stringify(map[path.split("?")[0]] || {})));
    }
    if ((m = path.match(/^\/outbox\/(\d+)\/(approve|undo|reject|edit)$/))) {
      var row = D.outbox.waiting.filter(function (x) { return String(x.id) === m[1]; })[0];
      if (row && m[2] === "approve") {
        if (!body || !body.confirm_token) return Promise.resolve({ status: "needs_confirmation", confirm_token: "x", description: "Send to " + row.company + " (" + row.to_addr + ") from Friday's Gmail. You can undo for 10 seconds." });
        if (row.domain === "lumen.invalid" && !body.ok_other_agent) return Promise.resolve({ status: "needs_ok", outbox_id: row.id, reason: "Another tool wrote to lumen.invalid in the last 90 days. Press Approve again and tick that you know, if you want to send it." });
        row.state = "undo"; row.undo_left = 10; return Promise.resolve({ status: "undo", outbox_id: row.id, undo_until: 10 });
      }
      if (row && m[2] === "undo") { row.state = "waiting"; row.undo_left = 0; }
      if (row && m[2] === "reject") D.outbox.waiting = D.outbox.waiting.filter(function (x) { return x !== row; });
      if (row && m[2] === "edit") { row.subject = body.subject; row.body = body.body; }
      return Promise.resolve({ ok: true });
    }
    if ((m = path.match(/^\/drafts\/(\d+)\/(allow_word|rewrite)$/))) {
      if (m[2] === "allow_word" && !body.confirm_token) return Promise.resolve({ status: "needs_confirmation", confirm_token: "x", description: "Say that \"" + body.word + "\" is true about you. Friday may then write it in your applications." });
      D.outbox.failed_drafts = (D.outbox.failed_drafts || []).filter(function (x) { return String(x.id) !== m[1] || m[2] === "allow_word"; });
      return Promise.resolve({ ok: true, state: "waiting" });
    }
    if (path === "/blocklist" && method === "PUT") { D.blocklist = body.text; return Promise.resolve({ ok: true, text: body.text }); }
    if (path === "/pause") {
      if (body.paused === false && !body.confirm_token) return Promise.resolve({ status: "needs_confirmation", confirm_token: "x", description: "Switch Friday on. She will fetch jobs and send the emails you approve." });
      D.status.paused = !!body.paused; return Promise.resolve({ ok: true });
    }
    return Promise.resolve({ ok: true });
  }

  /* ---------- data ---------- */
  /* The first view needs only the core calls. The heavy ones (jobs, profile, CV, settings, boards, sheets ...) load when a screen that uses them opens
     and then at most once a minute (DJ14LF: the first view stays under 2 seconds). */
  var CORE = [["status", ""], ["outbox", "/outbox"], ["cards", "/cards"], ["cboard", "/board"], ["command", null], ["digest", "/digest"]];
  var HEAVY = [["jobs", "/jobs?limit=300"], ["profile", "/profile"], ["cv", "/cv"], ["dataset", "/dataset"], ["boards", "/boards"], ["settings", "/settings"], ["events", "/events"], ["blocklist", "/blocklist"], ["sheets", "/sheets"]];
  var LIGHT_SECTIONS = ["today", "companies", "company", "wait"];
    /* One load at a time: a second call while one is running gets the same promise (DJ14LG). The first render, the change hook and the
     poller used to start the same calls two or three times. */
  var pending = null;
  function load() {
    if (pending) return pending;
    pending = loadNow().then(function (v) { pending = null; return v; }, function (e) { pending = null; throw e; });
    return pending;
  }
  function loadNow() {
    var heavy = LIGHT_SECTIONS.indexOf(S.section) < 0 && Date.now() - S.heavyAt > 60000;
    var calls = CORE.map(function (x) { return call("GET", x[1] === null ? "/command?span=" + S.span : x[1]); });
    if (heavy) HEAVY.forEach(function (x) { calls.push(call("GET", x[1])); });
    return Promise.all(calls).then(function (r) {
      S.loaded = true; S.off = false; S.err = "";
      CORE.forEach(function (x, i) { S[x[0]] = r[i]; });
      if (heavy) { HEAVY.forEach(function (x, i) { var v = r[CORE.length + i]; S[x[0]] = x[0] === "blocklist" ? ((v && v.text) || "") : (v || S[x[0]]); }); S.heavyAt = Date.now(); }
      if (S.copen && S.section === "company") call("GET", "/board/" + S.copen).then(function (x) { S.cdetail = x; draw(); });
      S.lastLoad = Date.now();
      var t = Date.now();
      (S.outbox.waiting || []).forEach(function (x) { if (x.state === "undo") { var k = S.undo[x.id]; if (!k || k.stale) S.undo[x.id] = { until: t + (x.undo_left || 0) * 1000 }; } else delete S.undo[x.id]; if (x.state !== "waiting" && x.state !== "held") delete S.needok[x.id]; });
      if (S.onChange) S.onChange();
    }, function (e) {
      S.loaded = true;
      if (e.status === 404 || e.status === 405) S.off = true; else S.err = e.message;
      if (S.onChange) S.onChange();
    });
  }
  /* One action at a time for one control. The answer is undefined when it failed or was cancelled; the page then shows the true state. */
  function act(id, label, fn) {
    if (S.busy[id]) return Promise.resolve(undefined);
    S.busy[id] = label; S.err = ""; S.notice = ""; draw();
    return fn().then(function (r) { delete S.busy[id]; return r; }, function (e) {
      delete S.busy[id];
      if (!e.cancelled) S.err = e.message; else say(e.message);
      return undefined;
    }).then(function (r) { return load().then(function () { draw(); return r; }); });
  }
  /* Every open email: waiting for the owner, in the 10 seconds, queued for a cap or the window, or being sent (DJ11 S11). */
  function waiting() { return S.outbox.waiting || []; }
  function needsYou() { return waiting().filter(function (x) { return x.state === "waiting" || x.state === "held"; }); }
  function counts() {
    var st = S.status || {};
    return { wait: needsYou().length + (S.outbox.failed_drafts || []).length, forms: (S.cards.forms || []).length, hand: (S.cards.search || []).length,
             jobs: (S.jobs || []).filter(function (j) { return j.score != null && j.score >= 70; }).length };
  }

  /* ---------- the next step ---------- */
  function nextStep() {
    var st = S.status || {}, cv = S.cv || {}, pr = S.profile || {}, ds = S.dataset || {};
    if (!st.login || !st.login.configured) return { n: 1, what: "Connect Friday's Gmail", why: "Friday needs her own mailbox before she can write. She sends nothing yet.", go: "settings", btn: "Connect Gmail" };
    if (!cv.cv) return { n: 2, what: "Add your CV", why: "Friday can only use facts from your CV. She is waiting for it.", go: "cv", btn: "Add your CV" };
    if (!(cv.ticked > 0)) return { n: 3, what: "Tick the facts Friday may use", why: "She writes only what you tick. Nothing is ticked yet, so she scores no job.", go: "cv", btn: "Open CV facts" };
    if (!pr.approved) {
      var read = pr.state && pr.state !== "none";
      return { n: 4, what: read ? "Check how your emails sound" : "Let Friday read your sent mail", why: read ? "Friday read your sent mail to learn your style. Keep or remove each item, then press Use this profile." : "Friday learns your style from the job emails you sent before. She only reads. Press the button on the profile page.", go: "profile", btn: read ? "Open profile review" : "Open profile review" };
    }
    var ss = S.settings || {};
    if (!(ss.home && ss.home.country) || !(ss.titles || []).length) return { n: 5, what: "Say where you live and which jobs you want", why: "Friday assumes nothing. She needs your country, your time zone and the job titles you want before she filters any job.", go: "settings", btn: "Open settings" };
    if (!(ds.boards > 0)) return { n: 6, what: "Add the companies Friday watches", why: "Without company boards Friday finds few jobs. Add your own list of companies on the Boards page, then she checks them once a day.", go: "boards", btn: "Open boards" };
    return null;
  }

  /* ---------- the top strip ---------- */
  function stripHtml() {
    var st = S.status, c = st.caps || {}, paused = !!st.paused, busy = S.busy["pause"];
    var sent = c.sent_today || 0, cap = c.per_day || 0, n = needsYou().length + (S.outbox.failed_drafts || []).length;
    var pct = cap ? Math.min(100, Math.round(sent / cap * 100)) : 0;
    var started = !!(st.login && st.login.configured);
    var state = paused ? "Paused" : started ? "Running" : "Not started yet";
    var why = paused ? "Friday is paused. She fetches nothing and sends nothing. Press Switch on to start her again." : started ? "Friday fetches jobs once a day and writes drafts. She sends only what you approve." : "Connect her Gmail first. Until then she fetches nothing and sends nothing.";
    var block = !paused && started && st.writer_blocker ? '<span class="dn-block" role="status">' + E(st.writer_blocker) + "</span>" : "";
    return '<div class="dn-strip' + (paused ? " paused" : "") + '" role="region" aria-label="Friday status">' +
      '<div class="dn-st"><i class="dn-dot' + (paused || !started ? " off" : "") + '" aria-hidden="true"></i><div><b>' + (started || paused ? "Friday is " + state.toLowerCase() : state) + '</b><span>' + E(why) + "</span>" + block + "</div></div>" +
      '<div class="dn-stat"><span class="dn-lab">Sent today</span><b>' + sent + " of " + cap + '</b><div class="dn-bar" role="img" aria-label="' + sent + " of " + cap + ' emails sent today"><i style="width:' + pct + '%"></i></div></div>' +
      '<div class="dn-stat"><span class="dn-lab">Waiting for you</span><b>' + n + "</b></div>" +
      '<button type="button" class="dn-pause' + (paused ? " on" : "") + '" data-dn-act="pause" data-fk="pause"' + (busy ? " disabled" : "") + ">" + (busy ? "One moment…" : paused ? "Switch on" : "Pause Friday") + "</button></div>";
  }
  /* ---------- the top: Friday now, what she needs from you, budgets, setup (DJ14J, sample C) ---------- */
  function topHtml() {
    var c = S.command;
    if (!c) return stripHtml() + nextHtml();
    var st = c.state, paused = !!st.paused, busy = S.busy["pause"];
    var h = '<div class="dn-cc"><section class="dn-card dn-now" aria-labelledby="dn-now-h"><div class="dn-nowl"><p class="dn-lab" id="dn-now-h">Friday now</p><div class="dn-state"><i class="dn-dot ' + E(st.tone) + '" aria-hidden="true"></i><div><b>' + E(st.head) + "</b><p>" + E(st.sub) + "</p></div></div>";
    h += '<ul class="dn-log" aria-label="What Friday did today">' + (c.log.length ? c.log.map(function (x) { return "<li><time>" + E(x.time) + "</time>" + E(U.cut(x.text, 110)) + "</li>"; }).join("") : '<li class="dn-dim">Nothing yet today.</li>') + "</ul>";
    h += '<button type="button" class="dn-pause' + (paused ? " on" : "") + '" data-dn-act="pause" data-fk="pause"' + (busy ? " disabled" : "") + ">" + (busy ? "One moment…" : paused ? "Switch on" : "Pause Friday") + "</button></div>";
    h += '<div class="dn-nowr"><p class="dn-lab">She needs from you <span class="dn-r">' + (c.needs.length ? plural(c.needs.length, "thing", "things") + ", about " + c.needs_mins + " min" : "") + "</span></p>";
    h += c.needs.length ? '<ol class="dn-needs">' + c.needs.map(function (n, i) { return '<li class="dn-need"><span class="dn-k">' + (i + 1) + '</span><div><b>' + E(n.title) + "</b><small>" + E(n.why) + " About " + n.mins + ' min.</small></div><button type="button" class="' + (i === 0 ? "dn-go" : "dn-btn") + ' small" data-dn-act="goto" data-to="' + n.go + '" data-fk="need-' + n.id + '">' + E(n.btn) + "</button></li>"; }).join("") + "</ol>" : '<p class="dn-empty"><b>Nothing needs you right now.</b> Friday keeps working.</p>';
    h += "</div></section>";
    h += '<section class="dn-card dn-side" aria-label="Budgets and setup"><p class="dn-lab">Today\'s limits</p><div class="dn-meters">' + c.budgets.map(function (b) {
      var pct = b.max ? Math.min(100, Math.round(b.used / b.max * 100)) : 0;
      return '<div class="dn-meter' + (b.out ? " out" : "") + '"><span>' + E(b.name) + '</span><b class="dn-v">' + b.used + " of " + b.max + '</b><div class="dn-bar" role="img" aria-label="' + E(b.name) + " " + b.used + " of " + b.max + '"><i style="width:' + pct + '%"></i></div>' + (b.note ? '<span class="dn-bnote">' + E(b.note) + ' <a href="#" class="dn-link" data-dn-act="goto" data-to="settings" data-fk="raise-' + b.id + '">Raise it</a></span>' : "") + "</div>";
    }).join("") + "</div>";
    var left = c.setup_left, done = c.setup.length - left;
    h += '<p class="dn-lab">Setup ' + done + " of " + c.setup.length + '</p><div class="dn-prog" role="img" aria-label="Setup ' + done + " of " + c.setup.length + '"><i style="width:' + Math.round(done / c.setup.length * 100) + '%"></i></div><div class="dn-setup">' + c.setup.map(function (x) { return '<button type="button" class="dn-step ' + (x.done ? "done" : "todo") + '" data-dn-act="goto" data-to="' + x.go + '" data-fk="su-' + x.id + '">' + (x.done ? "Done: " : "To do: ") + E(x.name) + "</button>"; }).join("") + "</div></section></div>";
    return h;
  }
  function nextHtml() {
    var s = nextStep();
    if (!s) return "";
    return '<section class="dn-next" aria-labelledby="dn-next-h"><span class="dn-tag">Next step</span><h2 id="dn-next-h">' + E(s.what) + '</h2><p>' + E(s.why) + '</p><button type="button" class="dn-go" data-dn-act="goto" data-to="' + s.go + '" data-fk="next-go">' + E(s.btn) + "</button></section>";
  }
  function tabsHtml() {
    var c = counts();
    return '<nav class="dn-tabs" aria-label="Friday sections">' + SECTIONS.map(function (s) {
      var n = c[s[0]];
      return '<button type="button" data-dn-act="goto" data-to="' + s[0] + '" data-fk="tab-' + s[0] + '"' + (S.section === s[0] ? ' aria-current="page"' : "") + ">" + s[1] + (n ? ' <span class="dn-n">' + n + "</span>" : "") + "</button>";
    }).join("") + "</nav>";
  }

  /* ---------- waiting for you ---------- */
  var ORIGIN = { post: "the job post", hn_post: "a Hacker News post", careers_page: "the company's careers page", contact_page: "the company's contact page", public_post: "a public post by the hiring manager" };
  function sourceHtml(x) {
    var s = x.source || {}, o = ORIGIN[s.origin] || "a public page";
    return '<p class="dn-src"><span class="dn-tag">Where the address came from</span> ' + E(o) + (s.date ? ", seen " + E(s.date) : "") + (safeUrl(s.url) ? " · " + link(s.url, "Open the page") : "") + "</p>";
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }
  function cardHtml(x) {
    var id = x.id, bz = !!S.busy["o" + id], ed = S.edit[id] !== undefined, un = S.undo[id], name = E(x.company || x.domain);
    if (x.state === "undo" && un && un.until - Date.now() > 0) {
      var left = Math.max(0, Math.ceil((un.until - Date.now()) / 1000));
      return '<li class="dn-item dn-undo" data-card="' + id + '"><p role="status"><b>Sending to ' + name + ' in <span data-dn-count="' + id + '">' + left + '</span> seconds.</b> Press Undo to stop it.</p>' +
        '<button type="button" class="dn-go" data-dn-act="undo" data-id="' + id + '" data-fk="un-' + id + '">Undo</button></li>';
    }
    if (x.state === "undo" || x.state === "sending")      /* the 10 seconds are over: it goes out now, and cannot be undone */
      return '<li class="dn-item dn-undo" data-card="' + id + '"><p role="status"><b>Sending to ' + name + ' now.</b> The 10 seconds are over, so Undo is closed.</p></li>';
    if (x.state === "queued")
      return '<li class="dn-item" data-card="' + id + '"><div class="dn-head"><h3>' + name + '</h3><span class="dn-tag">Approved</span></div>' +
        '<p class="dn-to"><span class="dn-lab">To</span> ' + E(x.to_addr) + '</p><p class="dn-why"><span class="dn-tag warn">Goes out later</span> ' + E(x.reason || "It waits for a limit or for the sending window.") + ' It goes out by itself. Press Reject if you changed your mind.</p>' +
        '<div class="dn-btns"><button type="button" class="dn-btn dn-no" data-dn-act="reject" data-id="' + id + '" data-fk="rj-' + id + '" aria-label="Reject email to ' + name + '"' + (bz ? " disabled" : "") + '>Reject</button></div></li>';
    var d = ed ? S.edit[id] : { subject: x.subject, body: x.body };
    var body = ed ? '<label class="dn-l">Subject<input type="text" maxlength="200" data-dn-edit="subject" data-id="' + id + '" data-fk="es-' + id + '" value="' + E(d.subject) + '"></label>' +
        '<label class="dn-l">Email<textarea rows="14" maxlength="6000" data-dn-edit="body" data-id="' + id + '" data-fk="eb-' + id + '">' + E(d.body) + "</textarea></label>"
      : '<p class="dn-subj"><span class="dn-lab">Subject</span> ' + E(x.subject) + '</p><pre class="dn-body">' + E(clip(x.body, 3000)) + "</pre>";
    var held = x.state === "held" ? '<p class="dn-why"><span class="dn-tag warn">On hold</span> ' + E(x.reason || "A check stopped this email.") + " Fix it or reject it. When you press Approve again, Friday checks it again.</p>" : "";
    var ok = S.needok[id] ? '<p class="dn-why" role="status"><span class="dn-tag warn">Another agent wrote there</span> ' + E(S.needok[id]) + '</p>' : "";
    var fit = (x.why ? '<p class="dn-why"><span class="dn-tag ok">Why it fits</span> ' + E(x.why) + "</p>" : "") + (x.gap ? '<p class="dn-why"><span class="dn-tag warn">Gap to mention honestly</span> ' + E(x.gap) + "</p>" : "");
    var sl = x.sounds_like_me;
    return '<li class="dn-item" data-card="' + id + '"><div class="dn-head"><h3>' + name + '</h3><span class="dn-sub">' + E(U.cut(x.location || "", 60)) + "</span>" + (x.score != null ? ' <span class="dn-score" aria-label="Score ' + x.score + ' of 100">' + x.score + "</span>" : "") + "</div>" +
      '<p class="dn-to"><span class="dn-lab">To</span> ' + E(x.to_addr) + '</p><p class="dn-to"><span class="dn-lab">Attached</span> ' + E(S.outbox.cv_file || "your CV") + "</p>" + sourceHtml(x) + fit + held + ok + body +
      '<div class="dn-btns"><button type="button" class="dn-go" data-dn-act="' + (S.needok[id] ? "approve-anyway" : "approve") + '" data-id="' + id + '" data-fk="ap-' + id + '" aria-label="Approve email to ' + name + '"' + (bz ? " disabled" : "") + ">" + (bz ? E(S.busy["o" + id]) : S.needok[id] ? "I know, send anyway" : ed ? "Approve with my changes" : "Approve") + "</button>" +
      '<button type="button" class="dn-btn" data-dn-act="edit" data-id="' + id + '" data-fk="ed-' + id + '">' + (ed ? "Cancel the edit" : "Edit") + "</button>" +
      '<button type="button" class="dn-btn dn-no" data-dn-act="reject" data-id="' + id + '" data-fk="rj-' + id + '" aria-label="Reject email to ' + name + '"' + (bz ? " disabled" : "") + ">Reject</button>" +
      '<span class="dn-sl" role="group" aria-label="Does this sound like you?"><button type="button" class="dn-btn" aria-pressed="' + (sl === true) + '" data-dn-act="sounds" data-id="' + (x.draft_id || id) + '" data-yes="1" data-fk="sy-' + id + '">Sounds like me</button>' +
      '<button type="button" class="dn-btn" aria-pressed="' + (sl === false) + '" data-dn-act="sounds" data-id="' + (x.draft_id || id) + '" data-yes="0" data-fk="sn-' + id + '">Not like me</button></span></div></li>';
  }
  function failedHtml() {
    var f = S.outbox.failed_drafts || [];
    if (!f.length) return "";
    return '<h3 class="dn-sec">Drafts that did not pass the check (' + f.length + ')</h3><p class="dn-dim">Friday will not write a claim she cannot back up. If a refused word is true about you, allow it. Then press Write again.</p><ul class="dn-list">' + f.map(function (x) {
      var bz = !!S.busy["w" + x.id];
      return '<li class="dn-item"><div class="dn-head"><h3>' + E(U.cut(x.company, 80)) + '</h3><span class="dn-sub">' + E(U.cut(x.title || "", 80)) + '</span></div><p class="dn-why"><span class="dn-tag warn">Why it failed</span> ' + E(x.reason || "The draft did not pass the check.") + "</p>" +
        '<div class="dn-btns">' + (x.unknown || []).map(function (w, i) { return '<button type="button" class="dn-btn" data-dn-act="allowword" data-id="' + x.id + '" data-word="' + E(w) + '" data-fk="aw-' + x.id + "-" + i + '"' + (bz ? " disabled" : "") + ">Allow \u201c" + E(U.cut(w, 40)) + "\u201d</button>"; }).join("") +
        (x.job_id != null ? '<button type="button" class="dn-go" data-dn-act="rewrite" data-id="' + x.id + '" data-fk="rw-' + x.id + '"' + (bz ? " disabled" : "") + ">" + (bz ? E(S.busy["w" + x.id]) : "Write again") + "</button>" : "") + "</div></li>";
    }).join("") + "</ul>";
  }
  function waitHtml() {
    var w = waiting(), mine = needsYou(), slm = S.outbox.sounds_like_me || {}, nfail = (S.outbox.failed_drafts || []).length;
    var h = '<section class="dn-card" aria-labelledby="dn-h-wait"><h2 id="dn-h-wait" tabindex="-1">Waiting for you' + (mine.length + nfail ? ' <span class="dn-n">' + (mine.length + nfail) + "</span>" : "") + "</h2>" +
      '<p class="dn-dim">Nothing is sent until you press Approve. After you press it, you have ' + ((S.status && S.status.undo_seconds) || 10) + " seconds to Undo. Approved mail goes out in the recipient's weekday working hours.</p>" +
      (S.notice ? '<p class="dn-why dn-notice" role="status">' + E(S.notice) + "</p>" : "");
    if (!w.length && !nfail) {
      var ns = nextStep();
      h += '<p class="dn-empty"><b>Nothing is waiting.</b> ' + (ns ? "Finish the next step above and Friday starts to write." : "Friday writes a new draft when she finds a job that fits. She checks the boards once a day.") + "</p>";
    } else if (w.length) h += '<ul class="dn-list">' + w.map(cardHtml).join("") + "</ul>";
    h += failedHtml();
    var rec = S.outbox.recent || [];
    if (slm && (slm.yes != null || slm.no != null)) h += '<p class="dn-dim">You marked ' + (slm.yes || 0) + " as \"sounds like me\" and " + (slm.no || 0) + " as \"not like me\".</p>";
    if (rec.length) h += '<details class="dn-more"><summary>Recent emails (' + rec.length + ')</summary><ul class="dn-recent">' + rec.slice(0, 15).map(function (x) {
      return "<li><b>" + E(U.cut(x.company || x.domain, 60)) + '</b> <span class="dn-tag">' + E(stateWord(x.state)) + '</span> <span class="dn-dim">' + E(when(x.sent_ts || x.decided || x.created)) + (x.reason ? " · " + E(U.cut(x.reason, 120)) : "") + "</span></li>";
    }).join("") + "</ul></details>";
    return h + "</section>";
  }
  function stateWord(s) { return { sent: "Sent", rejected: "Rejected", refused: "Not allowed", failed: "Failed", queued: "Goes out soon", sending: "Sending" }[s] || s; }

  /* ---------- cards ---------- */
  function formsHtml() {
    var f = S.cards.forms || [];
    var h = '<section class="dn-card" aria-labelledby="dn-h-forms"><h2 id="dn-h-forms" tabindex="-1">Apply by form' + (f.length ? ' <span class="dn-n">' + f.length + "</span>" : "") + "</h2>" +
      '<p class="dn-dim">These jobs have no email address for hiring. You apply on the company page. Friday prepares a cover note and answers to copy.</p>';
    if (!f.length) return h + '<p class="dn-empty"><b>No form cards.</b> A card appears when a good job takes applications only through a form.</p></section>';
    return h + '<ul class="dn-list">' + f.map(function (c) {
      var id = c.job_id, bz = !!S.busy["f" + id];
      var ans = (c.answers || []).map(function (a) { return "<li><span class=\"dn-lab\">" + E(a.question) + "</span> " + (a.missing ? '<em class="dn-miss">Add this in Settings</em>' : E(a.answer)) + "</li>"; }).join("");
      return '<li class="dn-item"><div class="dn-head"><h3>' + E(c.company) + '</h3><span class="dn-sub">' + E(c.title) + "</span>" + (c.score != null ? ' <span class="dn-score">' + c.score + "</span>" : "") + "</div>" +
        '<p class="dn-why"><span class="dn-tag">Why a form</span> ' + E(c.reason) + "</p>" + (c.why ? '<p class="dn-why"><span class="dn-tag ok">Why it fits</span> ' + E(c.why) + "</p>" : "") + (c.gap ? '<p class="dn-why"><span class="dn-tag warn">Gap to mention honestly</span> ' + E(c.gap) + "</p>" : "") +
        (c.cover_note ? '<p class="dn-lab">Cover note</p><pre class="dn-body">' + E(c.cover_note) + "</pre>" : c.cover_note_problem ? '<p class="dn-why"><span class="dn-tag warn">No cover note</span> ' + E(c.cover_note_problem) + "</p>" : "") +
        (ans ? '<details class="dn-more"><summary>Answers for the form</summary><ul class="dn-qa">' + ans + "</ul></details>" : "") +
        '<div class="dn-btns">' + (safeUrl(c.apply_url) ? link(c.apply_url, "Open the form", "dn-go") : "") + (safeUrl(c.job_url) ? link(c.job_url, "Read the job", "dn-btn") : "") +
        '<button type="button" class="dn-btn" data-dn-act="applied" data-id="' + id + '" data-fk="fa-' + id + '"' + (bz ? " disabled" : "") + ">" + (bz ? "Saving…" : "Mark as applied") + "</button></div></li>";
    }).join("") + "</ul></section>";
  }
  function handHtml() {
    var c = S.cards.search || [];
    var h = '<section class="dn-card" aria-labelledby="dn-h-hand"><h2 id="dn-h-hand" tabindex="-1">Search by hand</h2><p class="dn-dim">Friday does not read these sites. Each link opens a saved search with your titles and places. Optional: do this when you have time.</p>';
    if (!c.length) return h + '<p class="dn-empty"><b>No saved searches yet.</b> Add job titles in Settings and the links appear here.</p></section>';
    return h + '<ul class="dn-list">' + c.map(function (x) {
      return '<li class="dn-item"><details class="dn-more dn-site"><summary><span>' + E(x.name) + '</span><span class="dn-tag">Optional</span><span class="dn-dim">' + plural((x.links || []).length, "search", "searches") + '</span></summary><p class="dn-dim">' + E(x.why) + '</p><ul class="dn-links">' + (x.links || []).map(function (l) { return "<li>" + link(l.url, E(U.cut(l.label, 70))) + "</li>"; }).join("") + "</ul></details></li>";
    }).join("") + "</ul></section>";
  }

  /* ---------- jobs ---------- */
  function jobRow(j) {
    var band = j.score == null ? "" : j.score >= 70 ? "good" : j.score >= 50 ? "maybe" : "low";
    return '<li class="dn-job"><div class="dn-head"><h3>' + E(U.cut(j.company, 80)) + '</h3><span class="dn-sub">' + E(U.cut(j.title, 80)) + "</span>" + (j.score != null ? ' <span class="dn-score ' + band + '" aria-label="Score ' + j.score + ' of 100">' + j.score + "</span>" : "") + "</div>" +
      '<p class="dn-dim">' + E(U.cut(j.location || "", 80)) + (j.remote ? " · Remote" : "") + (j.route === "card" ? " · Apply by form" : j.route === "email" ? " · By email" : "") + "</p>" +
      (j.why ? '<p class="dn-why"><span class="dn-tag ok">Why it fits</span> ' + E(j.why) + "</p>" : "") + (j.gap ? '<p class="dn-why"><span class="dn-tag warn">Gap to mention honestly</span> ' + E(j.gap) + "</p>" : "") +
      (!j.why && j.reason ? '<p class="dn-why"><span class="dn-tag">Why not scored</span> ' + E(j.reason) + "</p>" : "") + (safeUrl(j.job_url) ? "<p>" + link(j.job_url, "Read the job") + "</p>" : "") + "</li>";
  }
  function jobsHtml() {
    var all = S.jobs || [], good = all.filter(function (j) { return j.score != null && j.score >= 70; }), maybe = all.filter(function (j) { return j.score != null && j.score >= 50 && j.score < 70; });
    var unsc = all.length - good.length - maybe.length, LIM = 20;
    var h = '<section class="dn-card" aria-labelledby="dn-h-jobs"><h2 id="dn-h-jobs" tabindex="-1">Jobs found <span class="dn-n" aria-label="' + good.length + ' good fits">' + good.length + '</span></h2><p class="dn-dim">' + plural(all.length, "job", "jobs") + " in all: " + good.length + " good, " + maybe.length + " maybe, " + (all.length - good.length - maybe.length) + " not scored or under 50.</p>" + '<p class="dn-dim">70 or more: Friday writes a draft or a card. 50 to 69: the maybe list below. Under 50: dropped. Each score says why, and what to mention honestly.</p>';
    if (!all.length) return h + '<p class="dn-empty"><b>No jobs yet.</b> Friday checks the boards once a day. She needs your CV facts first, then she scores each job.</p></section>';
    var shown = S.jobsAll ? good : good.slice(0, LIM);
    h += "<h3 class=\"dn-sec\">Good fits (" + good.length + ")</h3>" + (good.length ? '<ul class="dn-list">' + shown.map(jobRow).join("") + "</ul>" : '<p class="dn-empty">No job scores 70 or more yet.</p>');
    if (good.length > LIM) h += '<button type="button" class="dn-btn" data-dn-act="alljobs" data-fk="jobs-all">' + (S.jobsAll ? "Show fewer" : "Show all " + good.length) + "</button>";
    h += '<h3 class="dn-sec">Maybe (' + maybe.length + ')</h3><button type="button" class="dn-btn" aria-expanded="' + S.maybe + '" data-dn-act="maybe" data-fk="maybe">' + (S.maybe ? "Hide the maybe list" : "Show the maybe list") + "</button>";
    if (S.maybe) h += maybe.length ? '<ul class="dn-list">' + maybe.map(jobRow).join("") + "</ul>" : '<p class="dn-empty">Nothing in the maybe list.</p>';
    if (unsc > 0) h += '<p class="dn-dim">' + unsc + " more " + (unsc === 1 ? "job is" : "jobs are") + " not scored yet or scored under 50.</p>";
    return h + "</section>";
  }

  /* ---------- profile review ---------- */
  function profileHtml() {
    var p = S.profile || {}, h = '<section class="dn-card" aria-labelledby="dn-h-profile"><h2 id="dn-h-profile" tabindex="-1">Profile review</h2>';
    var learn = '<button type="button" class="dn-btn" data-dn-act="learn" data-fk="learn"' + (S.busy.learn ? " disabled" : "") + ">" + (S.busy.learn ? "Reading… this can take a minute" : p.state === "none" ? "Let Friday read my Sent folder" : "Read my Sent folder again") + "</button>";
    if (p.state === "none" || !p.groups) return h + '<p class="dn-dim">Friday learns your writing style from the job emails you sent before. She only reads; she changes nothing in your mailbox.</p><p class="dn-empty"><b>' + E(p.message || "No profile yet.") + "</b></p>" + (S.status && S.status.login && S.status.login.configured ? learn : '<p class="dn-dim">Connect Friday\'s Gmail first (Settings).</p>') + "</section>";
    h += '<p class="dn-dim">' + (p.approved ? "This profile is in use." : "Keep what is right. Remove what is wrong. Then press Use this profile.") + " Friday read " + (p.mails_read || 0) + " of your emails and found " + (p.applications || 0) + " applications.</p>";
    h += p.groups.map(function (g) {
      return '<h3 class="dn-sec">' + E(g.title) + '</h3><ul class="dn-keep">' + g.items.map(function (i) {
        return '<li><span class="' + (i.keep ? "" : "dn-struck") + '">' + E(U.cut(i.text, 300)) + '</span><button type="button" class="dn-btn small" aria-pressed="' + i.keep + '" data-dn-act="keepitem" data-id="' + E(i.id) + '" data-keep="' + (i.keep ? 0 : 1) + '" data-fk="ki-' + E(i.id) + '" aria-label="' + (i.keep ? "Remove: " : "Keep: ") + E(U.cut(i.text, 60)) + '">' + (i.keep ? "Remove" : "Keep") + "</button></li>";
      }).join("") + "</ul>";
    }).join("");
    var ex = p.examples || [];
    if (ex.length) h += '<h3 class="dn-sec">Past applications Friday may copy your style from (' + ex.length + ')</h3><ul class="dn-list">' + ex.map(function (e) {
      return '<li class="dn-item"><p class="dn-subj">' + E(U.cut(e.subject, 120)) + '</p><details class="dn-more"><summary>Read it</summary><pre class="dn-body">' + E(clip(e.body, 2500)) + '</pre></details><div class="dn-btns"><button type="button" class="dn-btn" aria-pressed="' + (e.state === "keep") + '" data-dn-act="keepex" data-id="' + e.id + '" data-fk="kx-' + e.id + '">' + (e.state === "keep" ? "Kept as an example" : "Keep as an example") + '</button><button type="button" class="dn-btn dn-no" data-dn-act="removeex" data-id="' + e.id + '" data-fk="rx-' + e.id + '">Remove and delete the text</button></div></li>';
    }).join("") + "</ul>";
    h += '<div class="dn-btns">' + (p.approved ? "" : '<button type="button" class="dn-go" data-dn-act="useprofile" data-fk="useprofile"' + (S.busy.useprofile ? " disabled" : "") + ">Use this profile</button>") + learn + "</div>";
    return h + "</section>";
  }

  /* ---------- CV facts ---------- */
  var KIND = { skill: "Skills", years: "Years of experience", role: "Roles", employer: "Employers", project: "Projects", education: "Education" };
  function cvHtml() {
    var c = S.cv || {}, m = c.cv, facts = c.facts || [];
    var h = '<section class="dn-card" aria-labelledby="dn-h-cv"><h2 id="dn-h-cv" tabindex="-1">CV facts</h2><p class="dn-dim">Friday may claim only the facts you tick. She never invents one.</p>';
    h += m ? '<p class="dn-file"><b>' + E(U.cut(m.filename, 80)) + '</b> <span class="dn-dim">added ' + E(when(m.uploaded_ts)) + "</span></p>" : '<p class="dn-empty"><b>No CV yet.</b> Choose your CV as a PDF (5 MB at most).</p>';
    h += '<label class="dn-file-btn dn-btn" for="dn-cvfile">' + (m ? "Replace the CV" : "Choose a PDF") + '</label><input id="dn-cvfile" class="vh" type="file" accept="application/pdf" data-dn-file="cv" data-fk="cvfile">' + (S.busy.cv ? '<span class="dn-dim"> Reading the file…</span>' : "");
    if (m && !facts.length) h += '<details class="dn-more" open><summary>The PDF has no readable text</summary><p class="dn-dim">Paste the text of your CV here.</p><label class="dn-l">CV text<textarea rows="6" maxlength="60000" data-dn-cvtext="1" data-fk="cvtext"></textarea></label><button type="button" class="dn-btn" data-dn-act="cvtext" data-fk="cvtext-go">Use this text</button></details>';
    if (facts.length) {
      h += '<p class="dn-dim"><b>' + (c.ticked || 0) + "</b> of " + facts.length + ' facts ticked.</p><div class="dn-btns">' + (c.repeats ? '<button type="button" class="dn-btn small" data-dn-act="mergefacts" data-fk="merge-facts">Merge ' + c.repeats + " repeated rows</button>" : "") + '<button type="button" class="dn-btn small" data-dn-act="tickall" data-ticked="1" data-fk="tick-all">Tick all</button><button type="button" class="dn-btn small" data-dn-act="tickall" data-ticked="0" data-fk="tick-none">Untick all</button></div>';
      (c.kinds || Object.keys(KIND)).forEach(function (k) {
        var fs = facts.filter(function (f) { return f.kind === k; });
        if (!fs.length) return;
        h += '<h3 class="dn-sec">' + E(KIND[k] || k) + '</h3><ul class="dn-facts">' + fs.map(function (f) {
          return '<li><label><input type="checkbox" data-dn-tick="' + f.id + '" data-fk="ft-' + f.id + '"' + (f.ticked ? " checked" : "") + "> <span>" + E(U.cut(f.text, 300)) + '</span></label><button type="button" class="dn-btn small dn-no" data-dn-act="delfact" data-id="' + f.id + '" data-fk="fd-' + f.id + '" aria-label="Delete this fact: ' + E(U.cut(f.text, 50)) + '">Delete</button></li>';
        }).join("") + "</ul>";
      });
    }
    h += '<form class="dn-form" data-dn-form="fact" autocomplete="off"><h3 class="dn-sec">Add a fact</h3><div class="dn-row"><label class="dn-l">Kind<select name="kind" data-fk="nf-kind">' + (c.kinds || Object.keys(KIND)).map(function (k) { return '<option value="' + k + '">' + E(KIND[k] || k) + "</option>"; }).join("") + '</select></label><label class="dn-l">The fact<input type="text" name="text" required maxlength="300" data-fk="nf-text"></label></div><button type="submit" class="dn-btn" data-fk="nf-go">Add the fact</button></form>';
    return h + "</section>";
  }

  /* ---------- companies and boards ---------- */
  function boardsHtml() {
    var d = S.dataset || {}, by = d.by_state || {}, filt = S.boardFilter;
    var list = (S.boards || []).filter(function (b) { return filt === "all" || b.state === filt; });
    var h = '<section class="dn-card" aria-labelledby="dn-h-boards"><h2 id="dn-h-boards" tabindex="-1">Companies and boards</h2><p class="dn-dim">Friday reads the job boards of these companies once a day. "Moved" means the board is gone and she is looking for the new one.</p>';
    h += '<p class="dn-counts">' + [["active", "On"], ["watch", "Watching"], ["moved", "Moved"], ["off", "Off"]].map(function (s) { return "<span><b>" + (by[s[0]] || 0) + "</b> " + s[1] + "</span>"; }).join("") + "<span><b>" + (d.boards || 0) + "</b> job boards in all</span><span>The Companies page lists <b>" + ((S.cboard && S.cboard.companies) ? S.cboard.companies.length : (d.companies || 0)) + "</b> companies. A company has a board only when its careers link is on a known job site.</span></p>";
    if (!(d.boards > 0)) h += '<p class="dn-empty"><b>No boards yet.</b> Add a list of companies from a CSV file or a sheet on the Sheets page.</p>' + ((S.status && S.status.has_seed) ? '<button type="button" class="dn-go" data-dn-act="seed" data-fk="seed"' + (S.busy.seed ? " disabled" : "") + ">" + (S.busy.seed ? "Adding…" : "Add the starter companies") + "</button>" : "");
    else {
      h += '<div class="dn-filter" role="group" aria-label="Show boards">' + [["active", "On"], ["watch", "Watching"], ["moved", "Moved"], ["off", "Off"], ["all", "All"]].map(function (s) { return '<button type="button" class="dn-btn small" aria-pressed="' + (filt === s[0]) + '" data-dn-act="bfilter" data-to="' + s[0] + '" data-fk="bf-' + s[0] + '">' + s[1] + "</button>"; }).join("") + "</div>";
      h += list.length ? '<ul class="dn-boards">' + list.slice(0, 80).map(function (b) {
        var nxt = b.state === "active" ? ["off", "Turn off"] : ["on", "Turn on"];
        return '<li><div class="dn-bn"><b>' + E(U.cut(b.company, 80)) + '</b><span class="dn-dim">' + E(b.ats) + (b.india_ok ? " · Location fit: " + E(b.india_ok) : "") + '</span></div><span class="dn-tag">' + E({ active: "On", watch: "Watching", moved: "Moved", off: "Off" }[b.state] || b.state) + '</span><button type="button" class="dn-btn small" data-dn-act="board" data-id="' + b.id + '" data-to="' + nxt[0] + '" data-fk="bd-' + b.id + '">' + nxt[1] + "</button></li>";
      }).join("") + "</ul>" + (list.length > 80 ? '<p class="dn-dim">Showing 80 of ' + list.length + ".</p>" : "") : '<p class="dn-empty">No boards in this group.</p>';
    }
    return h + "</section>";
  }

  /* ---------- the command centre (DJ14J): timeline, funnel, company board ---------- */
  var NEEDS = ["draft_ready", "reply", "interview", "offer"], STOPPED = ["site_blocked", "blocklisted"], REPLIED = ["reply", "interview", "offer"];
  var FUNNEL = [["found", "Found"], ["fit", "Fit you"], ["s70", "Scored 70+"], ["drafted", "Drafted"], ["stopped", "Stopped"], ["ready", "Ready"], ["sent", "Sent"]];
  var PAGE = 50;
  function cData() { return S.cboard || { companies: [], counts: {}, statuses: [], stages: [], hand_ok: [], budget: {}, starter: false, funnel: {} }; }
  function chip(c) { return '<span class="dn-chip ' + E(c.tone) + '">' + E(c.label) + "</span>"; }
  function cFiltered() {
    var d = cData(), q = S.cq.trim().toLowerCase();
    var rows = d.companies.filter(function (c) {
      if (S.cfilter === "needs" && !(NEEDS.indexOf(c.status) >= 0 || c.unconfirmed)) return false;
      if (S.cfilter === "stopped" && STOPPED.indexOf(c.status) < 0 && !c.failed) return false;
      if (S.cfilter === "replied" && REPLIED.indexOf(c.status) < 0) return false;
      if (S.cstep && (c.step || []).indexOf(S.cstep) < 0) return false;
      if (S.cstatus && c.status !== S.cstatus) return false;
      if (S.ccountry && c.country !== S.ccountry) return false;
      return !q || (c.name + " " + c.country + " " + c.source).toLowerCase().indexOf(q) >= 0;
    });
    if (S.csort === "score") rows = rows.slice().sort(function (a, b) { return (b.best == null ? -1 : b.best) - (a.best == null ? -1 : a.best); });
    else if (S.csort === "name") rows = rows.slice().sort(function (a, b) { return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1; });
    else if (S.csort === "recent") rows = rows.slice().sort(function (a, b) { return b.changed_ts - a.changed_ts; });
    return rows;
  }
  function cRow(c) {
    return '<li><button type="button" class="dn-crow' + (S.copen === c.id ? " on" : "") + '" data-dn-act="copen" data-id="' + c.id + '" data-fk="co-' + c.id + '"' + (S.copen === c.id ? ' aria-current="true"' : "") + ' title="' + E(c.name) + '"><span class="dn-cmain"><b class="dn-two">' + E(c.name) + "</b>" +
      '<span class="dn-dim dn-one">' + E([c.country, c.source].filter(Boolean).join(" · ")) + '</span><span class="dn-why">' + E(c.next) + "</span></span>" +
      '<span class="dn-cside">' + chip(c) + '<span class="dn-dim">' + (c.roles ? c.roles + (c.roles === 1 ? " role" : " roles") : "no roles") + (c.best != null ? " · best " + c.best : "") + "</span></span></button></li>";
  }
  function cPanel() {
    var c = S.cdetail;
    if (!S.copen) return '<aside class="dn-panel empty" aria-label="Company details"><p class="dn-dim">Choose a company to see its roles, drafts, replies and history here.</p></aside>';
    if (!c) return '<aside class="dn-panel" aria-label="Company details"><p class="dn-dim">Loading…</p></aside>';
    var d = cData(), h = '<aside class="dn-panel" aria-labelledby="dn-cp-h"><button type="button" class="dn-btn small dn-close" data-dn-act="cclose" data-fk="cp-close">Close</button>';
    h += '<h3 id="dn-cp-h" tabindex="-1">' + E(c.name) + "</h3><p>" + chip(c) + ' <span class="dn-dim">' + E([c.country, c.source].filter(Boolean).join(" · ")) + "</span></p>";
    h += '<p class="dn-nextline"><b>' + E(c.next) + '</b><br><span class="dn-dim">' + E(c.why) + "</span></p>";
    if (c.unconfirmed) h += '<div class="dn-btns"><button type="button" class="dn-btn small" data-dn-act="cstatus" data-id="' + c.id + '" data-to="rejected" data-fk="cp-conf">Yes, it is a rejection</button><button type="button" class="dn-btn small" data-dn-act="cdismiss" data-id="' + c.id + '" data-fk="cp-dis">No, it is not</button></div>';
    h += '<label class="dn-l">Move to<select data-dn-cmove="' + c.id + '" data-fk="cp-move"><option value="">Choose a status</option><option value="auto">Let Friday decide</option>' + (d.statuses || []).filter(function (x) { return d.hand_ok.indexOf(x.id) >= 0; }).map(function (x) { return '<option value="' + x.id + '"' + (c.hand === x.id ? " selected" : "") + ">" + E(x.label) + "</option>"; }).join("") + '</select></label><p class="dn-dim">Moving a company sends nothing.</p>';
    h += '<label class="dn-l">Your note<textarea rows="3" maxlength="1000" data-dn-cnote="' + c.id + '" data-fk="cp-note">' + E(c.note || "") + '</textarea></label><button type="button" class="dn-btn small" data-dn-act="cnote" data-id="' + c.id + '" data-fk="cp-save">Save note</button>';
    if (c.warm && c.warm.people && c.warm.people.length) h += '<h4 class="dn-sec">People you know here</h4><ul class="dn-plain">' + c.warm.people.map(function (p) { return "<li><b>" + E(p.name) + '</b> <span class="dn-dim">' + E(U.cut(p.position || "", 60)) + (p.connected_on ? " · connected " + E(p.connected_on) : "") + "</span></li>"; }).join("") + '</ul><label class="dn-l">A note you can send him yourself<textarea rows="6" readonly data-fk="cp-ask">' + E(c.warm.ask) + '</textarea></label><p class="dn-dim">Friday never messages anyone on LinkedIn. You copy this and send it yourself.</p>';
    var sec = function (t, items) { return items.length ? '<h4 class="dn-sec">' + t + '</h4><ul class="dn-plain">' + items.join("") + "</ul>" : ""; };
    h += sec("Roles", (c.roles || []).map(function (r) { return "<li><b>" + E(U.cut(r.title, 70)) + "</b> " + (r.score != null ? '<span class="dn-tag">' + r.score + "</span>" : "") + '<br><span class="dn-dim">' + E(U.cut(r.location || "", 50)) + (r.why ? " · " + E(U.cut(r.why, 120)) : "") + "</span> " + link(r.url, "Open the post") + "</li>"; }));
    h += sec("Drafts", (c.drafts || []).map(function (x) { return "<li>" + E(U.cut(x.subject, 80)) + ' <span class="dn-dim">' + E(x.state) + "</span></li>"; }));
    h += sec("Sent", (c.sent || []).map(function (x) { return "<li>" + E(U.cut(x.subject, 80)) + ' <span class="dn-dim">' + E(when(x.sent_ts)) + "</span></li>"; }));
    h += sec("Replies", (c.replies || []).map(function (x) { return "<li><b>" + E(U.cut(x.subject, 80)) + '</b> <span class="dn-dim">' + E(when(x.ts)) + (x.kind === "rejection" ? " · looks like a rejection" : "") + "</span><br>" + E(U.cut(x.snippet, 300)) + "</li>"; }));
    var ins = Object.keys(c.insights || {});
    if (ins.length) h += '<h4 class="dn-sec">From the sheet</h4><dl class="dn-ins">' + ins.map(function (k) { return "<dt>" + E(k) + "</dt><dd>" + E(U.cut(String(c.insights[k]), 200)) + "</dd>"; }).join("") + '</dl><p class="dn-dim">Friday never opens or messages these people.</p>';
    h += sec("History", (c.log || []).map(function (x) { return "<li>" + E(when(x.ts)) + " · <b>" + E((c.labels && c.labels[x.status]) || x.status) + '</b> <span class="dn-dim">' + E(x.by === "owner" ? "you" : "Friday") + ": " + E(x.reason) + "</span></li>"; }));
    return h + "</aside>";
  }
  function emptyFilterText() {
    if (S.cq.trim()) return "No company matches “" + S.cq.trim() + "”. Clear the search.";
    if (S.cfilter === "stopped" || S.cstep === "stopped") return "No stopped drafts. Good.";
    if (S.cfilter === "needs") return "Nothing needs you right now. Good.";
    if (S.cfilter === "replied") return "No replies yet. Friday checks Gmail after each daily run.";
    if (S.cstep === "sent" || S.ctab === "sent") return "Nothing sent yet. Nothing goes out until you press Approve.";
    if (S.ctab === "needs") return "Nothing needs you right now. Good.";
    if (S.ctab === "stopped") return "No stopped companies. Good.";
    if (S.ctab === "replied") return "No replies yet. Friday looks in Gmail after each daily run.";
    if (S.ctab === "closed") return "Nothing is closed yet.";
    if (S.ctab === "watch") return "Friday is not watching any company yet. Add a sheet or the starter companies.";
    if (S.cgrade) return "No company has fit " + S.cgrade + " yet. Friday scores a role after she reads the board.";
    return "No company is in this group.";
  }
  function funnelHtml() {
    var d = cData(), f = d.funnel || {}, tl = (S.command && S.command.timeline) || {};
    return '<p class="dn-fnote">Companies at each step. Press a count to filter the list.</p><div class="dn-fn" role="group" aria-label="Funnel. Each count filters the list.">' + FUNNEL.map(function (x) {
      var n = f[x[0]] || 0, tone = x[0] === "stopped" && n ? " bad" : x[0] === "ready" && n ? " good" : "";
      return '<button type="button" class="dn-fnb' + tone + '" aria-pressed="' + (S.cstep === x[0]) + '" data-dn-act="cstep" data-to="' + x[0] + '" data-fk="fn-' + x[0] + '"><span class="dn-fnn">' + n + '</span><span class="dn-fnt">' + x[1] + "</span></button>";
    }).join("") + "</div>" + (tl.say ? '<p class="dn-fnote">' + E(tl.say) + (tl.near_misses ? ' <a href="#" data-dn-act="cstep" data-to="fit" class="dn-link" data-fk="fn-near">See companies with scores</a>' : "") + "</p>" : "");
  }
  function timelineHtml() {
    var tl = (S.command && S.command.timeline) || { steps: [] };
    if (!tl.steps.length) return "";
    var live = 0;
    tl.steps.forEach(function (x, i) { if (x.n > 0) live = i; });
    var h = '<section class="dn-card dn-tl" aria-labelledby="dn-h-tl"><div class="dn-tlhead"><div><h2 id="dn-h-tl">Where today\'s jobs are</h2><p class="dn-dim">Every job moves left to right. Each step says what happened.</p></div>' +
      '<div class="dn-seg" role="group" aria-label="Time range">' + [["today", "Today"], ["week", "This week"]].map(function (r) { return '<button type="button" class="dn-btn small" aria-pressed="' + (S.span === r[0]) + '" data-dn-act="span" data-to="' + r[0] + '" data-fk="span-' + r[0] + '">' + r[1] + "</button>"; }).join("") + "</div></div>";
    h += '<ol class="dn-flow" style="--live:' + live + '">' + tl.steps.map(function (x, i) {
      var cls = x.id === "stopped" && x.n ? "stop" : x.id === "ready" && x.n ? "ready" : x.n ? "done" : "todo";
      return '<li class="dn-stage ' + cls + '"><div class="dn-pip">' + x.n + '</div><p class="dn-sname">' + E(x.name) + '</p><p class="dn-swhy">' + E(x.why) + "</p></li>";
    }).join("") + "</ol>";
    return h + "</section>";
  }
  function boardHtml2() {
    var d = cData(), all = d.companies, b = d.budget || {};
    var h = '<section class="dn-card dn-cb" aria-labelledby="dn-h-co"><h2 id="dn-h-co" tabindex="-1">Companies <span class="dn-n">' + all.length + "</span></h2>";
    if (!all.length) return h + '<p class="dn-empty"><b>0 companies.</b> Add the 178 starter companies, or add a sheet of companies.</p><div class="dn-btns"><button type="button" class="dn-go" data-dn-act="seed" data-fk="co-seed">Add the starter companies</button><button type="button" class="dn-btn" data-dn-act="goto" data-to="sheets" data-fk="co-sheets">Add a sheet</button></div></section>';
    h += funnelHtml();
    h += '<div class="dn-chips" role="group" aria-label="Show">' + [["all", "All"], ["needs", "Needs me"], ["stopped", "Stopped"], ["replied", "Replied"]].map(function (f) { return '<button type="button" class="dn-chipb" aria-pressed="' + (S.cfilter === f[0]) + '" data-dn-act="cfilter" data-to="' + f[0] + '" data-fk="cf-' + f[0] + '">' + f[1] + "</button>"; }).join("") +
      (d.statuses || []).filter(function (s) { return d.counts[s.id]; }).map(function (s) { return '<button type="button" class="dn-chipb ' + E(s.tone) + '" aria-pressed="' + (S.cstatus === s.id) + '" data-dn-act="cstat" data-to="' + s.id + '" data-fk="cs-' + s.id + '">' + E(s.label) + " <b>" + d.counts[s.id] + "</b></button>"; }).join("") + "</div>";
    var countries = Object.keys(all.reduce(function (m, c) { if (c.country) m[c.country] = 1; return m; }, {})).sort();
    h += '<div class="dn-row"><label class="dn-l">Search<input type="search" value="' + E(S.cq) + '" maxlength="80" data-dn-cq="1" data-fk="cq" placeholder="Company, country or source"></label><label class="dn-l">Country<select data-dn-csel="ccountry" data-fk="cc-sel"><option value="">All</option>' + countries.slice(0, 150).map(function (c) { return '<option value="' + E(c) + '"' + (S.ccountry === c ? " selected" : "") + ">" + E(U.cut(c, 30)) + "</option>"; }).join("") + '</select></label><label class="dn-l">Sort<select data-dn-csel="csort" data-fk="cso-sel">' + [["needs", "Needs me first"], ["score", "Best score"], ["recent", "Last change"], ["name", "Name"]].map(function (o) { return '<option value="' + o[0] + '"' + (S.csort === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></label></div>";
    var rows = cFiltered(), pages = Math.max(1, Math.ceil(rows.length / PAGE)), pg = Math.min(S.cpage, pages - 1);
    S.cpage = pg;
    h += '<div class="dn-board' + (S.copen ? " open" : "") + '"><div class="dn-blist">';
    if (!rows.length) h += '<p class="dn-empty">' + E(emptyFilterText()) + "</p>";
    else h += '<ul class="dn-clist">' + rows.slice(pg * PAGE, pg * PAGE + PAGE).map(cRow).join("") + "</ul>";
    if (rows.length > PAGE) h += '<div class="dn-pager"><button type="button" class="dn-btn small" data-dn-act="cpage" data-to="-1" data-fk="pg-prev"' + (pg ? "" : " disabled") + '>Previous 50</button><span class="dn-dim">' + (pg * PAGE + 1) + " to " + Math.min(rows.length, pg * PAGE + PAGE) + " of " + rows.length + '</span><button type="button" class="dn-btn small" data-dn-act="cpage" data-to="1" data-fk="pg-next"' + (pg < pages - 1 ? "" : " disabled") + ">Next 50</button></div>";
    h += "</div>" + cPanel() + "</div>";
    h += '<div class="dn-btns"><button type="button" class="dn-btn small" data-dn-act="creplies" data-fk="co-replies"' + (S.busy.creplies ? " disabled" : "") + ">" + (S.busy.creplies ? "Reading Gmail…" : "Look for replies") + "</button></div>";
    return h + "</section>";
  }
  function digestHtml() {
    var g = S.digest;
    if (!g) return "";
    var h = '<section class="dn-card" aria-labelledby="dn-h-dg"><h2 id="dn-h-dg">This week</h2><p class="dn-counts"><span><b>' + g.applied + "</b> applied</span><span><b>" + g.followups + "</b> follow-ups</span><span><b>" + g.replies + "</b> replies</span><span><b>" + g.interviews + "</b> interviews</span></p>";
    h += g.learning.enough ? (g.best_sources && g.best_sources.length ? '<p class="dn-dim">Best sources so far: ' + g.best_sources.map(function (x) { return E(x.name) + " (" + x.interviews + " interviews from " + x.applied + ")"; }).join(", ") + ".</p>" : '<p class="dn-dim">No interviews yet.</p>') : '<p class="dn-dim">Friday learns what works after 30 results. She has ' + g.learning.resolved + " so far, so she changes nothing yet.</p>";
    if (g.top && g.top.length) h += '<h3 class="dn-sec">Next week\'s top ' + g.top.length + '</h3><ol class="dn-plain">' + g.top.map(function (x) { return "<li><b>" + E(U.cut(x.company, 50)) + "</b> " + E(U.cut(x.title, 60)) + ' <span class="dn-tag">' + x.score + "</span></li>"; }).join("") + "</ol>";
    h += '<details class="dn-more"><summary>People you know (LinkedIn export)</summary><p class="dn-dim">Choose the Connections.csv you downloaded from LinkedIn. Friday reads the file only. She never opens LinkedIn and never messages anyone. ' + (g.connections ? g.connections + " people kept." : "") + '</p><label class="dn-file-btn dn-btn" for="dn-connfile">Choose Connections.csv</label><input id="dn-connfile" class="vh" type="file" accept=".csv,text/csv" data-dn-file="conn" data-fk="conn-file"></details>';
    return h + "</section>";
  }
  function todayHtml() { return timelineHtml() + boardHtml2() + digestHtml(); }
  function healthHtml() { var c = S.command; return c ? '<p class="dn-health"><i class="' + (c.health.ok ? "ok" : "bad") + '" aria-hidden="true"></i>' + E(c.health.text) + "</p>" : ""; }
  function updatedHtml() {
    if (!S.lastLoad) return "";
    var d = new Date(S.lastLoad), t = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    return '<p class="dn-updated" role="status">Updated ' + t + ". Friday's page refreshes by itself.</p>";
  }
  function openCompany(id) {
    S.copen = id; S.cdetail = null; S.section = "company"; draw(); window.scrollTo(0, 0);
    call("GET", "/board/" + id).then(function (r) { if (S.copen === id) { S.cdetail = r; draw(); var hh = document.getElementById("dn-cp-h"); if (hh) hh.focus({ preventScroll: true }); } });
  }

  /* ---------- job-hunt sheets (DJ14H) ---------- */
  var TRACK_WORD = { applied: "Applied", interviewing: "Interviewing", rejected: "Rejected", offer: "Offer" };
  function sheetsHtml() {
    var sh = S.sheets || {}, list = sh.sheets || [], tr = sh.tracked || [], fields = sh.fields || {}, bl = sh.blocked_today || [];
    var h = '<section class="dn-card" aria-labelledby="dn-h-sheets"><h2 id="dn-h-sheets" tabindex="-1">Sheets</h2><p class="dn-dim">Add a job tracker or a list of companies. Friday reads it once and keeps her own copy. She works from that copy, so you can make the sheet private again afterwards.</p>';
    h += '<form class="dn-form" data-dn-form="sheet" autocomplete="off"><label class="dn-l">Google Sheets link<input type="url" name="url" placeholder="https://docs.google.com/spreadsheets/d/…" maxlength="500" data-fk="sh-url"></label><button type="submit" class="dn-btn" data-fk="sh-go"' + (S.busy.sheet ? " disabled" : "") + ">" + (S.busy.sheet ? "Reading…" : "Add the sheet") + '</button></form>';
    h += '<p class="dn-dim">Or choose a file (CSV or Excel, 5 MB at most).</p><label class="dn-file-btn dn-btn" for="dn-sheetfile">Choose a file</label><input id="dn-sheetfile" class="vh" type="file" accept=".csv,.xlsx,text/csv" data-dn-file="sheet" data-fk="sh-file">';
    if (bl.length) h += '<p class="dn-dim">Site blocks automated visits, skipped today: ' + bl.map(E).join(", ") + ".</p>";
    if (!list.length) return h + '<p class="dn-empty"><b>No sheets yet.</b> Paste a share link above. Set General access to "Anyone with the link: Viewer" first.</p></section>';
    h += '<ul class="dn-sheets">' + list.map(function (s) {
      var tabs = s.tabs || [];
      var li = '<li><div class="dn-bn"><b>' + E(U.cut(s.name, 80)) + '</b> <span class="dn-dim">' + (s.kind === "google" ? "Google Sheet" : "File") + " · " + tabs.length + (tabs.length === 1 ? " tab" : " tabs") + " · " + s.rows + " rows · " + s.companies_added + " companies added · " + s.jobs_found + " jobs found · " + (s.tracked || 0) + ' tracked · read ' + (s.last_ts ? E(when(s.last_ts)) : "never") + "</span></div>";
      if (s.problem) li += '<p class="dn-conn bad" role="alert"><i aria-hidden="true"></i>' + E(s.problem) + "</p>";
      tabs.forEach(function (t, ti) {
        li += '<details class="dn-more"' + (!Object.keys(t.mapping || {}).length ? " open" : "") + '><summary>' + E(t.name) + " · columns found (" + E(t.source === "model" ? "one model run" : t.source === "owner" ? "set by you" : t.source === "none" ? "not understood" : "by the headers") + ')</summary>' + (t.note ? '<p class="dn-dim">' + E(t.note) + "</p>" : "");
        li += '<div class="dn-map" data-sheet="' + s.id + '" data-tab="' + E(t.name) + '">' + Object.keys(fields).map(function (f) {
          return '<label class="dn-l">' + E(fields[f]) + '<select data-dn-map="' + f + '" data-fk="map-' + s.id + "-" + ti + "-" + f + '"><option value="">Not used</option>' + (t.headers || []).map(function (hd, i) { return hd.trim() ? '<option value="' + i + '"' + (t.mapping && t.mapping[f] === i ? " selected" : "") + ">" + E(U.cut(hd, 40)) + "</option>" : ""; }).join("") + "</select></label>";
        }).join("") + '</div><div class="dn-scroll"><table class="dn-prev"><caption class="dn-dim">First rows</caption><thead><tr>' + (t.headers || []).map(function (hd) { return "<th>" + E(U.cut(hd, 30)) + "</th>"; }).join("") + "</tr></thead><tbody>" + (t.preview || []).map(function (r) { return "<tr>" + (t.headers || []).map(function (_x, i) { return "<td>" + E(U.cut(r[i] || "", 50)) + "</td>"; }).join("") + "</tr>"; }).join("") + '</tbody></table></div><button type="button" class="dn-btn small" data-dn-act="usemap" data-id="' + s.id + '" data-tab="' + E(t.name) + '" data-fk="use-' + s.id + "-" + ti + '">Use this</button></details>';
      });
      li += '<div class="dn-btns">' + (s.kind === "google" ? '<button type="button" class="dn-btn small" data-dn-act="again" data-id="' + s.id + '" data-fk="again-' + s.id + '"' + (S.busy["sh" + s.id] ? " disabled" : "") + ">" + (S.busy["sh" + s.id] ? "Reading…" : "Read again") + "</button>" : "") + '<button type="button" class="dn-btn small dn-no" data-dn-act="rmsheet" data-id="' + s.id + '" data-fk="rm-' + s.id + '">Remove</button></div></li>';
      return li;
    }).join("") + "</ul>";
    h += '<h3 class="dn-sec">Tracked</h3>';
    h += tr.length ? '<ul class="dn-boards">' + tr.slice(0, 100).map(function (x) { return '<li><div class="dn-bn"><b>' + E(U.cut(x.company, 60)) + "</b>" + '<span class="dn-dim">' + E(U.cut(x.title, 80)) + (x.applied_date ? " · " + E(x.applied_date) : "") + '</span></div><span class="dn-tag">' + E(TRACK_WORD[x.status] || x.status) + "</span></li>"; }).join("") + "</ul>" : '<p class="dn-empty">Nothing tracked yet. Rows marked applied, interviewing, rejected or offer appear here. Friday never writes to those again.</p>';
    return h + "</section>";
  }

  /* ---------- settings ---------- */
  var ANSWER_KEYS = [["notice", "Notice period"], ["salary", "Salary"], ["location", "Where you are based"], ["timezone", "Time zone"], ["authorisation", "Visa or sponsorship"], ["years", "Years of experience"], ["why", "Why you want to work there"]];
  function answerKeys() {
    var q = S.settings && S.settings.answer_questions;
    return q && q.length ? q : ANSWER_KEYS.map(function (k) { return { key: k[0], question: k[1] }; });
  }
  function settingsHtml() {
    var s = S.settings || {}, st = S.status || {}, lg = st.login || {}, c = s.caps || {}, a = s.answers || {};
    var h = '<section class="dn-card" aria-labelledby="dn-h-set"><h2 id="dn-h-set" tabindex="-1">Settings</h2>';
    h += '<h3 class="dn-sec">Friday\'s Gmail</h3><p class="dn-conn ' + (lg.configured ? "" : "bad") + '"><i aria-hidden="true"></i>' + (lg.configured ? "Connected as " + E(lg.address) : "Not connected") + "</p>";
    h += '<details class="dn-more"' + (lg.configured ? "" : " open") + "><summary>" + (lg.configured ? "Change the login" : "Connect Gmail") + '</summary><form class="dn-form" data-dn-form="connect" autocomplete="off"><p class="dn-dim">Use an app password from Google (2-step verification on). It is stored in a private file on this Mac and never shown again. Friday sends nothing when you connect.</p>' +
      '<div class="dn-row"><label class="dn-l">Gmail address<input type="email" name="address" required data-fk="cn-addr" value="' + E(lg.address || "") + '"></label><label class="dn-l">App password<input type="password" name="app_password" required autocomplete="new-password" data-fk="cn-pw"></label></div><div class="dn-btns"><button type="submit" class="dn-go" data-fk="cn-go">Save the login</button>' +
      (lg.configured ? '<button type="button" class="dn-btn" data-dn-act="test" data-fk="cn-test"' + (S.busy.test ? " disabled" : "") + ">" + (S.busy.test ? "Testing…" : "Test the login") + "</button>" : "") + "</div>" + (S.note ? '<p class="dn-dim" role="status">' + E(S.note) + "</p>" : "") + "</form></details>";
    h += '<form class="dn-form" data-dn-form="prefs" autocomplete="off"><h3 class="dn-sec">What to look for</h3><label class="dn-l">Words a job title may hold (one per line)<textarea rows="6" name="titles" data-fk="st-titles">' + E((s.titles || []).join("\n")) + '</textarea></label>' +
      '<label class="dn-l">Places (one per line)<textarea rows="3" name="locations" data-fk="st-loc">' + E((s.locations || []).join("\n")) + '</textarea></label>' +
      '<h3 class="dn-sec">Where you live</h3><p class="dn-dim">Friday uses this to drop jobs you cannot take and to match time zones. Nothing is assumed.</p>' +
      '<div class="dn-row"><label class="dn-l">Country<input type="text" name="home_country" maxlength="80" placeholder="Brazil" value="' + E((s.home && s.home.country) || "") + '" data-fk="st-hc"></label>' +
      '<label class="dn-l">Time zone<input type="text" name="home_tz" maxlength="60" placeholder="America/Sao_Paulo" value="' + E((s.home && s.home.tz) || "") + '" data-fk="st-htz"></label>' +
      '<label class="dn-l">Country code (two letters, optional)<input type="text" name="home_cc" maxlength="2" placeholder="br" value="' + E((s.home && s.home.country_code) || "") + '" data-fk="st-hcc"></label></div>' +
      '<label class="dn-l">Your cities and other names for your country (one per line)<textarea rows="3" name="home_places" data-fk="st-hp">' + E(((s.home && s.home.places) || []).join("\n")) + '</textarea></label>' +
      '<label class="dn-l">Wider regions you can work from, such as latam or emea (one per line, optional)<textarea rows="2" name="home_regions" data-fk="st-hr">' + E(((s.home && s.home.regions) || []).join("\n")) + '</textarea></label>' +
      '<div class="dn-row"><label class="dn-l">Lowest pay you would take (optional)<input type="text" name="salary_floor" maxlength="60" value="' + E(s.salary_floor || "") + '" data-fk="st-sf"></label>' +
      '<label class="dn-l">Work type<select name="work_type" data-fk="st-wt">' + [["", "No preference"], ["remote", "Remote"], ["hybrid", "Hybrid"], ["onsite", "On-site"], ["any", "Any"]].map(function (o) { return '<option value="' + o[0] + '"' + (s.work_type === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></label></div>" +
      '<label class="dn-l">Sign-off<input type="text" name="sign_off" maxlength="200" value="' + E(s.sign_off || "") + '" data-fk="st-sign"></label>' +
      '<label class="dn-l">Time zone for the sending window<input type="text" name="recipient_tz" maxlength="60" placeholder="Europe/London" value="' + E(s.recipient_tz || "") + '" data-fk="st-tz"></label>' +
      '<h3 class="dn-sec">Answers for application forms</h3><div class="dn-row">' + answerKeys().map(function (q) { return '<label class="dn-l">' + E(q.question) + '<input type="text" name="ans_' + E(q.key) + '" maxlength="300" value="' + E(a[q.key] || "") + '" data-fk="an-' + E(q.key) + '"></label>'; }).join("") + "</div>" +
      '<h3 class="dn-sec">Limits</h3><div class="dn-row"><label class="dn-l">Emails a day (most ' + (c.max_per_day || 0) + ')<input type="number" min="0" max="' + (c.max_per_day || 0) + '" name="cap_per_day" value="' + (c.per_day != null ? c.per_day : "") + '" data-fk="st-cd"></label><label class="dn-l">Emails an hour (most ' + (c.max_per_hour || 0) + ')<input type="number" min="0" max="' + (c.max_per_hour || 0) + '" name="cap_per_hour" value="' + (c.per_hour != null ? c.per_hour : "") + '" data-fk="st-ch"></label></div><p class="dn-dim">Lowering a limit is one step. Raising it asks you to confirm.</p>' +
      '<button type="submit" class="dn-go" data-fk="st-go"' + (S.busy.prefs ? " disabled" : "") + ">" + (S.busy.prefs ? "Saving…" : "Save settings") + "</button></form>";
    h += '<form class="dn-form" data-dn-form="blocklist" autocomplete="off"><h3 class="dn-sec">Companies Friday never writes to</h3><p class="dn-dim">Put your own employer here. One rule a line: a domain (example.com), a company name (name: Example Ltd) or a job-board name (board: example). Friday drops these roles before she scores them, and the sender checks again before every send. The list stays on this Mac.</p>' +
      '<label class="dn-l">Blocklist<textarea rows="5" name="text" data-fk="bl-text" placeholder="example.com&#10;name: Example Ltd">' + E(S.blocklist || "") + '</textarea></label><button type="submit" class="dn-go" data-fk="bl-go"' + (S.busy.blocklist ? " disabled" : "") + ">" + (S.busy.blocklist ? "Saving…" : "Save the blocklist") + "</button></form>";
    var ev = S.events || [];
    if (ev.length) h += '<details class="dn-more"><summary>What Friday did lately (' + Math.min(ev.length, 30) + ')</summary><ol class="dn-events">' + ev.slice(0, 30).map(function (e) { return "<li><span class=\"dn-dim\">" + E(when(e.ts)) + "</span> " + E(U.cut(e.message || e.text || e.kind || "", 200)) + "</li>"; }).join("") + "</ol></details>";
    return h + "</section>";
  }

  /* ---------- the page ---------- */
  /* ---------- Friday's page, sample E (DJ14L): Today, Companies, one company. Light only. ---------- */
  var MORE = [["forms", "Apply by form"], ["hand", "Search by hand"], ["jobs", "Jobs found"], ["profile", "Profile review"], ["cv", "CV facts"], ["boards", "Boards"], ["sheets", "Sheets"], ["settings", "Settings"]];
  var TABS = [["all", "All", null], ["needs", "Needs you", null], ["watch", "Watching", ["not_checked", "checking", "no_roles", "roles_found"]],
              ["sent", "Sent", ["applied"]], ["replied", "Replied", ["reply", "interview", "offer"]], ["stopped", "Stopped", ["site_blocked", "blocklisted"]], ["closed", "Closed", ["rejected", "not_interested"]]];
  function gradeBadge(g, big) { return '<span class="e-grade ' + (g || "N") + (big ? " big" : "") + '" aria-label="' + (g ? "Fit " + g : "Not scored") + '">' + (g || "–") + "</span>"; }
  function eChip(c) { var t = c.tone; return '<span class="e-chip ' + (c.unconfirmed ? "check" : t === "amber" ? "check" : t === "green" ? "ready" : t === "red" ? "stop" : "working") + '">' + E(c.label) + "</span>"; }
  function topBar() {
    var st = S.status || {}, paused = !!st.paused, busy = S.busy["pause"], c = S.command;
    var h = '<div class="e-bar"><div class="e-live' + (c && c.state.tone === "stop" ? " stop" : "") + '" role="status"><i class="e-orb" aria-hidden="true"></i><div class="e-txt"><b>' + E(c ? c.state.head : "Friday") + "</b><span>" + E(c ? c.state.sub : "") + "</span></div>";
    h += '<button type="button" class="e-btn" data-dn-act="pause" data-fk="pause"' + (busy ? " disabled" : "") + ">" + (busy ? "One moment…" : paused ? "Switch on" : "Pause Friday") + "</button></div>";
    if (st.writer_blocker && !paused) h += '<p class="e-block" role="status">' + E(st.writer_blocker) + "</p>";
    return h + "</div>";
  }
  function nav() {
    var c = counts();
    var h = '<nav class="e-nav" aria-label="Friday"><div class="e-tabs">' + SECTIONS.slice(0, 3).map(function (x) {
      var n = x[0] === "wait" ? c.wait : x[0] === "today" || x[0] === "companies" ? (S.command ? S.command.needs_companies : 0) : 0;
      var cur = S.section === x[0] || (x[0] === "companies" && S.section === "company");
      return '<button type="button" class="e-tab" data-dn-act="goto" data-to="' + x[0] + '" data-fk="tab-' + x[0] + '"' + (cur ? ' aria-current="page"' : "") + ">" + x[1] + (n ? ' <span class="e-n">' + n + "</span>" : "") + "</button>";
    }).join("") + '</div><div class="e-more" aria-label="More">' + MORE.map(function (x) {
      return '<button type="button" class="e-mbtn" data-dn-act="goto" data-to="' + x[0] + '" data-fk="tab-' + x[0] + '"' + (S.section === x[0] ? ' aria-current="page"' : "") + ">" + x[1] + "</button>";
    }).join("") + "</div></nav>";
    return h;
  }
  function updatedLine() {
    return '<p class="e-fresh" role="status"><i class="' + (S.command && S.command.health.ok ? "ok" : "bad") + '" aria-hidden="true"></i>' + E(S.command ? S.command.health.text : "") + " This page refreshes by itself.</p>";
  }
  function needRows(limit) {
    var d = cData(), rows = d.companies.filter(function (c) { return c.needs; });
    return rows.slice(0, limit);
  }
  function eTodayView() {
    var c = S.command, d = cData();
    if (!c) return '<p class="e-dim">Loading…</p>';
    var n = c.needs_companies != null ? c.needs_companies : c.needs.length;
    var h = '<div class="e-hero"><section class="e-panel e-need" aria-labelledby="e-h1"><h1 id="e-h1">' + (n ? n + (n === 1 ? " company needs" : " companies need") + " you" : "Nothing needs you right now") + '</h1><p class="e-lede">Nothing is sent until you press Approve. ' + (n ? "About " + c.needs_mins + " minutes." : "Friday keeps working.") + "</p>";
    if (n) h += '<h2 class="e-subh">Next for you</h2><ol class="e-tasks">' + c.needs.map(function (x, i) { return '<li><div><b>' + E(x.title) + "</b><span>" + E(x.why) + " About " + x.mins + ' min.</span></div><button type="button" class="' + (i === 0 ? "e-btn primary" : "e-btn") + '" data-dn-act="goto" data-to="' + x.go + '" data-fk="need-' + x.id + '">' + E(x.btn) + "</button></li>"; }).join("") + "</ol>";
    var rows = needRows(4);
    if (rows.length) h += '<h2 class="e-subh">Companies waiting on you</h2><ul class="e-next">' + rows.map(function (r) { return '<li><button type="button" class="e-nrow" data-dn-act="company" data-id="' + r.id + '" data-fk="nx-' + r.id + '" title="' + E(r.name) + '">' + gradeBadge(r.grade) + '<span class="e-nm"><b>' + E(r.name) + '</b><small>' + E(r.next) + "</small></span>" + eChip(r) + '<span class="e-arrow" aria-hidden="true">›</span></button></li>'; }).join("") + "</ul>";
    h += "</section>";
    h += '<aside class="e-panel e-side" aria-labelledby="e-h-lim"><h2 id="e-h-lim">Today\'s limits</h2><p class="e-dim">Friday stops at each limit and tells you here.</p><ul class="e-meters">' + c.budgets.map(function (b) {
      var pct = b.max ? Math.min(100, Math.round(b.used / b.max * 100)) : 0;
      return '<li class="e-meter' + (b.out ? " low" : "") + '"><div class="e-row"><span>' + E(b.name) + '</span><b>' + b.used + " of " + b.max + '</b></div><div class="e-bar2" role="img" aria-label="' + E(b.name) + " " + b.used + " of " + b.max + '"><i style="width:' + pct + '%"></i></div>' + (b.note ? '<p class="e-hint">' + E(b.note) + '</p><button type="button" class="e-btn small" data-dn-act="goto" data-to="settings" data-fk="raise-' + b.id + '">Change the limit</button>' : "") + "</li>";
    }).join("") + "</ul>";
    var left = c.setup_left;
    h += '<div class="e-foot"><b>Setup ' + (c.setup.length - left) + " of " + c.setup.length + "</b><div class=\"e-setup\">" + c.setup.map(function (x) { return '<button type="button" class="e-step ' + (x.done ? "done" : "todo") + '" data-dn-act="goto" data-to="' + x.go + '" data-fk="su-' + x.id + '">' + (x.done ? "Done: " : "To do: ") + E(x.name) + "</button>"; }).join("") + "</div></div></aside></div>";
    h += eFlow();
    h += '<section class="e-panel e-sec" aria-labelledby="e-h-day"><h2 id="e-h-day">Friday\'s day</h2><ul class="e-day">' + (c.log.length ? c.log.map(function (x) { return "<li><time>" + E(x.time) + '</time><i class="e-d" aria-hidden="true"></i><span>' + E(U.cut(x.text, 140)) + "</span></li>"; }).join("") : '<li class="e-dim">Nothing yet today.</li>') + "</ul></section>";
    h += digestHtml();
    return h;
  }
  function eFlow() {
    var tl = (S.command && S.command.timeline) || { steps: [] };
    if (!tl.steps.length) return "";
    var live = 0; tl.steps.forEach(function (x, i) { if (x.n > 0) live = i; });
    var h = '<section class="e-panel e-sec" aria-labelledby="e-h-flow"><div class="e-sechead"><div><h2 id="e-h-flow">Where today\'s jobs are</h2><p class="e-dim">Every job moves left to right. Press a step to see its companies.</p></div><div class="e-seg" role="group" aria-label="Time range">' +
      [["today", "Today"], ["week", "This week"]].map(function (r) { return '<button type="button" class="e-btn small" aria-pressed="' + (S.span === r[0]) + '" data-dn-act="span" data-to="' + r[0] + '" data-fk="span-' + r[0] + '">' + r[1] + "</button>"; }).join("") + "</div></div>";
    h += '<ol class="e-flow" style="--live:' + live + '">' + tl.steps.map(function (x) {
      var cls = x.id === "stopped" && x.n ? "stop" : x.id === "ready" && x.n ? "rdy" : x.n ? "done" : "todo";
      return '<li class="e-stage ' + cls + '"><button type="button" data-dn-act="stepgo" data-to="' + x.id + '" data-fk="fl-' + x.id + '"><span class="e-pip">' + x.n + '</span><span class="e-name">' + E(x.name) + '</span><span class="e-why">' + E(x.why) + "</span></button></li>";
    }).join("") + "</ol>";
    if (tl.say) h += '<div class="e-say"><p>' + E(tl.say) + "</p></div>";
    return h + "</section>";
  }
  function eCompaniesView() {
    var d = cData(), all = d.companies;
    var h = '<div class="e-pageh"><div><h1>Companies</h1><p class="e-dim">Fit grades: A is 80 or more, B is 70 to 79, C is a near miss (60 to 69).</p></div></div>';
    if (!all.length) return h + '<section class="e-panel e-empty"><h2>0 companies</h2><p>Add the 178 starter companies, or add a sheet of companies.</p><div class="e-btns"><button type="button" class="e-btn primary" data-dn-act="seed" data-fk="co-seed">Add the starter companies</button><button type="button" class="e-btn" data-dn-act="goto" data-to="sheets" data-fk="co-sheets">Add a sheet</button></div></section>';
    var cur = TABS.filter(function (t) { return t[0] === S.ctab; })[0] || TABS[0];
    var countOf = function (t) { return t[0] === "needs" ? all.filter(function (c) { return c.needs; }).length : t[2] ? all.filter(function (c) { return t[2].indexOf(c.status) >= 0; }).length : all.length; };
    h += '<div class="e-controls"><div class="e-tabs2" role="tablist" aria-label="Status">' + TABS.map(function (t) { return '<button type="button" role="tab" class="e-tab2" aria-selected="' + (S.ctab === t[0]) + '" data-dn-act="ctab" data-to="' + t[0] + '" data-fk="ct-' + t[0] + '">' + t[1] + ' <span class="e-n">' + countOf(t) + "</span></button>"; }).join("") + "</div>";
    h += '<div class="e-gradef" role="group" aria-label="Fit"><span>Fit</span>' + [["", "All"], ["A", "A"], ["B", "B"], ["C", "C"]].map(function (g) { return '<button type="button" aria-pressed="' + (S.cgrade === g[0]) + '" data-dn-act="cgrade" data-to="' + g[0] + '" data-fk="cg-' + (g[0] || "all") + '">' + g[1] + "</button>"; }).join("") + "</div>";
    h += '<label class="e-search"><span class="vh">Search</span><input type="search" value="' + E(S.cq) + '" maxlength="80" placeholder="Search companies" data-dn-cq="1" data-fk="cq"></label></div>';
    if (S.cstep) h += '<p class="e-dim">Showing the step “' + E((FUNNEL.filter(function (f) { return f[0] === S.cstep; })[0] || [0, S.cstep])[1]) + '”. <button type="button" class="e-link" data-dn-act="cstep" data-to="' + S.cstep + '" data-fk="step-clear">Show all</button></p>';
    var rows = eFiltered(), pages = Math.max(1, Math.ceil(rows.length / PAGE)), pg = Math.min(S.cpage, pages - 1);
    S.cpage = pg;
    h += '<section class="e-panel e-board">';
    if (!rows.length) h += '<div class="e-empty"><h2>' + E(emptyFilterText()) + "</h2></div>";
    else {
      h += '<div class="e-thead" aria-hidden="true"><span>Fit</span><span>Company and why</span><span>Where</span><span>Status</span><span></span></div><ul class="e-rows">' + rows.slice(pg * PAGE, pg * PAGE + PAGE).map(function (r) {
        return '<li><button type="button" class="e-crow" data-dn-act="company" data-id="' + r.id + '" data-fk="co-' + r.id + '" title="' + E(r.name) + '">' + gradeBadge(r.grade) + '<span><b class="e-two">' + E(r.name) + '</b><span class="e-meta">' + (r.best != null ? "Score " + r.best + " of 100 · " : "") + (r.roles ? r.roles + (r.roles === 1 ? " role" : " roles") : "no roles") + '</span></span><span class="e-who"><b>' + E(U.cut(r.country || "", 30)) + "</b><span>" + E(U.cut(r.source, 40)) + '</span></span><span>' + eChip(r) + '<span class="e-nx">' + E(r.next) + '</span></span><span class="e-arrow" aria-hidden="true">›</span></button></li>';
      }).join("") + "</ul>";
      if (rows.length > PAGE) h += '<div class="e-pager"><span class="e-dim">' + (pg * PAGE + 1) + " to " + Math.min(rows.length, pg * PAGE + PAGE) + " of " + rows.length + '</span><div class="e-btns"><button type="button" class="e-btn small" data-dn-act="cpage" data-to="-1" data-fk="pg-prev"' + (pg ? "" : " disabled") + '>Previous 50</button><button type="button" class="e-btn small" data-dn-act="cpage" data-to="1" data-fk="pg-next"' + (pg < pages - 1 ? "" : " disabled") + ">Next 50</button></div></div>";
    }
    h += "</section>";
    h += '<div class="e-btns"><button type="button" class="e-btn small" data-dn-act="creplies" data-fk="co-replies"' + (S.busy.creplies ? " disabled" : "") + ">" + (S.busy.creplies ? "Reading Gmail…" : "Check for replies now") + "</button></div>";
    return h;
  }
  function eFiltered() {
    var all = cData().companies, t = TABS.filter(function (x) { return x[0] === S.ctab; })[0] || TABS[0], q = S.cq.trim().toLowerCase();
    var rows = all.filter(function (c) {
      if (t[0] === "needs") { if (!c.needs) return false; }
      else if (t[2] && t[2].indexOf(c.status) < 0) return false;
      if (S.cgrade && c.grade !== S.cgrade) return false;
      if (S.cstep && (c.step || []).indexOf(S.cstep) < 0) return false;
      return !q || (c.name + " " + c.country + " " + c.source).toLowerCase().indexOf(q) >= 0;
    });
    return rows;
  }
  function eCompanyView() {
    var c = S.cdetail;
    var back = '<div class="e-crumbs"><button type="button" class="e-btn" data-dn-act="goto" data-to="companies" data-fk="back">‹ Companies</button>';
    var order = needRows(200), idx = order.map(function (x) { return x.id; }).indexOf(S.copen);
    if (idx >= 0) back += '<span class="e-dim">Company ' + (idx + 1) + " of " + order.length + ' that need you</span><span class="e-pn"><button type="button" class="e-icon" aria-label="Previous company" data-dn-act="company" data-id="' + (order[idx - 1] || {}).id + '" data-fk="pv"' + (idx ? "" : " disabled") + '>‹</button><button type="button" class="e-icon" aria-label="Next company" data-dn-act="company" data-id="' + (order[idx + 1] || {}).id + '" data-fk="nx"' + (idx < order.length - 1 ? "" : " disabled") + ">›</button></span>";
    back += "</div>";
    if (!c) return back + '<p class="e-dim">Loading…</p>';
    var d = cData();
    var left = '<section class="e-panel e-card"><div class="e-cohead">' + gradeBadge(c.grade, true) + '<div><p class="e-gl">' + (c.fit ? "Fit " + c.grade + " · " + c.fit.total + " of 100" : "Not scored yet") + '</p><h1>' + E(c.name) + '</h1><p class="e-where">' + E([c.country, c.source].filter(Boolean).join(" · ")) + "</p></div>" + eChip(c) + "</div>";
    left += '<p class="e-short"><b>' + E(c.next) + "</b> " + E(c.why) + "</p>";
    if (c.unconfirmed) left += '<div class="e-btns"><button type="button" class="e-btn" data-dn-act="cstatus" data-id="' + c.id + '" data-to="rejected" data-fk="cp-conf">Yes, it is a rejection</button><button type="button" class="e-btn" data-dn-act="cdismiss" data-id="' + c.id + '" data-fk="cp-dis">No, it is not</button></div>';
    var r = c.route;
    left += '<div class="e-route">' + (r && r.kind === "email" ? '<div class="e-r main"><span class="e-tag">Write to</span><b>' + E(r.person ? r.person + " · " + r.to : r.to) + "</b><span>" + E({ post: "From the job post", hn_post: "From a Hacker News post", careers_page: "From the careers page", contact_page: "From the contact page", public_post: "From a public post" }[r.origin] || "A published address") + "</span></div>" : r && r.kind === "card" ? '<div class="e-r main"><span class="e-tag">Apply</span><b>Apply on their site</b><span>' + E(r.reason || "No hiring address is published.") + "</span>" + (r.apply_url ? " " + link(r.apply_url, "Open the form") : "") + "</div>" : '<div class="e-r"><span class="e-tag">Route</span><b>No route yet</b><span>Friday finds one after she scores a role.</span></div>') + "</div></section>";
    if (c.fit) left += '<section class="e-panel e-card"><h2>Why it fits</h2><p class="e-dim">' + E(c.fit.why || "") + "</p><ul class=\"e-factors\">" + c.fit.parts.map(function (p) { return '<li class="e-factor' + (p.weak ? " weak" : "") + '"><div class="e-row"><span>' + E(p.label) + '</span><b>' + p.got + " <small>of " + p.max + '</small></b></div><div class="e-bar2" role="img" aria-label="' + E(p.label) + " " + p.got + " of " + p.max + '"><i style="width:' + Math.round(p.got / p.max * 100) + '%"></i></div></li>'; }).join("") + "</ul></section>";
    if (c.fit && c.fit.gap) left += '<section class="e-panel e-card"><h2>Gaps</h2><ul class="e-gaps"><li><b>What Friday leaves out</b><span>' + E(c.fit.gap) + "</span></li></ul></section>";
    var roles = c.roles || [];
    if (roles.length) {
      var roleLi = function (x) { return "<li><div><b>" + E(U.cut(x.title, 70)) + '</b><span>' + E(U.cut(x.location || "", 50)) + "</span></div>" + (x.score != null ? '<span class="e-chip working">' + x.score + "</span>" : "") + link(x.url, "Open the post") + "</li>"; };
      var good = roles.filter(function (x) { return x.match; }), rest = roles.filter(function (x) { return !x.match; }), first = good.slice(0, 5), more = good.slice(5).concat(rest);
      left += '<section class="e-panel e-card"><h2>Open roles here</h2><p class="e-dim">' + (good.length ? good.length + " of " + (c.roles_total || roles.length) + " roles match your titles." : "No role matches your titles. " + (c.roles_total || roles.length) + " other roles are open.") + '</p><ul class="e-roles">' + first.map(roleLi).join("") + "</ul>" + (more.length ? '<details class="e-more2"><summary>Show all ' + (c.roles_total || roles.length) + " roles (" + more.length + ' more)</summary><ul class="e-roles">' + more.map(roleLi).join("") + "</ul></details>" : "") + "</section>";
    }
    left += eMoreCards(c);
    return back + '<div class="e-detail"><div class="e-dleft">' + left + "</div>" + eEmailCard(c) + "</div>";
  }
  function eMoreCards(c) {
    var d = cData(), h = '<section class="e-panel e-card"><h2>Your decision</h2><label class="e-l">Move to<select data-dn-cmove="' + c.id + '" data-fk="cp-move"><option value="">Choose a status</option><option value="auto">Let Friday decide</option>' + (d.statuses || []).filter(function (x) { return d.hand_ok.indexOf(x.id) >= 0; }).map(function (x) { return '<option value="' + x.id + '"' + (c.hand === x.id ? " selected" : "") + ">" + E(x.label) + "</option>"; }).join("") + '</select></label><p class="e-dim">Moving a company sends nothing.</p><label class="e-l">Your note<textarea rows="3" maxlength="1000" data-dn-cnote="' + c.id + '" data-fk="cp-note">' + E(c.note || "") + '</textarea></label><button type="button" class="e-btn small" data-dn-act="cnote" data-id="' + c.id + '" data-fk="cp-save">Save note</button></section>';
    var sec = function (t, items) { return items.length ? '<section class="e-panel e-card"><h2>' + t + '</h2><ul class="e-plain">' + items.join("") + "</ul></section>" : ""; };
    if (c.warm && c.warm.people && c.warm.people.length) h += '<section class="e-panel e-card"><h2>People you know here</h2><ul class="e-plain">' + c.warm.people.map(function (p) { return "<li><b>" + E(p.name) + '</b> <span class="e-dim">' + E(U.cut(p.position || "", 60)) + (p.connected_on ? " · connected " + E(p.connected_on) : "") + "</span></li>"; }).join("") + '</ul><label class="e-l">A note you can send him yourself<textarea rows="6" readonly data-fk="cp-ask">' + E(c.warm.ask) + '</textarea></label><p class="e-dim">Friday never messages anyone on LinkedIn. You copy this and send it yourself.</p></section>';
    h += sec("Replies", (c.replies || []).map(function (x) { return "<li><b>" + E(U.cut(x.subject, 80)) + '</b> <span class="e-dim">' + E(when(x.ts)) + ({ rejection: " · looks like a rejection", invite: " · an invitation", question: " · a question", auto: " · automatic" }[x.kind] || "") + "</span><br>" + E(U.cut(x.snippet, 300)) + "</li>"; }));
    var ins = Object.keys(c.insights || {});
    if (ins.length) h += '<section class="e-panel e-card"><h2>From the sheet</h2><dl class="e-ins">' + ins.map(function (k) { return "<dt>" + E(k) + "</dt><dd>" + E(U.cut(String(c.insights[k]), 200)) + "</dd>"; }).join("") + '</dl><p class="e-dim">Friday never opens or messages these people.</p></section>';
    h += sec("History", (c.log || []).map(function (x) { return "<li>" + E(when(x.ts)) + " · <b>" + E((c.labels && c.labels[x.status]) || x.status) + '</b> <span class="e-dim">' + E(x.by === "owner" ? "you" : "Friday") + ": " + E(x.reason) + "</span></li>"; }));
    return h;
  }
  function eEmailCard(c) {
    var m = c.email;
    if (!m) return '<aside class="e-panel e-draft"><h2>Email</h2><p class="e-dim">No email is written for this company yet. Friday writes one when a role scores 70 or more and she has an address.</p></aside>';
    var id = m.id, bz = id && !!S.busy["o" + id], ed = id && S.edit[id] !== undefined, locked = (c.checks || []).length > 0 || m.state === "failed";
    var h = '<aside class="e-panel e-draft" aria-labelledby="e-h-mail"><div class="e-draftH"><h2 id="e-h-mail">The email</h2>' + (m.state === "held" ? '<span class="e-chip check">On hold</span>' : m.state === "failed" ? '<span class="e-chip check">Needs a check</span>' : m.state === "undo" ? '<span class="e-chip ready">Sending</span>' : m.state === "queued" ? '<span class="e-chip working">Approved</span>' : '<span class="e-chip ready">Ready</span>') + "</div>";
    h += '<div class="e-trust"><div><b>To</b>' + E(m.to) + "</div><div><b>From</b>" + E(m.sender || "Friday's Gmail") + "</div><div><b>Goes out</b>" + E(m.goes_out) + "</div><div><b>Facts used</b>" + ((m.facts_used || []).length ? m.facts_used.map(E).join("; ") : "None found in the text") + "</div></div>";
    if (m.reason) h += '<p class="e-warn" role="status">' + E(m.reason) + "</p>";
    if (ed) h += '<label class="e-fld">Subject<input class="e-subj" type="text" maxlength="200" data-dn-edit="subject" data-id="' + id + '" data-fk="es-' + id + '" value="' + E(S.edit[id].subject) + '"></label><label class="e-fld">Email<textarea class="e-body" rows="14" maxlength="6000" data-dn-edit="body" data-id="' + id + '" data-fk="eb-' + id + '">' + E(S.edit[id].body) + "</textarea></label>";
    else h += '<p class="e-fld">Subject</p><p>' + E(m.subject) + '</p><p class="e-fld">Email</p><pre class="e-bodytxt">' + E(clip(m.body, 3000)) + "</pre>";
    if (locked) {
      h += '<ul class="e-checks" aria-label="Is this true?">' + (c.checks || []).map(function (k) { return '<li><span>The draft says <b>' + E(k.word) + '</b>. Is this true about you?</span><span class="e-btns"><button type="button" class="e-btn small" data-dn-act="allowword" data-id="' + k.draft_id + '" data-word="' + E(k.word) + '" data-fk="yes-' + E(k.word) + '">Yes, it is true</button></span></li>'; }).join("") + "</ul>";
      h += '<div class="e-decide"><div class="e-btns"><button type="button" class="e-btn" data-dn-act="rewrite" data-id="' + m.draft_id + '" data-fk="rw-' + m.draft_id + '">Write again</button><button type="button" class="e-btn primary" disabled aria-disabled="true" data-fk="ap-locked">Approve</button></div><p class="e-dim">Approve unlocks when every line above is answered and the draft passes the check.</p></div>';
    } else if (id && (m.state === "waiting" || m.state === "held")) {
      h += '<div class="e-decide"><h3>Your decision</h3><div class="e-btns"><button type="button" class="e-btn primary" data-dn-act="' + (S.needok[id] ? "approve-anyway" : "approve") + '" data-id="' + id + '" data-fk="ap-' + id + '"' + (bz ? " disabled" : "") + ">" + (bz ? E(S.busy["o" + id]) : S.needok[id] ? "I know, send anyway" : ed ? "Approve with my changes" : "Approve") + '</button><button type="button" class="e-btn" data-dn-act="edit" data-id="' + id + '" data-fk="ed-' + id + '">' + (ed ? "Cancel the edit" : "Edit") + '</button><button type="button" class="e-btn" data-dn-act="reject" data-id="' + id + '" data-fk="rj-' + id + '">Reject</button></div>' + (S.needok[id] ? '<p class="e-warn" role="status">' + E(S.needok[id]) + "</p>" : "") + '<p class="e-dim">After Approve you have 10 seconds to undo. Nothing goes out before that.</p></div>';
    } else if (id && m.state === "queued") h += '<div class="e-decide"><p class="e-dim">Approved. It goes out later by itself.</p><button type="button" class="e-btn" data-dn-act="reject" data-id="' + id + '" data-fk="rj-' + id + '">Reject</button></div>';
    return h + "</aside>";
  }
  function undoToast() {
    var out = "";
    (S.outbox.waiting || []).forEach(function (x) {
      var un = S.undo[x.id];
      if (x.state === "undo" && un && un.until - Date.now() > 0) {
        var left = Math.max(0, Math.ceil((un.until - Date.now()) / 1000));
        out += '<div class="e-toast" role="status"><div><b>Sending to ' + E(x.company || x.domain) + ' in <span data-dn-count="' + x.id + '">' + left + '</span> seconds.</b><span>Press Undo to stop it.</span></div><button type="button" class="e-btn primary" data-dn-act="undo" data-id="' + x.id + '" data-fk="un-' + x.id + '">Undo</button></div>';
      }
    });
    return out;
  }
  function pageE() {
    var main;
    if (S.section === "today") main = eTodayView();
    else if (S.section === "companies") main = eCompaniesView();
    else if (S.section === "company") main = eCompanyView();
    else main = { wait: waitHtml, forms: formsHtml, hand: handHtml, jobs: jobsHtml, profile: profileHtml, cv: cvHtml, boards: boardsHtml, sheets: sheetsHtml, settings: settingsHtml }[S.section]();
    return '<div class="e-wrap">' + topBar() + nav() + main + updatedLine() + undoToast() + "</div>";
  }
  function pageHtml() {
    return pageE();
  }
  function pageHtmlOld() {
    var body = { today: todayHtml, wait: waitHtml, forms: formsHtml, hand: handHtml, jobs: jobsHtml, profile: profileHtml, cv: cvHtml, boards: boardsHtml, sheets: sheetsHtml, settings: settingsHtml }[S.section]();
    return topHtml() + updatedHtml() + healthHtml() + tabsHtml() + body;
  }
  /* A text box, a text area or a menu holds what the owner is typing. A file picker, a tick box and a button do not: the page redraws and puts the focus back (put). */
  function typing(a, root) {
    if (!a || !root.contains(a)) return false;
    if (a.tagName === "TEXTAREA" || a.tagName === "SELECT") return true;
    return a.tagName === "INPUT" && !/^(file|checkbox|radio|button|submit)$/.test(a.type);
  }
  function loadCommand() { return call("GET", "/command?span=" + S.span).then(function (r) { S.command = r; }); }
  function draw(force) {
    var root = $("root");
    if (!root || !S.shown) return;
    var sec = document.querySelector('[data-tab="donna"]');
    if (sec) sec.setAttribute("data-theme", "light");        /* Friday's page is light only (DJ14L) */
    var err = $("err");
    err.hidden = !S.err; err.textContent = S.err || "";
    var a = document.activeElement;
    if (!force && typing(a, root)) return;      /* never redraw under someone who is typing (the search box asks for it) */
    if (S.off) { $("off").hidden = false; root.hidden = true; return; }
    $("off").hidden = true;
    if (!S.status) { root.hidden = true; return; }
    root.hidden = false;
    put(root, pageHtml());
  }
  function editedValues(id) {
    var s = document.querySelector('[data-dn-edit="subject"][data-id="' + id + '"]'), b = document.querySelector('[data-dn-edit="body"][data-id="' + id + '"]');
    return s && b ? { subject: s.value, body: b.value } : null;
  }
  /* What the engine answered to Approve. Only "undo" starts the countdown; every other answer is shown as it is (DJ11 S1). */
  function answerOf(id, r) {
    var st = r && r.status, secs = (S.status && S.status.undo_seconds) || 10;
    if (st === "undo") { S.undo[id] = { until: Date.now() + secs * 1000, stale: false }; delete S.needok[id]; say("Approved. You have " + secs + " seconds to undo."); return; }
    if (st === "needs_ok") { S.needok[id] = r.reason || "Another agent wrote to this company lately."; S.notice = "Not approved yet. Read the note on the email."; return; }
    if (st === "already") { S.notice = "This email is already approved."; return; }
    S.notice = (st === "held" ? "Not sent. " : st === "refused" ? "Not allowed. " : "Not approved. ") + (r && r.reason ? r.reason : "A check stopped it.");
    say(S.notice);
  }
  function lines(v) { return String(v || "").split("\n").map(function (x) { return x.trim(); }).filter(Boolean); }

  function bind() {
    if (S.bound) return;
    S.bound = true;
    S.paper = U.paper();
    window.addEventListener("bots-theme", function (e) { S.paper = !!e.detail; draw(); });
    var root = $("root");
    root.addEventListener("click", function (e) {
      var b = e.target.closest("[data-dn-act]");
      if (!b) return;
      if (b.tagName === "A") e.preventDefault();
      var a = b.getAttribute("data-dn-act"), id = b.getAttribute("data-id");
      if (a === "goto") { S.section = b.getAttribute("data-to"); draw(); if (LIGHT_SECTIONS.indexOf(S.section) < 0) load().then(draw); var h = root.querySelector("h2[tabindex]"); if (h) h.focus({ preventScroll: true }); return; }
      if (a === "pause") return void act("pause", "…", function () { return call("POST", "/pause", { paused: !(S.status && S.status.paused) }); });
      if (a === "approve" || a === "approve-anyway") {
        var ed = editedValues(id), p = Promise.resolve(), anyway = a === "approve-anyway";
        if (ed) p = act("o" + id, "Saving your changes…", function () { return call("POST", "/outbox/" + id + "/edit", ed); }).then(function () { delete S.edit[id]; });
        return void p.then(function () {
          return act("o" + id, "Waiting for your OK…", function () {
            return call("POST", "/outbox/" + id + "/approve", anyway ? { ok_other_agent: true } : {}).then(function (r) { answerOf(id, r); return r; });
          });
        });
      }
      if (a === "edit") { if (S.edit[id] !== undefined) delete S.edit[id]; else { var x = waiting().filter(function (w) { return String(w.id) === id; })[0]; S.edit[id] = { subject: x.subject, body: x.body }; } return void draw(); }
      if (a === "reject") return void act("o" + id, "Rejecting…", function () { return call("POST", "/outbox/" + id + "/reject", {}); }).then(function () { say("Rejected. Nothing was sent."); });
      if (a === "undo") return void act("o" + id, "Undoing…", function () { return call("POST", "/outbox/" + id + "/undo", {}); }).then(function () { delete S.undo[id]; say("Stopped. The email is back in the list."); draw(); });
      if (a === "allowword") return void act("w" + id, "…", function () { return call("POST", "/drafts/" + id + "/allow_word", { word: b.getAttribute("data-word") }); }).then(function (r) { if (r) say("Allowed. Press Write again."); });
      if (a === "rewrite") return void act("w" + id, "Writing…", function () { return call("POST", "/drafts/" + id + "/rewrite", {}); }).then(function (r) { if (r) { S.notice = r.state === "waiting" ? "The new draft is in the list." : r.state === "failed" ? "The new draft did not pass the check either. Read why below." : (r.reason || "Friday could not write it now."); draw(); } });
      if (a === "sounds") return void act("s" + id, "…", function () { return call("POST", "/drafts/" + id + "/sounds_like_me", { yes: b.getAttribute("data-yes") === "1" }); });
      if (a === "applied") return void act("f" + id, "Saving…", function () { return call("POST", "/cards/" + id + "/applied", {}); });
      if (a === "alljobs") { S.jobsAll = !S.jobsAll; return void draw(); }
      if (a === "maybe") { S.maybe = !S.maybe; return void draw(); }
      if (a === "learn") return void act("learn", "Reading…", function () { return call("POST", "/profile/learn", {}); });
      if (a === "keepitem") return void act("k" + id, "…", function () { return call("POST", "/profile/items/" + encodeURIComponent(id), { keep: b.getAttribute("data-keep") === "1" }); });
      if (a === "keepex") return void act("k" + id, "…", function () { return call("POST", "/profile/examples/" + id, { state: "keep" }); });
      if (a === "removeex") return void U.confirmBox("Remove this example?", ["Friday deletes the text of this email. You cannot get it back."], "Remove it").then(function (yes) { if (yes) act("k" + id, "…", function () { return call("POST", "/profile/examples/" + id, { state: "remove" }); }); });
      if (a === "useprofile") return void act("useprofile", "…", function () { return call("POST", "/profile/approve", {}); }).then(function (r) { if (r) say("Profile saved. Friday writes in your style now."); });
      if (a === "tickall") { var ids = (S.cv.facts || []).map(function (f) { return f.id; }); return void act("tick", "…", function () { return call("POST", "/cv/facts/tick", { ids: ids, ticked: b.getAttribute("data-ticked") === "1" }); }); }
      if (a === "delfact") return void U.confirmBox("Delete this fact?", ["Friday will not use it in any email. You can add it again."], "Delete it").then(function (yes) { if (yes) act("d" + id, "…", function () { return call("DELETE", "/cv/facts/" + id); }); });
      if (a === "mergefacts") return void act("merge", "…", function () { return call("POST", "/cv/facts/merge", {}); }).then(function (r) { if (r) say(r.removed + " repeated rows removed. Your ticks stay."); });
      if (a === "cvtext") { var t = root.querySelector("[data-dn-cvtext]"); return void act("cv", "…", function () { return call("POST", "/cv/text", { text: t ? t.value : "" }); }); }
      if (a === "seed") return void act("seed", "…", function () { return call("POST", "/dataset/import", { seed: true }); });
      if (a === "copen" || a === "company") { if (id && id !== "undefined") return void openCompany(parseInt(id, 10)); return; }
      if (a === "ctab") { S.ctab = b.getAttribute("data-to"); S.cpage = 0; return void draw(); }
      if (a === "cgrade") { S.cgrade = b.getAttribute("data-to"); S.cpage = 0; return void draw(); }
      if (a === "stepgo") { S.cstep = b.getAttribute("data-to"); S.ctab = "all"; S.cgrade = ""; S.cq = ""; S.cpage = 0; S.section = "companies"; return void draw(); }
      if (a === "cclose") { var back = S.copen; S.copen = 0; S.cdetail = null; draw(); var bk = root.querySelector('[data-fk="co-' + back + '"],[data-fk="cc-' + back + '"]'); if (bk) bk.focus({ preventScroll: true }); return; }
      if (a === "cfilter") { S.cfilter = b.getAttribute("data-to"); S.cstatus = ""; S.cstep = ""; S.cpage = 0; return void draw(); }
      if (a === "cstat") { var to = b.getAttribute("data-to"); S.cstatus = S.cstatus === to ? "" : to; S.cfilter = "all"; S.cstep = ""; S.cpage = 0; return void draw(); }
      if (a === "cstep") { e.preventDefault(); var st = b.getAttribute("data-to"); S.cstep = S.cstep === st ? "" : st; S.cfilter = "all"; S.cstatus = ""; S.cpage = 0; return void draw(); }
      if (a === "span") { S.span = b.getAttribute("data-to"); return void loadCommand().then(draw); }
      if (a === "cpage") { S.cpage = Math.max(0, S.cpage + parseInt(b.getAttribute("data-to"), 10)); return void draw(); }
      if (a === "creplies") return void act("creplies", "…", function () { return call("POST", "/board/replies", {}); }).then(function (r) { if (r) say(r.new ? r.new + " new replies." : "No new replies."); });
      if (a === "cstatus") return void act("cs" + id, "…", function () { return call("POST", "/board/" + id + "/status", { status: b.getAttribute("data-to") }); }).then(function () { openCompany(parseInt(id, 10)); });
      if (a === "cdismiss") return void act("cs" + id, "…", function () { return call("POST", "/board/" + id + "/dismiss", {}); }).then(function () { openCompany(parseInt(id, 10)); });
      if (a === "cnote") { var ta = root.querySelector("[data-dn-cnote]"); return void act("cn" + id, "…", function () { return call("POST", "/board/" + id + "/note", { note: ta ? ta.value : "" }); }).then(function () { say("Note saved."); }); }
      if (a === "again") return void act("sh" + id, "…", function () { return call("POST", "/sheets/" + id + "/read-again", {}); }).then(function (r) { if (r) say("Read again. New rows were added."); });
      if (a === "rmsheet") return void U.confirmBox("Remove this sheet?", ["Friday deletes her copy of the rows. The companies and jobs she already added stay."], "Remove it").then(function (yes) { if (yes) act("sh" + id, "…", function () { return call("DELETE", "/sheets/" + id); }); });
      if (a === "usemap") {
        var box = b.parentNode.querySelector(".dn-map"), m = {};
        box.querySelectorAll("[data-dn-map]").forEach(function (sel) { if (sel.value !== "") m[sel.getAttribute("data-dn-map")] = parseInt(sel.value, 10); });
        return void act("sh" + id, "…", function () { return call("POST", "/sheets/" + id + "/mapping", { tab: b.getAttribute("data-tab"), mapping: m }); }).then(function (r) { if (r) say("Done. Friday read the rows again with your columns."); });
      }
      if (a === "bfilter") { S.boardFilter = b.getAttribute("data-to"); return void draw(); }
      if (a === "board") return void act("b" + id, "…", function () { return call("POST", "/boards/" + id, { state: b.getAttribute("data-to") }); });
      if (a === "test") return void act("test", "Testing…", function () { return call("POST", "/test", {}); }).then(function (r) { if (r) { S.note = (r.imap === "ok" && r.smtp === "ok") ? "The login works. Nothing was sent." : "The login did not work. Check the address and the app password."; draw(); } });
    });
    root.addEventListener("change", function (e) {
      var t = e.target;
      if (t.matches("[data-dn-cmove]")) { var mid = t.getAttribute("data-dn-cmove"); if (!t.value) return; return void act("cs" + mid, "…", function () { return call("POST", "/board/" + mid + "/status", { status: t.value }); }).then(function () { openCompany(parseInt(mid, 10)); }); }
      if (t.matches("[data-dn-csel]")) { S[t.getAttribute("data-dn-csel")] = t.value; S.cpage = 0; return void draw(true); }
      if (t.matches("[data-dn-tick]")) return void act("t" + t.getAttribute("data-dn-tick"), "…", function () { return call("PATCH", "/cv/facts/" + t.getAttribute("data-dn-tick"), { ticked: t.checked }); });
      if (t.matches("[data-dn-file=conn]")) {
        var cf = t.files && t.files[0];
        if (!cf) return;
        if (cf.size > 5 * 1024 * 1024) { S.err = "The file is larger than 5 MB."; return void draw(); }
        var rc = new FileReader();
        rc.onload = function () { act("conn", "…", function () { return call("POST", "/connections", { csv: String(rc.result) }); }).then(function (r) { if (r) say(r.added + " people added."); }); };
        return void rc.readAsText(cf);
      }
      if (t.matches("[data-dn-file=sheet]")) {
        var sf = t.files && t.files[0];
        if (!sf) return;
        if (sf.size > 5 * 1024 * 1024) { S.err = "The file is larger than 5 MB."; return void draw(); }
        var rs = new FileReader();
        rs.onload = function () { var data = String(rs.result).split(",")[1] || ""; act("sheet", "…", function () { return call("POST", "/sheets", { filename: sf.name, data_base64: data }); }).then(function (r) { if (r) say("Friday read the file."); }); };
        return void rs.readAsDataURL(sf);
      }
      if (t.matches("[data-dn-file]")) {
        var f = t.files && t.files[0];
        if (!f) return;
        if (f.size > 5 * 1024 * 1024) { S.err = "The CV is larger than 5 MB. Save a smaller PDF and try again."; return void draw(); }
        var rd = new FileReader();
        rd.onload = function () { var data = String(rd.result).split(",")[1] || ""; act("cv", "…", function () { return call("POST", "/cv/upload", { filename: f.name, data_base64: data }); }); };
        rd.readAsDataURL(f);
      }
    });
    document.addEventListener("keydown", function (e) {
      if (!S.shown || (S.section !== "today" && S.section !== "companies") || e.ctrlKey || e.metaKey || e.altKey) return;
      var t = e.target, typingNow = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if (typingNow) { if (e.key === "Escape") t.blur(); return; }
      var k = e.key;
      if (k === "/") { var q = root.querySelector("[data-dn-cq]"); if (q) { e.preventDefault(); q.focus(); } }
      else if (k === "j" || k === "k") {                    /* move through the list; Enter opens the focused row. There is no key for Approve. */
        var rows = [].slice.call(root.querySelectorAll(".e-crow")), i = rows.indexOf(document.activeElement);
        if (!rows.length) return;
        i = k === "j" ? Math.min(rows.length - 1, i + 1) : Math.max(0, i < 0 ? 0 : i - 1);
        rows[i].focus(); e.preventDefault();
      }
      else if (k === "t") U.setPaper(!U.paper());
      else if (k === "?") U.confirmBox("Keyboard keys", ["/ search the list", "J and K move down and up the list", "Enter opens a company", "1 to 7 filter by funnel step", "T switches light and dark", "Escape closes the company panel", "There is no key for Approve. You press it with the mouse or touch."], "Close");
      else if (/^[1-7]$/.test(k)) { S.cstep = S.cstep === FUNNEL[+k - 1][0] ? "" : FUNNEL[+k - 1][0]; S.ctab = "all"; S.cgrade = ""; S.cpage = 0; S.section = "companies"; draw(); }
    });
    root.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && S.section === "company") { S.section = "companies"; draw(); }
    });
    root.addEventListener("input", function (e) {
      var t = e.target;
      if (t.matches("[data-dn-cq]")) { S.cq = t.value; S.cpage = 0; return void draw(true); }
      if (t.matches("[data-dn-edit]")) { var id = t.getAttribute("data-id"); if (S.edit[id]) S.edit[id][t.getAttribute("data-dn-edit")] = t.value; }
    });
    root.addEventListener("submit", function (e) {
      var f = e.target.closest("[data-dn-form]");
      if (!f) return;
      e.preventDefault();
      var d = new FormData(f), kind = f.getAttribute("data-dn-form");
      if (kind === "sheet") return void act("sheet", "…", function () { return call("POST", "/sheets", { url: d.get("url") }); }).then(function (r) { if (r) { say("Friday read the sheet and keeps her own copy."); f.reset(); } });
      if (kind === "fact") return void act("fact", "…", function () { return call("POST", "/cv/facts", { kind: d.get("kind"), text: d.get("text") }); }).then(function () { f.reset(); });
      if (kind === "connect") return void act("connect", "…", function () { return call("PUT", "/connect", { address: d.get("address"), app_password: d.get("app_password") }); }).then(function (r) { S.note = r ? "Saved. Press Test the login to check it. Nothing was sent." : ""; f.reset(); draw(); });
      if (kind === "prefs") {
        var ans = {}; answerKeys().forEach(function (q) { var v = String(d.get("ans_" + q.key) || "").trim(); if (v) ans[q.key] = v; });
        var cur = S.settings || {}, titles = lines(d.get("titles")), places = lines(d.get("locations"));
        var body = { sign_off: String(d.get("sign_off") || ""), recipient_tz: String(d.get("recipient_tz") || ""), answers: ans };
        /* Send a list only when the owner changed it. The title list in force is wider than a first save would make it (DJ11 S3). */
        if (JSON.stringify(titles) !== JSON.stringify(cur.titles || [])) body.titles = titles;
        if (JSON.stringify(places) !== JSON.stringify(cur.locations || [])) body.locations = places;
        body.home = { country: String(d.get("home_country") || "").trim(), tz: String(d.get("home_tz") || "").trim(), country_code: String(d.get("home_cc") || "").trim(), places: lines(d.get("home_places")), regions: lines(d.get("home_regions")) };
        body.salary_floor = String(d.get("salary_floor") || "").trim(); body.work_type = String(d.get("work_type") || "");
        if (d.get("cap_per_day") !== "") body.cap_per_day = Number(d.get("cap_per_day"));
        if (d.get("cap_per_hour") !== "") body.cap_per_hour = Number(d.get("cap_per_hour"));
        act("prefs", "…", function () { return call("PUT", "/settings", body); }).then(function (r) { if (r) say("Settings saved."); });
      }
      if (kind === "blocklist") act("blocklist", "…", function () { return call("PUT", "/blocklist", { text: String(d.get("text") || "") }); }).then(function (r) { if (r) say("Blocklist saved."); });
    });
    /* The Undo countdown ticks on this page only; the engine holds the real time. */
    setInterval(function () {
      if (!S.shown) return;
      var any = false;
      Object.keys(S.undo).forEach(function (id) {
        var el = document.querySelector('[data-dn-count="' + id + '"]');
        if (!el) return;
        any = true;
        var left = Math.max(0, Math.ceil((S.undo[id].until - Date.now()) / 1000));
        el.textContent = String(left);
        if (left === 0 && !S.undo[id].done) { S.undo[id].done = true; setTimeout(function () { load().then(function () { delete S.undo[id]; draw(); }); }, 1200); }
      });
      /* An email whose 10 seconds are over goes out within seconds: look again every 3 seconds until it has moved on. */
      var onItsWay = (S.outbox.waiting || []).some(function (x) { return x.state === "sending" || (x.state === "undo" && !(S.undo[x.id] && S.undo[x.id].until - Date.now() > 0)); });
      if (onItsWay && !document.hidden && Date.now() - S.lastLoad > 3000 && !S.polling) { S.polling = true; load().then(draw).then(function () { S.polling = false; }, function () { S.polling = false; }); }
      if (any && !document.hidden) { /* nothing else */ }
    }, 500);
  }

  function render() {
    S.shown = true;
    bind();
    if (B.mock && !S.sample) {
      var q = location.search, v = /donna=empty/.test(q) ? "empty" : /donna=long/.test(q) ? "long" : "many";
      S.sample = window.BOTS_DONNA_SAMPLE ? window.BOTS_DONNA_SAMPLE(v) : null;
      if (!S.sample) { var sc = document.createElement("script"); sc.src = "donna-sample.js"; sc.onload = function () { S.sample = window.BOTS_DONNA_SAMPLE(v); load().then(draw); }; document.head.appendChild(sc); return; }
    }
    if (!S.loaded) load().then(draw); else draw();
  }
  function hide() { S.shown = false; }
  function init(o) {
    S.onChange = o && o.onChange;
    if (S.started || B.mock) return;
    S.started = true;
    S.timer = setInterval(function () { if (!document.hidden && S.shown) load().then(draw); else if (!document.hidden && S.loaded) load(); }, 45000);
  }
  function count() {
    var n = S.loaded && S.status ? needsYou().length + (S.outbox.failed_drafts || []).length : 0;
    return n ? { n: n, text: n + " waiting" } : null;
  }
  window.BotsDonna = { init: init, render: render, hide: hide, count: count };
})();
