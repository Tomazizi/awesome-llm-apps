/** Backtest tab. The point of this screen is falsification: the gate funnel
 *  shows how little survives, the delay curve shows how fast an edge decays
 *  with hesitation, and the sensitivity table shows whether the chosen
 *  threshold is on a plateau or a lucky spike. */
import React from 'react';
import { Card, KPI, Bar, Table, LineChart, BarChart, Scatter, fmt, fmtPct, fmtSigned, shortTs } from '../ui.jsx';

export default function Backtest({ backtest, onRun, busy }) {
  if (!backtest) {
    return (
      <Card title="Backtest" right={
        <button className="btn" disabled={busy} onClick={onRun}>{busy ? 'Running…' : 'Run backtest'}</button>
      }>
        <div className="sub">
          No backtest yet. It walks the loaded history bar by bar, builds the panel once, and
          re-runs every gate at each bar — entries always at the <i>next</i> bar's open, so no
          look-ahead.
        </div>
      </Card>
    );
  }

  const bt = backtest;
  const mt = bt.metrics || {};
  const funnel = bt.funnel || [];
  const delay = bt.delay || [];
  const trades = bt.trades || [];
  const equity = bt.equity || [];
  const cal = bt.calibration || [];
  const att = bt.attribution || [];
  const sens = bt.sensitivity || [];

  const maxFunnel = Math.max(1, ...funnel.map((f) => f.surviving ?? f.count ?? 0));

  if (!trades.length) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        <Card title="Backtest" right={<button className="btn ghost" disabled={busy} onClick={onRun}>Rerun</button>}>
          <div className="warn">
            <b>Zero trades on this sample.</b> That is a real result, not a bug — the gate funnel
            below shows exactly where every candidate was eliminated. Loosen the conviction
            threshold or the reversal gate in <b>Settings</b> to see what you would be trading
            instead, and note what it does to expectancy.
          </div>
        </Card>
        <Card title="Gate funnel">
          {funnel.map((f, i) => (
            <Bar key={i} label={f.gate || f.stage || Object.keys(f)[0]}
                 value={f.surviving ?? f.count ?? 0} max={maxFunnel} color="var(--gold)" />
          ))}
        </Card>
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Card title="Headline metrics"
            right={
              <span className="sub">
                {bt.meta.bars} bars · {bt.meta.interval} · {bt.meta.signals} raw signals ·{' '}
                <button className="btn ghost" style={{ padding: '3px 10px' }} disabled={busy} onClick={onRun}>
                  {busy ? 'Running…' : 'Rerun'}
                </button>
              </span>
            }>
        <div className="grid g7">
          <KPI label="Trades" value={mt.trades ?? '—'} />
          <KPI label="Win rate" value={fmtPct(mt.win_rate)} />
          <KPI label="Expectancy" value={fmtSigned(mt.expectancy_r)}
               sub={`${fmtSigned(mt.expectancy_r)}R / trade`}
               color={(mt.expectancy_r ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'} />
          <KPI label="Profit factor" value={isFinite(mt.profit_factor) ? fmt(mt.profit_factor) : '∞'} />
          <KPI label="Sharpe (R)" value={fmt(mt.sharpe_r)} />
          <KPI label="Max DD" value={`${fmt(mt.max_dd_r)}R`} color="var(--red)" />
          <KPI label="Avg hold" value={fmt(mt.avg_bars_held, 1)} sub="bars" />
        </div>
        <div className="grid g7" style={{ marginTop: 10 }}>
          <KPI label="Median R" value={fmtSigned(mt.median_r)} />
          <KPI label="Best / Worst" value={`${fmt(mt.best_r)}`} sub={`worst ${fmt(mt.worst_r)}`} />
          <KPI label="Sortino (R)" value={fmt(mt.sortino_r)} />
          <KPI label="Mean MAE" value={fmt(mt.mean_mae_r)} sub="adverse excursion" />
          <KPI label="Mean MFE" value={fmt(mt.mean_mfe_r)} sub="favourable excursion" />
          <KPI label="Entry efficiency" value={fmtPct(mt.entry_efficiency)} />
          <KPI label="Clean entry" value={fmtPct(mt.clean_entry_rate)} sub="no -1R drawdown first" />
        </div>
      </Card>

      <div className="grid g2" style={{ alignItems: 'start' }}>
        <Card title="Gate funnel — how little survives">
          {funnel.map((f, i) => (
            <Bar key={i} label={f.gate || f.stage || Object.keys(f)[0]}
                 value={f.surviving ?? f.count ?? 0} max={maxFunnel} color="var(--gold)" />
          ))}
          <div className="sub" style={{ marginTop: 8 }}>
            Read this before trusting any expectancy number: it is the difference between a model
            that is selective and one that is just rare.
          </div>
        </Card>

        <Card title="Entry delay curve — the cost of hesitation">
          <BarChart
            data={delay.map((d) => ({ label: `${d.delay_bars ?? d.delay}`, y: d.mean_r }))}
            colorFor={(d) => (d.y >= 0 ? 'rgba(38,166,154,0.8)' : 'rgba(239,83,80,0.8)')}
            labelFmt={(v) => v.toFixed(2)}
            yLabel="mean R"
          />
          <div className="sub">
            Each bar of delay re-enters the same signal with the structural stop held fixed. If
            expectancy collapses after one bar, the edge is in the <i>timing</i> — which is exactly
            what this app is built to measure.
          </div>
          <Table
            columns={[
              { key: 'delay_bars', label: 'Delay', num: true },
              { key: 'n', label: 'n', num: true },
              { key: 'mean_r', label: 'Mean R', num: true, render: (r) => fmt(r.mean_r, 3) },
              { key: 'win_rate', label: 'Win', num: true, render: (r) => fmtPct(r.win_rate) },
              { key: 'mean_mae_r', label: 'MAE', num: true, render: (r) => fmt(r.mean_mae_r, 2) },
            ]}
            rows={delay}
            maxHeight={200}
          />
        </Card>
      </div>

      <Card title="Equity curve (R)">
        <LineChart series={equity.map((p, i) => ({ x: i, y: p.r }))} height={210} yLabel="cumulative R" />
      </Card>

      <div className="grid g2" style={{ alignItems: 'start' }}>
        <Card title="Calibration — does the score mean anything?">
          <BarChart
            data={cal.map((c) => ({ label: `${Math.round(c.score_lo)}–${Math.round(c.score_hi)}`, y: c.mean_r }))}
            colorFor={(d) => (d.y >= 0 ? 'rgba(38,166,154,0.8)' : 'rgba(239,83,80,0.8)')}
            yLabel="mean R by score bucket"
          />
          <Table
            columns={[
              { key: 'bucket', label: 'Bucket' },
              { key: 'n', label: 'n', num: true },
              { key: 'mean_r', label: 'Mean R', num: true, render: (r) => fmt(r.mean_r, 3) },
              { key: 'median_r', label: 'Median R', num: true, render: (r) => fmt(r.median_r, 3) },
              { key: 'win_rate', label: 'Win', num: true, render: (r) => fmtPct(r.win_rate) },
              { key: 'clean_rate', label: 'Clean', num: true, render: (r) => fmtPct(r.clean_rate) },
            ]}
            rows={cal}
            maxHeight={220}
          />
          <div className="sub">A monotone increase across buckets is the whole point of a score.</div>
        </Card>

        <Card title="Attribution — information coefficient by model">
          <Table
            columns={[
              { key: 'model', label: 'Model' },
              { key: 'ic', label: 'IC', num: true, render: (r) => fmt(r.ic, 3) },
              { key: 'p', label: 'p', num: true, render: (r) => fmt(r.p, 3) },
              { key: 'n', label: 'n', num: true },
            ]}
            rows={att}
            maxHeight={260}
          />
          <div className="sub">
            Rank correlation between each component and realised R. A component with a near-zero
            IC is not paying for its weight.
          </div>
        </Card>
      </div>

      <div className="grid g2" style={{ alignItems: 'start' }}>
        <Card title="Threshold sensitivity">
          <Table
            columns={[
              { key: 'threshold', label: 'Min score', num: true },
              { key: 'signals', label: 'Trades', num: true },
              { key: 'win_rate', label: 'Win', num: true, render: (r) => fmtPct(r.win_rate) },
              { key: 'expectancy_r', label: 'Exp R', num: true, render: (r) => fmt(r.expectancy_r, 3) },
              { key: 'profit_factor', label: 'PF', num: true, render: (r) => (isFinite(r.profit_factor) ? fmt(r.profit_factor) : '∞') },
              { key: 'sharpe_r', label: 'Sharpe', num: true, render: (r) => fmt(r.sharpe_r, 2) },
            ]}
            rows={sens}
            maxHeight={260}
          />
          <div className="sub">You want a plateau, not a spike.</div>
        </Card>

        <Card title="MAE vs MFE — was the entry early or late?">
          <Scatter
            points={trades.map((t) => ({ x: Math.abs(t.mae ?? 0), y: t.mfe ?? 0, r: t.r ?? 0 }))}
            xLabel="|MAE| in R  →"
            yLabel="MFE in R"
          />
          <div className="sub">
            Points left of the diagonal were never deeply underwater: the entry was taken close to
            the decision point. This is the direct measure of <i>timing accuracy</i>.
          </div>
        </Card>
      </div>

      <Card title={`Trades (${trades.length})`}>
        <Table
          columns={[
            { key: 'ts', label: 'Time', render: (r) => <span className="mono">{shortTs(r.ts)}</span> },
            { key: 'dir', label: 'Dir', render: (r) => <span className={r.dir === 'LONG' ? 'long' : 'short'}>{r.dir}</span> },
            { key: 'score', label: 'Score', num: true, render: (r) => fmt(r.score, 0) },
            { key: 'tier', label: 'Tier' },
            { key: 'entry', label: 'Entry', num: true, render: (r) => fmt(r.entry) },
            { key: 'stop', label: 'Stop', num: true, render: (r) => fmt(r.stop) },
            { key: 'r', label: 'R', num: true, render: (r) => <span className={r.r >= 0 ? 'up' : 'down'}>{fmtSigned(r.r)}</span> },
            { key: 'exit', label: 'Exit' },
            { key: 'bars', label: 'Bars', num: true },
            { key: 'mae', label: 'MAE', num: true, render: (r) => fmt(r.mae, 2) },
            { key: 'mfe', label: 'MFE', num: true, render: (r) => fmt(r.mfe, 2) },
            { key: 'entry_eff', label: 'Entry eff', num: true, render: (r) => fmtPct(r.entry_eff) },
          ]}
          rows={trades.slice().reverse()}
          maxHeight={460}
        />
      </Card>
    </div>
  );
}
