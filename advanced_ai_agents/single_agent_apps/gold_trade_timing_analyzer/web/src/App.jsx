import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, connectWS } from './api.js';
import { loadGold, loadUniverse, loadSpot } from './marketData.js';
import { Badge, KPI, fmt, fmtPct, fmtSigned } from './ui.jsx';

import EntryTiming from './panels/EntryTiming.jsx';
import Models from './panels/Models.jsx';
import Backtest from './panels/Backtest.jsx';
import Alerts from './panels/Alerts.jsx';
import Journal from './panels/Journal.jsx';
import News from './panels/News.jsx';
import Data from './panels/Data.jsx';

const TABS = [
  ['timing', 'Entry Timing'],
  ['models', 'Models'],
  ['backtest', 'Backtest'],
  ['alerts', 'Alerts'],
  ['journal', 'Journal'],
  ['news', 'News & Research'],
  ['data', 'Data & Settings'],
];

export default function App() {
  const [tab, setTab] = useState('timing');
  const [status, setStatus] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [alerts, setAlerts] = useState(null);
  const [journal, setJournal] = useState(null);
  const [news, setNews] = useState(null);
  const [params, setParams] = useState(null);
  const [spot, setSpot] = useState(null);
  const [wsState, setWsState] = useState('connecting');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState([]);
  const [tf, setTf] = useState('1h');
  const [toasts, setToasts] = useState([]);
  const [flash, setFlash] = useState(false);

  const wsRef = useRef(null);

  const toast = useCallback((msg, bad = false) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, msg, bad }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 9000);
  }, []);

  const dismiss = (id) => setToasts((t) => t.filter((x) => x.id !== id));

  /* -------------------------------------------------------------- startup */
  useEffect(() => {
    (async () => {
      try {
        const [st, p, al, jr, nw] = await Promise.all([
          api.status(), api.paramsGet(), api.alerts(), api.journal(300), api.news(),
        ]);
        setStatus(st); setParams(p.params); setAlerts(al); setJournal(jr);
        setNews(nw.news);
        if (st.spot?.price) setSpot(st.spot);
        if (st.has_primary) {
          try { setAnalysis(await api.analysis()); } catch { /* not analysed yet */ }
          try { setBacktest(await api.backtest()); } catch { /* none yet */ }
        }
      } catch (e) {
        toast(`Backend unreachable: ${e.message}`, true);
      }
    })();
  }, [toast]);

  /* ---------------------------------------------------------- websocket */
  useEffect(() => {
    wsRef.current = connectWS(
      (msg) => {
        const d = msg.data;
        switch (msg.type) {
          case 'hello':
            if (d?.status?.spot?.price) setSpot(d.status.spot);
            break;
          case 'price':
            setSpot(d);
            setFlash(true);
            setTimeout(() => setFlash(false), 900);
            break;
          case 'analysis':
            setAnalysis((prev) => (prev ? { ...prev, ...d } : d));
            break;
          case 'backtest':
            setBacktest((prev) => (prev ? { ...prev, ...d } : d));
            break;
          case 'status':
            setStatus((s) => ({ ...(s || {}), ...d }));
            break;
          case 'alert': {
            setFlash(true);
            setTimeout(() => setFlash(false), 900);
            toast(d?.message, false);
            api.alerts().then(setAlerts).catch(() => {});
            api.journal(300).then(setJournal).catch(() => {});
            break;
          }
          case 'news':
            setNews((n) => (n ? { ...n, ...d } : d));
            break;
          case 'alert_config':
          case 'alert_test':
            api.alerts().then(setAlerts).catch(() => {});
            break;
          case 'journal':
            api.journal(300).then(setJournal).catch(() => {});
            break;
          default:
            break;
        }
      },
      setWsState,
    );
    return () => wsRef.current?.close();
  }, [toast]);

  /* -------------------------------------------------------- market data */
  const loadMarket = useCallback(async () => {
    setBusy(true);
    const lines = [];
    const push = (m) => { lines.push(m); setProgress([...lines]); };
    try {
      push('fetching live spot…');
      const sp = await loadSpot();
      if (sp.price) {
        setSpot(sp);
        await api.spot(sp.price, sp.source, sp.kind).catch(() => {});
      }

      push('fetching gold OHLCV…');
      const gold = await loadGold(tf, push);
      push(`gold: ${gold.source} — ${gold.bars.length} bars (${gold.kind})`);

      push('fetching macro universe…');
      const uni = await loadUniverse(tf, null, push);
      push(`universe: ${Object.keys(uni).length} series`);

      const series = {
        XAUUSD: { source: gold.source, kind: gold.kind, note: gold.note, bars: gold.bars },
        ...Object.fromEntries(Object.entries(uni).map(([k, v]) => [
          k, { source: v.source, kind: v.kind, note: v.note, bars: v.bars },
        ])),
      };

      push('ingesting into the engine…');
      const r = await api.ingest({ interval: tf, series, spot: sp.price ? sp : null });
      setStatus(await api.status());
      push(`loaded ${r.loaded.length} series${r.skipped.length ? ` · skipped ${r.skipped.join(', ')}` : ''}`);

      push('running the models…');
      const a = await api.analyze({});
      setAnalysis(a);
      push(`ETS ${a.signal.score.toFixed(0)} · tier ${a.signal.tier}`);

      push('running the backtest…');
      const b = await api.backtest({});
      setBacktest(b);
      push('done');
      toast(`Loaded ${r.loaded.length} series from ${gold.source}.`);
    } catch (e) {
      push(`FAILED: ${e.message}`);
      toast(`Market data failed: ${e.message}`, true);
    } finally {
      setBusy(false);
    }
  }, [tf, toast]);

  /* ------------------------------------------- auto-load on cold start */
  // Nothing is loaded on a cold start, so pull the first sample by itself. The
  // browser is the only component here guaranteed to have outbound internet.
  const autoLoaded = useRef(false);
  useEffect(() => {
    if (status && !status.has_primary && !autoLoaded.current && !busy) {
      autoLoaded.current = true;
      loadMarket();
    }
  }, [status, busy, loadMarket]);

  const runBacktest = useCallback(async () => {
    setBusy(true);
    try {
      setBacktest(await api.backtest({}));
      toast('Backtest complete.');
    } catch (e) {
      toast(`Backtest failed: ${e.message}`, true);
    } finally {
      setBusy(false);
    }
  }, [toast]);

  /* ------------------------------------------------------------- header */
  const sig = analysis?.signal;
  const tierColor = { A: 'var(--gold)', B: 'var(--blue)', C: 'var(--purple)' }[sig?.tier] || 'var(--muted)';
  const revRisk = analysis?.models?.reversal?.risk;
  const gate = analysis?.models?.reversal?.gate ?? 0.75;

  return (
    <div className="app">
      <div className="hero" style={flash ? { borderColor: 'var(--gold)' } : undefined}>
        <div>
          <div className="kpi-l">XAU / USD</div>
          <div className={`spot ${flash ? 'flash' : ''}`}>
            {spot?.price ? fmt(spot.price) : '—'}
          </div>
          <div className="row" style={{ marginTop: 4, gap: 6 }}>
            <Badge kind={spot?.kind || 'off'}>{spot?.kind || 'no feed'}</Badge>
            <span className="spot-sub">{spot?.source || 'load market data'}</span>
          </div>
        </div>

        <div>
          <div className="kpi-l">Entry Timing Score</div>
          <div className="kpi" style={{ color: sig ? (sig.direction === 'LONG' ? 'var(--green)' : 'var(--red)') : undefined }}>
            {sig ? fmt(sig.score, 0) : '—'}
          </div>
          <div className="spot-sub">{sig ? `${sig.direction} · tier ${sig.tier}` : 'no analysis'}</div>
        </div>

        <div>
          <div className="kpi-l">Reversal risk</div>
          <div className="kpi" style={{ color: revRisk == null ? undefined : revRisk >= gate ? 'var(--red)' : 'var(--green)' }}>
            {revRisk == null ? '—' : fmtPct(revRisk)}
          </div>
          <div className="spot-sub">gate {fmtPct(gate)}</div>
        </div>

        <div>
          <div className="kpi-l">Signals logged</div>
          <div className="kpi">{status?.signal_count ?? 0}</div>
          <div className="spot-sub">
            {status?.journal?.graded ? `${status.journal.graded} graded · ${fmtSigned(status.journal.expectancy_r)}R` : 'none graded yet'}
          </div>
        </div>

        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
            <span className={`pill`}><span className={`dot ${wsState === 'open' ? 'open' : wsState}`} /> live {wsState}</span>
          </div>
          <div className="row" style={{ justifyContent: 'flex-end', marginTop: 8, gap: 8 }}>
            <select style={{ width: 84 }} value={tf} onChange={(e) => setTf(e.target.value)} disabled={busy}>
              {['5m', '15m', '30m', '1h', '2h', '4h', '1d'].map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <button className="btn" disabled={busy} onClick={loadMarket}>
              {busy ? 'Working…' : 'Load market data'}
            </button>
          </div>
          <div className="spot-sub" style={{ marginTop: 6 }}>
            {status?.interval ? `${status.interval} · ${status.series?.length ?? 0} series` : 'nothing loaded'}
          </div>
        </div>
      </div>

      <div className="tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>
            {label}
            {k === 'alerts' && alerts?.config?.enabled && <span className="dot open" style={{ marginLeft: 6 }} />}
          </button>
        ))}
      </div>

      {tab === 'timing' && <EntryTiming analysis={analysis} spot={spot} status={status} />}
      {tab === 'models' && <Models analysis={analysis} />}
      {tab === 'backtest' && <Backtest backtest={backtest} onRun={runBacktest} busy={busy} />}
      {tab === 'alerts' && <Alerts alerts={alerts} setAlerts={setAlerts} api={api} toast={toast} analysis={analysis} />}
      {tab === 'journal' && <Journal journal={journal} setJournal={setJournal} api={api} toast={toast} />}
      {tab === 'news' && <News news={news} setNews={setNews} api={api} toast={toast} analysis={analysis} />}
      {tab === 'data' && (
        <Data status={status} setStatus={setStatus} params={params} setParams={setParams}
              api={api} toast={toast} onLoaded={async () => { setStatus(await api.status()); }}
              loadMarket={loadMarket} busy={busy} progress={progress} />
      )}

      <div className="toast-wrap">
        {toasts.map((t) => (
          <div key={t.id} className="toast" onClick={() => dismiss(t.id)} style={{ cursor: 'pointer' }}>
            <div style={{ fontSize: 12.5, whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{t.msg}</div>
            {t.bad && <div className="kpi-l" style={{ marginTop: 4, color: '#ff8a80' }}>error</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
