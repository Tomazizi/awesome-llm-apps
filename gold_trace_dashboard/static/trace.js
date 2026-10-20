/* ═══════════════ Gold Trace — hero canvas animation ═══════════════════
   Stage machine (loops forever):
     1 GROW   recursive branching — randomized angles & lengths, timed
              segment extension, node budget capped for phones
     2 STRIKE lightning bolt from cloud line down through the highest
              branch tip, staged flicker frames, branches flash
     3 WEAVE  spider hub appears at the trunk crown: 6 satellites on
              drifting orbits, spokes + web rings, pulse waves
     4 TRACE  data motes ride the branch paths (gold flowing through
              the layers), overlay label chips fade in/out
   Stage-specific captions render into #stage-caption.
   Node budget: desktop ≤ 200 segments / depth 5, phone ≤ 110 / depth 4.
   prefers-reduced-motion → one static composed frame.                  */

(() => {
  const canvas = document.getElementById("trace-canvas");
  const captionEl = document.getElementById("stage-caption");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let W = 0, H = 0, DPR = 1;
  let phone = false;

  // ── stage timing (seconds) ──────────────────────────────────────────
  const STAGES = [
    { name: "grow",   dur: 6.5, caption: "stage 1 · grow — recursive branches, randomized angles & lengths" },
    { name: "strike", dur: 2.6, caption: "stage 2 · strike — the bolt finds the crown, every layer lights up" },
    { name: "weave",  dur: 4.5, caption: "stage 3 · weave — the spider hub ties live, daily & slow layers together" },
    { name: "trace",  dur: 4.5, caption: "stage 4 · trace — gold flows through all ten layers" },
  ];
  const LOOP = STAGES.reduce((a, s) => a + s.dur, 0);

  // ── tree state ──────────────────────────────────────────────────────
  let segs = [];          // {x1,y1,x2,y2,w,depth,growAt,len}
  let tips = [];
  let crownTip = null;
  let treeSeed = 1;

  function buildTree() {
    segs = []; tips = [];
    const maxDepth = phone ? 4 : 5;
    const budget = phone ? 110 : 200;
    const rootX = W * (0.16 + ((treeSeed * 37) % 10) / 60);
    const rootY = H + 6;
    const trunkLen = H * (phone ? 0.42 : 0.46);
    let growClock = 0;

    function branch(x, y, angle, len, width, depth) {
      if (depth > maxDepth || segs.length >= budget || len < 6) return;
      const x2 = x + Math.cos(angle) * len;
      const y2 = y + Math.sin(angle) * len;
      segs.push({ x1: x, y1: y, x2, y2, w: width, depth,
                  growAt: growClock, len });
      growClock += phone ? 0.055 : 0.045;
      if (depth === maxDepth || segs.length >= budget) {
        tips.push({ x: x2, y: y2 });
        return;
      }
      const n = Math.random() < 0.25 ? 3 : 2;
      for (let i = 0; i < n; i++) {
        const spread = 0.42 + Math.random() * 0.5;         // randomized angles
        const dir = i === 0 ? -1 : (i === 1 ? 1 : (Math.random() < 0.5 ? -1 : 1));
        const a = angle + dir * spread * (0.6 + Math.random() * 0.7);
        const l = len * (0.62 + Math.random() * 0.18);     // randomized lengths
        branch(x2, y2, a, l, Math.max(0.6, width * 0.62), depth + 1);
      }
    }
    branch(rootX, rootY, -Math.PI / 2 + (Math.random() - 0.5) * 0.18,
           trunkLen, phone ? 4 : 5.5, 0);
    tips.sort((a, b) => a.y - b.y);
    crownTip = tips[0] || { x: rootX, y: rootY - trunkLen };
  }

  // ── lightning ───────────────────────────────────────────────────────
  let boltPts = [];
  let boltFlash = 0;

  function buildBolt() {
    boltPts = [];
    const tx = crownTip.x, ty = crownTip.y;
    const sx = tx + (Math.random() - 0.5) * W * 0.35;
    const steps = phone ? 9 : 13;
    for (let i = 0; i <= steps; i++) {
      const p = i / steps;
      const jitter = i === 0 || i === steps ? 0 : (Math.random() - 0.5) * 46 * (1 - p * 0.4);
      boltPts.push({ x: sx + (tx - sx) * p + jitter, y: -8 + (ty + 8) * p });
    }
    // small forks
    if (!phone && boltPts.length > 6) {
      const f = boltPts[4];
      boltPts.splice(5, 0, { x: f.x + 34, y: f.y + 26, fork: true },
                           { x: f.x + 8, y: f.y + 12, fork: true });
    }
  }

  // ── spider hub ──────────────────────────────────────────────────────
  const hub = { sats: [], appear: 0 };
  function buildHub() {
    hub.sats = [];
    const n = phone ? 5 : 6;
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * Math.PI * 2 + Math.random() * 0.4;
      hub.sats.push({
        a: a0, r: (phone ? 34 : 48) + Math.random() * 26,
        speed: (0.25 + Math.random() * 0.3) * (Math.random() < 0.5 ? -1 : 1),
        wobble: Math.random() * Math.PI * 2,
      });
    }
  }

  // ── trace motes + label chips (stage 4) ────────────────────────────
  const LABELS = ["XAU $4,085", "SGE +$34", "GLD +6.2 t", "COMEX 24.8 Moz",
                  "PAXG 🐋 12k oz", "COT +198k", "CB +42 t/mo", "TIPS 1.86%"];
  let motes = [], chips = [];
  function buildTrace() {
    motes = [];
    const n = phone ? 14 : 26;
    for (let i = 0; i < n; i++) {
      const s = segs[Math.floor(Math.random() * segs.length)];
      motes.push({ seg: s, p: Math.random(), speed: 0.25 + Math.random() * 0.5 });
    }
    chips = [];
    const cn = phone ? 3 : 5;
    for (let i = 0; i < cn; i++) {
      const t = tips[Math.floor(Math.random() * tips.length)] || crownTip;
      chips.push({ x: t.x, y: t.y - 12 - Math.random() * 26,
                   text: LABELS[(treeSeed * 3 + i) % LABELS.length],
                   phase: Math.random() * Math.PI * 2 });
    }
  }

  // ── sizing ──────────────────────────────────────────────────────────
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    W = rect.width; H = rect.height;
    phone = W < 640;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    regenerate();
  }

  function regenerate() {
    treeSeed++;
    buildTree(); buildHub(); buildBolt(); buildTrace();
  }

  // ── drawing ─────────────────────────────────────────────────────────
  function drawTree(t, stageIdx, stageT) {
    const growWindow = STAGES[0].dur;
    for (const s of segs) {
      // staged growth: segment extends when its growAt time is reached
      let g = stageIdx > 0 ? 1 : Math.min(1, Math.max(0, (stageT - s.growAt) / 0.55));
      if (g <= 0) continue;
      const x = s.x1 + (s.x2 - s.x1) * g;
      const y = s.y1 + (s.y2 - s.y1) * g;
      const fadeDepth = 1 - s.depth * 0.13;
      let glow = 0;
      if (stageIdx === 1) glow = boltFlash * 0.9;                 // strike flash
      if (stageIdx === 3) glow = 0.12 + 0.1 * Math.sin(t * 3 + s.growAt * 9);
      ctx.strokeStyle = `rgba(${212 + glow * 43 | 0}, ${166 + glow * 60 | 0}, ${58 + glow * 90 | 0}, ${0.5 * fadeDepth + glow * 0.45})`;
      ctx.lineWidth = s.w * (0.55 + 0.45 * g);
      ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(s.x1, s.y1); ctx.lineTo(x, y); ctx.stroke();
    }
    // glowing tips once grown
    const tipGlow = stageIdx >= 1 ? 1 : Math.min(1, stageT / growWindow);
    for (const tp of tips) {
      const a = (0.25 + 0.2 * Math.sin(t * 2.4 + tp.x)) * tipGlow;
      ctx.fillStyle = `rgba(240, 198, 69, ${a})`;
      ctx.beginPath(); ctx.arc(tp.x, tp.y, phone ? 1.6 : 2.2, 0, 7); ctx.fill();
    }
  }

  function drawBolt(stageT) {
    // staged frame-rendering: bolt draws segment-by-segment, then flickers
    const drawP = Math.min(1, stageT / 0.5);
    const flick = stageT < 0.75 ? (Math.sin(stageT * 70) > -0.2 ? 1 : 0.15)
                : Math.max(0, 1 - (stageT - 0.75) / 1.6) * (0.55 + 0.45 * Math.sin(stageT * 33));
    boltFlash = flick;
    if (flick <= 0.02) return;
    const n = Math.max(2, Math.floor(boltPts.length * drawP));
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const pass of [0, 1]) {
      ctx.strokeStyle = pass === 0
        ? `rgba(180, 210, 255, ${0.28 * flick})`
        : `rgba(255, 252, 235, ${0.95 * flick})`;
      ctx.lineWidth = pass === 0 ? (phone ? 6 : 9) : (phone ? 1.4 : 2);
      ctx.beginPath();
      ctx.moveTo(boltPts[0].x, boltPts[0].y);
      for (let i = 1; i < n; i++) ctx.lineTo(boltPts[i].x, boltPts[i].y);
      ctx.stroke();
    }
    // impact burst at crown
    if (stageT > 0.42 && stageT < 1.3) {
      const rp = (stageT - 0.42) / 0.9;
      ctx.strokeStyle = `rgba(255, 240, 190, ${(1 - rp) * 0.8 * flick})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(crownTip.x, crownTip.y, rp * (phone ? 46 : 74), 0, 7);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawHub(t, stageIdx, stageT) {
    const visible = stageIdx >= 2;
    hub.appear += ((visible ? 1 : 0) - hub.appear) * 0.06;
    if (hub.appear < 0.02) return;
    const cx = crownTip.x, cy = crownTip.y;
    const a = hub.appear;
    ctx.save();
    // web rings
    for (let ring = 1; ring <= 2; ring++) {
      ctx.strokeStyle = `rgba(240, 198, 69, ${0.10 * a})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, ring * (phone ? 26 : 38) + Math.sin(t * 1.4 + ring) * 3, 0, 7);
      ctx.stroke();
    }
    // satellites + spokes
    const pts = hub.sats.map((s, i) => {
      const ang = s.a + t * s.speed;
      const r = s.r * (stageIdx === 2 ? Math.min(1, stageT / 1.2) : 1);
      return { x: cx + Math.cos(ang) * r, y: cy + Math.sin(ang) * r * 0.72, i };
    });
    for (const p of pts) {
      ctx.strokeStyle = `rgba(200, 170, 90, ${0.30 * a})`;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p.x, p.y); ctx.stroke();
    }
    // neighbour threads (the web)
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i], q = pts[(i + 1) % pts.length];
      ctx.strokeStyle = `rgba(200, 170, 90, ${0.16 * a})`;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke();
    }
    // pulse wave travelling out the spokes
    const pulse = (t * 0.9) % 1;
    for (const p of pts) {
      const px = cx + (p.x - cx) * pulse, py = cy + (p.y - cy) * pulse;
      ctx.fillStyle = `rgba(255, 226, 130, ${0.55 * a * (1 - pulse)})`;
      ctx.beginPath(); ctx.arc(px, py, 1.7, 0, 7); ctx.fill();
    }
    // satellite nodes
    for (const p of pts) {
      ctx.fillStyle = `rgba(240, 198, 69, ${0.85 * a})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, phone ? 2.2 : 3, 0, 7); ctx.fill();
    }
    // hub core — the spider
    const breathe = 1 + 0.14 * Math.sin(t * 2.6);
    ctx.fillStyle = `rgba(255, 236, 170, ${0.95 * a})`;
    ctx.beginPath(); ctx.arc(cx, cy, (phone ? 3.6 : 5) * breathe, 0, 7); ctx.fill();
    ctx.strokeStyle = `rgba(255, 236, 170, ${0.3 * a})`;
    ctx.beginPath(); ctx.arc(cx, cy, (phone ? 8 : 11) * breathe, 0, 7); ctx.stroke();
    ctx.restore();
  }

  function drawTraceMotes(t, stageIdx, stageT) {
    if (stageIdx < 3) return;
    const a = Math.min(1, stageT / 0.8);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const m of motes) {
      m.p += m.speed * 0.016;
      if (m.p > 1) { m.p = 0; m.seg = segs[Math.floor(Math.random() * segs.length)] || m.seg; }
      const s = m.seg; if (!s) continue;
      const x = s.x1 + (s.x2 - s.x1) * m.p;
      const y = s.y1 + (s.y2 - s.y1) * m.p;
      ctx.fillStyle = `rgba(255, 228, 140, ${0.8 * a})`;
      ctx.beginPath(); ctx.arc(x, y, 1.6, 0, 7); ctx.fill();
    }
    ctx.restore();
    // overlay label chips — balanced font size, few nodes on phones
    ctx.save();
    ctx.font = `${phone ? 9 : 10.5}px ui-monospace, Menlo, monospace`;
    for (const c of chips) {
      const vis = (Math.sin(t * 0.9 + c.phase) + 1) / 2;   // fade in/out
      if (vis < 0.25) continue;
      const alpha = Math.min(1, (vis - 0.25) / 0.5) * a;
      const w = ctx.measureText(c.text).width + 14;
      const yy = c.y + Math.sin(t * 1.3 + c.phase) * 4;
      ctx.fillStyle = `rgba(15, 18, 27, ${0.82 * alpha})`;
      ctx.strokeStyle = `rgba(240, 198, 69, ${0.4 * alpha})`;
      ctx.lineWidth = 1;
      roundRect(ctx, c.x - w / 2, yy - 9, w, 18, 6);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = `rgba(240, 198, 69, ${alpha})`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(c.text, c.x, yy + 0.5);
    }
    ctx.restore();
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  function drawStars(t) {
    // faint dust so the empty sky isn't dead
    ctx.fillStyle = "rgba(233, 230, 221, 0.16)";
    for (let i = 0; i < (phone ? 26 : 48); i++) {
      const x = ((i * 97.3 + treeSeed * 31) % W);
      const y = ((i * 57.7 + treeSeed * 17) % (H * 0.7));
      const tw = 0.5 + 0.5 * Math.sin(t * 1.7 + i * 2.3);
      ctx.globalAlpha = 0.10 + 0.14 * tw;
      ctx.fillRect(x, y, 1.3, 1.3);
    }
    ctx.globalAlpha = 1;
  }

  // ── main loop ───────────────────────────────────────────────────────
  let startTime = performance.now() / 1000;
  let lastStageIdx = -1;

  function frame(nowMs) {
    const t = nowMs / 1000;
    const elapsed = (t - startTime) % LOOP;
    let acc = 0, stageIdx = 0, stageT = elapsed;
    for (let i = 0; i < STAGES.length; i++) {
      if (elapsed < acc + STAGES[i].dur) { stageIdx = i; stageT = elapsed - acc; break; }
      acc += STAGES[i].dur;
    }
    if (stageIdx !== lastStageIdx) {
      lastStageIdx = stageIdx;
      if (captionEl) captionEl.textContent = STAGES[stageIdx].caption;
      if (stageIdx === 0) { regenerate(); }          // new tree each loop
      if (stageIdx === 1) { buildBolt(); }
      if (stageIdx === 3) { buildTrace(); }
    }

    ctx.clearRect(0, 0, W, H);
    drawStars(t);
    drawTree(t, stageIdx, stageT);
    if (stageIdx === 1) drawBolt(stageT);
    drawHub(t, stageIdx, stageT);
    drawTraceMotes(t, stageIdx, stageT);

    if (!reduced && !document.hidden) requestAnimationFrame(frame);
  }

  // reduced motion: one composed static frame
  function staticFrame() {
    const t = 3.0;
    ctx.clearRect(0, 0, W, H);
    drawStars(t);
    drawTree(t, 3, 1);
    hub.appear = 1; drawHub(t, 3, 1);
    buildTrace(); drawTraceMotes(t, 3, 1);
    if (captionEl) captionEl.textContent = "gold trace — static composition (reduced motion)";
  }

  window.addEventListener("resize", () => {
    resize();
    if (reduced) staticFrame();
  });
  resize();
  if (reduced) staticFrame();
  else requestAnimationFrame(frame);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !reduced) {
      startTime = performance.now() / 1000;   // restart loop timing cleanly
      requestAnimationFrame(frame);
    }
  });
})();
