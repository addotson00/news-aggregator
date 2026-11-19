/* NewScanner front end: loads static JSON, filters and searches in the browser. */
(() => {
  "use strict";

  const PAGE = 40;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Each topic page declares its slug: <meta name="newscanner-topic" content="...">
  const PAGE_TOPIC = document.querySelector('meta[name="newscanner-topic"]')?.content;
  const state = { topic: PAGE_TOPIC, q: "", show: "match", days: 0, where: "", tags: new Set(), hide: new Set(),
    sources: new Set(), limit: PAGE };
  const expanded = new Set();  // cards whose extra mention snippets are open (survives re-render)
  let urlHide = null;       // hide list from the URL; null = use the topic's default hidden_tags
  const defaultHide = () => new Set(topicMeta?.hidden_tags || []);
  const sameSet = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));
  let manifest = null;
  let topicMeta = null;
  let articles = [];        // current window
  let archived = null;      // loaded on demand when "All time" is chosen
  let archiveLoading = null;

  const prep = (a) => ({
    ...a,
    _time: Date.parse(a.published || a.first_seen) || 0,
    _hay: [a.title, a.summary, a.source_name, a.author, ...(a.tags || []), ...(a.categories || []),
      ...(a.also_at || []).map((x) => x.source_name), ...(a.mentions || [])].join(" \n ").toLowerCase(),
  });
  const pool = () => (state.days === -1 && archived ? articles.concat(archived) : articles);

  // ---------- URL state ----------
  function readUrl() {
    const p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.show = p.get("show") === "all" ? "all" : "match";
    state.days = Number(p.get("days") || 0);
    state.where = ["headline", "text"].includes(p.get("where")) ? p.get("where") : "";
    urlHide = p.has("hide") ? new Set((p.get("hide") || "").split("|").filter((t) => t && t !== "none")) : null;
    state.tags = new Set((p.get("tags") || "").split("|").filter(Boolean));
    state.sources = new Set((p.get("sources") || "").split("|").filter(Boolean));
  }
  function writeUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.show !== "match" && topicMeta?.mode !== "filter") p.set("show", state.show);
    if (state.days) p.set("days", state.days);
    if (state.where) p.set("where", state.where);
    if (topicMeta && !sameSet(state.hide, defaultHide())) p.set("hide", state.hide.size ? [...state.hide].join("|") : "none");
    if (state.tags.size) p.set("tags", [...state.tags].join("|"));
    if (state.sources.size) p.set("sources", [...state.sources].join("|"));
    const qs = p.toString();
    // Absolute pathname: the page uses <base href="../">, so a bare "?qs" would resolve to the hub.
    history.replaceState(null, "", location.pathname + (qs ? `?${qs}` : ""));
  }

  // ---------- Search ----------
  function parseQuery(q) {
    const terms = [];
    q.replace(/"([^"]+)"|(\S+)/g, (_, phrase, word) => { terms.push((phrase || word).toLowerCase()); });
    return terms;
  }
  const reEsc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Search terms: case-insensitive substrings. `exact` (keyword hits such as "UAF"): case-sensitive whole words.
  function highlight(text, terms, exact = []) {
    const parts = [];
    // split() with a capture group puts the matches at odd indices.
    const exactRe = exact.length ? new RegExp(`(?<![\\w-])(${exact.map(reEsc).join("|")})(?![\\w-])`) : null;
    const searchRe = terms.length ? new RegExp(`(${terms.map(reEsc).join("|")})`, "gi") : null;
    const chunks = exactRe ? String(text).split(new RegExp(exactRe.source, "g")) : [String(text)];
    chunks.forEach((chunk, i) => {
      if (exactRe && i % 2) { parts.push(`<mark class="kw">${esc(chunk)}</mark>`); return; }
      if (!searchRe) { parts.push(esc(chunk)); return; }
      chunk.split(searchRe).forEach((p, j) => parts.push(j % 2 ? `<mark>${esc(p)}</mark>` : esc(p)));
    });
    return parts.join("");
  }

  // ---------- Filtering ----------
  function passes(a, terms, skip) {
    if (state.show === "match" && !a.match) return false;
    if (state.days > 0 && a._time < Date.now() - state.days * 864e5) return false;
    if (skip !== "sources" && state.sources.size && !state.sources.has(a.source_id)) return false;
    if (skip !== "tags" && state.tags.size && !a.tags.some((t) => state.tags.has(t))) return false;
    if (skip !== "tags" && state.hide.size && a.tags.some((t) => state.hide.has(t))) return false;
    if (state.where && a.found_in !== state.where) return false;
    for (const t of terms) if (!a._hay.includes(t)) return false;
    return true;
  }

  // ---------- Rendering ----------
  const dateFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });
  function relTime(ms) {
    const d = (Date.now() - ms) / 1000;
    if (d < 3600) return `${Math.max(1, Math.round(d / 60))}m ago`;
    if (d < 86400) return `${Math.round(d / 3600)}h ago`;
    if (d < 86400 * 7) return `${Math.round(d / 86400)}d ago`;
    return dateFmt.format(ms);
  }

  function renderFacets(terms) {
    const tagCounts = {}, srcCounts = {};
    for (const a of pool()) {
      if (passes(a, terms, "tags")) for (const t of a.tags) tagCounts[t] = (tagCounts[t] || 0) + 1;
      if (passes(a, terms, "sources")) srcCounts[a.source_id] = (srcCounts[a.source_id] || 0) + 1;
    }
    $("tag-list").innerHTML = topicMeta.tags.map((t) => {
      const hidden = state.hide.has(t);
      const item = facetItem("tag", t, t, tagCounts[t] || 0, state.tags.has(t), hidden);
      return item.replace("</label></li>", `</label><button type="button" class="hide-btn" data-hide="${esc(t)}"
        aria-pressed="${hidden}" title="${hidden ? "Show" : "Hide"} articles tagged ${esc(t)}">${hidden ? "hidden" : "hide"}</button></li>`);
    }).join("");
    const srcs = topicMeta.sources.filter((s) => s.state !== "disabled");
    $("source-list").innerHTML = srcs.map((s) => facetItem("source", s.id, s.name, srcCounts[s.id] || 0, state.sources.has(s.id))).join("");
  }
  function facetItem(kind, value, label, n, checked, hidden = false) {
    const cls = [n || checked ? "" : "zero", hidden ? "is-hidden" : ""].join(" ").trim();
    return `<li><label class="${cls}"><input type="checkbox" data-${kind}="${esc(value)}" ${checked ? "checked" : ""} ${hidden ? "disabled" : ""}>${esc(label)}<span class="n">${n}</span></label></li>`;
  }

  function renderArticles(list, terms) {
    $("articles").innerHTML = list.slice(0, state.limit).map((a) => {
      const chips = a.tags.map((t) => `<button type="button" class="chip${state.tags.has(t) ? " on" : ""}" data-chip="${esc(t)}">${esc(t)}</button>`).join("");
      const date = a._time ? `<time datetime="${esc(a.published || a.first_seen)}" title="${esc(new Date(a._time).toLocaleString())}">${relTime(a._time)}</time>` : "";
      const also = (a.also_at || []).map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.source_name)}</a>`).join(", ");
      const kw = a.matched_terms || [];
      const badge = a.found_in === "text" ? `<span class="badge" title="The keyword appears in the article body, not the headline">in article text</span>` : "";
      const mentions = (a.mentions || []).length
        ? `<ul class="mentions">${a.mentions.map((m) => `<li>${highlight(m, terms, kw)}</li>`).join("")}</ul>` : "";
      return `<li class="card${expanded.has(a.id) ? " expanded" : ""}" data-id="${esc(a.id)}">
        <div class="meta"><span class="src">${esc(a.source_name)}</span>${date ? "<span>·</span>" + date : ""}${a.author ? `<span>·</span><span>${esc(a.author)}</span>` : ""}${badge}</div>
        <h3><a href="${esc(a.url)}" target="_blank" rel="noopener">${highlight(a.title, terms, kw)}</a></h3>
        ${a.summary ? `<p class="summary">${highlight(a.summary, terms, kw)}</p>` : ""}
        ${mentions}${(a.mentions || []).length > 1
          ? `<button type="button" class="more-mentions" data-more-mentions>+${a.mentions.length - 1} more mention${a.mentions.length > 2 ? "s" : ""}</button>` : ""}
        ${also ? `<div class="also">Also at ${also}</div>` : ""}
        ${chips ? `<div class="chips">${chips}</div>` : ""}
      </li>`;
    }).join("");
    $("more").hidden = list.length <= state.limit;
    $("empty").hidden = list.length > 0;
    const scope = state.show === "match" && topicMeta.mode !== "filter" ? "keyword matches" : "articles";
    $("count").textContent = `${list.length} ${scope}` + (list.length > state.limit ? ` · showing ${state.limit}` : "");
  }

  // Active filters as removable chips (and the count on the phone Filters button).
  function activeFilters() {
    const out = [];
    const dayLabel = { 1: "Last 24h", 7: "Last 7 days", "-1": "All time (incl. archive)" };
    if (state.show === "all" && topicMeta.mode !== "filter") out.push(["All articles", () => { state.show = "match"; }]);
    if (state.where) out.push([state.where === "text" ? "In article text" : "In headline", () => { state.where = ""; }]);
    if (dayLabel[state.days]) out.push([dayLabel[state.days], () => { state.days = 0; }]);
    for (const t of state.tags) out.push([`Tag: ${t}`, () => state.tags.delete(t)]);
    const defaults = defaultHide();
    for (const t of state.hide) if (!defaults.has(t)) out.push([`Hiding: ${t}`, () => state.hide.delete(t)]);
    for (const t of defaults) if (!state.hide.has(t)) out.push([`Showing: ${t}`, () => state.hide.add(t)]);
    const names = Object.fromEntries(topicMeta.sources.map((s) => [s.id, s.name]));
    for (const id of state.sources) out.push([`Source: ${names[id] || id}`, () => state.sources.delete(id)]);
    return out;
  }
  let activeRemovers = [];
  function renderActive() {
    const items = activeFilters();
    activeRemovers = items.map(([, remove]) => remove);
    $("active-filters").hidden = !items.length;
    $("active-filters").innerHTML = items.map(([label], i) =>
      `<button type="button" class="active-chip" data-remove="${i}" aria-label="Remove filter: ${esc(label)}">${esc(label)}<span aria-hidden="true">×</span></button>`).join("");
    const btn = $("filters-open");
    btn.textContent = items.length ? `Filters · ${items.length}` : "Filters";
    btn.classList.toggle("has-active", items.length > 0);
  }

  function renderControls() {
    for (const b of $("show-seg").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.show === state.show));
    for (const b of $("days-seg").querySelectorAll("button")) b.setAttribute("aria-pressed", String(Number(b.dataset.days) === state.days));
    for (const b of $("where-seg").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.where === state.where));
    if ($("search").value !== state.q) $("search").value = state.q;
  }

  async function ensureArchive() {
    if (archived || !topicMeta.archive?.length) return;
    if (!archiveLoading) {
      $("count").textContent = "Loading archive…";
      archiveLoading = Promise.all(topicMeta.archive.map((m) =>
        fetch(`data/${topicMeta.slug}/archive/${m.month}.json`).then((r) => r.json()).then((d) => d.articles)
      )).then((months) => {
        const current = new Set(articles.map((a) => a.id));
        archived = months.flat().filter((a) => !current.has(a.id)).map(prep);
      });
    }
    await archiveLoading;
  }

  async function render() {
    if (state.days === -1) await ensureArchive();
    const terms = parseQuery(state.q);
    const list = pool().filter((a) => passes(a, terms)).sort((x, y) => y._time - x._time);
    renderControls();
    renderFacets(terms);
    renderArticles(list, terms);
    renderActive();
    $("filters-apply").textContent = `Show ${list.length} article${list.length === 1 ? "" : "s"}`;
    writeUrl();
  }

  function renderHealth() {
    const srcs = topicMeta.sources;
    const ok = srcs.filter((s) => s.state === "ok").length;
    const active = srcs.filter((s) => s.state !== "disabled").length;
    const recentGap = (s) => s.last_gap && Date.now() - Date.parse(s.last_gap.detected) < 7 * 864e5;
    const gaps = srcs.filter(recentGap).length;
    $("health-summary").textContent = `Source status: ${ok}/${active} feeds OK` + (gaps ? ` · ${gaps} with possible gaps this week` : "");
    $("health").innerHTML = srcs.map((s) => {
      let detail;
      if (s.state === "ok") {
        detail = s.not_modified ? "no change at last check" : `${s.fetched} entries${s.pages > 1 ? ` (${s.pages} pages)` : ""}, ${s.new} new at last check`;
        if (s.last_ok) detail += ` · last OK ${relTime(Date.parse(s.last_ok))}`;
      } else detail = s.state === "error" ? `${s.error}${s.last_ok ? ` · last OK ${relTime(Date.parse(s.last_ok))}` : ""}` : s.note || "disabled";
      const gap = recentGap(s)
        ? `<span class="error">possible gap ${relTime(Date.parse(s.last_gap.detected))}: no overlap with the previous fetch</span>` : "";
      return `<li><span class="${s.state}">${s.state === "ok" ? "●" : s.state === "error" ? "▲" : "○"}</span>
        <a href="${esc(s.homepage)}" target="_blank" rel="noopener">${esc(s.name)}</a><span class="muted">${esc(detail)}</span>${gap}</li>`;
    }).join("");
    $("updated").textContent = new Date(topicMeta.updated_at).toLocaleString();
  }

  // ---------- Loading ----------
  async function loadTopic(slug) {
    topicMeta = manifest.topics.find((t) => t.slug === slug);
    if (!topicMeta) {
      $("empty").hidden = false;
      $("empty").textContent = `No data for "${slug}" yet.`;
      return;
    }
    document.title = topicMeta.title;
    $("topic-title").textContent = topicMeta.title;
    $("topic-desc").textContent = topicMeta.description;
    $("topic-select").value = topicMeta.slug;
    // "filter" topics only store matches, so the toggle is meaningless there.
    $("show-seg").closest(".filter-block").hidden = topicMeta.mode === "filter";
    if (topicMeta.mode === "filter") state.show = "all";
    $("where-seg").closest(".filter-block").hidden = !topicMeta.text_check;
    state.hide = urlHide ?? defaultHide();

    const res = await fetch(`data/${topicMeta.slug}/articles.json`, { cache: "no-cache" });
    const doc = await res.json();
    articles = doc.articles.map(prep);
    archived = null;
    archiveLoading = null;

    const days = topicMeta.retention_days || 30;
    $("days-current").textContent = `${days} days`;
    const archCount = (topicMeta.archive || []).reduce((n, m) => n + m.count, 0);
    $("archive-note").hidden = !archCount;
    $("archive-note").textContent = `${archCount} older articles in the archive (${topicMeta.archive?.length || 0} months). Choose "All time" to include them.`;
    renderHealth();
    render();
  }

  async function init() {
    readUrl();
    try {
      const res = await fetch("data/manifest.json", { cache: "no-cache" });
      manifest = await res.json();
    } catch (e) {
      $("empty").hidden = false;
      $("empty").textContent = "No data yet. Run `uv run newscanner run` to generate it.";
      return;
    }
    if (manifest.topics.length > 1) {
      $("topic-picker").hidden = false;
      $("topic-select").innerHTML = manifest.topics.map((t) => `<option value="${esc(t.slug)}">${esc(t.title)}</option>`).join("");
    }
    const phone = window.matchMedia("(max-width: 760px)");
    $("sources-group").open = !phone.matches;  // phone: sources start collapsed inside the panel
    if (phone.matches) $("search").placeholder = "Search articles";
    const topbar = document.querySelector(".topbar");
    new ResizeObserver(() => document.documentElement.style.setProperty("--topbar-h", `${topbar.offsetHeight}px`)).observe(topbar);
    await loadTopic(state.topic);
  }

  // ---------- Events ----------
  let debounce;
  $("search").addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.q = e.target.value.trim(); state.limit = PAGE; render(); }, 120);
  });
  $("show-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.show = b.dataset.show; state.limit = PAGE; render();
  });
  $("where-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.where = b.dataset.where; state.limit = PAGE; render();
  });
  $("days-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.days = Number(b.dataset.days); state.limit = PAGE; render();
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.tag !== undefined) toggle(state.tags, t.dataset.tag);
    else if (t.dataset.source !== undefined) toggle(state.sources, t.dataset.source);
    else return;
    state.limit = PAGE; render();
  });
  document.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-chip]");
    if (chip) { toggle(state.tags, chip.dataset.chip); state.limit = PAGE; render(); return; }
    const remove = e.target.closest("[data-remove]");
    if (remove) { activeRemovers[Number(remove.dataset.remove)]?.(); state.limit = PAGE; render(); return; }
    const moreMentions = e.target.closest("[data-more-mentions]");
    if (moreMentions) {
      const card = moreMentions.closest(".card");
      expanded.add(card.dataset.id);
      card.classList.add("expanded");
      return;
    }
    const hide = e.target.closest("[data-hide]");
    if (hide) {
      const t = hide.dataset.hide;
      toggle(state.hide, t);
      state.tags.delete(t);  // a tag can't be both required and hidden
      state.limit = PAGE; render(); return;
    }
    const clear = e.target.closest("[data-clear]");
    if (clear) {
      e.preventDefault();  // the Sources "clear" sits inside a <summary>; don't toggle it
      state[clear.dataset.clear].clear();
      if (clear.dataset.clear === "tags") state.hide = defaultHide();
      state.limit = PAGE; render();
    }
  });
  $("more").addEventListener("click", () => { state.limit += PAGE; render(); });

  // Phone filter panel
  function setPanel(open) {
    document.body.classList.toggle("filters-open", open);
    $("filters-open").setAttribute("aria-expanded", String(open));
    if (open) $("filters-panel").scrollTop = 0;
    else window.scrollTo({ top: Math.min(window.scrollY, document.querySelector(".results").offsetTop - 80) });
  }
  $("filters-open").addEventListener("click", () => setPanel(true));
  $("filters-close").addEventListener("click", () => setPanel(false));
  $("filters-apply").addEventListener("click", () => setPanel(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("filters-open")) setPanel(false);
  });
  $("reset").addEventListener("click", () => {
    Object.assign(state, { q: "", show: topicMeta.mode === "filter" ? "all" : "match", days: 0, where: "", limit: PAGE });
    state.tags.clear(); state.sources.clear(); state.hide = defaultHide(); render();
  });
  $("topic-select").addEventListener("change", (e) => {
    location.href = new URL(`${encodeURIComponent(e.target.value)}/`, document.baseURI).href;
  });

  function toggle(set, v) { set.has(v) ? set.delete(v) : set.add(v); }

  init();
})();
