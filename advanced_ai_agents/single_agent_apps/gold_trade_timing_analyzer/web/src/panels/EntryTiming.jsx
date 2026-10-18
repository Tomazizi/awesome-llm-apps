/** Entry Timing tab: the headline score, the gate funnel, and the trade card. */
import React from 'react';
import PriceChart from '../PriceChart.jsx';
import { Card, KPI, Bar, Gate, fmt, fmtPct, shortTs } from '../ui.jsx';

const WEIGHT_LABELS = {
  tcisd: 'tCISD structure',
  fib: 'Fibonacci location',
  mn: 'Market-neutral',
  ssmt: 'SSMT intermarket',
  exhaust: 'Exhaustion (inverted)',
  reversal: 'Reversal safety',
  session: 'Session',
  news: 'News alignment',
};

export default function EntryTiming({ analysis, spot, status }) {
  if (!analysis?.signal) {
    return (
      <Card title="Entry timing">
        <div className="sub">
          No analysis yet. Use <b>Load market data</b> in the header — the browser fetches
          real XAU/USD history and hands it to the engine.
        </div>
      </Card>
    );
  }

  const sig = analysis.signal;
  const tr = sig.trade;
  const m = analysis.models || {};
  const weights = analysis.weights || {};
  const dirColor = sig.direction === 'LONG' ? 'var(--green)' : 'var(--red)';
  const tierColor = { A: 'var(--gold)', B: 'var(--blue)', C: 'var(--purple)' }[sig.tier] || 'var(--muted)';

  const comps = sig.components || {};
  const riskPct = m.reversal?.risk;

  return (
    <div className="grid g2" style={{ alignItems: 'start' }}>
      <div style={{ display: 'grid', gap: 12 }}>
        <Card
          title="Entry Timing Score"
          right={
            <span className="badge" style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid var(--line-2)' }}>
              {analysis.meta.interval} · HTF {analysis.meta.htf}
            </span>
          }
        >
          <div className="row" style={{ alignItems: 'baseline', gap: 16 }}>
            <div>
              <div className="spot" style={{ color: dirColor }}>{fmt(sig.score, 0)}</div>
              <div className="spot-sub">/100 · {sig.direction}</div>
            </div>
            <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
              <div className="kpi" style={{ color: tierColor }}>TIER {sig.tier}</div>
              <div className="kpi-l">{tr ? 'tradeable' : 'no trade'}</div>
            </div>
          </div>

          <div className="grid g2" style={{ marginTop: 12 }}>
            <Bar label="Long score" value={sig.score_long} max={100}
                 color="var(--green)" right={fmt(sig.score_long, 0)} />
            <Bar label="Short score" value={sig.score_short} max={100}
                 color="var(--red)" right={fmt(sig.score_short, 0)} />
          </div>

          <hr className="sep" />

          <h4 style={{ margin: '0 0 8px' }}>Gates</h4>
          {(sig.gates || []).map((g, i) => (
            <Gate key={i} ok={g.passed} detail={g.detail}>{g.name}</Gate>
          ))}
        </Card>

        <Card title="Composite components (weighted)">
          {Object.entries(WEIGHT_LABELS).map(([k, label]) => {
            const w = weights[k] ?? 0;
            const key = { tcisd: 'tCISD', fib: 'Fibonacci', mn: 'MarketNeutral', ssmt: 'SSMT',
                          exhaust: 'ExhaustionRisk', reversal: 'ReversalRisk',
                          session: 'Session', news: 'NewsAlignment' }[k];
            const v = comps[key];
            const invert = k === 'exhaust' || k === 'reversal';
            const contrib = v == null ? null : w * (invert ? 1 - v / 100 : v / 100) * 100;
            return (
              <div key={k} className="bar-row">
                <div className="bar-top">
                  <span>{label} <span style={{ color: 'var(--muted)' }}>· w {fmtPct(w, 0)}</span></span>
                  <span className="mono">
                    {fmt(v, 0)} <span style={{ color: 'var(--muted)' }}>
                      → {contrib === null ? '—' : `+${contrib.toFixed(1)}`}
                    </span>
                  </span>
                </div>
                <div className="bar-track">
                  <div className="bar-fill"
                       style={{ width: `${Math.max(0, Math.min(100, v ?? 0))}%`,
                                background: invert
                                  ? `linear-gradient(90deg, var(--red), var(--orange))`
                                  : 'linear-gradient(90deg, var(--blue), var(--gold))' }} />
                </div>
              </div>
            );
          })}
          <div className="sub" style={{ marginTop: 8 }}>
            Exhaustion and reversal risk enter <i>inverted</i>: a high score means the move is
            tired, so they subtract from conviction rather than add to it.
          </div>
        </Card>
      </div>

      <div style={{ display: 'grid', gap: 12 }}>
        <Card title="Trade card" right={<span className="sub">{shortTs(sig.bar)} UTC</span>}>
          {tr ? (
            <>
              <div className="grid g4" style={{ marginBottom: 10 }}>
                <KPI label="Entry" value={fmt(tr.entry)} sub="live price" />
                <KPI label="Stop" value={fmt(tr.stop)} sub={`${fmt(tr.risk_atr)} ATR`} color="var(--red)" />
                <KPI label="TP1 / TP2" value={`${fmt(tr.tp1)}`} sub={`TP2 ${fmt(tr.tp2)} · TP3 ${fmt(tr.tp3)}`} color="var(--green)" />
                <KPI label="R:R" value={fmt(tr.rr)} sub={`${fmt(tr.size)} lots · $${fmt(tr.risk_usd, 0)} risk`} />
              </div>
              {tr.notes?.length > 0 && (
                <div className="note">{tr.notes.map((n, i) => <div key={i}>· {n}</div>)}</div>
              )}
            </>
          ) : (
            <div className="warn">
              No trade. A signal requires an <b>active tCISD structural trigger</b>, reversal risk
              below the gate, conviction ≥ threshold and R:R ≥ 1.5. The gates panel shows exactly
              which one is failing.
            </div>
          )}
        </Card>

        <Card title="Market state">
          <div className="grid g4">
            <KPI label="Spot" value={spot?.price ? fmt(spot.price) : '—'}
                 sub={spot?.source || 'unavailable'} />
            <KPI label="ATR" value={fmt(sig.atr)} sub={`${analysis.meta.interval} · ${analysis.meta.bars} bars`} />
            <KPI label="Reversal risk" value={fmtPct(riskPct)} sub={`gate ${fmtPct(m.reversal?.gate)}`}
                 color={riskPct >= (m.reversal?.gate ?? 0.75) ? 'var(--red)' : 'var(--green)'} />
            <KPI label="Prevailing" value={sig.prevailing}
                 color={sig.prevailing === 'up' ? 'var(--green)' : 'var(--red)'} sub="HTF structure" />
          </div>
          <div className="grid g4" style={{ marginTop: 10 }}>
            <KPI label="CISD events" value={m.tcisd?.event_count ?? '—'} sub={`${m.tcisd?.active_events ?? 0} active`} />
            <KPI label="Open FVGs" value={m.tcisd?.open_fvg ?? '—'} sub="unfilled imbalances" />
            <KPI label="MN z-score" value={fmt(m.market_neutral?.z)} sub={`HL ${fmt(m.market_neutral?.halflife, 1)} bars`} />
            <KPI label="SSMT div" value={fmt(m.ssmt?.div)} sub={m.ssmt?.div > 0 ? 'gold leading' : 'gold lagging'} />
          </div>
        </Card>

        <Card title="Structure · Fibonacci · FVG"
              right={<span className="sub">blue = bullish gap · red = bearish</span>}>
          <PriceChart
            candles={analysis.candles}
            fvgs={analysis.structure?.fvgs || []}
            legs={analysis.structure?.legs || []}
            events={analysis.structure?.events || []}
            trade={tr}
          />
        </Card>
      </div>
    </div>
  );
}
