"use strict";
// One page, three views, state entirely in location.hash so every view is a
// link and the back button works:
//   #order=date                 newest first, no filter
//   #q=radium&company=maudio    full-text search inside one company
//   #view=sources&source=amd    panel switched to the low-level source list
//   #r/1234                     one release
//   #audit                      the gap dashboard
//
// Nothing from the API is ever assigned to innerHTML. Titles, excerpts and
// legacy bodies go in as textContent; a row's body_html - the small subset
// richtext.py emits - is parsed into an inert document and then REBUILT node
// by node against the allowlist below. Same result as a sanitizer, except the
// browser never sees a tag we did not construct ourselves. The bodies here
// are scraped from dead sites and full of raw markup and encoding damage, and
// innerHTML on that is both an injection and a display bug.
//
// RICH_TAGS mirrors richtext._ALLOWED on the Python side, the same way
// db._MOJIBAKE_SQL mirrors encoding.C1_RE: two languages, one rule. Change
// one and change the other.

const $ = (sel) => document.querySelector(sel);
const panel = $("#panel"), list = $("#list"), statusEl = $("#status");
const more = $("#more"), heading = $("#view-heading");

// The panel is pinned under the sticky topbar and capped at what is left of the
// viewport, and both need the topbar's real height. It is not a constant:
// between 60rem and ~72rem the filter form wraps to a second row. Measure it
// once and publish it as a token; app.css keeps 8.5rem as the pre-JS fallback.
// ResizeObserver fires immediately on observe(), so there is nothing to call at
// startup and no resize listener to add.
new ResizeObserver(([e]) => {
  document.documentElement.style.setProperty(
    "--topbar-h", e.borderBoxSize[0].blockSize + "px");
}).observe($(".topbar"));

const FLAGS = ["teaser", "short", "nodate", "mojibake", "plain"];
const FLAG_LABEL = {
  teaser: "teasery", short: "krótkie body",
  nodate: "bez daty", mojibake: "uszkodzone kodowanie",
  plain: "bez formatowania",
};

let companies = [];    // /api/companies, fetched once
let sources = [];      // flattened out of companies - same rows, one level down
let cursor = null;     // opaque `next` from the last page
let state = {};        // parsed hash

// ---- hash <-> state ------------------------------------------------------

function blank() {
  return { view: "list", panel: "companies", q: "", company: [], source: [],
           from: "", to: "", order: "rank", flags: [] };
}

function parseHash() {
  const raw = location.hash.replace(/^#/, "");
  const detail = raw.match(/^r\/(\d+)$/);
  if (detail) return { ...blank(), view: "detail", id: Number(detail[1]) };

  const audit = raw === "audit" || raw.startsWith("audit&");
  const p = new URLSearchParams(audit ? raw.slice("audit".length + 1) : raw);
  const csv = (key, allowed) => (p.get(key) || "").split(",")
    .filter((v) => v && (!allowed || allowed.includes(v)));
  return {
    view: audit ? "audit" : "list",
    // The panel is a display choice, not a filter, so it survives every view.
    panel: p.get("view") === "sources" ? "sources" : "companies",
    q: p.get("q") || "",
    company: csv("company"),
    source: csv("source"),
    from: p.get("from") || "",
    to: p.get("to") || "",
    order: p.get("order") === "date" ? "date" : "rank",
    flags: csv("flags", FLAGS),
  };
}

function toHash(s) {
  const p = new URLSearchParams();
  if (s.q) p.set("q", s.q);
  if (s.company && s.company.length) p.set("company", s.company.join(","));
  if (s.source && s.source.length) p.set("source", s.source.join(","));
  if (s.from) p.set("from", s.from);
  if (s.to) p.set("to", s.to);
  if (s.order === "date") p.set("order", "date");
  if (s.flags && s.flags.length) p.set("flags", s.flags.join(","));
  if (s.panel === "sources") p.set("view", "sources");
  const query = p.toString();
  if (s.view === "audit") return "#audit" + (query ? "&" + query : "");
  return "#" + query;
}

const go = (s) => { location.hash = toHash(s); };

// ---- small DOM helpers ---------------------------------------------------

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function link(href, text, cls) {
  const a = el("a", cls, text);
  a.href = href;
  return a;
}

function excerptNode(text) {
  // snippet() wraps matches in >>> <<<; turn those into <mark> without ever
  // handing the surrounding text to the HTML parser.
  const p = el("p");
  String(text).split(/>>>|<<</).forEach((part, i) => {
    p.appendChild(i % 2 ? el("mark", null, part) : document.createTextNode(part));
  });
  return p;
}

// tag -> attributes kept. Everything else is dropped; anything not listed
// here at all is unwrapped, children and all. Mirrors richtext._ALLOWED.
const RICH_TAGS = {
  p: [], br: [], hr: [], h3: [], h4: [], blockquote: [],
  ul: [], ol: [], li: [],
  table: [], thead: [], tbody: [], tfoot: [], tr: [],
  th: ["colspan", "rowspan"], td: ["colspan", "rowspan"],
  strong: [], em: [], sub: [], sup: [],
  a: ["href"], img: ["src", "alt", "title", "width", "height"],
};

const SAFE_SCHEME = /^(https?:|mailto:|ftp:)/i;

function safeUrl(value) {
  const v = (value || "").trim();
  if (!v) return false;
  if (SAFE_SCHEME.test(v)) return true;
  // No scheme before the first /, ? or # means a relative path, which is
  // harmless. Most of this corpus is relative paths into dead sites.
  return !/^[a-z0-9+.-]*:/i.test(v.split(/[/?#]/)[0]);
}

function imgNode(src, source) {
  // These src values mostly point at sites that no longer exist, so a plain
  // <img> would leave the page full of broken-image icons. The element is
  // still built from the stored src verbatim - if the file is ever recovered
  // it starts working with no reparse - and only the failed load degrades to
  // a caption naming the address that did not resolve.
  const img = document.createElement("img");
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  img.src = src;
  img.alt = source.getAttribute("alt") || "";
  ["title", "width", "height"].forEach((a) => {
    if (source.hasAttribute(a)) img.setAttribute(a, source.getAttribute(a));
  });
  img.addEventListener("error", () => {
    // A 1x1 spacer.gif that fails to load was never visible in the first
    // place - these templates are full of them - so it just goes. The <img>
    // itself stays in body_html either way; this is only about what a broken
    // load leaves on screen.
    const w = Number(source.getAttribute("width"));
    const h = Number(source.getAttribute("height"));
    if ((w && w <= 2) || (h && h <= 2)) { img.remove(); return; }
    img.replaceWith(el("span", "img-missing", "[obrazek: " + src + "]"));
  }, { once: true });
  return img;
}

function rebuild(source, target) {
  source.childNodes.forEach((node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      target.appendChild(document.createTextNode(node.nodeValue));
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;   // comments, doctypes
    const tag = node.tagName.toLowerCase();
    const keep = RICH_TAGS[tag];
    if (!keep) { rebuild(node, target); return; }      // unwrap the unknown
    if (tag === "img") {
      const src = node.getAttribute("src");
      if (safeUrl(src)) target.appendChild(imgNode(src, node));
      return;
    }
    const built = document.createElement(tag);
    keep.forEach((attr) => {
      const value = node.getAttribute(attr);
      if (value === null) return;
      if (attr === "href" && !safeUrl(value)) return;
      built.setAttribute(attr, value);
    });
    if (tag === "a" && built.hasAttribute("href")) built.rel = "noreferrer";
    rebuild(node, built);
    target.appendChild(built);
  });
}

function bodyNode(row) {
  if (!row.body_html) {
    // Pre-richtext row: one flat blob, shown with white-space: pre-wrap so
    // whatever newlines did survive the old get_text() still break.
    return el("p", "body body--text", row.body || "(pusty body)");
  }
  const doc = new DOMParser().parseFromString(row.body_html, "text/html");
  const out = el("div", "body body--rich");
  rebuild(doc.body, out);
  return out;
}

function dateNode(iso, cls) {
  if (!iso) return el("span", cls, "bez daty");
  const t = el("time", cls, iso);
  t.dateTime = iso;
  return t;
}

function badges(row) {
  const out = [];
  // row.grade, not row.detail_id: the verdict is its own column now, so the
  // browser no longer has to know which detail_id values are not references.
  if (row.grade === "teaser" || row.grade === "stub") {
    out.push(["teaser", row.grade]);
  } else if (row.body_len < 300) {
    out.push(["short", row.body_len + " zn."]);
  } else {
    out.push(["full", (row.body_len / 1000).toFixed(1) + "k"]);
  }
  // row.damaged comes from the API, judged over the whole body - the excerpt
  // alone would miss damage further down and disagree with the audit filter.
  if (row.damaged) out.push(["damaged", "kodowanie"]);
  // Provenance, deliberately NOT folded into the teaser badge: the text was
  // read off a page that is not this release's own (db.body_origin). On
  // terratec_new's CMS that listing usually carries the full release - median
  // 0.98 of the article's length over the 125 releases where both versions are
  // cached - so calling it a teaser would be wrong for most of them, while
  // saying nothing hides the one thing that is certain.
  // "z listingu" only when the capture is of a *different* document. The same
  // file on a sibling domain is a copy, and one scraper covers both addresses -
  // see serve.capture_kind.
  if (row.capture_page) {
    out.push(row.capture_kind === "mirror"
      ? ["listing", "z innej domeny", row.capture_page]
      : ["listing", "z listingu", row.capture_page]);
  }
  return out.map(([cls, label, title]) => {
    const b = el("span", "badge " + cls, label);
    if (title) b.title = title;
    return b;
  });
}

// ---- list view -----------------------------------------------------------

function hitNode(row) {
  const li = el("li", "hit");
  const meta = el("div", "meta");
  meta.appendChild(el("span", "firm", row.company || ""));
  meta.appendChild(el("span", "tag", row.source));
  meta.appendChild(dateNode(row.date));
  badges(row).forEach((b) => meta.appendChild(b));
  li.appendChild(meta);

  const h = el("h3");
  h.appendChild(link("#r/" + row.id, row.title || "(bez tytułu)"));
  li.appendChild(h);

  if (row.excerpt) li.appendChild(excerptNode(row.excerpt));
  return li;
}

function filterSummary() {
  const bits = [];
  if (state.company.length) {
    bits.push("firmy: " + state.company.map(companyLabel).join(", "));
  }
  if (state.source.length) bits.push("źródła: " + state.source.join(", "));
  if (state.from || state.to) bits.push(`daty: ${state.from || "…"}–${state.to || "…"}`);
  if (state.flags.length) {
    bits.push("filtry: " + state.flags.map((f) => FLAG_LABEL[f]).join(", "));
  }
  return bits;
}

async function loadList(append) {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  state.company.forEach((c) => p.append("company", c));
  state.source.forEach((s) => p.append("source", s));
  if (state.from) p.set("from", state.from);
  if (state.to) p.set("to", state.to);
  p.set("order", state.order);
  if (state.flags.length) p.set("flags", state.flags.join(","));
  if (append && cursor) p.set("after", cursor);
  p.set("limit", "50");

  list.setAttribute("aria-busy", "true");
  statusEl.textContent = "Szukam…";
  const res = await fetch("/api/search?" + p.toString());
  const data = await res.json();
  list.removeAttribute("aria-busy");
  if (!res.ok) {
    statusEl.textContent = "Błąd: " + (data.error || res.status);
    return;
  }

  let ol = list.querySelector("ol.hits");
  if (!append || !ol) {
    list.textContent = "";
    ol = el("ol", "hits");
    list.appendChild(ol);
  }
  data.results.forEach((row) => ol.appendChild(hitNode(row)));

  cursor = data.next;
  more.hidden = !cursor;
  const shown = ol.childElementCount;
  const bits = [state.q ? shown + " trafień" : shown + " wierszy"].concat(filterSummary());
  if (data.query_mode === "literal") {
    bits.push("zapytanie potraktowane jako fraza (składnia FTS się nie parsuje)");
  }
  if (data.truncated) bits.push("dalsze strony obcięte (limit stronicowania po trafności)");
  if (!shown) bits.push("nic nie znaleziono");
  statusEl.textContent = bits.join(" · ");
}

// ---- detail view ---------------------------------------------------------

// The tail of a url, for naming the page a capture is of: "presse.html",
// "print.php?sid=195", "index.php?do=media.media_pr". The full address stays
// in the link's title attribute.
function pageName(url) {
  return String(url).replace(/\/+$/, "").split("/").pop() || url;
}

async function loadDetail(id) {
  more.hidden = true;
  statusEl.textContent = "";
  const res = await fetch("/api/release/" + id);
  const row = await res.json();
  list.textContent = "";
  if (!res.ok) {
    statusEl.textContent = "Błąd: " + (row.error || res.status);
    return;
  }
  setHeading(row.title || "(bez tytułu)");

  const art = el("article", "detail");
  const meta = el("div", "meta");
  // Company first because that is how you got here; the source tag stays,
  // because it is the only thing that says which mirror this row came from.
  meta.appendChild(link(toHash({ ...blank(), panel: state.panel, order: "date",
                                 company: [row.company_slug] }),
                        row.company, "firm"));
  meta.appendChild(link(toHash({ ...blank(), panel: state.panel, order: "date",
                                 source: [row.source] }),
                        row.source, "tag"));
  meta.appendChild(dateNode(row.date));
  badges(row).forEach((b) => meta.appendChild(b));
  art.appendChild(meta);

  const links = el("p", "links");
  links.appendChild(document.createTextNode("oryginał: "));
  const orig = link(row.url, row.url);
  orig.rel = "noreferrer";
  links.appendChild(orig);
  if (row.wayback_url) {
    links.appendChild(document.createTextNode(" · "));
    // The capture's own timestamp, not the row's detail_id: they differ for a
    // row whose bytes came from a sibling domain, and the label has to name the
    // capture the link actually opens.
    const wb = link(row.wayback_url, "capture " + (row.capture_ts || row.detail_id));
    wb.rel = "noreferrer";
    links.appendChild(wb);
    if (row.capture_page) {
      // The timestamp names a capture of a *different* page - the listing (or
      // print view) this release was read out of, because archive.org has no
      // capture of the article itself. The link now goes there, so say which
      // page it is: an unannotated link would read as the article's own
      // capture, which is the misreading #4414 started from.
      wb.title = row.capture_page;
      links.appendChild(document.createTextNode(
        (row.capture_kind === "mirror" ? " (kopia z: " : " (capture strony: ")
        + (row.capture_kind === "mirror"
             ? row.capture_page.split("/")[2] : pageName(row.capture_page)) + ")"));
    }
  } else {
    links.appendChild(document.createTextNode(" · detail_id: " + (row.detail_id || "brak")));
  }
  art.appendChild(links);
  art.appendChild(bodyNode(row));

  const nav = el("nav", "nav-rel");
  nav.setAttribute("aria-label", "Sąsiednie komunikaty tego źródła");
  const nb = row.neighbours || {};
  ["prev", "next"].forEach((key) => {
    const n = nb[key];
    if (!n) { nav.appendChild(el("span")); return; }
    nav.appendChild(link("#r/" + n.id,
      (key === "prev" ? "← " : "→ ") + (n.date || "bez daty") + "  " +
      (n.title || "(bez tytułu)")));
  });
  art.appendChild(nav);
  list.appendChild(art);
}

// ---- audit view ----------------------------------------------------------

const AUDIT_CARDS = [
  // "bez capture" is not "from a live source": 241 of those rows are archive
  // rows whose attachment bytes were never cached, so no origin could be
  // established for them. The counter says what it can prove.
  ["total", "wiersze"], ["wayback", "z capture"], ["platform_id", "bez capture"],
  ["teaser", "teasery"], ["stub", "stuby"], ["short", "body < 300"],
  ["empty", "body puste"], ["nodate", "bez daty"], ["mojibake", "kodowanie"],
  ["plain", "bez formatowania"],
];
const GAP_COLS = [
  ["teaser", "teasery"], ["short", "krótkie"],
  ["nodate", "bez daty"], ["mojibake", "kodowanie"],
  ["plain", "bez form."],
];

function gapTable(rows, byCompany) {
  const table = el("table", "audit");
  table.appendChild(el("caption", null, byCompany
    ? "Braki per firma — każda liczba prowadzi do tych wierszy"
    : "Braki per źródło — każda liczba prowadzi do tych wierszy"));

  const head = el("tr");
  [byCompany ? "firma" : "źródło", "wiersze", "od", "do"]
    .concat(GAP_COLS.map((c) => c[1]))
    .forEach((label) => {
      const th = el("th", null, label);
      th.scope = "col";
      head.appendChild(th);
    });
  table.appendChild(el("thead")).appendChild(head);

  const body = el("tbody");
  rows.forEach((row) => {
    const base = { ...blank(), panel: state.panel, order: "date" };
    const pick = byCompany ? { company: [row.company] } : { source: [row.source] };
    const tr = el("tr");
    const th = el("th");
    th.scope = "row";
    th.appendChild(link(toHash({ ...base, ...pick }), byCompany ? row.label : row.source));
    tr.appendChild(th);
    tr.appendChild(el("td", null, String(row.count)));
    tr.appendChild(el("td", null, row.first || "—"));
    tr.appendChild(el("td", null, row.last || "—"));
    GAP_COLS.forEach(([flag]) => {
      const td = el("td", row[flag] ? null : "zero");
      if (row[flag]) {
        td.appendChild(link(toHash({ ...base, ...pick, flags: [flag] }), String(row[flag])));
      } else {
        td.textContent = "0";
      }
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  table.appendChild(body);

  const wrap = el("div", "table-wrap");
  wrap.appendChild(table);
  return wrap;
}

async function loadAudit() {
  more.hidden = true;
  statusEl.textContent = "Gdzie są dziury — liczby są linkami do tych wierszy";
  const q = await (await fetch("/api/quality")).json();
  list.textContent = "";

  const cards = el("div", "cards");
  AUDIT_CARDS.forEach(([key, label]) => {
    const card = el("div", "card");
    const b = el("b");
    if (FLAGS.includes(key)) {
      b.appendChild(link(toHash({ ...blank(), panel: state.panel, order: "date",
                                  flags: [key] }), String(q[key] || 0)));
    } else {
      b.textContent = String(q[key] || 0);
    }
    card.appendChild(b);
    card.appendChild(el("span", null, label));
    cards.appendChild(card);
  });
  list.appendChild(cards);
  list.appendChild(gapTable(state.panel === "sources" ? sources : companies,
                            state.panel !== "sources"));
}

// ---- panel ---------------------------------------------------------------

const companyLabel = (slug) => {
  const hit = companies.find((c) => c.company === slug);
  return hit ? hit.label : slug;
};

function switchLinks() {
  const box = el("div", "switch");
  [["companies", "firmy"], ["sources", "źródła"]].forEach(([which, label]) => {
    // Switching level drops the other level's selection: keeping a source
    // filter alive while the panel shows companies would hide rows with no
    // visible reason.
    const target = { ...state, panel: which, company: [], source: [] };
    const a = link(toHash(target), label);
    if (state.panel === which) a.setAttribute("aria-current", "true");
    box.appendChild(a);
  });
  return box;
}

// "2002–2006", or "2002" when a source has a single year, or "" when it has
// no dated rows at all. The dates arrive as full ISO strings; only the years
// are worth the width in a 15rem panel.
function yearSpan(first, last) {
  const a = (first || "").slice(0, 4), b = (last || "").slice(0, 4);
  if (!a && !b) return "";
  if (!a || !b || a === b) return a || b;
  return a + "\u2013" + b;
}

function pickItem(label, count, span, picked, target, altTarget, years) {
  const li = el("li");
  const a = link(toHash(target), null, "pick");
  a.appendChild(el("span", null, label));
  a.appendChild(el("span", "n", String(count)));
  // Own line rather than a third column: the longest source tag
  // (midiman_net_media_news) already fills the panel, so sharing the line
  // would mean truncating the one string you came here to read.
  if (years) a.appendChild(el("span", "yrs", years));
  if (span) a.title = span;
  if (picked) a.setAttribute("aria-current", "true");
  // The href is the plain-click meaning (switch to this one), so the item stays
  // a real link - copyable, and the whole panel works with JS-less semantics.
  // Ctrl/Cmd-click means "also this one", which costs the browser's
  // open-in-new-tab on these links: deliberate, because a panel item is a
  // filter toggle, not a destination. Ctrl+Enter from the keyboard reports the
  // same modifier, so it works there too.
  if (altTarget) {
    a.addEventListener("click", (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      go(altTarget);
    });
  }
  li.appendChild(a);
  return li;
}

function renderPanel() {
  const byCompany = state.panel !== "sources";
  panel.textContent = "";
  panel.appendChild(el("h2", null, byCompany
    ? companies.length + " firm"
    : sources.length + " źródeł (tagi per domenę)"));
  panel.appendChild(switchLinks());
  panel.appendChild(el("p", "hint", "Klik wybiera jedno · Ctrl/\u2318 + klik \u2014 kilka"));

  const ul = el("ul");
  const base = { ...state, view: state.view === "detail" ? "list" : state.view };
  const total = companies.reduce((n, c) => n + c.count, 0);
  ul.appendChild(pickItem("wszystkie", total, null,
    !state.company.length && !state.source.length,
    { ...base, company: [], source: [] }));

  const key = byCompany ? "company" : "source";
  const item = (row) => {
    const id = byCompany ? row.company : row.source;
    const picked = state[key].includes(id);
    // Default is a switch: one click, one company. Re-clicking the only picked
    // item clears back to "wszystkie", so the link always leads somewhere.
    const one = picked && state[key].length === 1 ? [] : [id];
    // With Ctrl/Cmd the same item adds itself to (or drops itself from) the
    // selection, which is how several companies get combined.
    const many = picked ? state[key].filter((x) => x !== id) : state[key].concat(id);
    return pickItem(byCompany ? row.label : row.source, row.count,
      (row.first || "?") + " – " + (row.last || "?"), picked,
      { ...base, [key]: one }, { ...base, [key]: many },
      byCompany ? "" : yearSpan(row.first, row.last));
  };

  if (byCompany) {
    companies.forEach((row) => ul.appendChild(item(row)));
  } else {
    // Sources grouped under their company, as a nested list. Without the
    // label the order of 24 tags reads as arbitrary - you can see it is not
    // alphabetical but not why. A nested <ul> rather than headings: the panel
    // already owns the only h2 here, and five more headings would clutter a
    // screen reader's outline of a nav for no gain.
    companies.forEach((c) => {
      if (!c.sources.length) return;
      const li = el("li", "grp");
      li.appendChild(el("p", "grp-label", c.label));
      const inner = el("ul");
      inner.setAttribute("aria-label", c.label);
      c.sources.forEach((row) => inner.appendChild(item(row)));
      li.appendChild(inner);
      ul.appendChild(li);
    });
  }
  panel.appendChild(ul);
}

// ---- wiring --------------------------------------------------------------

function setHeading(text) {
  heading.textContent = text;
  document.title = text + " — pressroom";
}

function listHeading() {
  if (state.q) return "Wyniki: " + state.q;
  if (state.company.length === 1 && !state.source.length) {
    return companyLabel(state.company[0]);
  }
  if (state.source.length === 1 && !state.company.length) return state.source[0];
  if (filterSummary().length) return "Wybrane wiersze";
  return "Najnowsze";
}

function syncControls() {
  $("#q").value = state.q || "";
  $("#from").value = state.from || "";
  $("#to").value = state.to || "";
  $("#order").value = state.order || "rank";
  document.querySelectorAll("#flags input").forEach((box) => {
    box.checked = state.flags.includes(box.value);
  });
}

function readControls() {
  return {
    ...state,
    view: "list",
    q: $("#q").value.trim(),
    from: $("#from").value.trim(),
    to: $("#to").value.trim(),
    order: $("#order").value,
    flags: Array.from(document.querySelectorAll("#flags input:checked")).map((b) => b.value),
  };
}

async function route() {
  state = parseHash();
  cursor = null;
  syncControls();
  renderPanel();
  if (state.view === "audit") setHeading("Audyt jakości");
  else if (state.view === "list") setHeading(listHeading());
  try {
    if (state.view === "detail") await loadDetail(state.id);
    else if (state.view === "audit") await loadAudit();
    else await loadList(false);
  } catch (e) {
    statusEl.textContent = "Błąd: " + e.message;
  }
  // A hash change replaces the whole main region; without moving focus a
  // keyboard or screen-reader user stays parked wherever the last link was.
  heading.focus();
}

$("#filters").addEventListener("submit", (e) => { e.preventDefault(); go(readControls()); });
$("#flags").addEventListener("change", () => go(readControls()));
$("#order").addEventListener("change", () => go(readControls()));
more.addEventListener("click", () => loadList(true));
window.addEventListener("hashchange", route);

(async function start() {
  companies = (await (await fetch("/api/companies")).json()).companies;
  // The company rows carry their own sources, so the low-level panel needs no
  // second request.
  // Alphabetical in both panels: the API sorts by size, which is the right
  // order for an audit and the wrong one for finding a name you already know.
  const collate = (a, b) => a.localeCompare(b, "pl");
  companies.sort((a, b) => collate(a.label, b.label));
  // Within a company, chronologically: the year span is what says which CMS
  // generation a tag covers, so `terratec_early` 1996 before `terratec_new_de`
  // 2007 reads as the site's history. Alphabetically they interleave by domain
  // and the sequence is lost. A source with no dated row at all sorts last -
  // "" would otherwise come before 1996.
  const chrono = (a, b) =>
    (a.first || "9999").localeCompare(b.first || "9999") ||
    (a.last || "9999").localeCompare(b.last || "9999") ||
    collate(a.source, b.source);
  companies.forEach((c) => c.sources.sort(chrono));
  sources = companies.flatMap((c) => c.sources);
  // Arriving with no hash: newest rows by date, so the corpus is browsable
  // without typing anything. Setting the hash fires hashchange -> route().
  if (!location.hash) { location.hash = "#order=date"; return; }
  await route();
})();
