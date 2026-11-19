/*
 * Member assistant chat widget.
 *
 * Embed on any page:
 *   <script src="https://<assistant server>/widget.js" defer></script>
 *
 * Optional attributes on that script tag:
 *   data-api="https://..."   server to talk to (default: wherever this file was loaded from)
 *   data-accent="#0f4c75"    brand color
 *   data-open="true"         open the chat when the page loads
 *
 * Everything renders inside a shadow root, so the host page's styles can't break the widget
 * and the widget's styles can't leak into the page. The conversation is kept in sessionStorage,
 * so it survives moving between pages of the site but ends when the tab closes.
 */
(function () {
  "use strict";
  if (window.__memberAssistantLoaded) return;
  window.__memberAssistantLoaded = true;

  var script = document.currentScript;
  var API = ((script && script.dataset.api) || (script ? new URL(script.src).origin : location.origin)).replace(/\/$/, "");
  var ACCENT = (script && script.dataset.accent) || "#0f4c75";
  var OPEN_ON_LOAD = script && script.dataset.open === "true";
  var STORE_KEY = "member-assistant:" + API;

  // ------------------------------------------------------------ storage (may be unavailable)

  function load() {
    try { return JSON.parse(sessionStorage.getItem(STORE_KEY)) || {}; } catch (e) { return {}; }
  }
  function save() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }
  var state = load();
  state.messages = state.messages || [];

  // ------------------------------------------------------------ text rendering

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function emphasis(html) {
    return html
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }
  function prettyUrl(url) {
    try {
      var u = new URL(url);
      var path = u.pathname.replace(/\/$/, "");
      var text = u.hostname.replace(/^www\./, "") + (path.length > 28 ? "/…" + path.slice(-24) : path);
      return text;
    } catch (e) { return url; }
  }
  function link(href, html) {
    return '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + html + "</a>";
  }
  var TOKEN_RE = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()]*[^\s<>().,;:!?'"*_\]])|([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;
  function inline(text) {
    var out = "", last = 0, m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(text))) {
      out += emphasis(esc(text.slice(last, m.index)));
      if (m[1]) out += link(m[2], emphasis(esc(m[1])));
      else if (m[3]) out += link(m[3], esc(prettyUrl(m[3])));
      else out += '<a href="mailto:' + esc(m[4]) + '">' + esc(m[4]) + "</a>";
      last = TOKEN_RE.lastIndex;
    }
    return out + emphasis(esc(text.slice(last)));
  }
  // A small Markdown subset: paragraphs, bullet and numbered lists, headings, bold, italics, links.
  function markdown(text) {
    var html = "", para = [], list = null;
    function flushPara() { if (para.length) { html += "<p>" + para.join("<br>") + "</p>"; para = []; } }
    function flushList() { if (list) { html += "<" + list.tag + ">" + list.items.join("") + "</" + list.tag + ">"; list = null; } }
    String(text || "").split("\n").forEach(function (line) {
      var bullet = line.match(/^\s*[-*•]\s+(.*)$/), numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      var heading = line.match(/^\s*#{1,6}\s+(.*)$/);
      if (bullet || numbered) {
        flushPara();
        var tag = bullet ? "ul" : "ol";
        if (!list || list.tag !== tag) { flushList(); list = { tag: tag, items: [] }; }
        list.items.push("<li>" + inline((bullet || numbered)[1]) + "</li>");
      } else if (!line.trim()) {
        flushPara(); flushList();
      } else if (heading) {
        flushPara(); flushList();
        html += '<p class="h">' + inline(heading[1]) + "</p>";
      } else {
        flushList();
        para.push(inline(line.trim()));
      }
    });
    flushPara(); flushList();
    return html;
  }

  // ------------------------------------------------------------ icons

  var ICONS = {
    chat: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.2 3.6A.5.5 0 0 1 5 19.2V16h-.5A2.5 2.5 0 0 1 2 13.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="8.5" cy="9.5" r="1.1" fill="currentColor"/><circle cx="12" cy="9.5" r="1.1" fill="currentColor"/><circle cx="15.5" cy="9.5" r="1.1" fill="currentColor"/>',
    close: '<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    refresh: '<path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.7M20 4v4.7h-4.7M20 12a8 8 0 0 1-13.7 5.6L4 15.3M4 20v-4.7h4.7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    send: '<path d="M4 12l15-7-5.5 15-2.6-6.1L4 12z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    search_knowledge_base: '<circle cx="10.5" cy="10.5" r="6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M15 15l5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    read_document: '<path d="M6 3h8l4 4v14H6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 12h6M9 16h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    list_programs: '<path d="M9 6h11M9 12h11M9 18h11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="4.5" cy="6" r="1.3" fill="currentColor"/><circle cx="4.5" cy="12" r="1.3" fill="currentColor"/><circle cx="4.5" cy="18" r="1.3" fill="currentColor"/>',
    find_contact: '<circle cx="12" cy="8" r="4" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    escalate_to_staff: '<path d="M4 12l15-7-5.5 15-2.6-6.1L4 12z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    log_visitor_need: '<path d="M5 4h14v16H5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8.5 12.5l2.5 2.5 4.5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    lock: '<rect x="5" y="10" width="14" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M8 10V7a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="1.8"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
  };
  function icon(name, size) {
    return '<svg viewBox="0 0 24 24" width="' + (size || 16) + '" height="' + (size || 16) + '" aria-hidden="true">' +
      (ICONS[name] || ICONS.search_knowledge_base) + "</svg>";
  }

  // ------------------------------------------------------------ styles

  var CSS = [
    ":host{all:initial;--accent:" + ACCENT + ";--ink:#1c2430;--muted:#5d6878;--line:#e3e7ed;--soft:#f3f5f8;--bg:#fff;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;font-size:15px;line-height:1.45;color:var(--ink)}",
    "*{box-sizing:border-box}",
    "button{font:inherit;cursor:pointer}",
    ".launcher{position:fixed;right:24px;bottom:24px;width:60px;height:60px;border-radius:50%;border:0;background:var(--accent);color:#fff;",
    "display:grid;place-items:center;box-shadow:0 8px 24px rgba(15,30,50,.28);z-index:2147483646;transition:transform .18s ease}",
    ".launcher:hover{transform:scale(1.06)}",
    ".launcher:focus-visible,.icon-btn:focus-visible,.chip:focus-visible,.send:focus-visible{outline:3px solid #f5b83d;outline-offset:2px}",
    ".teaser{position:fixed;right:96px;bottom:36px;max-width:240px;background:#fff;color:var(--ink);border-radius:14px;padding:10px 14px;",
    "box-shadow:0 6px 24px rgba(15,30,50,.18);z-index:2147483646;font-size:14px;display:flex;gap:8px;align-items:flex-start;animation:pop .25s ease}",
    ".teaser button{border:0;background:none;color:var(--muted);padding:0 0 0 4px;font-size:16px;line-height:1}",
    ".panel{position:fixed;right:24px;bottom:96px;width:390px;height:min(640px,calc(100vh - 120px));background:var(--bg);border-radius:18px;",
    "box-shadow:0 18px 50px rgba(15,30,50,.30);display:flex;flex-direction:column;overflow:hidden;z-index:2147483647;animation:pop .2s ease}",
    "[hidden]{display:none!important}",
    "@keyframes pop{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:none}}",
    ".head{background:var(--accent);color:#fff;padding:14px 12px 14px 16px;display:flex;align-items:center;gap:12px}",
    ".avatar{width:38px;height:38px;border-radius:50%;background:rgba(255,255,255,.18);display:grid;place-items:center;font-weight:700;font-size:14px;flex:none}",
    ".title{flex:1;min-width:0}.title b{display:block;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".title span{font-size:12.5px;opacity:.85;display:flex;align-items:center;gap:6px}",
    ".dot{width:7px;height:7px;border-radius:50%;background:#5ee08f;display:inline-block}",
    ".icon-btn{border:0;background:transparent;color:#fff;width:34px;height:34px;border-radius:8px;display:grid;place-items:center;opacity:.9}",
    ".icon-btn:hover{background:rgba(255,255,255,.15);opacity:1}",
    ".log{flex:1;overflow-y:auto;padding:16px 14px 8px;display:flex;flex-direction:column;gap:12px;background:#fafbfc;scroll-behavior:smooth}",
    ".msg{max-width:88%;padding:10px 13px;border-radius:16px;word-wrap:break-word;overflow-wrap:anywhere}",
    ".msg p{margin:0 0 8px}.msg p:last-child{margin-bottom:0}.msg p.h{font-weight:650}",
    ".msg ul,.msg ol{margin:4px 0 8px;padding-left:20px}.msg li{margin:2px 0}",
    ".msg a{color:var(--accent);font-weight:550}.msg code{background:var(--soft);padding:1px 4px;border-radius:4px;font-size:13px}",
    ".user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:5px;white-space:pre-wrap}",
    ".bot{align-self:flex-start;background:var(--bg);border:1px solid var(--line);border-bottom-left-radius:5px}",
    ".error{align-self:flex-start;background:#fff4f2;border:1px solid #f3c7bf;color:#8a2b17;font-size:14px}",
    ".error button{margin-top:8px;border:1px solid #e0a99c;background:#fff;color:#8a2b17;border-radius:8px;padding:4px 10px;font-size:13px}",
    ".chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:2px}",
    ".chip{border:1px solid var(--line);background:#fff;color:var(--accent);border-radius:999px;padding:7px 12px;font-size:13.5px;text-align:left}",
    ".chip:hover{border-color:var(--accent);background:var(--soft)}",
    ".steps{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:7px}",
    ".steps li{display:flex;gap:9px;align-items:flex-start;font-size:13.5px;color:var(--muted);animation:pop .2s ease}",
    ".steps li svg{flex:none;margin-top:2px;color:var(--accent)}",
    ".steps li.err svg{color:#b4472f}",
    ".steps small{display:block;color:#8a93a1;font-size:12px;margin-top:1px}",
    ".thinking{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--muted)}",
    ".dots{display:inline-flex;gap:3px}.dots i{width:6px;height:6px;border-radius:50%;background:var(--accent);opacity:.35;animation:blink 1.2s infinite}",
    ".dots i:nth-child(2){animation-delay:.2s}.dots i:nth-child(3){animation-delay:.4s}",
    "@keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}",
    ".working{display:flex;flex-direction:column;gap:9px}",
    "details.how{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}",
    "details.how summary{cursor:pointer;font-size:12.5px;color:var(--muted);list-style:none;display:flex;align-items:center;gap:6px}",
    "details.how summary::-webkit-details-marker{display:none}",
    "details.how summary:before{content:'▸';font-size:11px;transition:transform .15s}",
    "details.how[open] summary:before{transform:rotate(90deg)}",
    "details.how .steps{margin-top:8px}",
    ".sources{display:flex;flex-direction:column;gap:6px;margin-top:10px}",
    ".source{display:flex;gap:8px;align-items:center;text-decoration:none;border:1px solid var(--line);border-radius:10px;padding:7px 10px;color:var(--ink)!important;font-weight:400!important;background:var(--soft)}",
    ".source:hover{border-color:var(--accent)}",
    ".source svg{flex:none;color:var(--accent)}",
    ".source span{min-width:0}.source b{display:block;font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".source small{display:block;font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".ref{margin-top:10px;background:#eef7f0;border:1px solid #c7e3cd;color:#1f5130;border-radius:10px;padding:8px 10px;font-size:13px}",
    ".foot{border-top:1px solid var(--line);padding:10px 12px 8px;background:var(--bg)}",
    ".row{display:flex;gap:8px;align-items:flex-end}",
    "textarea{flex:1;resize:none;border:1px solid var(--line);border-radius:12px;padding:10px 12px;font:inherit;font-size:14.5px;max-height:120px;min-height:42px;color:var(--ink);background:#fff}",
    "textarea:focus,input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 18%,transparent)}",
    ".send{width:42px;height:42px;border-radius:12px;border:0;background:var(--accent);color:#fff;display:grid;place-items:center;flex:none}",
    ".send:disabled{opacity:.4;cursor:default}",
    ".note{font-size:11.5px;color:var(--muted);margin-top:6px;display:flex;justify-content:space-between;gap:8px}",
    ".count.over{color:#b4472f;font-weight:600}",
    ".gate{flex:1;display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;padding:28px;gap:12px;background:#fafbfc}",
    ".gate .badge{width:52px;height:52px;border-radius:50%;background:var(--soft);color:var(--accent);display:grid;place-items:center}",
    ".gate p{margin:0;color:var(--muted);font-size:14px}",
    ".gate form{display:flex;gap:8px;width:100%;max-width:280px;margin-top:6px}",
    ".gate input{flex:1;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;min-width:0}",
    ".gate button{border:0;background:var(--accent);color:#fff;border-radius:10px;padding:0 14px}",
    ".gate .err{color:#b4472f;font-size:13px;min-height:18px}",
    "@media (max-width:480px){.panel{right:0;bottom:0;width:100vw;height:100%;border-radius:0}",
    ".launcher{right:16px;bottom:16px}.teaser{right:84px;bottom:26px}}",
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"
  ].join("");

  // ------------------------------------------------------------ DOM

  var host = document.createElement("div");
  host.id = "member-assistant";
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML =
    "<style>" + CSS + "</style>" +
    '<div class="teaser" hidden><span>Questions? Ask our website assistant.</span><button type="button" aria-label="Dismiss">×</button></div>' +
    '<button class="launcher" type="button" aria-label="Open chat assistant" aria-expanded="false">' + icon("chat", 28) + "</button>" +
    '<section class="panel" role="dialog" aria-label="Website assistant" hidden>' +
    '  <header class="head"><div class="avatar" aria-hidden="true"></div>' +
    '    <div class="title"><b class="org">Website assistant</b><span><i class="dot"></i>AI assistant · answers from our website</span></div>' +
    '    <button class="icon-btn reset" type="button" aria-label="Start a new conversation" title="New conversation">' + icon("refresh", 18) + "</button>" +
    '    <button class="icon-btn close" type="button" aria-label="Close chat">' + icon("close", 18) + "</button>" +
    "  </header>" +
    '  <div class="log" aria-live="polite"></div>' +
    '  <div class="gate" hidden><div class="badge">' + icon("lock", 24) + "</div>" +
    "    <b>This is a private preview</b><p>Enter the access code you were given to try the assistant.</p>" +
    '    <form><input type="password" autocomplete="off" aria-label="Access code" placeholder="Access code"><button type="submit">Start</button></form>' +
    '    <div class="err" role="alert"></div></div>' +
    '  <footer class="foot"><div class="row">' +
    '    <textarea rows="1" placeholder="Ask a question…" aria-label="Your question"></textarea>' +
    '    <button class="send" type="button" aria-label="Send" disabled>' + icon("send", 20) + "</button></div>" +
    '    <div class="note"><span>AI can make mistakes. Please don\'t share personal details.</span><span class="count"></span></div>' +
    "  </footer>" +
    "</section>";
  document.body.appendChild(host);

  var $ = function (sel) { return root.querySelector(sel); };
  var panel = $(".panel"), launcher = $(".launcher"), logEl = $(".log"), input = $("textarea"),
      sendBtn = $(".send"), gate = $(".gate"), foot = $(".foot"), teaser = $(".teaser"), countEl = $(".count");
  var cfg = { org_name: "Website assistant", suggestions: [], max_message_chars: 1000, access_code_required: false };
  var busy = false;

  function initials(name) {
    var words = String(name).replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "AI").slice(0, 2)).toUpperCase();
  }
  function scrollDown() { logEl.scrollTop = logEl.scrollHeight; }

  // ------------------------------------------------------------ rendering messages

  function stepItem(step) {
    return '<li class="' + (step.is_error ? "err" : "") + '">' + icon(step.tool) + "<span>" + esc(step.label) +
      (step.detail ? "<small>" + esc(step.detail) + "</small>" : "") + "</span></li>";
  }
  function renderReply(m) {
    var r = m.reply || {}, html = markdown(r.text || m.text || "");
    var cites = (r.citations || []).slice(0, 4);
    if (cites.length) {
      html += '<div class="sources">' + cites.map(function (c) {
        return '<a class="source" href="' + esc(c.url) + '" target="_blank" rel="noopener noreferrer">' + icon("link", 15) +
          "<span><b>" + esc(c.title) + "</b><small>" + esc(prettyUrl(c.url)) + "</small></span></a>";
      }).join("") + "</div>";
    }
    (r.escalations || []).forEach(function (e) {
      html += '<div class="ref">Request passed to staff · reference <b>' + esc(e.reference) + "</b></div>";
    });
    var steps = m.steps || [];
    if (steps.length) {
      var secs = r.latency_s ? " · " + Math.round(r.latency_s) + " s" : "";
      html += '<details class="how"><summary>How I answered · ' + steps.length + " step" + (steps.length > 1 ? "s" : "") + secs +
        '</summary><ul class="steps">' + steps.map(stepItem).join("") + "</ul></details>";
    }
    return html;
  }
  function render(m) {
    var el = document.createElement("div");
    if (m.role === "user") { el.className = "msg user"; el.textContent = m.text; }
    else if (m.role === "error") {
      el.className = "msg error";
      el.innerHTML = esc(m.text) + (m.retry ? '<br><button type="button">Try again</button>' : "");
      if (m.retry) el.querySelector("button").onclick = function () { el.remove(); dropError(m); send(m.retry, true); };
    } else if (m.role === "welcome") {
      el.className = "msg bot";
      el.innerHTML = "<p>Hi! I can answer questions using <b>" + esc(cfg.org_name) + "</b>'s published pages and reports, " +
        "point you to the right contact, or pass a request on to staff.</p><p>What would you like to know?</p>";
    } else { el.className = "msg bot"; el.innerHTML = renderReply(m); }
    logEl.appendChild(el);
    return el;
  }
  function renderChips() {
    if (!cfg.suggestions.length) return;
    var wrap = document.createElement("div");
    wrap.className = "chips";
    cfg.suggestions.forEach(function (s) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "chip"; b.textContent = s;
      b.onclick = function () { wrap.remove(); send(s); };
      wrap.appendChild(b);
    });
    logEl.appendChild(wrap);
  }
  function renderAll() {
    logEl.innerHTML = "";
    render({ role: "welcome" });
    state.messages.forEach(render);
    if (!state.messages.length) renderChips();
    scrollDown();
  }
  function dropError(m) {
    var i = state.messages.indexOf(m);
    if (i >= 0) { state.messages.splice(i, 1); save(); }
  }

  // ------------------------------------------------------------ server calls

  function api(path, body) {
    return fetch(API + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
  }
  function errorText(res) {
    return res.json().then(function (b) { return b.error || "Something went wrong."; },
                           function () { return "Something went wrong (" + res.status + ")."; });
  }
  function startSession(code) {
    return api("/api/session", { access_code: code || state.code || "" }).then(function (res) {
      if (!res.ok) return errorText(res).then(function (t) { var e = new Error(t); e.status = res.status; throw e; });
      return res.json().then(function (b) { state.session = b.session_id; if (code) state.code = code; save(); });
    });
  }

  // Reads a Server-Sent Events stream from a POST response and calls onEvent(name, data).
  function readStream(res, onEvent) {
    var reader = res.body.getReader(), decoder = new TextDecoder(), buffer = "";
    function pump() {
      return reader.read().then(function (chunk) {
        if (chunk.done) return;
        buffer += decoder.decode(chunk.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop();
        parts.forEach(function (block) {
          var name = "message", data = "";
          block.split("\n").forEach(function (line) {
            if (line.indexOf("event: ") === 0) name = line.slice(7);
            else if (line.indexOf("data: ") === 0) data += line.slice(6);
          });
          if (data) { try { onEvent(name, JSON.parse(data)); } catch (e) { /* ignore a malformed event */ } }
        });
        return pump();
      });
    }
    return pump();
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    updateSend();
  }
  function updateSend() {
    var n = input.value.trim().length, max = cfg.max_message_chars;
    sendBtn.disabled = busy || !n || n > max;
    countEl.textContent = n > max * 0.8 ? n + " / " + max : "";
    countEl.className = "count" + (n > max ? " over" : "");
  }

  function send(text, isRetry) {
    text = String(text || "").trim();
    if (!text || busy) return;
    var chips = logEl.querySelector(".chips");
    if (chips) chips.remove();
    if (!isRetry) {
      var userMsg = { role: "user", text: text };
      state.messages.push(userMsg); save(); render(userMsg);
    }
    setBusy(true);

    var bubble = document.createElement("div");
    bubble.className = "msg bot";
    bubble.innerHTML = '<div class="working"><div class="thinking"><span class="dots"><i></i><i></i><i></i></span>' +
      '<span class="label">Thinking…</span></div><ul class="steps"></ul></div>';
    logEl.appendChild(bubble); scrollDown();
    var stepsEl = bubble.querySelector(".steps"), labelEl = bubble.querySelector(".label");
    var steps = [], done = false;

    function fail(message) {
      bubble.remove();
      var m = { role: "error", text: message, retry: text };
      state.messages.push(m); save(); render(m); scrollDown();
    }
    function attempt(canRenew) {
      var ready = state.session ? Promise.resolve() : startSession();
      return ready.then(function () {
        return api("/api/chat", { session_id: state.session, message: text });
      }).then(function (res) {
        if (res.status === 404 && canRenew) { state.session = null; save(); return attempt(false); }
        if (!res.ok) return errorText(res).then(fail);
        return readStream(res, function (name, data) {
          if (name === "step") {
            steps.push(data);
            stepsEl.insertAdjacentHTML("beforeend", stepItem(data));
            labelEl.textContent = "Working…";
            scrollDown();
          } else if (name === "reply") {
            done = true;
            var m = { role: "bot", reply: data, steps: steps };
            state.messages.push(m); save();
            bubble.remove(); render(m); scrollDown();
          } else if (name === "error") {
            done = true; fail(data.message);
          }
        }).then(function () { if (!done) fail("The connection closed before the answer arrived. Please try again."); });
      });
    }
    attempt(true).catch(function (err) {
      if (err && err.status === 401) { state.session = null; state.code = null; save(); bubble.remove(); showGate(err.message); return; }
      fail(err && err.message && err.status ? err.message : "Couldn't reach the assistant. Check your connection and try again.");
    }).then(function () { setBusy(false); input.focus(); });
  }

  // ------------------------------------------------------------ access code

  function showGate(message) {
    gate.hidden = false; logEl.hidden = true; foot.hidden = true;
    $(".gate .err").textContent = message || "";
    setTimeout(function () { $(".gate input").focus(); }, 50);
  }
  function hideGate() { gate.hidden = true; logEl.hidden = false; foot.hidden = false; }
  $(".gate form").addEventListener("submit", function (e) {
    e.preventDefault();
    var code = $(".gate input").value.trim();
    if (!code) return;
    startSession(code).then(function () { hideGate(); renderAll(); input.focus(); },
                            function (err) { $(".gate .err").textContent = err.message; });
  });

  // ------------------------------------------------------------ open / close

  function open() {
    panel.hidden = false; teaser.hidden = true;
    launcher.setAttribute("aria-expanded", "true");
    launcher.setAttribute("aria-label", "Close chat assistant");
    launcher.innerHTML = icon("close", 24);
    state.open = true; state.teaserSeen = true; save();
    if (cfg.access_code_required && !state.session) showGate(); else { hideGate(); input.focus(); }
    scrollDown();
  }
  function close() {
    panel.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-label", "Open chat assistant");
    launcher.innerHTML = icon("chat", 28);
    state.open = false; save();
    launcher.focus();
  }
  launcher.onclick = function () { panel.hidden ? open() : close(); };
  $(".close").onclick = close;
  $(".reset").onclick = function () {
    state.messages = []; state.session = null; save();
    renderAll();
    if (cfg.access_code_required && !state.code) showGate();
    input.focus();
  };
  teaser.querySelector("button").onclick = function (e) { e.stopPropagation(); teaser.hidden = true; state.teaserSeen = true; save(); };
  teaser.onclick = open;
  root.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) close(); });

  input.addEventListener("input", function () {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
    updateSend();
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!sendBtn.disabled) { var t = input.value; input.value = ""; input.style.height = "auto"; updateSend(); send(t); }
    }
  });
  sendBtn.onclick = function () { var t = input.value; input.value = ""; input.style.height = "auto"; updateSend(); send(t); };

  // ------------------------------------------------------------ start

  fetch(API + "/api/config").then(function (r) { return r.json(); }).then(function (c) {
    cfg = c;
    $(".org").textContent = c.org_name;
    $(".avatar").textContent = initials(c.org_name);
    panel.setAttribute("aria-label", c.org_name + " website assistant");
  }).catch(function () { /* keep defaults; errors show when the visitor sends a message */ }).then(function () {
    renderAll();
    if (state.open || OPEN_ON_LOAD) open();
    else if (!state.teaserSeen) setTimeout(function () { if (panel.hidden) teaser.hidden = false; }, 1500);
  });
})();
