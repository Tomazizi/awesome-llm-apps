/**
 * Candlestick chart with the engine's structural overlays:
 *   • fair value gaps (imbalances) as translucent boxes until filled
 *   • Fibonacci retracement / extension levels from the primary leg
 *   • CISD trigger levels and liquidity-sweep markers
 *   • live trade levels: entry, stop, TP1 / TP2
 *
 * Hand-rolled SVG: the overlays this engine needs are not stock chart features,
 * and this keeps the bundle dependency-free.
 */
import React, { useMemo, useState } from 'react';

const PAD = { l: 8, r: 62, t: 10, b: 46 };
const VOL_H = 46;

export default function PriceChart({ candles, fvgs = [], legs = [], events = [], trade, showVolume = true }) {
  const [nBars, setNBars] = useState(160);
  const [show, setShow] = useState({ fvg: true, fib: true, cisd: true });

  const view = useMemo(() => {
    const src = (candles || []).filter((c) => c && c.length >= 5);
    return src.length > nBars ? src.slice(-nBars) : src;
  }, [candles, nBars]);

  const geom = useMemo(() => {
    if (!view.length) return null;
    const t0 = view[0][0], t1 = view[view.length - 1][0];
    const lows = view.map((c) => c[3]), highs = view.map((c) => c[2]);
    let lo = Math.min(...lows), hi = Math.max(...highs);
    const padY = (hi - lo) * 0.06 || 1;
    lo -= padY; hi += padY;
    return { t0, t1, lo, hi };
  }, [view]);

  if (!geom) return <div className="sub">No candles loaded.</div>;

  const W = 1000;
  const H = 420 + (showVolume ? VOL_H : 0);
  const priceH = H - (showVolume ? VOL_H : 0) - PAD.b;
  const plotW = W - PAD.l - PAD.r;
  const sx = (t) => PAD.l + ((t - geom.t0) / Math.max(1, geom.t1 - geom.t0)) * plotW;
  const sy = (p) => PAD.t + (1 - (p - geom.lo) / (geom.hi - geom.lo)) * (priceH - PAD.t);
  const bw = Math.max(1.2, plotW / view.length * 0.68);
  const maxVol = Math.max(...view.map((c) => c[5] || 0), 1);

  const primary = legs.find((l) => l.leg === 'primary') || legs[0];
  const fibLines = [];
  if (show.fib && primary) {
    Object.entries(primary.retr_levels || {}).forEach(([ratio, price]) => {
      if (price >= geom.lo && price <= geom.hi) {
        fibLines.push({ price, label: ratio, kind: 'retr' });
      }
    });
    Object.entries(primary.ext_levels || {}).forEach(([ratio, price]) => {
      if (price >= geom.lo && price <= geom.hi) {
        fibLines.push({ price, label: `ext ${ratio}`, kind: 'ext' });
      }
    });
  }

  const gridVals = [0, 0.25, 0.5, 0.75, 1].map((f) => geom.lo + f * (geom.hi - geom.lo));

  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <span className="sub" style={{ marginRight: 6 }}>Bars</span>
        <select style={{ width: 92 }} value={nBars} onChange={(e) => setNBars(+e.target.value)}>
          {[80, 160, 260, 400, 700].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        {['fvg', 'fib', 'cisd'].map((k) => (
          <label key={k} className="pill" style={{ cursor: 'pointer', marginLeft: 10 }}>
            <input type="checkbox" checked={show[k]} onChange={() => setShow((s) => ({ ...s, [k]: !s[k] }))} />
            {k.toUpperCase()}
          </label>
        ))}
        <span className="sub" style={{ marginLeft: 'auto' }}>
          green = bullish imbalance · red = bearish · ◆ = liquidity sweep
        </span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none"
           style={{ background: 'rgba(0,0,0,0.18)', borderRadius: 10, border: '1px solid var(--line)' }}>
        {/* grid + price axis */}
        {gridVals.map((v, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(v)} y2={sy(v)} stroke="rgba(255,255,255,0.05)" />
            <text x={W - PAD.r + 6} y={sy(v) + 3} fontSize="10" fill="#8892a6">{v.toFixed(2)}</text>
          </g>
        ))}

        {/* fair value gaps */}
        {show.fvg && fvgs.map((g, i) => {
          const x = sx(g.ts);
          if (x < PAD.l - 20) return null;
          const end = g.filled_ts ? sx(g.filled_ts) : W - PAD.r;
          return (
            <rect key={i} x={x} y={sy(g.hi)} width={Math.max(2, end - x)}
                  height={Math.max(1.5, sy(g.lo) - sy(g.hi))}
                  fill={g.dir > 0 ? 'rgba(66,165,245,0.13)' : 'rgba(239,83,80,0.12)'} />
          );
        })}

        {/* fibonacci levels */}
        {fibLines.map((f, i) => (
          <g key={`f${i}`}>
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(f.price)} y2={sy(f.price)}
                  stroke={f.kind === 'retr' ? 'rgba(245,197,66,0.55)' : 'rgba(156,204,101,0.45)'}
                  strokeDasharray={f.kind === 'retr' ? '4 4' : '2 5'} strokeWidth="1" />
            <text x={PAD.l + 4} y={sy(f.price) - 3} fontSize="9" fill="#f5c542aa">{f.label}</text>
          </g>
        ))}

        {/* CISD trigger levels + sweeps */}
        {show.cisd && events.map((e, i) => {
          if (e.state === 'INVALIDATED') return null;
          const y = sy(e.cisd_level);
          if (y < PAD.t || y > priceH) return null;
          const sxw = sx(e.sweep_ts);
          return (
            <g key={`c${i}`}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y} y2={y}
                    stroke={e.dir === 'LONG' ? 'rgba(38,166,154,0.75)' : 'rgba(239,83,80,0.7)'} strokeWidth="1.2" />
              <text x={PAD.l + 4} y={y - 3} fontSize="9" fill={e.dir === 'LONG' ? '#4fd1b3' : '#ff8a80'}>
                CISD {e.dir}
              </text>
              {sxw >= PAD.l && (
                <>
                  <path d={`M${sxw},${sy(e.swept_level)} l-5,-8 l10,0 z`} fill={e.dir === 'LONG' ? '#26a69a' : '#ef5350'} />
                  <text x={sxw} y={sy(e.swept_level) + (e.dir === 'LONG' ? 18 : -8)} fontSize="9"
                        textAnchor="middle" fill="#8892a6">sweep</text>
                </>
              )}
            </g>
          );
        })}

        {/* candles */}
        {view.map((c, i) => {
          const [t, o, h, l, close] = c;
          const up = close >= o;
          const col = up ? '#26a69a' : '#ef5350';
          const x = sx(t);
          return (
            <g key={i}>
              <line x1={x} x2={x} y1={sy(h)} y2={sy(l)} stroke={col} strokeWidth="1" opacity="0.85" />
              <rect x={x - bw / 2} y={sy(Math.max(o, close))} width={bw}
                    height={Math.max(1, sy(Math.min(o, close)) - sy(Math.max(o, close)))}
                    fill={up ? '#26a69a' : '#ef5350'} opacity="0.95" />
            </g>
          );
        })}

        {/* volume */}
        {showVolume && view.map((c, i) => {
          const x = sx(c[0]);
          const hgt = ((c[5] || 0) / maxVol) * (VOL_H - 8);
          const up = c[4] >= c[1];
          return (
            <rect key={`v${i}`} x={x - bw / 2} y={priceH + 6 + (VOL_H - 8 - hgt)}
                  width={bw} height={Math.max(0.5, hgt)}
                  fill={up ? 'rgba(38,166,154,0.35)' : 'rgba(239,83,80,0.32)'} />
          );
        })}

        {/* trade levels */}
        {trade && [
          { p: trade.entry, c: '#42a5f5', label: `ENTRY ${trade.entry.toFixed(2)}` },
          { p: trade.stop, c: '#ef5350', label: `STOP ${trade.stop.toFixed(2)}` },
          { p: trade.tp1, c: '#26a69a', label: `TP1 ${trade.tp1.toFixed(2)}` },
          { p: trade.tp2, c: '#26a69a', label: `TP2 ${trade.tp2.toFixed(2)}` },
        ].filter((l) => l.p && l.p >= geom.lo && l.p <= geom.hi).map((l, i) => (
          <g key={`t${i}`}>
            <line x1={PAD.l} x2={W - PAD.r} y1={sy(l.p)} y2={sy(l.p)} stroke={l.c} strokeWidth="1.6" />
            <rect x={W - PAD.r + 2} y={sy(l.p) - 7} width={54} height={14} rx="3" fill={l.c} opacity="0.9" />
            <text x={W - PAD.r + 5} y={sy(l.p) + 3} fontSize="8.5" fill="#0a0d14" fontWeight="700">{l.label}</text>
          </g>
        ))}

        {/* time axis */}
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
          const t = geom.t0 + f * (geom.t1 - geom.t0);
          const d = new Date(t);
          return (
            <text key={i} x={sx(t)} y={H - 12} fontSize="9.5" fill="#8892a6" textAnchor="middle">
              {`${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:00`}
            </text>
          );
        })}
      </svg>
    </div>
  );
}
