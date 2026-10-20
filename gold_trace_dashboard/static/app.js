/* ═══════════════ Gold Trace — dashboard app ═══════════════════════════
   One aggregate poll (/api/bootstrap, 5s) feeds every panel. Charts are
   hand-rolled canvas renderers: no CDN, phone-tuned font sizes, DPR-aware.
   ═══════════════════════════════════════════════════════════════════ */

const $ = (id) => document.getElementById(id);
const COL = {
  gold: "#f0c645", goldDim: "#b8933a", text: "#e9e6dd", muted: "#8b93a7",
  green: "#3ddc84", red: "#ff5f5f", amber: "#ffb454", blue: "#6ab7ff",
  purple: "#c79bff", grid: "rgba(35,42,58,0.7)", panel: "#171c28",
};
const DAY = 86400;

/* ──────────────────────────── formatters ──────────────────────────── */
const nf = (v, d = 2) => v == null ? "—" :
  v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (v) => v == null ? "—" : "$" + nf(v, v >= 1000 ? 0 : 2);
const signed = (v, d = 1, suffix = "") => v == null ? "—" :
  (v >= 0 ? "+" : "") + nf(v, d) + suffix;
const compact = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "k";
  return v.toFixed(0);
};
const timeAgo = (ts, now) => {
  if (!ts) return "—";
  const s = Math.max(0, (now || Date.now() / 1000) - ts);
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < DAY) return Math.round(s / 3600) + "h ago";
  return Math.round(s / DAY) + "d ago";
};
const dateStr = (ts) => new Date(ts * 1000).toLocaleDateString("en-US",
  { month: "short", day: "numeric", timeZone: "UTC" });
const dtStr = (ts) => new Date(ts * 1000).toLocaleString("en-US",
  { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "UTC" }) + " UTC";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tagColor = (tag) => ({ "strong-bull": COL.green, "bull": "#9fe6bf",
  "neutral": COL.muted, "bear": "#ffb1a0", "strong-bear": COL.red }[tag] || COL.muted);

/* ──────────────────────────── canvas chart core ───────────────────── */
function setupCanvas(cv) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const r = cv.getBoundingClientRect();
  if (r.width === 0) return null;
  if (cv.width !== Math.round(r.width * dpr) || cv.height !== Math.round(r.height * dpr)) {
    cv.width = Math.round(r.width * dpr);
    cv.height = Math.round(r.height * dpr);
  }
  const c = cv.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, r.width, r.height);
  return { c, W: r.width, H: r.height };
}

function niceStep(range, targetTicks) {
  const raw = range / Math.max(1, targetTicks);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}

/**
 * series: [{pts:[{t,v}], color, label, axis:'y1'|'y2', kind:'line'|'bar'|'area', width}]
 * opts:   {fmtY1, fmtY2, minTicksX, zeroBase}
 */
function drawChart(canvasId, series, opts = {}) {
  const cv = $(canvasId);
  if (!cv) return;
  const s = setupCanvas(cv);
  if (!s) return;
  const { c, W, H } = s;
  series = series.filter((x) => x && x.pts && x.pts.length > 1);
  if (!series.length) {
    c.fillStyle = COL.muted; c.font = "11px sans-serif"; c.textAlign = "center";
    c.fillText("warming up…", W / 2, H / 2);
    return;
  }
  const phone = W < 560;
  const padL = 8, padR = 8, padT = 20, padB = 18;
  const hasY2 = series.some((x) => x.axis === "y2");
  const mL = padL + 44, mR = padR + (hasY2 ? 44 : 6);
  const plotW = W - mL - mR, plotH = H - padT - padB;

  const allT = series.flatMap((x) => x.pts.map((p) => p.t));
  const t0 = Math.min(...allT), t1 = Math.max(...allT);

  function extent(axis) {
    const vals = series.filter((x) => (x.axis || "y1") === axis)
      .flatMap((x) => x.pts.map((p) => p.v));
    if (!vals.length) return null;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (opts.zeroBase) lo = Math.min(0, lo);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.08;
    return { lo: lo - pad, hi: hi + pad, vals };
  }
  const e1 = extent("y1"), e2 = hasY2 ? extent("y2") : null;
  if (!e1) return;

  const X = (t) => mL + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * plotW;
  const Y = (v, axis) => {
    const e = axis === "y2" ? e2 : e1;
    return padT + plotH - ((v - e.lo) / (e.hi - e.lo)) * plotH;
  };

  /* grid + y labels */
  c.font = `${phone ? 9 : 10}px ui-monospace, Menlo, monospace`;
  const step1 = niceStep(e1.hi - e1.lo, 4);
  for (let v = Math.ceil(e1.lo / step1) * step1; v <= e1.hi; v += step1) {
    const y = Y(v, "y1");
    c.strokeStyle = COL.grid; c.lineWidth = 1;
    c.beginPath(); c.moveTo(mL, y); c.lineTo(W - mR, y); c.stroke();
    c.fillStyle = COL.muted; c.textAlign = "left"; c.textBaseline = "middle";
    c.fillText((opts.fmtY1 || compact)(v), 4, y);
  }
  if (e2) {
    const step2 = niceStep(e2.hi - e2.lo, 4);
    c.textAlign = "right";
    for (let v = Math.ceil(e2.lo / step2) * step2; v <= e2.hi; v += step2) {
      c.fillStyle = "rgba(139,147,167,0.75)";
      c.fillText((opts.fmtY2 || compact)(v), W - 4, Y(v, "y2"));
    }
  }
  /* x labels */
  const xTicks = phone ? 3 : 5;
  c.textAlign = "center"; c.textBaseline = "top"; c.fillStyle = COL.muted;
  for (let i = 0; i <= xTicks; i++) {
    const t = t0 + (t1 - t0) * (i / xTicks);
    c.fillText(dateStr(t), X(t), H - padB + 5);
  }
  /* zero line */
  if (e1.lo < 0 && e1.hi > 0) {
    c.strokeStyle = "rgba(139,147,167,0.4)"; c.setLineDash([3, 3]);
    c.beginPath(); c.moveTo(mL, Y(0, "y1")); c.lineTo(W - mR, Y(0, "y1"));
    c.stroke(); c.setLineDash([]);
  }

  /* series */
  const barGroups = series.filter((x) => x.kind === "bar");
  if (barGroups.length) {
    const pts0 = barGroups[0].pts;
    const bw = Math.max(2, Math.min(26, (plotW / Math.max(pts0.length, 1)) * 0.62 / barGroups.length));
    barGroups.forEach((sr, gi) => {
      c.fillStyle = sr.color;
      sr.pts.forEach((p) => {
        const yBase = Y(Math.max(0, e1.lo), "y1");
        const y = Y(p.v, sr.axis || "y1");
        const x = X(p.t) - (barGroups.length * bw) / 2 + gi * bw;
        c.globalAlpha = 0.9;
        c.fillRect(x, Math.min(y, yBase), bw - 1, Math.abs(yBase - y));
      });
      c.globalAlpha = 1;
    });
  }
  for (const sr of series) {
    if (sr.kind === "bar") continue;
    const axis = sr.axis || "y1";
    c.beginPath();
    sr.pts.forEach((p, i) => i ? c.lineTo(X(p.t), Y(p.v, axis)) : c.moveTo(X(p.t), Y(p.v, axis)));
    if (sr.kind === "area") {
      c.save();
      c.lineTo(X(sr.pts[sr.pts.length - 1].t), padT + plotH);
      c.lineTo(X(sr.pts[0].t), padT + plotH);
      c.closePath();
      const g = c.createLinearGradient(0, padT, 0, padT + plotH);
      g.addColorStop(0, sr.color + "44"); g.addColorStop(1, sr.color + "05");
      c.fillStyle = g; c.fill();
      c.restore();
      c.beginPath();
      sr.pts.forEach((p, i) => i ? c.lineTo(X(p.t), Y(p.v, axis)) : c.moveTo(X(p.t), Y(p.v, axis)));
    }
    c.strokeStyle = sr.color; c.lineWidth = sr.width || 1.6;
    c.lineJoin = "round"; c.stroke();
    /* last-point dot */
    const lp = sr.pts[sr.pts.length - 1];
    c.fillStyle = sr.color;
    c.beginPath(); c.arc(X(lp.t), Y(lp.v, axis), 2.2, 0, 7); c.fill();
  }

  /* legend */
  let lx = mL + 2;
  c.font = `600 ${phone ? 9 : 10}px sans-serif`;
  c.textAlign = "left"; c.textBaseline = "middle";
  for (const sr of series) {
    const label = sr.label + " " + ((sr.fmt || compact)(sr.pts[sr.pts.length - 1].v));
    c.fillStyle = sr.color;
    c.fillRect(lx, 7, 8, 3);
    c.fillStyle = "rgba(233,230,221,0.85)";
    c.fillText(label, lx + 12, 9);
    lx += 12 + c.measureText(label).width + 16;
    if (lx > W - 60) break;
  }
}

/* ──────────────────────────── gauges ──────────────────────────────── */
function drawGauge(canvasId, score, tag, centerLabel) {
  const cv = $(canvasId);
  if (!cv) return;
  const s = setupCanvas(cv);
  if (!s) return;
  const { c, W, H } = s;
  const cx = W / 2, cy = H * 0.86, R = Math.min(W * 0.42, H * 0.78);
  const a0 = Math.PI, a1 = Math.PI * 2;
  /* track */
  c.lineCap = "round";
  c.strokeStyle = "#1c2230"; c.lineWidth = 11;
  c.beginPath(); c.arc(cx, cy, R, a0, a1); c.stroke();
  /* zone tints */
  const zones = [
    [0.00, 0.225, "rgba(255,95,95,0.35)"], [0.225, 0.375, "rgba(255,177,160,0.3)"],
    [0.375, 0.625, "rgba(139,147,167,0.3)"], [0.625, 0.775, "rgba(159,230,191,0.3)"],
    [0.775, 1.0, "rgba(61,220,132,0.4)"],
  ];
  c.lineWidth = 11;
  for (const [lo, hi, col] of zones) {
    c.strokeStyle = col;
    c.beginPath(); c.arc(cx, cy, R, a0 + (a1 - a0) * lo, a0 + (a1 - a0) * hi); c.stroke();
  }
  /* needle */
  const p = Math.min(1, Math.max(0, (score + 100) / 200));
  const ang = a0 + (a1 - a0) * p;
  c.strokeStyle = tagColor(tag); c.lineWidth = 2.5;
  c.beginPath(); c.moveTo(cx, cy);
  c.lineTo(cx + Math.cos(ang) * (R - 12), cy + Math.sin(ang) * (R - 12));
  c.stroke();
  c.fillStyle = tagColor(tag);
  c.beginPath(); c.arc(cx, cy, 4.5, 0, 7); c.fill();
  /* value */
  c.fillStyle = tagColor(tag);
  c.font = `800 ${Math.min(26, W * 0.09)}px ui-monospace, Menlo, monospace`;
  c.textAlign = "center"; c.textBaseline = "alphabetic";
  c.fillText((score >= 0 ? "+" : "") + nf(score, 1), cx, cy - 14);
  if (centerLabel) {
    c.fillStyle = COL.muted; c.font = "600 9.5px sans-serif";
    c.fillText(centerLabel, cx, cy - 2);
  }
  c.fillStyle = "rgba(139,147,167,0.8)";
  c.font = "9px ui-monospace, Menlo, monospace";
  c.textAlign = "left"; c.fillText("−100", cx - R - 2, cy + 12);
  c.textAlign = "right"; c.fillText("+100", cx + R + 2, cy + 12);
}

/* ──────────────────────────── DOM renderers ───────────────────────── */
function renderFactors(el, factors) {
  el.innerHTML = factors.map((f) => {
    const pct = Math.abs(f.component) * 50;               // half-width per side
    const pos = f.component >= 0;
    const left = pos ? 50 : 50 - pct;
    const color = pos ? COL.green : COL.red;
    return `<div class="factor">
      <span class="f-name">${esc(f.name)} <span class="dim">· w${Math.round(f.weight * 100)}</span></span>
      <span class="f-val">${esc(f.value)}</span>
      <div class="f-bar"><div class="f-fill" style="left:${left}%;width:${pct}%;background:${color}"></div></div>
      ${f.note ? `<span class="f-note">${esc(f.note)}</span>` : ""}
    </div>`;
  }).join("");
}

function renderScores(d) {
  const s = d.scores.structural, m = d.scores.macro, comp = d.scores.composite;
  drawGauge("gauge-structural", s.score, s.tag, "STRUCTURAL");
  drawGauge("gauge-macro", m.score, m.tag, "MACRO");
  drawGauge("gauge-composite", comp.score, comp.tag, "COMPOSITE");
  $("struct-bias").innerHTML = `<span class="${s.tag}">${esc(s.bias)}</span>`;
  $("macro-bias").innerHTML = `<span class="${m.tag}">${esc(m.lean)}</span>`;
  $("comp-bias").innerHTML = `<span class="${comp.tag}" style="font-size:15px">${esc(comp.stance)}</span>`;
  $("struct-playbook").textContent = s.playbook || "";
  $("comp-modifier").textContent = comp.modifier || "";
  $("comp-kv").innerHTML = `
    <div><span class="k">confidence</span><span class="v">${comp.confidence}%</span></div>
    <div><span class="k">formula</span><span class="v">0.6·structural + 0.4·macro</span></div>
    <div><span class="k">structural</span><span class="v ${s.tag}">${signed(s.score)}</span></div>
    <div><span class="k">macro</span><span class="v ${m.tag}">${signed(m.score)}</span></div>`;
  renderFactors($("struct-factors"), s.factors);
  renderFactors($("macro-factors"), m.factors);
  /* hero strip */
  $("stance-value").textContent = signed(comp.score, 0);
  $("stance-value").className = "stance-value " + comp.tag;
  $("stance-name").textContent = comp.stance;
  $("struct-mini").textContent = signed(s.score, 0);
  $("struct-mini").className = "stance-value small " + s.tag;
  $("struct-name").textContent = s.bias;
  $("macro-mini").textContent = signed(m.score, 0);
  $("macro-mini").className = "stance-value small " + m.tag;
  $("macro-name").textContent = m.lean;
}

function renderCharts(ch) {
  const g = (n) => ch[n] || { points: [] };
  const pts = (n) => g(n).points;
  const lastV = (n) => g(n).last;

  $("spot-mini").textContent = lastV("spot_xau") != null ? money(lastV("spot_xau")) : "···";
  const sp = pts("spot_xau");
  if (sp.length > 30) {
    const chg = ((sp[sp.length - 1].v - sp[0].v) / sp[0].v) * 100;
    $("spot-chg").innerHTML = `<span class="${chg >= 0 ? "pos" : "neg"}">${signed(chg, 2, "%")}</span> / 10d`;
  }

  drawChart("chart-price", [
    { pts: pts("spot_xau"), color: COL.gold, label: "XAU", kind: "area", fmt: money },
    { pts: pts("comex_gc"), color: COL.blue, label: "GC", fmt: money },
  ]);
  drawChart("chart-yields", [
    { pts: pts("tips_10y"), color: COL.amber, label: "TIPS10y %", fmt: (v) => nf(v, 2) + "%" },
    { pts: pts("fedwatch_cut_prob"), color: COL.green, label: "cut %", axis: "y2", fmt: (v) => nf(v, 0) + "%" },
  ], { fmtY1: (v) => nf(v, 1), fmtY2: (v) => nf(v, 0) });
  drawChart("chart-fx", [
    { pts: pts("dxy"), color: COL.blue, label: "DXY", fmt: (v) => nf(v, 1) },
    { pts: pts("brent"), color: COL.red, label: "Brent", axis: "y2", fmt: money },
  ], { fmtY1: (v) => nf(v, 0), fmtY2: (v) => "$" + nf(v, 0) });
  drawChart("chart-etf", [
    { pts: pts("gld_tonnes"), color: COL.gold, label: "GLD t", kind: "area", fmt: (v) => nf(v, 0) + "t" },
    { pts: pts("iau_tonnes"), color: COL.purple, label: "IAU t", fmt: (v) => nf(v, 0) + "t" },
  ], { fmtY1: (v) => nf(v, 0) });
  drawChart("chart-comex", [
    { pts: pts("comex_registered"), color: COL.gold, label: "reg Moz", kind: "area",
      fmt: (v) => nf(v / 1e6, 1) },
    { pts: pts("comex_eligible"), color: COL.blue, label: "elig Moz", axis: "y2",
      fmt: (v) => nf(v / 1e6, 0) },
  ], { fmtY1: (v) => nf(v / 1e6, 0), fmtY2: (v) => nf(v / 1e6, 0) });
  drawChart("chart-premium", [
    { pts: pts("sge_premium"), color: COL.gold, label: "SGE $", kind: "bar", fmt: (v) => "$" + nf(v, 0) },
    { pts: pts("india_premium"), color: COL.amber, label: "India $", fmt: (v) => "$" + nf(v, 0) },
  ], { fmtY1: (v) => "$" + nf(v, 0), zeroBase: true });
  drawChart("chart-withdrawals", [
    { pts: pts("sge_withdrawals"), color: COL.gold, label: "t/wk", kind: "bar", fmt: (v) => nf(v, 0) + "t" },
  ], { fmtY1: (v) => nf(v, 0) });
  drawChart("chart-cot", [
    { pts: pts("cot_mm_net"), color: COL.gold, label: "MM net", kind: "area", fmt: compact },
    { pts: pts("cot_comm_net"), color: COL.blue, label: "COMM net", axis: "y2", fmt: compact },
  ], { fmtY1: compact, fmtY2: compact });
  drawChart("chart-cb", [
    { pts: pts("cb_net_purchases"), color: COL.gold, label: "net t/mo", kind: "bar", fmt: (v) => nf(v, 0) + "t" },
  ], { fmtY1: (v) => nf(v, 0), zeroBase: true });
  drawChart("chart-trade", [
    { pts: pts("swiss_gold_exports"), color: COL.gold, label: "Swiss exp t", kind: "bar", fmt: (v) => nf(v, 0) + "t" },
    { pts: pts("china_gold_imports"), color: COL.red, label: "China imp t", kind: "bar", fmt: (v) => nf(v, 0) + "t" },
  ], { fmtY1: (v) => nf(v, 0), zeroBase: true });
  drawChart("chart-13f", [
    { pts: pts("inst_gold_alloc_pct"), color: COL.gold, label: "alloc %", kind: "area", fmt: (v) => nf(v, 2) + "%" },
    { pts: pts("inst_gold_fund_count"), color: COL.purple, label: "funds", axis: "y2", fmt: compact },
  ], { fmtY1: (v) => nf(v, 1), fmtY2: compact });

  /* options OI chip */
  const oi = g("options_oi");
  if (oi.last != null) {
    const ex = oi.extra || {};
    $("oi-chip").textContent = `OI ${compact(oi.last)} · PCR ${ex.pcr ?? "—"}`;
    $("oi-chip").className = "chip right-chip " + (oi.mode === "live" ? "live" : "sim");
  }
}

function renderLayers(d) {
  const speedClass = (sp) => sp.startsWith("Live") ? "speed-live" :
    sp.startsWith("Daily") ? "speed-daily" : sp.startsWith("Weekly") ? "speed-weekly" :
    sp.startsWith("Monthly") ? "speed-monthly" : "speed-quarterly";
  $("layer-grid").innerHTML = d.layers.map((l) => {
    const fresh = l.last_ts ? d.now - l.last_ts < (l.speed.startsWith("Live") ? 60 : 600) : false;
    return `<div class="layer-card">
      <div class="layer-head">
        <span class="layer-name">${esc(l.layer)}</span>
        <span class="speed-chip ${speedClass(l.speed)}">${esc(l.speed).toUpperCase()}</span>
      </div>
      <div class="layer-detail">${esc(l.detail)}</div>
      <div class="layer-foot">
        <span><span class="pulse ${fresh ? "" : (l.speed.startsWith("Live") ? "stale" : "slow")}"></span>${timeAgo(l.last_ts, d.now)}</span>
        <span class="chip ${l.mode === "live" ? "live" : "sim"}" style="padding:3px 7px">${(l.mode || "—").toUpperCase()}</span>
      </div>
    </div>`;
  }).join("");
}

function renderWhales(d) {
  const w = d.whales;
  $("whale-agg").textContent = `${compact(w.agg_24h_oz)} oz / 24h`;
  $("whale-feed").innerHTML = w.transfers.slice(0, 22).map((t) => `
    <div class="feed-row ${t.flagged ? "flagged" : ""}">
      <div class="feed-top">
        <span><span class="token-chip ${esc(t.token)}">${esc(t.token)}</span>
        <span class="whale-amt">${t.flagged ? "🐋 " : ""}${nf(t.amount_oz, 0)} oz</span></span>
        <span class="feed-time">${timeAgo(t.ts, d.now)}</span>
      </div>
      <div class="whale-route">${esc(t.src)} → ${esc(t.dst)} · ~${money(t.usd_value)}
        ${t.flagged ? ' · <span style="color:var(--gold)">ANOMALY ≥5k oz</span>' : ""}</div>
    </div>`).join("") || '<p class="dim">no whale transfers in window</p>';
}

function renderEvents(d) {
  const ev = d.events;
  $("countdown-cards").innerHTML = ev.upcoming.map((u) => {
    const s = u.countdown_s;
    const dd = Math.floor(s / DAY), hh = Math.floor(s % DAY / 3600),
          mm = Math.floor(s % 3600 / 60), ss = s % 60;
    return `<div class="cd-card" data-ts="${u.ts}">
      <div class="cd-name">${esc(u.name)}</div>
      <div class="cd-date">${dtStr(u.ts)}</div>
      <div class="cd-clock" data-cd>${dd}d ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}</div>
      <div class="cd-cons">consensus ${u.consensus} ${esc(u.unit || "")}</div>
    </div>`;
  }).join("") || '<p class="dim">no upcoming events</p>';

  const cred = ev.credibility;
  $("matrix-cred").textContent = cred.released
    ? `· matrix credibility ${cred.hit_rate}% over ${cred.released} prints` : "";

  /* matrix cells (static config from first upcoming, plus both names) */
  const seen = new Set();
  $("matrix-box").innerHTML = ev.upcoming.map((u) => {
    if (seen.has(u.name)) return "";
    seen.add(u.name);
    return `<div class="matrix-group"><h4>${esc(u.name)}</h4>` +
      u.cells.map((cell, i) => `
      <div class="matrix-cell ${i === 0 ? "m-hot" : i === 2 ? "m-cool" : "m-line"}">
        <span class="m-bucket">${esc(cell.bucket)}</span>
        <span class="m-chain">${esc(cell.chain)}</span>
        <span class="m-gold">Gold: ${esc(cell.gold)}</span>
        <span class="m-play">▶ ${esc(cell.play)}</span>
      </div>`).join("") + "</div>";
  }).join("");

  $("event-history").innerHTML = `<table>
    <thead><tr><th>Event</th><th>Released</th><th class="num">Cons</th>
    <th class="num">Actual</th><th class="num">24h $</th><th>Matrix cell</th></tr></thead>
    <tbody>${ev.history.map((h) => {
      const surprise = (h.actual ?? 0) - (h.consensus ?? 0);
      return `<tr>
      <td>${esc(h.name)}</td>
      <td class="dim">${dateStr(h.ts)}</td>
      <td class="num">${h.consensus}</td>
      <td class="num ${surprise > 0 ? "pos" : surprise < 0 ? "neg" : "dim"}">${h.actual}</td>
      <td class="num ${h.reaction_usd >= 0 ? "pos" : "neg"}">${signed(h.reaction_usd, 0)}</td>
      <td class="dim" style="max-width:220px">${esc(h.reaction || "—")}</td></tr>`;
    }).join("")}</tbody></table>`;
}

/* live countdown ticker between polls */
setInterval(() => {
  document.querySelectorAll(".cd-card").forEach((card) => {
    const ts = +card.dataset.ts, el = card.querySelector("[data-cd]");
    if (!el) return;
    const s = Math.max(0, ts - Math.floor(Date.now() / 1000));
    const dd = Math.floor(s / DAY), hh = Math.floor(s % DAY / 3600),
          mm = Math.floor(s % 3600 / 60), ss = s % 60;
    el.textContent = `${dd}d ${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  });
}, 1000);

function renderAlerts(d) {
  $("alert-feed").innerHTML = d.alerts.alerts.map((a) => `
    <div class="feed-row sev-${esc(a.severity)}">
      <div class="feed-top">
        <span class="feed-title">${esc(a.title)}</span>
        <span class="feed-time">${timeAgo(a.ts, d.now)}</span>
      </div>
      <div class="feed-body">${esc(a.body || "")}</div>
      <div class="feed-top" style="margin-top:4px">
        <span class="dim" style="font-size:10.5px;font-family:var(--mono)">${esc(a.kind)}</span>
        <span class="tg-chip ${a.telegram_status === "sent" ? "sent" : ""}">TG: ${esc(a.telegram_status)}</span>
      </div>
    </div>`).join("") || '<p class="dim">no alerts yet</p>';
}

function renderJournal(d) {
  const j = d.journal, r = j.report;
  $("journal-summary").innerHTML = `
    <div class="stat"><span class="s-v">${r.total}</span><span class="s-k">trades</span></div>
    <div class="stat"><span class="s-v ${r.overall_win_rate >= 50 ? "pos" : "neg"}">${r.overall_win_rate ?? "—"}%</span><span class="s-k">win rate</span></div>
    <div class="stat"><span class="s-v ${r.net_r >= 0 ? "pos" : "neg"}">${signed(r.net_r, 1)}R</span><span class="s-k">net R</span></div>
    <div class="stat"><span class="s-v">${r.wins}–${r.losses}</span><span class="s-k">W–L</span></div>`;

  $("journal-types").innerHTML = `<table>
    <thead><tr><th>Setup type</th><th class="num">N</th><th>Win rate</th>
    <th class="num">Net R</th><th class="num">Avg R</th></tr></thead>
    <tbody>${r.by_setup_type.map((t) => `
      <tr><td>${esc(t.setup_type)}</td><td class="num">${t.trades}</td>
      <td><span class="wr-cell"><span class="wr-bar"><span class="wr-fill" style="width:${t.win_rate ?? 0}%"></span></span>
      <b class="${(t.win_rate ?? 0) >= 50 ? "pos" : "neg"}">${t.win_rate ?? "—"}%</b></span></td>
      <td class="num ${t.net_r >= 0 ? "pos" : "neg"}">${signed(t.net_r, 1)}</td>
      <td class="num">${signed(t.avg_r, 2)}</td></tr>`).join("")}</tbody></table>`;

  $("journal-weeks").innerHTML = `<table>
    <thead><tr><th>Week</th><th class="num">Trades</th><th class="num">Win rate</th>
    <th class="num">Net R</th></tr></thead>
    <tbody>${r.by_week.map((w) => `
      <tr><td class="dim">${esc(w.week)}</td><td class="num">${w.trades}</td>
      <td class="num ${w.win_rate >= 50 ? "pos" : "neg"}">${w.win_rate ?? "—"}%</td>
      <td class="num ${w.net_r >= 0 ? "pos" : "neg"}">${signed(w.net_r, 1)}</td></tr>`).join("")}</tbody></table>`;

  $("journal-entries").innerHTML = `<table>
    <thead><tr><th>Date</th><th>Setup</th><th>Dir</th><th class="num">Level</th>
    <th>Trigger</th><th>Result</th><th class="num">R</th></tr></thead>
    <tbody>${j.entries.slice(0, 24).map((e) => `
      <tr><td class="dim">${esc(e.trade_date)}</td><td>${esc(e.setup_type)}</td>
      <td class="${e.direction === "LONG" ? "pos" : "neg"}">${esc(e.direction)}</td>
      <td class="num">${e.level != null ? nf(e.level, 1) : "—"}</td>
      <td class="dim" style="max-width:240px">${esc(e.trigger || "")}</td>
      <td class="${e.result === "Win" ? "pos" : e.result === "Loss" ? "neg" : "dim"}">${esc(e.result)}</td>
      <td class="num ${e.r_multiple > 0 ? "pos" : e.r_multiple < 0 ? "neg" : "dim"}">${e.r_multiple != null ? signed(e.r_multiple, 1) : "—"}</td></tr>`).join("")}</tbody></table>`;

  const sel = $("setup-type-select");
  if (sel && !sel.options.length) {
    j.setup_types.forEach((t) => sel.add(new Option(t, t)));
  }
}

function renderHeader(d) {
  const liveLayers = d.layers.filter((l) => l.mode === "live").length;
  const modeChip = $("mode-chip");
  modeChip.textContent = liveLayers ? `${liveLayers} LIVE LAYERS` : "SIM MODE";
  modeChip.className = "chip " + (liveLayers ? "live" : "sim");
  $("tg-chip").textContent = d.telegram_configured ? "TG: armed" : "TG: outbox";
  $("foot-mode").textContent = d.mode_note;
}

/* ──────────────────────────── journal form ────────────────────────── */
$("journal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, btn = $("journal-submit"), msg = $("journal-msg");
  const body = Object.fromEntries(new FormData(f).entries());
  ["level", "r_multiple"].forEach((k) => body[k] = body[k] === "" ? null : parseFloat(body[k]));
  btn.disabled = true; msg.textContent = "saving…";
  try {
    const r = await fetch("/api/journal", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    msg.textContent = "saved ✓"; f.reset();
    f.trade_date.valueAsDate = new Date();
    await refresh();
    setTimeout(() => (msg.textContent = ""), 2500);
  } catch (err) {
    msg.textContent = "error: " + err.message;
  } finally {
    btn.disabled = false;
  }
});

/* ──────────────────────────── main loop ───────────────────────────── */
let bootstrapped = false;
async function refresh() {
  try {
    const r = await fetch("/api/bootstrap");
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    window.__lastData = d.charts;
    renderHeader(d);
    renderScores(d);
    renderCharts(d.charts);
    renderLayers(d);
    renderWhales(d);
    renderEvents(d);
    renderAlerts(d);
    renderJournal(d);
    bootstrapped = true;
  } catch (err) {
    if (!bootstrapped) console.warn("bootstrap pending…", err);
  }
}

(function init() {
  const dateInput = document.querySelector('input[name="trade_date"]');
  if (dateInput) dateInput.valueAsDate = new Date();
  refresh();
  setInterval(refresh, 5000);
  let rt;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => window.__lastData && renderCharts(window.__lastData), 200);
  });
})();
