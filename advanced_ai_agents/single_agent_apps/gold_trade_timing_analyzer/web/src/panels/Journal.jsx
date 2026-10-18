/** Signal journal: every alerted setup, graded against what actually happened.
 *  This is what separates a signal generator from something with a track record. */
import React, { useMemo } from 'react';
import { Card, KPI, Table, Bar, fmt, fmtPct, fmtSigned, shortTs } from '../ui.jsx';

export default function Journal({ journal, setJournal, api, toast }) {
  const signals = journal?.signals || [];
  const stats = journal?.stats || {};

  const byTier = useMemo(() => {
    const acc = {};
    for (const s of signals) {
      if (s.outcome_r == null) continue;
      (acc[s.tier || '-'] ||= []).push(s.outcome_r);
    }
    return Object.entries(acc).map(([tier, rs]) => ({
      tier,
      n: rs.length,
      exp: rs.reduce((a, b) => a + b, 0) / rs.length,
      win: rs.filter((x) => x > 0).length / rs.length,
    })).sort((a, b) => String(a.tier).localeCompare(String(b.tier)));
  }, [signals]);

  const grade = async () => {
    const r = await api.journalGrade();
    toast(`Graded ${r.graded} open signal${r.graded === 1 ? '' : 's'}.`);
    setJournal(await api.journal(300));
  };

  const reset = async () => {
    const r = await api.journalReset();
    setJournal(await api.journal(300));
    toast('Journal cleared.');
  };

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Card
        title="Track record"
        right={
          <div className="row">
            <button className="btn" onClick={grade}>Grade now</button>
            <button className="btn danger" onClick={reset}>Clear</button>
          </div>
        }
      >
        <div className="grid g4">
          <KPI label="Graded" value={stats.graded ?? 0} sub={`${signals.length} logged`} />
          <KPI label="Win rate" value={fmtPct(stats.win_rate)}
               color={(stats.win_rate ?? 0) >= 0.5 ? 'var(--green)' : undefined} />
          <KPI label="Expectancy" value={`${fmtSigned(stats.expectancy_r)}R`}
               color={(stats.expectancy_r ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'} />
          <KPI label="Total R" value={fmtSigned(stats.total_r)}
               color={(stats.total_r ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'}
               sub={`best ${fmt(stats.best_r)} · worst ${fmt(stats.worst_r)}`} />
        </div>

        <div className="sub" style={{ marginTop: 10 }}>
          Every time the alert engine fires, the setup is written here with its full component
          breakdown. The background grader then walks forward through new bars and marks each one
          hit-stop, hit-target or expired at the max-hold horizon, in R. Nothing is edited by hand.
        </div>

        {byTier.length > 0 && (
          <>
            <hr className="sep" />
            <div className="grid g3">
              {byTier.map((t) => (
                <Card key={t.tier} title={`Tier ${t.tier}`}>
                  <div className="grid g3">
                    <KPI label="n" value={t.n} />
                    <KPI label="Win" value={fmtPct(t.win)} />
                    <KPI label="Exp" value={`${fmtSigned(t.exp)}R`}
                         color={t.exp >= 0 ? 'var(--green)' : 'var(--red)'} />
                  </div>
                </Card>
              ))}
            </div>
          </>
        )}
      </Card>

      {signals.length > 0 && (
        <Card title="R by signal">
          <div className="grid" style={{ gap: 3 }}>
            {signals.slice(0, 60).reverse().map((s) => (
              <Bar
                key={s.id}
                label={<span className="mono">{String(s.ts).slice(5, 16).replace('T', ' ')} · {s.direction} {s.tier}</span>}
                value={Math.abs(s.outcome_r ?? 0)}
                max={Math.max(3, ...signals.map((x) => Math.abs(x.outcome_r ?? 0)))}
                color={(s.outcome_r ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'}
                right={s.outcome_r == null ? 'open' : `${fmtSigned(s.outcome_r)}R`}
              />
            ))}
          </div>
        </Card>
      )}

      <Card title={`Signals (${signals.length})`}>
        <Table
          columns={[
            { key: 'ts', label: 'Bar', render: (r) => <span className="mono">{shortTs(r.ts)}</span> },
            { key: 'direction', label: 'Dir', render: (r) => <span className={r.direction === 'LONG' ? 'long' : 'short'}>{r.direction}</span> },
            { key: 'tier', label: 'Tier' },
            { key: 'score', label: 'Score', num: true, render: (r) => fmt(r.score, 0) },
            { key: 'entry', label: 'Entry', num: true, render: (r) => fmt(r.entry) },
            { key: 'stop', label: 'Stop', num: true, render: (r) => fmt(r.stop) },
            { key: 'tp1', label: 'TP1', num: true, render: (r) => fmt(r.tp1) },
            { key: 'rr', label: 'R:R', num: true, render: (r) => fmt(r.rr) },
            { key: 'risk_usd', label: 'Risk $', num: true, render: (r) => fmt(r.risk_usd, 0) },
            { key: 'outcome', label: 'Outcome', render: (r) => (r.outcome ? String(r.outcome).replace(/[{}"]/g, '').replace('exit_reason:', '') : <span className="sub">open</span>) },
            { key: 'outcome_r', label: 'R', num: true, render: (r) => (r.outcome_r == null ? '—' : <span className={r.outcome_r >= 0 ? 'up' : 'down'}>{fmtSigned(r.outcome_r)}</span>) },
          ]}
          rows={signals}
          empty="No signals logged yet. Arm the alert engine and let it run."
          maxHeight={520}
        />
      </Card>
    </div>
  );
}
