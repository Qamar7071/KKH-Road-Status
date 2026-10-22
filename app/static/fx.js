/* KKH hero photo effects (Passu Cones on the KKH).
   The painted centre-line was removed from the photo (scripts/build_hero.py); this canvas
   redraws it in the photo's own perspective and moves it toward the viewer, so the road
   in a real photograph appears to roll by. On top: rain or snow from the selected
   segment's forecast, and mist drifting across the peaks. Traffic slows as risk rises.
   Coordinates are in the photo's pixel space (750 x 880). */
(() => {
  "use strict";
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const IMG_W = 750, IMG_H = 880;
  // Road centre-line, far -> near (y ascending), measured on the photo
  const PATH = [[301, 791], [306, 798], [315, 807.5], [323, 815], [330, 821.5], [337.5, 828], [348.5, 837], [364, 852], [392, 876], [400, 885]];
  const HORIZON_Y = 760, K = 120, PERIOD = 0.25, DASH = 0.115, Y_MIN = 803;

  const xAt = (y) => {
    for (let i = 1; i < PATH.length; i++) {
      if (y <= PATH[i][1]) { const [x0, y0] = PATH[i - 1], [x1, y1] = PATH[i]; return x0 + ((y - y0) / (y1 - y0 || 1)) * (x1 - x0); }
    }
    return PATH[PATH.length - 1][0];
  };
  const yAtZ = (z) => HORIZON_Y + K / z;
  const halfW = (y) => Math.max(0.6, (y - HORIZON_Y) * 0.043);

  class FX {
    constructor(host) {
      this.host = host;
      this.cv = host.querySelector("canvas");
      this.ctx = this.cv.getContext("2d");
      this.cond = { rain: 0, snow: 0, risk: 0 };
      this.phase = 0; this.parts = []; this.mist = []; this.visible = true; this.running = false; this.last = 0;
      new ResizeObserver(() => this.resize()).observe(host);
      new IntersectionObserver((e) => { this.visible = e[0].isIntersecting; if (this.visible) this.loop(); }).observe(host);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) this.loop(); });
      this.resize();
    }
    set(cond) {
      this.cond = { ...this.cond, ...cond };
      this.host.style.setProperty("--storm", Math.min(1, this.cond.risk * 1.2 + (this.cond.rain > 8 ? 0.25 : 0)).toFixed(2));
      this.spawn(); this.frame(0); this.loop();
    }
    resize() {
      const r = this.cv.getBoundingClientRect();
      if (!r.width) return;
      this.dpr = Math.min(2, window.devicePixelRatio || 1);
      this.W = r.width; this.H = r.height;
      this.cv.width = Math.round(r.width * this.dpr); this.cv.height = Math.round(r.height * this.dpr);
      // object-fit: cover mapping from photo pixels to the box
      this.s = Math.max(this.W / IMG_W, this.H / IMG_H);
      // honour CSS object-position (the phone layout crops toward the road)
      const pos = getComputedStyle(this.host.querySelector("img")).objectPosition.split(" ").map((v) => (v.endsWith("%") ? parseFloat(v) / 100 : 0.5));
      this.ox = (this.W - IMG_W * this.s) * (pos[0] ?? 0.5); this.oy = (this.H - IMG_H * this.s) * (pos[1] ?? 0.5);
      this.spawn(); this.frame(0);
    }
    spawn() {
      if (!this.W) return;
      const { W, H } = this, c = this.cond;
      const nRain = Math.min(380, Math.round(c.rain * 8)), nSnow = Math.min(240, Math.round(c.snow * 12));
      this.parts = [];
      for (let i = 0; i < nRain; i++) this.parts.push({ k: "r", x: Math.random() * W, y: Math.random() * H, v: 10 + Math.random() * 8, l: 10 + Math.random() * 12 });
      for (let i = 0; i < nSnow; i++) this.parts.push({ k: "s", x: Math.random() * W, y: Math.random() * H, v: 0.5 + Math.random(), r: 0.8 + Math.random() * 2, w: Math.random() * 6.3 });
      const nMist = 4 + Math.round(c.risk * 6 + (c.rain > 5 ? 3 : 0));
      this.mist = Array.from({ length: nMist }, () => ({ x: Math.random() * W, y: (140 + Math.random() * 220) * this.s + this.oy, r: (60 + Math.random() * 110) * this.s, v: 0.05 + Math.random() * 0.12, a: 0.12 + Math.random() * 0.22 }));
    }
    P(x, y) { return [this.ox + x * this.s, this.oy + y * this.s]; }

    frame(dt) {
      if (!this.W) return;
      const ctx = this.ctx, d = this.dpr, { W, H } = this;
      ctx.setTransform(d, 0, 0, d, 0, 0);
      ctx.clearRect(0, 0, W, H);
      // mist over the peaks
      for (const m of this.mist) {
        m.x += m.v * dt * 0.05; if (m.x - m.r > W) m.x = -m.r;
        const g = ctx.createRadialGradient(m.x, m.y, 0, m.x, m.y, m.r);
        g.addColorStop(0, `rgba(255,255,255,${m.a})`); g.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = g; ctx.fillRect(m.x - m.r, m.y - m.r, m.r * 2, m.r * 2);
      }
      // centre-line dashes rolling toward the viewer; traffic slows as risk rises
      const speed = 0.0011 * (1 - Math.min(0.85, this.cond.risk * 1.1));
      this.phase = (this.phase + dt * speed) % 1;
      ctx.fillStyle = "rgba(238,238,232,.93)";
      for (let n = -1; n < 12; n++) {
        const z1 = 1 + (n + 1 - this.phase) * PERIOD - PERIOD, z2 = z1 + DASH;
        if (z2 <= 0.35) continue;
        const y1 = yAtZ(Math.max(0.35, z1)), y2 = yAtZ(z2);
        if (y2 < Y_MIN) break;
        const yA = Math.min(y1, IMG_H + 40), yB = Math.max(y2, Y_MIN);
        if (yA <= yB) continue;
        const fade = Math.min(1, (yB - Y_MIN) / 9);
        ctx.globalAlpha = fade;
        const [ax, ay] = this.P(xAt(yA), yA), [bx, by] = this.P(xAt(yB), yB);
        const wa = halfW(yA) * this.s, wb = halfW(yB) * this.s;
        ctx.beginPath(); ctx.moveTo(ax - wa, ay); ctx.lineTo(ax + wa, ay); ctx.lineTo(bx + wb, by); ctx.lineTo(bx - wb, by); ctx.closePath(); ctx.fill();
      }
      ctx.globalAlpha = 1;
      // rain / snow
      ctx.lineCap = "round";
      for (const p of this.parts) {
        if (p.k === "r") {
          p.y += p.v * dt * 0.06; p.x -= p.v * dt * 0.012;
          if (p.y > H) { p.y = -20; p.x = Math.random() * (W + 60); }
          ctx.strokeStyle = "rgba(220,232,245,.55)"; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x - p.l * 0.2, p.y + p.l); ctx.stroke();
        } else {
          p.w += dt * 0.002; p.y += p.v * dt * 0.05; p.x += Math.sin(p.w) * 0.35;
          if (p.y > H) { p.y = -6; p.x = Math.random() * W; }
          ctx.fillStyle = "rgba(255,255,255,.9)"; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
        }
      }
    }
    loop() {
      if (reduce || this.running) return;
      this.running = true; this.last = 0;
      const step = (ts) => {
        if (!this.visible || document.hidden) { this.running = false; return; }
        const dt = this.last ? Math.min(50, ts - this.last) : 16;
        this.last = ts; this.frame(dt);
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
  }
  window.KKHFX = { inst: null, mount(host) { this.inst = new FX(host); return this.inst; } };
})();
