/** Shared presentational primitives. No chart library: everything is plain SVG
 *  so the bundle stays small and the overlays (fair value gaps, fib levels,
 *  CISD lines, trade levels) are exactly what this engine needs. */
import React from 'react';

export const fmt = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) || !isFinite(v)
    ? '—'
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export const fmtPct = (v, d = 0) =>
  v === null || v === undefined || !isFinite(v) ? '—' : `${(Number(v) * 100).toFixed(d)}%`;

export const fmtSigned = (v, d = 2) =>
  v === null || v === undefined || !isFinite(v) ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(d)}`;

export const shortTs = (iso) => (iso ? String(iso).slice(0, 16).replace('T', ' ') : '—');

export function Card({ title, right, children, style, className = '' }) {
  return (
    <div className={`card ${className}`} style={style}>
      {(title || right) && (
        <div className="spread" style={{ alignItems: 'center', marginBottom: 8 }}>
          {title && <h4 style={{ margin: 0 }}>{title}</h4>}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function KPI({ label, value, sub, color }) {
  return (
    <div className="card" style={{ padding: '11px 13px' }}>
      <div className="kpi-l">{label}</div>
      <div className="kpi" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="kpi-l" style={{ textTransform: 'none', letterSpacing: 0 }}>{sub}</div>}
    </div>
  );
}

export function Bar({ label, value, max = 100, color = 'var(--gold)', right }) {
  const pct = Math.max(0, Math.min(100, (Number(value) / max) * 100 || 0));
  return (
    <div className="bar-row">
      <div className="bar-top">
        <span>{label}</span>
        <span className="mono">{right ?? fmt(value, 0)}</span>
      </div>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

export function Table({ columns, rows, empty = 'Nothing to show yet.', maxHeight = 420 }) {
  if (!rows || rows.length === 0) return <div className="sub">{empty}</div>;
  return (
    <div className="scroll" style={{ maxHeight }}>
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c.key} style={c.num ? { textAlign: 'right' } : undefined}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? i}>
              {columns.map((c) => (
                <td key={c.key} className={c.num ? 'num mono' : undefined}
                    style={c.color ? { color: c.color(r) } : undefined}>
                  {c.render ? c.render(r) : (r[c.key] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Gate({ ok, children, detail }) {
  return (
    <div style={{ display: 'flex', gap: 9, alignItems: 'flex-start', margin: '7px 0', fontSize: 12.5 }}>
      <span style={{ color: ok ? 'var(--green)' : 'var(--red)', fontWeight: 800 }}>{ok ? '●' : '●'}</span>
      <span style={{ flex: 1 }}>
        {children}
        {detail && <span className="mono" style={{ color: 'var(--muted)', marginLeft: 6 }}>{detail}</span>}
      </span>
    </div>
  );
}

export function Badge({ kind, children }) {
  const cls = { real: 'b-real', proxy: 'b-proxy', synthetic: 'b-synth', wait: 'b-wait', off: 'b-off' }[kind] || 'b-off';
  return <span className={`badge ${cls}`}>{children}</span>;
}

/* ------------------------------------------------------------------ charts */

function useScales({ width, height, xMin, xMax, yMin, yMax, pad }) {
  const sx = (v) => pad.l + ((v - xMin) / Math.max(1e-9, xMax - xMin)) * (width - pad.l - pad.r);
  const sy = (v) => pad.t + (1 - (v - yMin) / Math.max(1e-9, yMax - yMin)) * (height - pad.t - pad.b);
  return { sx, sy };
}

export function LineChart({ series, height = 180, yLabel, zeroLine = true, color = 'var(--gold)' }) {
  const width = 640, pad = { l: 44, r: 10, t: 10, b: 22 };
  const data = (series || []).filter((p) => p && isFinite(p.y));
  if (!data.length) return <div className="sub">No data.</div>;
  const xs = data.map((p) => p.x), ys = data.map((p) => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  if (zeroLine) { yMin = Math.min(yMin, 0); yMax = Math.max(yMax, 0); }
  const span = yMax - yMin || 1;
  yMin -= span * 0.08; yMax += span * 0.08;
  const { sx, sy } = useScales({ width, height, xMin, xMax, yMin, yMax, pad });
  const path = data.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
      {[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const y = pad.t + f * (height - pad.t - pad.b);
        const v = yMax - f * (yMax - yMin);
        return (
          <g key={f}>
            <line x1={pad.l} x2={width - pad.r} y1={y} y2={y} stroke="rgba(255,255,255,0.06)" />
            <text x={pad.l - 6} y={y + 3} textAnchor="end" fontSize="9" fill="#8892a6">{v.toFixed(2)}</text>
          </g>
        );
      })}
      <path d={path} fill="none" stroke={color} strokeWidth="1.8" />
      {zeroLine && yMin < 0 && <line x1={pad.l} x2={width - pad.r} y1={sy(0)} y2={sy(0)} stroke="rgba(255,255,255,0.25)" strokeDasharray="3 3" />}
      {yLabel && <text x={pad.l} y={12} fontSize="9" fill="#8892a6">{yLabel}</text>}
    </svg>
  );
}

export function BarChart({ data, height = 190, colorFor, labelFmt = (v) => v.toFixed(2), yLabel }) {
  const width = 640, pad = { l: 44, r: 10, t: 12, b: 34 };
  if (!data?.length) return <div className="sub">No data.</div>;
  const vals = data.map((d) => d.y).filter((v) => isFinite(v));
  let yMin = Math.min(0, ...vals), yMax = Math.max(0, ...vals);
  const span = yMax - yMin || 1; yMax += span * 0.1; yMin -= span * 0.1;
  const { sy } = useScales({ width, height, xMin: 0, xMax: 1, yMin, yMax, pad });
  const bw = (width - pad.l - pad.r) / data.length;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
      {[0, 0.5, 1].map((f) => {
        const y = pad.t + f * (height - pad.t - pad.b);
        const v = yMax - f * (yMax - yMin);
        return (
          <g key={f}>
            <line x1={pad.l} x2={width - pad.r} y1={y} y2={y} stroke="rgba(255,255,255,0.06)" />
            <text x={pad.l - 6} y={y + 3} textAnchor="end" fontSize="9" fill="#8892a6">{v.toFixed(2)}</text>
          </g>
        );
      })}
      {data.map((d, i) => {
        const y = sy(isFinite(d.y) ? d.y : 0);
        const zero = sy(0);
        return (
          <g key={i}>
            <rect x={pad.l + i * bw + bw * 0.15} y={Math.min(y, zero)}
                  width={bw * 0.7} height={Math.max(1, Math.abs(zero - y))}
                  fill={colorFor ? colorFor(d) : 'var(--gold)'} opacity="0.85" rx="2" />
            <text x={pad.l + i * bw + bw / 2} y={height - 18} textAnchor="middle" fontSize="9" fill="#8892a6">{d.label}</text>
            {isFinite(d.y) && (
              <text x={pad.l + i * bw + bw / 2} y={Math.min(y, zero) - 4} textAnchor="middle" fontSize="9" fill="#e8ecf4">{labelFmt(d.y)}</text>
            )}
          </g>
        );
      })}
      {yLabel && <text x={pad.l} y={12} fontSize="9" fill="#8892a6">{yLabel}</text>}
    </svg>
  );
}

export function Scatter({ points, height = 260, xLabel, yLabel }) {
  const width = 640, pad = { l: 44, r: 12, t: 12, b: 30 };
  const pts = (points || []).filter((p) => isFinite(p.x) && isFinite(p.y));
  if (!pts.length) return <div className="sub">No trades yet.</div>;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const xMin = Math.min(...xs, 0), xMax = Math.max(...xs) * 1.05 || 1;
  const yMin = Math.min(...ys, 0), yMax = Math.max(...ys) * 1.05 || 1;
  const { sx, sy } = useScales({ width, height, xMin, xMax, yMin, yMax, pad });
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
      {[0, 0.5, 1].map((f) => {
        const y = pad.t + f * (height - pad.t - pad.b);
        return <line key={f} x1={pad.l} x2={width - pad.r} y1={y} y2={y} stroke="rgba(255,255,255,0.06)" />;
      })}
      <line x1={pad.l} x2={pad.l} y1={pad.t} y2={height - pad.b} stroke="rgba(255,255,255,0.18)" />
      <line x1={pad.l} x2={width - pad.r} y1={height - pad.b} y2={height - pad.b} stroke="rgba(255,255,255,0.18)" />
      {pts.map((p, i) => (
        <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="3.4"
                fill={p.r >= 0 ? 'rgba(38,166,154,0.75)' : 'rgba(239,83,80,0.75)'} />
      ))}
      <text x={width / 2} y={height - 6} textAnchor="middle" fontSize="10" fill="#8892a6">{xLabel}</text>
      <text x={10} y={height / 2} fontSize="10" fill="#8892a6" transform={`rotate(-90 10 ${height / 2})`}>{yLabel}</text>
    </svg>
  );
}

export function Heatmap({ rows, cols, get, label, colorFor }) {
  return (
    <div>
      <div className="sub" style={{ marginBottom: 6 }}>{label}</div>
      <div style={{ display: 'grid', gridTemplateColumns: `70px repeat(${cols.length}, 1fr)`, gap: 2 }}>
        <div />
        {cols.map((c) => <div key={c} style={{ fontSize: 10, color: 'var(--muted)', textAlign: 'center' }}>{c}</div>)}
        {rows.map((r) => (
          <React.Fragment key={r}>
            <div style={{ fontSize: 10.5, color: 'var(--muted)', paddingRight: 6, textAlign: 'right' }}>{r}</div>
            {cols.map((c) => {
              const v = get(r, c);
              return (
                <div key={c} title={`${r} / ${c}: ${v === null ? '—' : v.toFixed(2)}R`}
                     style={{
                       height: 22, borderRadius: 3, display: 'flex', alignItems: 'center',
                       justifyContent: 'center', fontSize: 9.5,
                       background: v === null ? 'rgba(255,255,255,0.03)' : colorFor(v),
                     }}>
                  {v === null ? '' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
