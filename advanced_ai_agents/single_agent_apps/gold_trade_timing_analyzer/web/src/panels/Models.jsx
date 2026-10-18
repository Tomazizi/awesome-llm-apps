/** Model detail tab: one card per statistical model, with the actual numbers
 *  driving the score rather than a black-box verdict. */
import React from 'react';
import { Card, KPI, Bar, Table, LineChart, fmt, fmtPct, fmtSigned } from '../ui.jsx';

function tcisdBlock(a) {
  const m = a.models.tcisd || {};
  return (
    <Card title="tCISD — time-decayed Change in State of Delivery">
      <div className="sub" style={{ marginBottom: 10 }}>
        A liquidity sweep beyond a swing pivot, then a displacement candle that closes through the
        last opposing candle's open, confirmed by a market structure shift. The signal decays with
        time (half-life) and with price distance from the CISD level.
      </div>
      <div className="grid g4">
        <KPI label="Long" value={fmt(m.long, 0)} color="var(--green)" sub="/100" />
        <KPI label="Short" value={fmt(m.short, 0)} color="var(--red)" sub="/100" />
        <KPI label="Bars since" value={fmt(m.bars_since, 0)} sub={`expiry ${m.expiry} bars`} />
        <KPI label="CISD level" value={fmt(m.level)} sub={`half-life ${fmt(m.half_life, 1)}`} />
      </div>
      <div className="grid g3" style={{ marginTop: 10 }}>
        <KPI label="Events detected" value={m.event_count ?? '—'} />
        <KPI label="Still active" value={m.active_events ?? '—'} />
        <KPI label="Open FVGs" value={m.open_fvg ?? '—'} />
      </div>
      <h3 className="sect">Recent events</h3>
      <Table
        columns={[
          { key: 'dir', label: 'Dir', render: (r) => <span className={r.dir === 'LONG' ? 'long' : 'short'}>{r.dir}</span> },
          { key: 'sweep_ts', label: 'Sweep', render: (r) => <span className="mono">{String(new Date(r.sweep_ts).toISOString()).slice(5, 16).replace('T', ' ')}</span> },
          { key: 'swept_level', label: 'Swept', num: true, render: (r) => fmt(r.swept_level) },
          { key: 'cisd_level', label: 'CISD lvl', num: true, render: (r) => fmt(r.cisd_level) },
          { key: 'body_atr', label: 'Body ATR', num: true, render: (r) => fmt(r.body_atr) },
          { key: 'quality', label: 'Quality', num: true, render: (r) => fmt(r.quality, 2) },
          { key: 'mss', label: 'MSS', render: (r) => (r.mss ? '✓' : '—') },
          { key: 'state', label: 'State', render: (r) => <span className={r.state === 'ACTIVE' ? 'up' : 'down'}>{r.state}</span> },
        ]}
        rows={(a.structure?.events || []).slice().reverse()}
        empty="No CISD events formed on this sample."
        maxHeight={300}
      />
    </Card>
  );
}

function fibBlock(a) {
  const m = a.models.fib || {};
  return (
    <Card title="Fibonacci — location inside the leg">
      <div className="sub" style={{ marginBottom: 10 }}>
        Retracement of the dominant swing leg, scored for being inside the optimal trade entry
        zone, plus confluence with nearby structural levels.
      </div>
      <div className="grid g4">
        <KPI label="Retracement" value={fmtPct(m.retr)} sub="of primary leg" />
        <KPI label="Long" value={fmt(m.long, 0)} color="var(--green)" />
        <KPI label="Short" value={fmt(m.short, 0)} color="var(--red)" />
        <KPI label="OTE" value={m.retr >= (m.ote?.[0] ?? 0.618) && m.retr <= (m.ote?.[1] ?? 0.786) ? 'INSIDE' : 'outside'}
             color={m.retr >= (m.ote?.[0] ?? 0.618) && m.retr <= (m.ote?.[1] ?? 0.786) ? 'var(--gold)' : 'var(--muted)'}
             sub={`${fmtPct(m.ote?.[0])}–${fmtPct(m.ote?.[1])}`} />
      </div>
      <h3 className="sect">Legs</h3>
      <Table
        columns={[
          { key: 'leg', label: 'Leg' },
          { key: 'dir', label: 'Dir', render: (r) => <span className={r.dir === 'up' ? 'long' : 'short'}>{r.dir}</span> },
          { key: 'anchor', label: 'Anchor', num: true, render: (r) => fmt(r.anchor) },
          { key: 'tip', label: 'Tip', num: true, render: (r) => fmt(r.tip) },
          { key: 'retr', label: 'Retr', num: true, render: (r) => fmt(r.retr, 3) },
          { key: 'in_ote', label: 'OTE', render: (r) => (r.in_ote ? '✓' : '—') },
          { key: 'size', label: 'Size', num: true, render: (r) => fmt(r.size) },
        ]}
        rows={m.legs || []}
        empty="No swing legs identified."
        maxHeight={220}
      />
    </Card>
  );
}

function mnBlock(a) {
  const m = a.models.market_neutral || {};
  const betas = Object.entries(m.betas || {});
  return (
    <Card title="Market-neutral model — is gold mispriced against its drivers?">
      <div className="sub" style={{ marginBottom: 10 }}>
        Gold is regressed on a real-yield proxy (log IEF/TIP), the dollar, the equity-complex,
        volatility, silver, crude and copper. Rolling, scale-aware ridge with an RLS follow —
        the residual is what is left <i>after</i> the macro has been hedged out.
      </div>
      <div className="grid g4">
        <KPI label="Residual z" value={fmt(m.z)} color={Math.abs(m.z ?? 0) > 1.5 ? 'var(--gold)' : undefined}
             sub="stretched vs fair value" />
        <KPI label="Fair-value z" value={fmt(m.fv_z)} sub="RLS fair value" />
        <KPI label="R²" value={fmt(m.r2)} sub={`${m.factors?.length ?? 0} factors`} />
        <KPI label="Mean-rev weight" value={fmtPct(m.w_meanrev)} sub="vs trend component" />
      </div>
      <div className="grid g4" style={{ marginTop: 10 }}>
        <KPI label="Half-life" value={fmt(m.halflife, 1)} sub="bars" />
        <KPI label="Hurst" value={fmt(m.hurst)} sub={m.hurst_level} />
        <KPI label="ADF t-stat" value={fmt(m.adf_t)} sub={`p ${fmt(m.adf_p, 3)}`} />
        <KPI label="Long score" value={fmt(m.long, 0)} sub="/100" />
      </div>
      <h3 className="sect">Factor betas</h3>
      <div className="grid" style={{ gap: 4 }}>
        {betas.length === 0 && <div className="sub">No factors available — load the macro universe.</div>}
        {betas.map(([k, v]) => (
          <Bar key={k} label={k} value={v} max={Math.max(0.2, ...betas.map(([, b]) => Math.abs(b || 0)))}
               color={v >= 0 ? 'var(--green)' : 'var(--red)'} right={fmtSigned(v, 3)} />
        ))}
      </div>
    </Card>
  );
}

function ssmtBlock(a) {
  const m = a.models.ssmt || {};
  const bd = m.breakdown || [];
  return (
    <Card title="SSMT — intermarket divergence">
      <div className="sub" style={{ marginBottom: 10 }}>
        Compares comparable swing legs across gold and its correlated complex. When silver or the
        dollar makes a higher high while gold does not, gold is lagging and the move is suspect.
      </div>
      <div className="grid g4">
        <KPI label="Divergence" value={fmt(m.div)} sub={m.div > 0 ? 'gold leading' : m.div < 0 ? 'gold lagging' : 'aligned'} />
        <KPI label="Rel. strength" value={fmt(m.rel_strength)} />
        <KPI label="Long" value={fmt(m.long, 0)} color="var(--green)" />
        <KPI label="Short" value={fmt(m.short, 0)} color="var(--red)" />
      </div>
      <h3 className="sect">Per-proxy breakdown</h3>
      <Table
        columns={Object.keys(bd[0] || {}).slice(0, 8).map((k) => ({
          key: k, label: k, num: typeof bd[0][k] === 'number',
          render: (r) => (typeof r[k] === 'number' ? fmt(r[k], 3) : String(r[k] ?? '—')),
        }))}
        rows={bd}
        empty="No correlated proxies loaded."
        maxHeight={260}
      />
    </Card>
  );
}

function reversalBlock(a) {
  const m = a.models.reversal || {};
  const coefs = m.coefficients || [];
  const passed = (m.risk ?? 1) < (m.gate ?? 0.75);
  return (
    <Card title="Reversal risk / exhaustion / divergence">
      <div className="sub" style={{ marginBottom: 10 }}>
        An L2 logistic model, refit walk-forward, estimates the probability that price moves
        against the trade by at least {fmt(m.move_atr, 1)} ATR within {m.horizon} bars. Trained
        only on data strictly before the evaluation window, so the reported AUC is out-of-sample.
      </div>
      <div className="grid g4">
        <KPI label="Reversal risk" value={fmtPct(m.risk)}
             color={passed ? 'var(--green)' : 'var(--red)'}
             sub={passed ? 'below gate' : `above gate ${fmtPct(m.gate)}`} />
        <KPI label="OOS AUC" value={fmt(m.auc, 3)} sub={`${m.n_oos ?? 0} out-of-sample rows`} />
        <KPI label="Base rate" value={fmtPct(m.base_rate)} sub="unconditional" />
        <KPI label="Horizon" value={`${m.horizon ?? '—'} bars`} sub={`${fmt(m.move_atr, 1)} ATR move`} />
      </div>
      <h3 className="sect">Coefficients</h3>
      <div className="grid" style={{ gap: 4 }}>
        {coefs.length === 0 && <div className="sub">Model not trained yet.</div>}
        {coefs.map((c) => (
          <Bar key={c.feature} label={c.feature} value={c.coef}
               max={Math.max(0.1, ...coefs.map((x) => Math.abs(x.coef || 0)))}
               color={c.coef >= 0 ? 'var(--red)' : 'var(--green)'} right={fmtSigned(c.coef, 3)} />
        ))}
      </div>
      {coefs.length > 0 && (
        <div className="sub" style={{ marginTop: 6 }}>
          Positive coefficients raise reversal risk. Negatives (trend alignment, momentum
          confirmation) are what make a continuation entry defensible.
        </div>
      )}

      <h3 className="sect">Exhaustion & divergence</h3>
      <div className="grid g4">
        <KPI label="Exhaustion up" value={fmt(a.models.exhaustion?.up, 0)} sub="climax risk longs" />
        <KPI label="Exhaustion down" value={fmt(a.models.exhaustion?.down, 0)} sub="climax risk shorts" />
        <KPI label="Bear divergence" value={fmt(a.models.exhaustion?.div_bear, 0)} />
        <KPI label="Bull divergence" value={fmt(a.models.exhaustion?.div_bull, 0)} />
      </div>
      <div className="grid g4" style={{ marginTop: 10 }}>
        <KPI label="ATR ratio" value={fmt(a.models.exhaustion?.atr_ratio)} sub="current / baseline" />
        <KPI label="Stacked FVG up" value={a.models.exhaustion?.stack_up ?? '—'} />
        <KPI label="Stacked FVG down" value={a.models.exhaustion?.stack_down ?? '—'} />
        <KPI label="Gate" value={fmtPct(m.gate)} sub="veto threshold" />
      </div>
    </Card>
  );
}

function seriesBlock(a, col, label) {
  const p = a.panel || {};
  const ts = p.ts || [];
  return (
    <Card title={label}>
      <LineChart
        series={ts.map((t, i) => ({ x: i, y: p[col]?.[i] }))}
        yLabel={col}
        color="var(--gold)"
      />
      <div className="sub">Latest {ts.length} bars.</div>
    </Card>
  );
}

export default function Models({ analysis }) {
  if (!analysis?.models) return <Card title="Models"><div className="sub">No analysis yet.</div></Card>;
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div className="grid g2" style={{ alignItems: 'start' }}>
        {tcisdBlock(analysis)}
        {fibBlock(analysis)}
      </div>
      <div className="grid g2" style={{ alignItems: 'start' }}>
        {mnBlock(analysis)}
        {ssmtBlock(analysis)}
      </div>
      {reversalBlock(analysis)}
      <div className="grid g3">
        {seriesBlock(analysis, 'mn_z', 'Market-neutral residual z')}
        {seriesBlock(analysis, 'reversal_risk', 'Reversal risk (probability)')}
        {seriesBlock(analysis, 'ssmt_div', 'SSMT divergence')}
      </div>
      <div className="grid g3">
        {seriesBlock(analysis, 'exh_up', 'Exhaustion — up')}
        {seriesBlock(analysis, 'exh_dn', 'Exhaustion — down')}
        {seriesBlock(analysis, 'atr', 'ATR')}
      </div>
    </div>
  );
}
