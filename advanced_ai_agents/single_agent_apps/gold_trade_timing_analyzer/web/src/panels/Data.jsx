/** Data & Settings: where the numbers come from, and every dial on the engine. */
import React, { useState } from 'react';
import { Card, KPI, Table, Badge, fmt, shortTs } from '../ui.jsx';

const PARAM_GROUPS = [
  {
    name: 'Structure / tCISD',
    items: [
      ['pivot_left', 'Pivot left bars', 2, 15, 1],
      ['pivot_right', 'Pivot right bars', 2, 15, 1],
      ['disp_body_atr', 'Displacement body (ATR)', 0.3, 3, 0.1],
      ['disp_body_frac', 'Displacement body / range', 0.2, 0.9, 0.05],
      ['fvg_min_atr', 'Min FVG size (ATR)', 0.0, 0.5, 0.01],
      ['tcisd_half_life', 'tCISD half-life (bars)', 2, 30, 1],
      ['tcisd_max_bars', 'tCISD hard expiry (bars)', 6, 60, 1],
      ['sweep_wick_frac', 'Sweep wick / range', 0.1, 0.8, 0.05],
    ],
  },
  {
    name: 'Fibonacci',
    items: [
      ['fib_lookback', 'Swing lookback (bars)', 80, 600, 10],
      ['ote_lo', 'OTE low', 0.5, 0.8, 0.001],
      ['ote_hi', 'OTE high', 0.6, 0.95, 0.001],
      ['fib_level_atr_tol', 'Level tolerance (ATR)', 0.05, 1.0, 0.05],
    ],
  },
  {
    name: 'Market-neutral',
    items: [
      ['mn_window', 'Rolling window', 100, 600, 10],
      ['mn_rls_forget', 'RLS forgetting factor', 0.97, 1.0, 0.001],
      ['mn_z_window', 'Z-score window', 40, 400, 10],
      ['mn_hurst_window', 'Hurst window', 100, 600, 10],
    ],
  },
  {
    name: 'Reversal risk',
    items: [
      ['rev_horizon', 'Forward horizon (bars)', 4, 48, 1],
      ['rev_move_atr', 'Adverse move (ATR)', 0.25, 3, 0.25],
      ['rev_train_window', 'Training window', 200, 2000, 50],
      ['rev_refit_every', 'Refit every (bars)', 10, 240, 10],
      ['rev_gate', 'Veto threshold', 0.5, 0.95, 0.01],
    ],
  },
  {
    name: 'Signal & risk',
    items: [
      ['min_conviction', 'Min conviction', 30, 90, 1],
      ['tier_a', 'Tier A floor', 60, 95, 1],
      ['tier_b', 'Tier B floor', 50, 90, 1],
      ['min_rr', 'Min R:R', 1.0, 4.0, 0.1],
      ['stop_buffer_atr', 'Stop buffer (ATR)', 0.0, 1.0, 0.05],
      ['tp1_r', 'TP1 (R)', 0.5, 3, 0.1],
      ['tp2_r', 'TP2 (R)', 1.0, 5, 0.1],
      ['equity', 'Account equity ($)', 1000, 5_000_000, 1000],
      ['risk_per_trade_pct', 'Risk per trade', 0.001, 0.03, 0.001],
    ],
  },
  {
    name: 'Backtest',
    items: [
      ['bt_max_hold', 'Max hold (bars)', 12, 400, 4],
      ['bt_cooldown', 'Cooldown (bars)', 0, 48, 1],
      ['bt_warmup', 'Warmup (bars)', 100, 800, 20],
    ],
  },
];

const WEIGHTS = [
  ['w_tcisd', 'tCISD'], ['w_fib', 'Fibonacci'], ['w_mn', 'Market-neutral'],
  ['w_ssmt', 'SSMT'], ['w_exhaust', 'Exhaustion'], ['w_reversal', 'Reversal'],
  ['w_session', 'Session'], ['w_news', 'News'],
];

export default function Data({ status, setStatus, params, setParams, api, toast, onLoaded, loadMarket, busy, progress }) {
  const [local, setLocal] = useState(null);
  const p = local || params || {};

  const change = (k, v) => setLocal({ ...(local || params), [k]: v });
  const dirty = local && Object.keys(local).some((k) => local[k] !== params?.[k]);

  const apply = async () => {
    const r = await api.paramsSet(local || {});
    setParams(r.params);
    setLocal(null);
    toast('Parameters applied — engine re-run.');
    const s = await api.status();
    setStatus(s);
    await onLoaded();
  };

  const reset = async () => {
    const r = await api.paramsSet({});
    setParams(r.params);
    setLocal(null);
    toast('Parameters reset to defaults.');
  };

  const series = status?.series || [];
  const kindOf = (k) => ({ real: 'real', proxy: 'proxy', synthetic: 'synthetic' }[k] || 'off');

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Card title="Data sources">
        <div className={status?.server_has_network ? 'ok' : 'note'}>
          {status?.server_has_network
            ? 'This server has outbound internet — it fetches market data directly.'
            : 'This server cannot reach market data hosts, so the browser does the fetching and POSTs the result. The data is still real: it never passes through a synthetic fallback.'}
        </div>
        <div className="grid g4" style={{ marginTop: 10 }}>
          <KPI label="Interval" value={status?.interval || '—'} />
          <KPI label="Series loaded" value={series.length} />
          <KPI label="Signals logged" value={status?.signal_count ?? 0} />
          <KPI label="Server network" value={status?.server_has_network ? 'yes' : 'no'}
               color={status?.server_has_network ? 'var(--green)' : 'var(--orange)'} />
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn" disabled={busy} onClick={loadMarket}>Load / refresh market data</button>
          <button className="btn ghost" disabled={busy} onClick={async () => { await api.clear(); setStatus(await api.status()); toast('Market data cleared.'); }}>
            Clear
          </button>
        </div>
        {progress?.length > 0 && (
          <div className="sub mono" style={{ marginTop: 8, maxHeight: 110, overflow: 'auto' }}>
            {progress.slice(-8).map((l, i) => <div key={i}>{l}</div>)}
          </div>
        )}
      </Card>

      <Card title="Loaded series">
        <Table
          columns={[
            { key: 'symbol', label: 'Symbol' },
            { key: 'kind', label: 'Provenance', render: (r) => <Badge kind={kindOf(r.kind)}>{r.kind}</Badge> },
            { key: 'source', label: 'Source', render: (r) => <span className="mono sub">{r.source}</span> },
            { key: 'bars', label: 'Bars', num: true },
            { key: 'from', label: 'From', render: (r) => <span className="mono">{shortTs(r.from)}</span> },
            { key: 'to', label: 'To', render: (r) => <span className="mono">{shortTs(r.to)}</span> },
            { key: 'last', label: 'Last', num: true, render: (r) => fmt(r.last) },
          ]}
          rows={series}
          empty="Nothing loaded yet."
          maxHeight={320}
        />
        <div className="sub" style={{ marginTop: 8 }}>
          <b>real</b> = a genuine market feed for that instrument. <b>proxy</b> = a real, liquid,
          exchange-traded stand-in (tokenised gold for spot XAU/USD). <b>synthetic</b> = generated,
          for offline development only, and never used silently.
        </div>
      </Card>

      <Card
        title="Engine parameters"
        right={
          <div className="row">
            <button className="btn" disabled={!dirty} onClick={apply}>{dirty ? 'Apply & re-run' : 'No changes'}</button>
            <button className="btn ghost" onClick={reset}>Reset defaults</button>
          </div>
        }
      >
        <div className="grid g3" style={{ alignItems: 'start' }}>
          {PARAM_GROUPS.map((g) => (
            <Card key={g.name} title={g.name}>
              {g.items.map(([k, label, min, max, step]) => (
                <div key={k} style={{ marginBottom: 9 }}>
                  <div className="spread">
                    <span className="sub">{label}</span>
                    <span className="mono">{p[k] ?? '—'}</span>
                  </div>
                  <input type="range" min={min} max={max} step={step} value={p[k] ?? min}
                         onChange={(e) => change(k, parseFloat(e.target.value))} style={{ width: '100%' }} />
                </div>
              ))}
            </Card>
          ))}
        </div>

        <h3 className="sect">Composite weights (normalised)</h3>
        <div className="grid g4">
          {WEIGHTS.map(([k, label]) => (
            <div key={k}>
              <div className="spread">
                <span className="sub">{label}</span>
                <span className="mono">{fmt(p[k], 2)}</span>
              </div>
              <input type="range" min="0" max="0.5" step="0.01" value={p[k] ?? 0}
                     onChange={(e) => change(k, parseFloat(e.target.value))} style={{ width: '100%' }} />
            </div>
          ))}
        </div>
        <div className="sub" style={{ marginTop: 10 }}>
          Weights are normalised to sum to 1 before scoring, so the sliders change relative
          emphasis rather than absolute conviction.
        </div>
      </Card>
    </div>
  );
}
