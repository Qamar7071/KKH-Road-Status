/* KKH Road Status: dashboard.
   Same code runs in two modes:
   - API mode (Flask app): data from /api/*
   - Static mode (single-file demo): data embedded as window.__KKH_BUNDLE__
   The what-if panel runs the trained gradient-boosting model in the browser (exported trees). */
(() => {
  "use strict";
  const B = window.__KKH_BUNDLE__ || null;
  const $ = (s) => document.querySelector(s);
  const NS = { svg: "http://www.w3.org/2000/svg", html: "http://www.w3.org/1999/xhtml" };
  function make(ns, tag, attrs = {}, kids = []) {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "text") n.textContent = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const c of [kids].flat(3)) if (c != null && c !== false) n.append(c);
    return n;
  }
  const el = (tag, attrs, ...kids) => make(NS.html, tag, attrs, kids);
  const S = (tag, attrs, ...kids) => make(NS.svg, tag, attrs, kids);

  // ------------------------------------------------------------------ icons (24px stroke)
  const ICON = {
    rain: '<path d="M7 15a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17 6.5a4 4 0 0 1 .5 7.97"/><path d="M8 18l-1 2M12 17l-1.5 3M16 18l-1 2"/>',
    snow: '<path d="M12 2v20M4.9 6.5l14.2 11M19.1 6.5 4.9 17.5"/><path d="M9.5 3.5 12 6l2.5-2.5M9.5 20.5 12 18l2.5 2.5"/>',
    temp: '<path d="M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0z"/><path d="M12 9v7"/>',
    wind: '<path d="M3 8h11a3 3 0 1 0-3-3M3 12h16a3 3 0 1 1-3 3M3 16h7"/>',
    drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
    news: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h10M7 12h10M7 16h6"/>',
    slide: '<path d="M3 20h18L14 7l-3 5-2-3z"/><circle cx="17" cy="16" r="1"/><circle cx="19" cy="13" r=".8"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    flake: '<path d="M12 2v20M2 12h20M5 5l14 14M19 5 5 19"/>',
    reset: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    swap: '<path d="M7 4 3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/>',
    route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a3.5 3.5 0 0 0 0-7H9a3.5 3.5 0 0 1 0-7h7"/>',
  };
  const icon = (name) => { const s = S("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }); s.innerHTML = ICON[name]; return s; };

  // ------------------------------------------------------------------ formatting
  const pct = (p) => (p >= 0.995 ? "100%" : p >= 0.1 ? `${Math.round(p * 100)}%` : `${(Math.max(p, 0.001) * 100).toFixed(1)}%`);
  const fmtDate = (iso, o = { weekday: "long", day: "numeric", month: "long", year: "numeric" }) =>
    new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { ...o, timeZone: "UTC" });
  const shortDate = (iso) => fmtDate(iso, { day: "numeric", month: "short", year: "numeric" });
  const bandOf = (p) => (p >= 0.5 ? "blocked" : p >= 0.2 ? "caution" : "open");
  const COLOR = { open: "var(--ok)", caution: "var(--warn)", blocked: "var(--bad)", closed: "var(--winter)" };
  const BAND_TEXT = { open: "Open", caution: "Delays possible", blocked: "Likely blocked", closed: "Closed for winter" };
  const BAND_UR = { open: "کھلی", caution: "تاخیر ممکن", blocked: "بند ہونے کا خدشہ", closed: "سردیوں میں بند" };
  const GROUP = {
    "Tomorrow's rain/snow forecast": ["Tomorrow's rain and snow", "rain"],
    "Rain in the last days": ["Rain in recent days", "drop"],
    "Snow & freeze-thaw": ["Snow and freeze-thaw", "snow"],
    "Heat / glacier melt": ["Heat and glacier melt", "sun"],
    "News & traveller reports": ["News and road reports", "news"],
    "Landslide history": ["Landslide history", "slide"],
    Season: ["Time of year", "cal"],
  };
  const BOUNDARIES = ["Havelian", "Besham", "Dasu", "Chilas", "Jaglot", "Gilgit", "Aliabad (Hunza)", "Sost", "Khunjerab Pass"];
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ data source
  const api = {
    async get(path) {
      const r = await fetch(path, { headers: { Accept: "application/json" } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
      return j;
    },
    segments: () => (B ? Promise.resolve(B.segments) : api.get("/api/segments")),
    geo: () => (B ? Promise.resolve(B.geo) : api.get("/static/geo.json")),
    model: () => (B ? Promise.resolve(B.model) : api.get("/api/model")),
    exportJson: () => (B ? Promise.resolve(B.export) : api.get("/api/model_export.json")),
    scenarios: () => (B ? Promise.resolve(B.scenarios) : api.get("/api/scenarios")),
    trend: (end) => (B ? Promise.resolve(B.trends?.[end || B.latest] || []) : api.get(`/api/trend?days=14${end ? `&end=${end}` : ""}`)),
    health: () => (B ? Promise.resolve({ replay_range: B.range }) : api.get("/api/health")),
    predict(date) {
      if (!B) return api.get(date ? `/api/predict?date=${encodeURIComponent(date)}` : "/api/predict");
      const res = B.replays[date || B.latest];
      if (!res) return Promise.reject(new Error("This demo stores only the evenings listed in the picker."));
      return Promise.resolve(date ? res : { ...res, mode: "snapshot" });
    },
    reports: () => (B ? Promise.resolve([]) : api.get("/api/reports")),
    async postReport(body) {
      const r = await fetch("/api/reports", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || `Could not send report (${r.status})`);
      return j;
    },
  };

  // ------------------------------------------------------------------ model in the browser
  let MODEL = null;
  const rawScore = (x) => {
    let s = MODEL.baseline;
    for (const t of MODEL.trees) {
      let i = 0;
      while (!t.leaf[i]) i = x[t.f[i]] <= t.t[i] ? t.l[i] : t.r[i];
      s += t.v[i];
    }
    return s;
  };
  const probability = (feat) => { const [a, b] = MODEL.platt; return 1 / (1 + Math.exp(-(a * rawScore(MODEL.features.map((f) => feat[f])) + b))); };

  // ------------------------------------------------------------------ state
  const state = { segInfo: null, profile: [], geo: null, pred: null, sel: null, whatif: null, firstMap: true };

  // ------------------------------------------------------------------ ring
  function ring(p, { size = 64, stroke = 7, band = bandOf(p), label, sub, cls = "" } = {}) {
    const r = (size - stroke) / 2, c = 2 * Math.PI * r;
    const bar = S("circle", { class: "bar", cx: size / 2, cy: size / 2, r, "stroke-width": stroke, "stroke-dasharray": c.toFixed(2), "stroke-dashoffset": c.toFixed(2) });
    const wrap = el("div", { class: `ring b-${band} ${cls}`, style: `width:${size}px;height:${size}px` },
      S("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, "aria-hidden": "true" },
        S("circle", { class: "track", cx: size / 2, cy: size / 2, r, "stroke-width": stroke }), bar),
      el("div", { class: "val", style: `font-size:${Math.round(size * 0.25)}px` }, el("span", {}, label ?? pct(p), sub ? el("small", { text: sub }) : null)));
    const target = (c * (1 - Math.min(1, Math.max(0.015, p)))).toFixed(2);
    if (reduceMotion) bar.setAttribute("stroke-dashoffset", target);
    else requestAnimationFrame(() => requestAnimationFrame(() => bar.setAttribute("stroke-dashoffset", target)));
    return wrap;
  }

  // ------------------------------------------------------------------ header
  function renderStatus(p) {
    const st = $("#status");
    st.dataset.mode = p.mode;
    $("#status-text").textContent = { live: "Live forecast", replay: `Replay, ${shortDate(p.as_of)}`, fallback: "Offline data", snapshot: "Demo snapshot" }[p.mode] || p.mode;
    $("#replay-live").hidden = p.mode !== "replay";
  }
  const names = (list) => { const n = list.map((s) => s.name); return n.length <= 2 ? n.join(" and ") : `${n.slice(0, -1).join(", ")} and ${n.at(-1)}`; };

  function renderHero(p) {
    const segs = p.segments;
    const blocked = segs.filter((s) => s.band.key === "blocked"), caution = segs.filter((s) => s.band.key === "caution");
    const winter = segs.filter((s) => s.seasonal_closure);
    const target = fmtDate(p.target_date);
    $("#when").textContent = {
      live: `Forecast for tomorrow, ${target}`,
      replay: `Evening of ${shortDate(p.as_of)}, forecast for ${target}`,
      fallback: `Latest stored data, ${target}`,
      snapshot: `Forecast for ${target}`,
    }[p.mode] || target;
    $("#headline").textContent = blocked.length ? `Expect blockages on ${names(blocked)}.`
      : caution.length ? `Mostly open. Watch ${names(caution)}.`
      : winter.length ? "Open up to Sost. Khunjerab is shut for winter." : "The KKH looks open, end to end.";

    const worst = segs.find((s) => s.id === p.summary.worst_segment);
    const up = worst && worst.drivers.find((d) => d.impact > 0.15);
    let lede = worst ? `Highest risk: ${worst.name}, ${pct(worst.probability)}` + (up ? `, driven by ${GROUP[up.group][0].toLowerCase()}.` : ".") : "";
    if (p.mode === "replay") {
      const actual = segs.filter((s) => s.actual && s.actual.closed && !s.seasonal_closure);
      const caught = actual.filter((s) => s.probability >= 0.2);
      lede += actual.length ? ` What happened: ${names(actual)} ${actual.length > 1 ? "were" : "was"} blocked. Flagged in advance: ${caught.length} of ${actual.length}.` : " What happened: every segment stayed open.";
    }
    $("#lede").textContent = lede;

    renderHealth(p);
    $("#notice").hidden = true;
    renderTicker(p);
  }

  // ------------------------------------------------------------------ route health
  function countUp(node, to, fmt) {
    const from = Number(node.dataset.v || 0);
    node.dataset.v = to;
    if (reduceMotion) { node.textContent = fmt(to); return; }
    const t0 = performance.now(), dur = 900;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - (1 - k) ** 3;
      node.textContent = fmt(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  async function renderHealth(p) {
    const segs = p.segments, open = segs.filter((s) => !s.seasonal_closure);
    const winter = open.length < segs.length;
    const clear = p.summary.p_full_route_open, band = clear >= 0.8 ? "open" : clear >= 0.5 ? "caution" : "blocked";
    const health = document.querySelector(".health");
    health.style.setProperty("--c", COLOR[band]);
    const real = p.data_mode === "real";
    $("#h-label").replaceChildren(winter ? "Clear run, Havelian to Sost" : "Clear run, Havelian to Khunjerab",
      el("span", { class: `data-badge ${real ? "real" : "sim"}`, title: real ? "Trained on Open-Meteo weather, NASA landslides and news archives" : "Weather and closures are simulated for this demo. Not today's real road conditions.",
        text: real ? "Real data" : "Simulated data" }));
    countUp($("#h-num"), clear * 100, (v) => `${v >= 99.5 ? Math.round(v) : v >= 10 ? Math.round(v) : v.toFixed(1)}%`);
    // strip: every segment scaled by length, shaded by its risk
    const info = state.segInfo;
    $("#h-km").textContent = `${info.reduce((a, s) => a + s.length_km, 0)} km`;
    $("#h-strip").replaceChildren(...segs.map((s, i) => {
      const key = s.band.key, p01 = s.seasonal_closure ? 1 : Math.min(1, s.probability / 0.6);
      const label = key === "closed" ? "Shut" : pct(s.probability);
      return el("button", { type: "button", class: p01 > 0.45 ? "hi" : null, style: `--w:${info[i].length_km};--c:${COLOR[key]};--p:${p01.toFixed(3)}`, "aria-pressed": String(state.sel === s.id),
        title: `${s.name}: ${key === "closed" ? "closed for winter" : `${label} chance of a blockage`}`, "aria-label": `${s.name}, ${label}`, onclick: () => select(s.id) },
      info[i].length_km >= 75 ? el("span", { text: label }) : null);
    }));
    // counts + riskiest
    const cnt = (k) => segs.filter((s) => s.band.key === k).length;
    $("#h-counts").replaceChildren(...[["open", "open"], ["caution", "delays"], ["blocked", "blocked"], ["closed", "winter"]]
      .filter(([k]) => k !== "closed" || cnt(k))
      .map(([k, t]) => el("span", { class: `h-pill${cnt(k) ? "" : " zero"}`, style: `--c:${COLOR[k]}` }, el("i"), `${cnt(k)} ${t}`)));
    const worst = segs.find((s) => s.id === p.summary.worst_segment);
    const w = $("#h-worst");
    w.style.setProperty("--c", COLOR[worst.band.key]);
    w.replaceChildren("Riskiest: ", el("b", { text: `${worst.name} · ${pct(worst.probability)}` }), " →");
    w.onclick = () => { select(worst.id); document.querySelector("#detail").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }); };
    // trend + delta vs the previous evening
    const end = p.mode === "live" || p.mode === "fallback" ? null : p.as_of;
    const tr = await api.trend(end).catch(() => []);
    if (state.pred !== p) return;             // a newer forecast loaded meanwhile
    renderSpark(tr, p);
    const dl = $("#h-delta");
    const prevDay = new Date(new Date(p.as_of + "T00:00:00Z").getTime() - 864e5).toISOString().slice(0, 10);
    const prev = tr.find((r) => r.as_of === prevDay);
    if (prev) {
      const d = (clear - prev.p_clear) * 100;
      dl.className = `h-delta ${Math.abs(d) < 0.5 ? "" : d > 0 ? "up" : "down"}`;
      dl.textContent = Math.abs(d) < 0.5 ? "same as yesterday" : `${d > 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(Math.abs(d) < 10 ? 1 : 0)} pts vs yesterday`;
      dl.hidden = false;
    } else dl.hidden = true;
  }

  function renderSpark(tr, p) {
    const svg = $("#h-spark");
    if (!tr.length) { svg.replaceChildren(); $("#h-spark-cap").textContent = ""; return; }
    const W = 168, H = 56, P = 4;
    const vals = tr.map((r) => r.p_clear * 100);
    if (tr.at(-1).as_of !== p.as_of && p.mode !== "replay") vals.push(p.summary.p_full_route_open * 100);
    const lo = Math.max(0, Math.min(...vals) - 6), hi = 100;
    const x = (i) => P + (i / Math.max(1, vals.length - 1)) * (W - 2 * P), y = (v) => H - P - ((v - lo) / (hi - lo || 1)) * (H - 2 * P);
    const pts = vals.map((v, i) => [x(i), y(v)]);
    const line = pts.map(([a, b], i) => `${i ? "L" : "M"}${a.toFixed(1)} ${b.toFixed(1)}`).join("");
    svg.replaceChildren(
      S("defs", {}, S("linearGradient", { id: "hsg", x1: 0, y1: 0, x2: 0, y2: 1 },
        S("stop", { offset: 0, "stop-color": "var(--c, var(--ice))", "stop-opacity": 0.35 }), S("stop", { offset: 1, "stop-color": "var(--c, var(--ice))", "stop-opacity": 0 }))),
      S("line", { class: "base", x1: P, x2: W - P, y1: y(hi), y2: y(hi) }),
      S("path", { class: "area", d: `${line} L${pts.at(-1)[0]} ${H} L${pts[0][0]} ${H} Z` }),
      S("path", { class: "line", d: line }),
      ...tr.map((r, i) => (r.blocked_actual ? S("circle", { class: "mark", cx: x(i), cy: H - 1.5, r: 1.8 }, S("title", { text: `${shortDate(r.target_date)}: ${r.blocked_actual} blocked` })) : null)).filter(Boolean),
      ...pts.map(([a, b], i) => S("circle", { cx: a, cy: b, r: 7, fill: "transparent" }, S("title", { text: `${i < tr.length ? shortDate(tr[i].target_date) : "Tomorrow"}: ${Math.round(vals[i])}% clear` }))),
      S("circle", { class: "dot", cx: pts.at(-1)[0], cy: pts.at(-1)[1], r: 3.5 }));
    $("#h-spark-cap").textContent = `Last ${tr.length} evenings · red ticks = real blockages`;
  }

  function renderTicker(p) {
    const notes = [];
    if (p.notice) notes.push(p.notice);
    if (p.mode === "snapshot") notes.push("Demo built from stored data. The full app pulls a live Open-Meteo forecast, news and road reports every 30 minutes.");
    if (p.mode === "replay") notes.push(`Time machine: showing the forecast made on the evening of ${shortDate(p.as_of)}.`);
    const items = [
      ...notes.map((t) => el("span", { class: "tick-item note" }, el("i"), t)),
      ...p.segments.map((s) => el("span", { class: "tick-item", style: `--c:${COLOR[s.band.key]}` }, el("i"), el("b", { text: s.name }),
        s.seasonal_closure ? "closed for winter" : `${pct(s.probability)} · ${BAND_TEXT[s.band.key].toLowerCase()}`,
        s.seasonal_closure ? null : `· ${s.tomorrow.precip_mm ?? 0} mm rain${s.tomorrow.snow_cm >= 0.5 ? `, ${s.tomorrow.snow_cm} cm snow` : ""}`)),
      el("span", { class: "tick-item" }, el("i"), "NHA helpline ", el("b", { text: "130" }), " · check before you travel"),
    ];
    const track = $("#ticker-track");
    track.replaceChildren(...items, ...items.map((n) => { const c = n.cloneNode(true); c.setAttribute("aria-hidden", "true"); return c; }));
    requestAnimationFrame(() => track.style.setProperty("--dur", `${Math.max(30, track.scrollWidth / 2 / 55)}s`));
  }

  // ------------------------------------------------------------------ hero scene
  let scene = null;
  function updateScene() {
    if (!scene || !state.pred) return;
    const s = state.pred.segments.find((q) => q.id === state.sel);
    const info = state.segInfo.find((q) => q.id === s.id);
    const idx = state.segInfo.indexOf(info);
    const t = s.tomorrow;
    scene.set({ rain: s.seasonal_closure ? 0 : (t.snow_cm > 0.5 ? 0 : t.precip_mm || 0),
      snow: s.seasonal_closure ? 4 : (t.snow_cm || 0), risk: s.seasonal_closure ? 0.3 : s.probability });
    const w = s.seasonal_closure ? "winter closure" : [t.precip_mm >= 0.5 ? `${t.precip_mm} mm rain` : "dry", t.snow_cm >= 0.5 ? `${t.snow_cm} cm snow` : null, `${Math.round(t.tmax_c)}°`].filter(Boolean).join(", ");
    $("#scene-tag").textContent = `Scene: ${s.name}, ${state.pred.mode === "replay" ? "that day" : "tomorrow"} · ${w}`;
  }

  // ------------------------------------------------------------------ map
  const map = { view: null, full: null, unit: 1, proj: null, built: false };
  function projector(bbox, W = 1000) {
    const [w, s, e, n] = bbox, rad = Math.PI / 180;
    const my = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * rad) / 2));
    const R = W / ((e - w) * rad);
    const H = (my(n) - my(s)) * R;
    return { W, H, p: (lon, lat) => [(lon - w) * rad * R, (my(n) - my(lat)) * R] };
  }
  const pathD = (coords, P) => coords.map(([lon, lat], i) => { const [x, y] = P.p(lon, lat); return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`; }).join("");
  const PIN_OFF = { "HZR-BSM": [42, 0], "BSM-DSU": [-44, 6], "DSU-CHL": [0, -36], "CHL-JGL": [0, 36], "JGL-GLT": [46, 6], "GLT-HNZ": [-46, 0], "HNZ-SST": [48, 12], "SST-KJB": [52, 22] };

  function buildMap() {
    const g = state.geo, P = projector(g.bbox);
    map.proj = P;
    map.full = { x: 0, y: 0, w: P.W, h: P.H };
    map.view = { ...map.full };
    const svg = $("#map");
    svg.setAttribute("viewBox", `0 0 ${P.W} ${P.H}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.replaceChildren(S("defs", {}, S("filter", { id: "glow", x: "-50%", y: "-50%", width: "200%", height: "200%" }, S("feGaussianBlur", { stdDeviation: 6 }))));
    const lay = (id) => svg.appendChild(S("g", { id }));
    const base = lay("m-base"), water = lay("m-water");
    lay("m-route"); lay("m-labels"); lay("m-pins");
    // graticule
    for (let lon = Math.ceil(g.bbox[0]); lon <= g.bbox[2]; lon++) {
      const [x] = P.p(lon, g.bbox[1]);
      base.append(S("line", { class: "grat", x1: x, x2: x, y1: 0, y2: P.H }));
    }
    for (let lat = Math.ceil(g.bbox[1]); lat <= g.bbox[3]; lat++) {
      const [, y] = P.p(g.bbox[0], lat);
      base.append(S("line", { class: "grat", x1: 0, x2: P.W, y1: y, y2: y }));
    }
    for (const b of g.borders) base.append(S("path", { class: "border", d: pathD(b, P) }));
    for (const r of g.rivers) water.append(S("path", { class: "river", d: pathD(r.coords, P), "stroke-width": 2.4 }));
    for (const l of g.lakes) water.append(S("path", { class: "lake", d: pathD(l.coords, P), "stroke-width": 6 }));
    map.built = true;
    initMapControls();
  }

  function labelGroup(lon, lat, child, cls = "") {
    const [x, y] = map.proj.p(lon, lat);
    const gEl = S("g", { class: `lbl ${cls}`, "data-x": x, "data-y": y }, child);
    return gEl;
  }

  function mapBadge(s, key, c, sel, ox, oy) {
    const r = 15, C = 2 * Math.PI * r, p = s.seasonal_closure ? 1 : s.probability;
    const txt = key === "closed" ? "Shut" : pct(s.probability);
    return S("g", { class: `badge b-${key}${sel ? " sel" : ""}`, style: `--c:${COLOR[key]}`, tabindex: 0, role: "button", "aria-label": `${s.name}, ${key === "closed" ? "closed for winter" : `${txt} chance of a blockage`}`,
      onclick: () => select(s.id), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(s.id); } } },
    S("line", { class: "leader", x1: 0, y1: 0, x2: ox, y2: oy }),
    S("circle", { class: "anchor", r: 3.2 }),
    S("g", { transform: `translate(${ox} ${oy})` },
      S("circle", { class: "b-bg", r: 20 }),
      S("circle", { class: "b-track", r }),
      S("circle", { class: "b-arc", r, transform: "rotate(-90)", "stroke-dasharray": `${(C * Math.max(0.035, p)).toFixed(1)} ${C.toFixed(1)}` }),
      S("text", { class: "b-txt", y: 3.6, "text-anchor": "middle", text: txt }),
      S("title", { text: `${s.name}: ${key === "closed" ? "closed for winter" : txt}` })));
  }

  function renderMap(p) {
    if (!map.built) buildMap();
    const g = state.geo, P = map.proj;
    const route = $("#m-route"), labels = $("#m-labels"), pins = $("#m-pins");
    route.replaceChildren(); labels.replaceChildren(); pins.replaceChildren();
    const animate = state.firstMap && !reduceMotion;
    // Real-road layering: risk glow, shoulder, asphalt, risk tint, moving lane line
    const L = { halo: S("g"), cs: S("g"), as: S("g"), tint: S("g"), lane: S("g"), hit: S("g") };
    route.append(...Object.values(L));
    p.segments.forEach((s, i) => {
      const coords = g.routes[s.id], d = pathD(coords, P), key = s.band.key, c = COLOR[key], sel = state.sel === s.id;
      L.halo.append(S("path", { class: `halo h-${key}${sel ? " sel" : ""}`, d, style: `--c:${c}`, "data-w": key === "open" ? 14 : sel ? 30 : 24 }));
      const cs = S("path", { class: `case${animate ? " draw" : ""}`, d, "data-w": sel ? 13 : 10.5 });
      const as = S("path", { class: `asphalt${animate ? " draw" : ""}`, d, "data-w": sel ? 10 : 8 });
      L.cs.append(cs); L.as.append(as);
      if (key !== "open") L.tint.append(S("path", { class: "tint", d, style: `--c:${c}`, "data-w": sel ? 10 : 8 }));
      const lane = S("path", { class: `lane${key === "blocked" ? " stopped" : ""}`, d, "data-w": 1.4, "data-dash": "4.5 5.5" });
      if (animate) lane.style.opacity = "0";
      L.lane.append(lane);
      if (animate) {
        const len = as.getTotalLength();
        for (const n of [cs, as]) { n.style.setProperty("--len", len.toFixed(0)); n.style.animationDelay = `${i * 0.16}s`; }
        setTimeout(() => { lane.style.transition = "opacity .6s"; lane.style.opacity = "1"; }, 1300 + i * 160);
      }
      L.hit.append(S("path", { class: "hit", d, onclick: () => select(s.id) }, S("title", { text: `${s.name}: ${s.seasonal_closure ? "closed for winter" : pct(s.probability)}` })));
      const mid = coords[Math.floor(coords.length / 2)];
      const [ox, oy] = PIN_OFF[s.id] || [0, -28];
      pins.append(labelGroup(mid[0], mid[1], mapBadge(s, key, c, sel, ox, oy)));
    });
    for (const t of g.towns) {
      const right = t.lon < 75.2 || t.name === "Skardu" || t.name === "Khunjerab Pass";
      labels.append(labelGroup(t.lon, t.lat, S("g", { class: `town${t.rank > 2 ? " minor" : ""}` },
        S("circle", { r: t.rank === 1 ? 4.5 : 3.5 }),
        S("text", { x: right ? 8 : -8, y: 4, "text-anchor": right ? "start" : "end", text: t.name }))));
    }
    for (const pk of g.peaks.filter((q) => q.elev > 7400)) {
      const left = pk.name === "Batura Sar";
      labels.append(labelGroup(pk.lon, pk.lat, S("g", { class: "peak" },
        S("path", { d: "M0 -7 L6 4 L-6 4 Z" }),
        S("text", left ? { x: -9, y: 4, "text-anchor": "end", text: `${pk.name} ${pk.elev.toLocaleString("en")} m` } : { x: 9, y: 4, text: `${pk.name} ${pk.elev.toLocaleString("en")} m` }))));
    }
    for (const lb of g.labels) labels.append(labelGroup(lb.lon, lb.lat, S("text", { class: "country", "text-anchor": "middle", text: lb.name.toUpperCase() })));
    const indus = g.rivers[0].coords.find((q) => q[0] > 75.05) || g.rivers[0].coords[0];
    labels.append(labelGroup(indus[0], indus[1], S("text", { class: "river-l", y: -8, text: "Indus" })));
    state.firstMap = false;
    applyView();
    renderBoard(p);
  }

  function renderBoard(p) {
    $("#board").replaceChildren(...p.segments.map((s) => {
      const key = s.band.key, w = s.seasonal_closure ? 100 : Math.max(2, s.probability * 100);
      return el("button", { type: "button", class: `board-row b-${key}`, "aria-pressed": String(state.sel === s.id), onclick: () => select(s.id) },
        el("i"), el("span", { class: "nm", text: s.name }),
        el("span", { class: "pc", text: key === "closed" ? "Shut" : pct(s.probability) }),
        el("span", { class: "st", text: `${BAND_TEXT[key]} · ${s.tomorrow.precip_mm ?? 0} mm${s.tomorrow.snow_cm >= 0.5 ? ` · ${s.tomorrow.snow_cm} cm snow` : ""}` }),
        el("span", { class: "bar" }, el("b", { style: `width:${w}%` })));
    }));
    $("#board-foot").textContent = `Chance of a blockage on ${fmtDate(p.target_date, { weekday: "short", day: "numeric", month: "short" })}`;
  }

  function applyView() {
    const svg = $("#map"), v = map.view;
    svg.setAttribute("viewBox", `${v.x} ${v.y} ${v.w} ${v.h}`);
    const r = svg.getBoundingClientRect();
    if (!r.width) return;
    const scale = Math.min(r.width / v.w, r.height / v.h);   // px per user unit (meet)
    const k = 1 / scale;
    svg.querySelectorAll("[data-w]").forEach((n) => n.setAttribute("stroke-width", (n.dataset.w * k).toFixed(2)));
    svg.querySelectorAll("[data-dash]").forEach((n) => {
      const dash = n.dataset.dash.split(" ").map(Number);
      n.setAttribute("stroke-dasharray", dash.map((v) => (v * k).toFixed(2)).join(" "));
      n.style.setProperty("--flow", (-(dash[0] + dash[1]) * k).toFixed(2));
    });
    svg.querySelectorAll(".lbl").forEach((n) => n.setAttribute("transform", `translate(${n.dataset.x} ${n.dataset.y}) scale(${k.toFixed(4)})`));
    svg.classList.toggle("zoomed", v.w < map.full.w * 0.75);
  }

  function zoom(f, cx, cy) {
    const v = map.view, F = map.full;
    const w = Math.min(F.w, Math.max(F.w / 6, v.w / f)), h = w * (F.h / F.w);
    cx = cx ?? v.x + v.w / 2; cy = cy ?? v.y + v.h / 2;
    const rx = (cx - v.x) / v.w, ry = (cy - v.y) / v.h;
    map.view = clampView({ x: cx - rx * w, y: cy - ry * h, w, h });
    applyView();
  }
  function clampView(v) {
    const F = map.full;
    return { ...v, x: Math.min(F.w - v.w, Math.max(0, v.x)), y: Math.min(F.h - v.h, Math.max(0, v.y)) };
  }
  function initMapControls() {
    const svg = $("#map");
    $("#zoom-in").onclick = () => zoom(1.6);
    $("#zoom-out").onclick = () => zoom(1 / 1.6);
    $("#zoom-reset").replaceChildren(icon("reset"));
    $("#zoom-reset").onclick = () => { map.view = { ...map.full }; applyView(); };
    let drag = null;
    svg.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".pin, .hit")) return;
      drag = { x: e.clientX, y: e.clientY, v: { ...map.view } };
      svg.setPointerCapture(e.pointerId); svg.classList.add("dragging");
    });
    svg.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const r = svg.getBoundingClientRect(), s = Math.min(r.width / drag.v.w, r.height / drag.v.h);
      map.view = clampView({ ...drag.v, x: drag.v.x - (e.clientX - drag.x) / s, y: drag.v.y - (e.clientY - drag.y) / s });
      applyView();
    });
    const end = () => { drag = null; svg.classList.remove("dragging"); };
    svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", end);
    svg.addEventListener("dblclick", (e) => {
      const r = svg.getBoundingClientRect(), v = map.view, s = Math.min(r.width / v.w, r.height / v.h);
      const ox = (r.width - v.w * s) / 2, oy = (r.height - v.h * s) / 2;
      zoom(1.8, v.x + (e.clientX - r.left - ox) / s, v.y + (e.clientY - r.top - oy) / s);
    });
    new ResizeObserver(applyView).observe(svg);
    $("#map-credit").textContent = "Natural Earth · route approximate";
    $("#legend").replaceChildren(...[["open", "Clear, under 20%"], ["caution", "Delays possible, 20–50%"], ["blocked", "Likely blocked, 50%+"], ["closed", "Winter closure"]]
      .map(([k, t]) => el("span", { style: `--c:${COLOR[k]}` }, el("i"), t)));
  }

  // ------------------------------------------------------------------ segment rail
  function outcome(s) {
    if (!s.actual || s.seasonal_closure) return null;
    const c = s.actual.closed, p = s.probability;
    if (c && p >= 0.2) return el("span", { class: "outcome hit", text: "✓ Blocked, flagged" });
    if (c) return el("span", { class: "outcome miss", text: "✗ Blocked, missed" });
    if (p >= 0.5) return el("span", { class: "outcome miss", text: "✗ False alarm" });
    return el("span", { class: "outcome", text: "Stayed open" });
  }
  function wx(s) {
    if (s.seasonal_closure) return el("div", { class: "wx" }, el("span", {}, icon("cal"), "Dec–Mar closure"));
    const t = s.tomorrow;
    return el("div", { class: "wx" },
      el("span", { title: "Rain" }, icon("rain"), `${t.precip_mm ?? 0} mm`),
      t.snow_cm >= 0.5 ? el("span", { title: "Snow" }, icon("snow"), `${t.snow_cm} cm`) : null,
      el("span", { title: "High" }, icon("temp"), `${Math.round(t.tmax_c)}°`));
  }
  function renderRail(p) {
    $("#seg-rail").replaceChildren(...p.segments.map((s, i) => el("button", {
      type: "button", class: `seg-card b-${s.band.key}`, role: "option", "aria-selected": String(state.sel === s.id), onclick: () => select(s.id),
    },
    el("div", { class: "seg-top" }, el("span", { class: "seg-idx", text: `Stretch ${i + 1} · ${s.length_km} km` }), el("span", { class: "chip", text: BAND_TEXT[s.band.key] })),
    el("div", { class: "seg-mid" }, ring(s.probability, { size: 62, stroke: 7, band: s.band.key, label: s.seasonal_closure ? "—" : pct(s.probability) }),
      el("div", {}, el("h3", { text: s.name }), el("p", { class: "ur", lang: "ur", dir: "rtl", text: s.name_ur }))),
    el("div", { class: "seg-foot" }, wx(s), outcome(s)))));
  }

  // ------------------------------------------------------------------ detail
  const tile = (ic, k, v, unit) => el("div", { class: "tile" }, el("span", { class: "k" }, icon(ic), k), el("span", { class: "v" }, v, unit ? el("small", { text: unit }) : null));
  const n1 = (v) => (v === null || v === undefined ? "–" : String(v));

  function ridge(s) {
    const info = state.segInfo, idx = info.findIndex((q) => q.id === s.id);
    const k0 = info.slice(0, idx).reduce((a, q) => a + q.length_km, 0), k1 = k0 + info[idx].length_km;
    const pr = state.profile;
    const at = (km) => { for (let i = 1; i < pr.length; i++) if (pr[i].km >= km) { const a = pr[i - 1], b = pr[i]; return a.elev + ((km - a.km) / (b.km - a.km || 1)) * (b.elev - a.elev); } return pr.at(-1).elev; };
    const pts = Array.from({ length: 40 }, (_, i) => { const km = k0 + (i / 39) * (k1 - k0); return [i / 39 * 600, at(km)]; });
    const y = (e) => 110 - (e / 5000) * 104;      // same scale for every stretch, so Khunjerab looks as steep as it is
    const line = pts.map(([x, e], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y(e).toFixed(1)}`).join(" ");
    return S("svg", { class: "ridge", viewBox: "0 0 600 110", preserveAspectRatio: "none", "aria-hidden": "true" },
      S("path", { class: "fill", d: `${line} L600 110 L0 110 Z` }), S("path", { class: "line", d: line }));
  }

  function renderDetail() {
    const s = state.pred.segments.find((q) => q.id === state.sel);
    const d = $("#detail");
    d.className = `panel detail b-${s.band.key}`;
    const hero = el("div", { class: "d-hero" }, ridge(s),
      el("div", { class: "d-title" }, el("span", { class: "chip", text: BAND_TEXT[s.band.key] }), el("h2", { text: s.name }),
        el("p", { class: "ur", lang: "ur", dir: "rtl", text: `${s.name_ur} — ${BAND_UR[s.band.key]}` }),
        el("p", { class: "meta", text: `${s.district} · ${s.length_km} km · around ${s.elevation_m.toLocaleString("en")} m` })),
      ring(s.probability, { size: 104, stroke: 10, band: s.band.key, label: s.seasonal_closure ? "Shut" : pct(s.probability), sub: s.seasonal_closure ? "winter" : "risk" }));
    const body = el("div", { class: "d-body" });
    if (s.notes) body.append(el("p", { class: "d-note", text: s.notes }));
    if (s.actual && !s.seasonal_closure) body.append(el("p", { class: "what-happened", html: `<b>What happened:</b> ${s.actual.closed ? `blocked${s.actual.cause && s.actual.cause !== "seasonal" ? ` (${s.actual.cause})` : ""}` : "stayed open"}.` }));
    const t = s.tomorrow, r = s.recent;
    body.append(el("div", {}, el("h3", { text: state.pred.mode === "replay" ? "Weather on the day" : "Tomorrow's weather" }),
      el("div", { class: "tiles" }, tile("rain", "Rain", n1(t.precip_mm), "mm"), tile("snow", "Snow", n1(t.snow_cm), "cm"),
        tile("temp", "High / low", `${Math.round(t.tmax_c)}° / ${Math.round(t.tmin_c)}°`), tile("wind", "Wind", n1(Math.round(t.wind_kmh)), "km/h"))));
    body.append(el("div", {}, el("h3", { text: "The days before" }),
      el("div", { class: "tiles" }, tile("drop", "Rain, 3 days", n1(r.rain_3d_mm), "mm"), tile("drop", "Rain, 7 days", n1(r.rain_7d_mm), "mm"),
        tile("news", "Blocked reports", String(r.news_block_reports_3d), "3 days"), tile("slide", "Slides", String(r.events_365d), "past year"))));
    if (!s.seasonal_closure && s.drivers.length) {
      const max = Math.max(2.5, ...state.pred.segments.flatMap((q) => q.drivers.map((x) => Math.abs(x.impact))));
      body.append(el("div", { class: "why" }, el("h3", { text: "Why this number" }),
        ...s.drivers.map((x) => {
          const up = x.impact >= 0, w = Math.min(50, (Math.abs(x.impact) / max) * 50);
          const [label, ic] = GROUP[x.group] || [x.group, "news"];
          return el("div", { class: "why-row" }, el("span", { class: "why-ico" }, icon(ic)), el("span", { text: label }),
            el("span", { class: "why-bar" }, el("i", { class: up ? "up" : "down", style: `width:${w}%` })),
            el("span", { class: `why-eff ${up ? "up" : "down"}`, text: Math.abs(x.impact) < 0.05 ? "neutral" : up ? "raises risk" : "lowers risk" }));
        })));
    }
    d.replaceChildren(hero, body);
    renderWhatIf(s);
  }

  // ------------------------------------------------------------------ what-if
  const SLIDERS = [
    { key: "f_precip", label: "Tomorrow's rain", unit: "mm", min: 0, max: 120, step: 1, ic: "rain" },
    { key: "f_snow", label: "Tomorrow's snow", unit: "cm", min: 0, max: 60, step: 1, ic: "snow" },
    { key: "f_tmax", label: "Tomorrow's high", unit: "°C", min: -20, max: 45, step: 1, ic: "temp" },
    { key: "rain7", label: "Rain in the last 7 days", unit: "mm", min: 0, max: 300, step: 5, ic: "drop" },
    { key: "news_block_t", label: "Blocked reports today", unit: "", min: 0, max: 4, step: 1, ic: "news" },
  ];
  function applyWhatIf(base, v, info) {
    const f = { ...base };
    f.f_precip = v.f_precip; f.f_snow = v.f_snow;
    f.f_phours = v.f_precip > 0 ? Math.min(24, Math.max(1, Math.round(2 + 0.6 * v.f_precip))) : 0;
    const spread = base.f_tmax - base.f_tmin;
    f.f_tmax = v.f_tmax; f.f_tmin = v.f_tmax - (Number.isFinite(spread) ? spread : 10);
    const heatThr = 33 - (6.5 * (info.elevation_m - 900)) / 1000;
    f.f_melt_excess = Math.max(0, f.f_tmax - heatThr) * (info.glacier_fed ? 1 : 0);
    f.f_freeze_thaw = f.f_tmin < 0 && f.f_tmax > 2 ? 1 : 0;
    const dk = v.rain7 - base.rain7;
    f.rain7 = v.rain7; f.rain3 = Math.min(v.rain7, Math.max(0, base.rain3 + dk * 0.45));
    f.rain14 = Math.max(f.rain7, base.rain14 + dk); f.rain30 = Math.max(f.rain14, base.rain30 + dk);
    f.storm_total = f.precip_t + f.f_precip;
    const dn = v.news_block_t - base.news_block_t;
    f.news_block_t = v.news_block_t; f.news_block_3d = Math.max(v.news_block_t, base.news_block_3d + dn);
    f.news_net = base.news_net + 0.5 * dn;
    if (v.news_block_t > 0) f.days_since_report = 0;
    return f;
  }
  function renderWhatIf(s) {
    const box = $("#whatif");
    if (s.seasonal_closure || !MODEL) {
      box.replaceChildren(el("h2", { text: "What if?" }), el("p", { class: "help", text: s.seasonal_closure ? "This stretch follows the fixed winter closure of the Pak–China border, so there is nothing to simulate." : "The simulator needs the exported model, which did not load." }));
      return;
    }
    const info = state.segInfo.find((q) => q.id === s.id), base = s.features;
    if (!state.whatif || state.whatif.id !== s.id) state.whatif = { id: s.id, v: Object.fromEntries(SLIDERS.map((c) => [c.key, Math.round(base[c.key] ?? 0)])) };
    const v = state.whatif.v, baseP = probability(base);
    const ringHost = el("div"), delta = el("span", { class: "delta" });
    let shown = null;
    const update = () => {
      const p = probability(applyWhatIf(base, v, info));
      if (shown === null || Math.abs(p - shown) > 1e-6) { ringHost.replaceChildren(ring(p, { size: 132, stroke: 12, sub: "risk" })); shown = p; }
      const diff = p - baseP;
      delta.className = `delta ${Math.abs(diff) < 0.005 ? "" : diff > 0 ? "up" : "down"}`;
      delta.textContent = Math.abs(diff) < 0.005 ? "Same as forecast" : `${diff > 0 ? "▲" : "▼"} ${Math.abs(diff * 100).toFixed(diff * 100 > 10 ? 0 : 1)} pts vs forecast`;
    };
    const rows = SLIDERS.map((c) => {
      const id = `wi-${c.key}`, val = Math.min(c.max, Math.max(c.min, v[c.key]));
      const out = el("output", { for: id, text: `${val} ${c.unit}`.trim() });
      const inp = el("input", { type: "range", id, min: c.min, max: c.max, step: c.step, value: val });
      const fill = () => inp.style.setProperty("--fill", `${((inp.value - c.min) / (c.max - c.min)) * 100}%`);
      fill();
      inp.addEventListener("input", () => { v[c.key] = Number(inp.value); out.textContent = `${inp.value} ${c.unit}`.trim(); fill(); update(); });
      return el("div", { class: "slider" }, el("div", { class: "slider-top" }, el("label", { for: id }, icon(c.ic), c.label), out), inp);
    });
    update();
    box.replaceChildren(
      el("div", { class: "wi-head" }, el("div", {}, el("h2", { text: "What if?" }), el("p", { text: `Change the weather for ${s.name} and rerun the trained model, right here in your browser.` }))),
      el("div", { class: "wi-head" }, ringHost, delta),
      ...rows,
      el("button", { type: "button", class: "btn ghost", onclick: () => { state.whatif = null; renderWhatIf(s); } }, "Reset to forecast"));
  }

  // ------------------------------------------------------------------ profile
  function smooth(pts) {
    let d = `M${pts[0][0]} ${pts[0][1]}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6], c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += ` C${c1[0].toFixed(1)} ${c1[1].toFixed(1)},${c2[0].toFixed(1)} ${c2[1].toFixed(1)},${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
    }
    return d;
  }
  function renderProfile(p) {
    const svg = $("#profile");
    const W = 1000, L = 44, R = 14, TOP = 30, BASE = 196, BAR_Y = 206, BAR_H = 26, MAXE = 5000;
    const total = state.segInfo.reduce((a, s) => a + s.length_km, 0);
    const x = (km) => L + (km / total) * (W - L - R), y = (e) => BASE - (e / MAXE) * (BASE - TOP);
    const pts = state.profile.map((q) => [x(q.km), y(q.elev)]);
    const ridgeD = smooth(pts), area = `${ridgeD} L${x(total)} ${BASE} L${x(0)} ${BASE} Z`;
    svg.replaceChildren(S("defs", {},
      S("linearGradient", { id: "pg", x1: 0, y1: 0, x2: 0, y2: 1 }, S("stop", { offset: 0, "stop-color": "var(--ice)", "stop-opacity": 0.35 }), S("stop", { offset: 1, "stop-color": "var(--ice)", "stop-opacity": 0.02 })),
      S("clipPath", { id: "pclip" }, S("path", { d: area }))));
    for (const e of [1000, 2000, 3000, 4000, 5000]) {
      svg.append(S("line", { class: "grid", x1: L, x2: W - R, y1: y(e), y2: y(e) }), S("text", { class: "axis", x: L - 8, y: y(e) + 4, "text-anchor": "end", text: `${e / 1000}k` }));
    }
    svg.append(S("path", { class: "area", d: area }));
    let km = 0;
    const bands = p.segments.map((s, i) => { const x0 = x(km), x1 = x(km + state.segInfo[i].length_km); km += state.segInfo[i].length_km; return { s, x0, x1 }; });
    for (const b of bands) svg.append(S("rect", { class: `tint b-${b.s.band.key}${state.sel === b.s.id ? " sel" : ""}`, x: b.x0, y: TOP - 20, width: b.x1 - b.x0, height: BASE - TOP + 20, "clip-path": "url(#pclip)" }));
    svg.append(S("path", { class: "ridge", d: ridgeD }));
    const boxes = [];
    state.profile.forEach((q) => {
      if (!q.place) return;
      const px = x(q.km), py = y(q.elev), label = q.km === total ? `${q.place} 4,693 m` : q.place;
      const w = label.length * 6.3, anchor = q.km === total ? "end" : q.km === 0 ? "start" : "middle";
      const left = anchor === "start" ? px : anchor === "end" ? px - w : px - w / 2;
      let ly = py - 9;
      const hits = (yy) => boxes.some((b) => left < b.r + 4 && left + w > b.l - 4 && yy > b.t - 2 && yy - 11 < b.b + 2);
      for (let k = 0; k < 8 && hits(ly); k++) ly -= 13;
      boxes.push({ l: left, r: left + w, t: ly - 11, b: ly }, { l: px - 1, r: px + 1, t: ly, b: py });
      svg.append(S("line", { class: "tick", x1: px, x2: px, y1: py - 2, y2: ly + 3 }), S("text", { class: "place", x: px, y: ly, "text-anchor": anchor, text: label }));
    });
    for (const b of bands) {
      const g = S("g", { class: `band b-${b.s.band.key}${state.sel === b.s.id ? " sel" : ""}`, tabindex: 0, role: "button", "aria-label": `${b.s.name}, ${pct(b.s.probability)}`,
        onclick: () => select(b.s.id), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(b.s.id); } } },
      S("rect", { x: b.x0 + 1.5, y: BAR_Y, width: b.x1 - b.x0 - 3, height: BAR_H, rx: 7 }),
      S("text", { x: (b.x0 + b.x1) / 2, y: BAR_Y + 17.5, "text-anchor": "middle", text: b.s.seasonal_closure ? "Winter" : pct(b.s.probability) }));
      svg.append(g);
    }
    for (const k of [0, 100, 200, 300, 400, 500, 600, 700, 800]) svg.append(S("text", { class: "axis", x: x(k), y: 252, "text-anchor": k === 0 ? "start" : "middle", text: k === 0 ? "0 km" : `${k}` }));
  }

  // ------------------------------------------------------------------ trip
  function initTrip() {
    const from = $("#trip-from"), to = $("#trip-to");
    BOUNDARIES.forEach((n, i) => { from.append(el("option", { value: i, text: n })); to.append(el("option", { value: i, text: n })); });
    from.value = 0; to.value = 5;
    from.onchange = to.onchange = renderTrip;
    $("#trip-swap").replaceChildren(icon("swap"));
    $("#trip-swap").onclick = () => { [from.value, to.value] = [to.value, from.value]; renderTrip(); };
  }
  function renderTrip() {
    if (!state.pred) return;
    let a = Number($("#trip-from").value), b = Number($("#trip-to").value);
    const out = $("#trip-out");
    if (a === b) { out.replaceChildren(el("p", { text: "Pick two different places." })); return; }
    if (a > b) [a, b] = [b, a];
    const segs = state.pred.segments.slice(a, b), info = state.segInfo.slice(a, b);
    if (segs.some((s) => s.seasonal_closure)) { out.replaceChildren(ring(0, { size: 72, stroke: 8, band: "closed", label: "Shut" }), el("p", { html: "<b>Closed past Sost.</b> The Khunjerab border is shut for winter, December to March." })); return; }
    const clear = segs.reduce((acc, s) => acc * (1 - s.probability), 1);
    const km = info.reduce((x, s) => x + s.length_km, 0);
    const worst = segs.reduce((m, s) => (s.probability > m.probability ? s : m), segs[0]);
    out.replaceChildren(ring(clear, { size: 72, stroke: 8, band: clear >= 0.8 ? "open" : clear >= 0.5 ? "caution" : "blocked" }),
      el("div", { style: "flex:1;min-width:0" },
        el("p", { html: `<b>${pct(clear)} chance</b> of driving ${km} km without a blockage.${segs.length > 1 ? ` Riskiest stretch: <b>${worst.name}</b> (${pct(worst.probability)}).` : ""}` }),
        el("div", { class: "trip-bar" }, ...segs.map((s, i) => el("i", { style: `--c:${COLOR[s.band.key]};--w:${info[i].length_km}`, title: `${s.name}: ${pct(s.probability)}` })))));
  }

  // ------------------------------------------------------------------ time machine
  async function initMachine() {
    const inp = $("#replay-date");
    if (B) {
      const sel = el("select", { id: "replay-select" }, ...Object.keys(B.replays).sort().reverse().map((d) => el("option", { value: d, text: shortDate(d) })));
      $("#replay-date-label").replaceChildren(el("span", { text: "Evening of (stored in this demo)" }), sel);
    } else {
      const h = await api.health().catch(() => null);
      if (h && h.replay_range) { inp.min = h.replay_range[0]; inp.max = h.replay_range[1]; inp.value = "2022-08-20"; }
    }
    $("#replay-go").onclick = () => load(B ? $("#replay-select").value : inp.value, true);
    $("#replay-live").onclick = () => load(null, true);
    const sc = await api.scenarios().catch(() => []);
    $("#scenarios").replaceChildren(...sc.map((x) => el("button", {
      type: "button", class: "chip-btn", "data-date": x.as_of, "aria-pressed": "false", onclick: () => load(x.as_of, true),
    }, icon("cal"), shortDate(x.target), x.segments_blocked ? el("b", { text: `${x.segments_blocked} blocked` }) : null)));
    $("#scenarios").querySelectorAll("svg").forEach((s) => { s.style.width = "14px"; s.style.height = "14px"; });
  }

  // ------------------------------------------------------------------ reports
  async function initReports() {
    const seg = $("#rep-seg");
    state.segInfo.forEach((s) => seg.append(el("option", { value: s.id, text: s.name })));
    seg.value = "DSU-CHL";
    if (B) {
      $("#report-form").replaceChildren(el("p", { class: "help", text: "Reports are collected by the full app, which stores them and feeds them into the next forecast. This demo page is read-only." }));
    } else {
      $("#report-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const msg = $("#rep-msg"), btn = $("#rep-submit");
        btn.disabled = true; msg.textContent = "Sending…";
        try {
          await api.postReport({ segment_id: seg.value, status: document.querySelector('input[name="status"]:checked').value, place: $("#rep-place").value, note: $("#rep-note").value });
          msg.textContent = "Report sent. Tomorrow's forecast now includes it.";
          $("#rep-place").value = ""; $("#rep-note").value = "";
          renderReports();
          if (state.pred && state.pred.mode !== "replay") load(null);
        } catch (err) { msg.textContent = err.message; }
        btn.disabled = false;
      });
    }
    renderReports();
  }
  async function renderReports() {
    const list = $("#report-list");
    const items = await api.reports().catch(() => []);
    if (!items.length) { list.replaceChildren(el("li", { class: "empty", text: B ? "No reports in this demo." : "No reports in the last 48 hours. If you are on the KKH, add the first one." })); return; }
    const segName = Object.fromEntries(state.segInfo.map((s) => [s.id, s.name]));
    list.replaceChildren(...items.map((r) => {
      const k = r.status === "blocked" ? "blocked" : r.status === "partial" ? "caution" : "open";
      return el("li", { class: `b-${k}` }, el("span", { class: "chip", text: { blocked: "Blocked", partial: "One-way", open: "Open" }[r.status] }),
        el("b", { text: `${segName[r.segment_id]}${r.place ? `, ${r.place}` : ""}` }),
        el("span", { class: "meta", text: `${new Date(r.created_at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}${r.note ? ` · ${r.note}` : ""}` }));
    }));
  }

  // ------------------------------------------------------------------ model card
  function renderModel(m) {
    const t = m.test, mm = t.model;
    $("#model-intro").textContent = `Scored on ${m.split.test.replace("→", "to")}: ${m.split.rows.test.toLocaleString("en")} segment-days the model never saw. A stretch is blocked on about ${(mm.base_rate * 100).toFixed(1)}% of days, so the job is to find rare events without crying wolf.`;
    const rel = m.reliability.filter((b) => b.n >= 20);
    const calErr = rel.reduce((a, b) => a + Math.abs(b.predicted - b.observed) * b.n, 0) / rel.reduce((a, b) => a + b.n, 0);
    const kpi = (v, k, s) => el("div", { class: "kpi" }, el("div", { class: "v", text: v }), el("div", { class: "k", text: k }), el("div", { class: "s", text: s }));
    $("#kpis").replaceChildren(
      kpi(mm.roc_auc.toFixed(2), "ROC-AUC", `season-only guess: ${t.season_baseline.roc_auc.toFixed(2)}`),
      kpi(`${Math.round(mm["recall@0.2"] * 100)}%`, "Blockages flagged at 20%+", `logistic regression: ${Math.round(t.logistic_baseline["recall@0.2"] * 100)}%`),
      kpi(`${Math.round(mm["precision@0.2"] * 100)}%`, "Right when it warns", "precision at 20%+"),
      kpi(`±${(calErr * 100).toFixed(1)}`, "Calibration gap, points", "forecast vs what happened"));
    const f2 = (x) => x.toFixed(2), p0 = (x) => `${Math.round(x * 100)}%`;
    $("#metrics").replaceChildren(
      el("thead", {}, el("tr", {}, ...["", "ROC-AUC", "PR-AUC", "Flagged at 20%+", "Right when warns"].map((h) => el("th", { scope: "col", text: h })))),
      el("tbody", {}, ...[["This model", mm], ["Logistic regression", t.logistic_baseline], ["Season only", t.season_baseline]].map(([n, r]) =>
        el("tr", {}, el("td", { text: n }), el("td", { text: f2(r.roc_auc) }), el("td", { text: f2(r.pr_auc) }), el("td", { text: p0(r["recall@0.2"]) }), el("td", { text: p0(r["precision@0.2"]) })))));
    $("#data-note").textContent = m.data_mode === "synthetic"
      ? "These scores test the pipeline, not real-world accuracy: this build was trained on a bootstrap dataset of simulated weather and closures calibrated to documented KKH events. Rebuilding with internet access retrains on Open-Meteo weather, NASA landslides and the GDELT news archive."
      : `Trained on Open-Meteo weather, NASA landslide records and news reports. Model trained ${m.trained_at.slice(0, 10)}.`;

    const svg = $("#calib");
    const L = 38, R = 10, T = 12, Bm = 228, W = 340;
    const x = (v) => L + v * (W - L - R), y = (v) => Bm - v * (Bm - T);
    svg.replaceChildren(S("defs", {}, S("linearGradient", { id: "cg", x1: 0, y1: 0, x2: 0, y2: 1 }, S("stop", { offset: 0, "stop-color": "var(--ice)", "stop-opacity": 0.3 }), S("stop", { offset: 1, "stop-color": "var(--ice)", "stop-opacity": 0 }))));
    for (const v of [0, 0.25, 0.5, 0.75, 1]) svg.append(S("line", { class: "grid", x1: x(0), x2: x(1), y1: y(v), y2: y(v) }), S("text", { class: "axis", x: L - 6, y: y(v) + 3, "text-anchor": "end", text: `${v * 100}%` }), S("text", { class: "axis", x: x(v), y: Bm + 15, "text-anchor": "middle", text: `${v * 100}%` }));
    svg.append(S("line", { class: "diag", x1: x(0), y1: y(0), x2: x(1), y2: y(1) }));
    const line = rel.map((b, i) => `${i ? "L" : "M"}${x(b.predicted)} ${y(b.observed)}`).join("");
    svg.append(S("path", { class: "area", d: `${line} L${x(rel.at(-1).predicted)} ${Bm} L${x(rel[0].predicted)} ${Bm} Z` }), S("path", { class: "line", d: line }));
    rel.forEach((b) => svg.append(S("circle", { class: "pt", cx: x(b.predicted), cy: y(b.observed), r: 4 }, S("title", { text: `Forecast ${pct(b.predicted)}, happened ${pct(b.observed)} (${b.n} segment-days)` }))));
    svg.append(S("text", { class: "axis", x: (L + W - R) / 2, y: 262, "text-anchor": "middle", text: "Forecast chance  →  how often it happened (dashed = perfect)" }));

    const imp = Object.entries(m.group_importance).sort((a, b) => b[1] - a[1]), mx = imp[0][1];
    $("#drivers-global").replaceChildren(...imp.map(([g, v]) => el("li", {}, el("span", { text: (GROUP[g] || [g])[0] }), el("span", { class: "bar" }, el("i", { style: `width:${(v / mx) * 100}%` })))));
    $("#foot-meta").textContent = `Model: ${m.n_trees} monotone gradient-boosted trees with Platt calibration, trained ${m.trained_at.slice(0, 10)} on ${m.data_mode} data.`;
    const au = m.audit;
    if (au) {
      const pctOr = (v) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);
      $("#audit").replaceChildren(el("h3", { text: "Audit checks" }), el("ul", { class: "audit-list" },
        el("li", { class: "ok" }, el("b", { text: `Stable year to year.` }), ` Trained on the past only and tested on each year ${au.backtest_years}: ROC-AUC ${au.backtest_auc_mean} ± ${au.backtest_auc_std}.`),
        el("li", { class: au.monotone_violations === 0 ? "ok" : "warn" }, el("b", { text: "Physically consistent." }), ` More rain, snow, melt or blockage reports never lower the risk (${au.monotone_violations}% violations).`),
        el("li", { class: au.replay_matches_training ? "ok" : "warn" }, el("b", { text: "No train/serve skew." }), " The time machine rebuilds exactly the features the model was trained on."),
        el("li", { class: "warn" }, el("b", { text: "Weak spot: first-day blockages." }), ` It catches ${pctOr(au.ongoing_recall)} of blockages already being reported, but only ${pctOr(au.onset_recall)} of brand-new ones with no report yet.`)));
    }
  }

  // ------------------------------------------------------------------ theme
  function initTheme() {
    const root = document.documentElement, btn = $("#theme");
    let saved = null;
    try { saved = localStorage.getItem("kkh-theme"); } catch { /* storage blocked */ }
    if (saved) root.dataset.theme = saved;
    const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches);
    const paint = () => btn.replaceChildren(icon(isDark() ? "sun" : "moon"));
    btn.onclick = () => { root.dataset.theme = isDark() ? "light" : "dark"; try { localStorage.setItem("kkh-theme", root.dataset.theme); } catch { /* ignore */ } paint(); };
    matchMedia("(prefers-color-scheme: light)").addEventListener("change", paint);
    paint();
  }

  function isDarkTheme() {
    const root = document.documentElement;
    return root.dataset.theme ? root.dataset.theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
  }

  // ------------------------------------------------------------------ flow
  function select(id) {
    state.sel = id;
    renderMap(state.pred); renderRail(state.pred); renderProfile(state.pred); renderDetail(); updateScene();
    document.querySelectorAll("#h-strip button").forEach((b, i) => b.setAttribute("aria-pressed", String(state.pred.segments[i].id === id)));
  }
  async function load(date, scroll = false) {
    $("#status-text").textContent = "Loading";
    try {
      const p = await api.predict(date);
      state.pred = p; state.whatif = null;
      if (!state.sel || !p.segments.some((s) => s.id === state.sel)) state.sel = p.summary.worst_segment;
      renderStatus(p); renderHero(p); renderMap(p); renderRail(p); renderDetail(); renderProfile(p); renderTrip(); updateScene();
      document.querySelectorAll("#scenarios .chip-btn").forEach((c) => c.setAttribute("aria-pressed", String(p.mode === "replay" && c.dataset.date === p.as_of)));
      if (scroll) window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
    } catch (err) {
      $("#status-text").textContent = "Could not load";
      $("#notice").textContent = `${err.message} Try another date, or reload the page.`;
      $("#notice").hidden = false;
    }
  }
  async function boot() {
    initTheme();
    if (window.KKHFX) scene = window.KKHFX.mount($("#hero-photo"));
    const [seg, geo, model, exp] = await Promise.all([api.segments(), api.geo(), api.model(), api.exportJson().catch(() => null)]);
    state.segInfo = seg.segments; state.profile = seg.profile; state.geo = geo; MODEL = exp;
    initTrip(); renderModel(model);
    await load(null);
    initMachine(); initReports();
  }
  boot().catch((err) => {
    $("#status-text").textContent = "Could not load";
    $("#headline").textContent = "The forecast could not be loaded.";
    $("#lede").textContent = `${err.message}. Check that the server is running, then reload.`;
  });
})();
