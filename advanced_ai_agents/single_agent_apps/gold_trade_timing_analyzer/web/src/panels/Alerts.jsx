/** Real-time alerts: what fires, where it goes, and what already fired. */
import React, { useEffect, useState } from 'react';
import { Card, KPI, Table, Badge, fmt, fmtPct } from '../ui.jsx';

const CH_KIND = {
  webhook: { label: 'Generic webhook', fields: ['url'], hint: 'POSTs JSON. Works with Zapier / Make / n8n / your own endpoint.' },
  discord: { label: 'Discord', fields: ['url'], hint: 'Paste a Discord channel webhook URL.' },
  telegram: { label: 'Telegram', fields: ['token', 'chat_id'], hint: 'Bot token from @BotFather and your chat id.' },
  log: { label: 'Journal only', fields: [], hint: 'Recorded in the signal journal, nothing leaves the machine.' },
};

const EMPTY = { type: 'webhook', enabled: true, url: '', token: '', chat_id: '' };

export default function Alerts({ alerts, setAlerts, api, toast, analysis, spot }) {
  const [cfg, setCfg] = useState(alerts?.config || null);
  const [editing, setEditing] = useState(null);
  const [testing, setTesting] = useState(null);

  useEffect(() => {
    if (alerts?.config && !cfg) setCfg(alerts.config);
  }, [alerts, cfg]);

  const refresh = async () => {
    const r = await api.alerts();
    setAlerts(r);
    setCfg(r.config);
  };

  const save = async (next) => {
    setCfg(next);
    const r = await api.alertsConfig(next);
    setCfg(r.config);
    await refresh();
    toast('Alert configuration saved.');
  };

  const patch = (k, v) => save({ ...cfg, [k]: v });

  const saveChannel = async (ch) => {
    const list = [...(cfg.channels || [])];
    if (editing === null) list.push(ch); else list[editing] = ch;
    setEditing(null);
    await save({ ...cfg, channels: list });
  };

  const removeChannel = async (i) => {
    await save({ ...cfg, channels: (cfg.channels || []).filter((_, j) => j !== i) });
  };

  const test = async (ch) => {
    setTesting(ch.type + ch.url + ch.chat_id);
    try {
      const r = await api.alertsTest(ch);
      toast(r.ok ? `Test sent to ${ch.type}.` : `Test failed: ${r.detail || r.error || 'unknown'}`, !r.ok);
    } finally {
      setTesting(null);
    }
  };

  if (!cfg) return <Card title="Alerts"><div className="sub">Loading…</div></Card>;

  const log = alerts?.log || [];
  const okCount = log.filter((l) => l.status === 'sent').length;

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Card
        title="Alert engine"
        right={
          <label className="pill" style={{ cursor: 'pointer' }}>
            <input type="checkbox" checked={cfg.enabled} onChange={(e) => patch('enabled', e.target.checked)} />
            {cfg.enabled ? 'ARMED' : 'DISARMED'}
          </label>
        }
      >
        <div className="grid g4">
          <KPI label="Dispatched" value={log.length} sub={`${okCount} delivered`} />
          <KPI label="Min tier" value={cfg.min_tier} sub={`score ≥ ${cfg.min_score}`} />
          <KPI label="Cooldown" value={`${cfg.cooldown_bars} bars`} sub="per direction" />
          <KPI label="Poll" value={`${cfg.poll_seconds}s`} sub="background re-evaluation" />
        </div>
        <div className="sub" style={{ marginTop: 10 }}>
          A signal only becomes an alert when it clears the conviction bar, and only once per
          setup. The cooldown exists because the same structural trigger stays live for several
          bars — a desk that fires on every one of them is a desk that gets muted.
        </div>
      </Card>

      <div className="grid g2" style={{ alignItems: 'start' }}>
        <Card title="Thresholds">
          <div className="grid g2" style={{ gap: 10 }}>
            <div>
              <label className="field">Minimum tier</label>
              <select value={cfg.min_tier} onChange={(e) => patch('min_tier', e.target.value)}>
                {['A', 'B', 'C'].map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="field">Minimum score</label>
              <input type="number" min="0" max="100" step="1"
                     value={cfg.min_score} onChange={(e) => patch('min_score', parseFloat(e.target.value))} />
            </div>
            <div>
              <label className="field">Cooldown (bars)</label>
              <input type="number" min="0" max="500" step="1"
                     value={cfg.cooldown_bars} onChange={(e) => patch('cooldown_bars', parseInt(e.target.value, 10))} />
            </div>
            <div>
              <label className="field">Poll interval (seconds)</label>
              <input type="number" min="15" max="3600" step="5"
                     value={cfg.poll_seconds} onChange={(e) => patch('poll_seconds', parseInt(e.target.value, 10))} />
            </div>
          </div>

          <label className="field" style={{ marginTop: 12 }}>Directions</label>
          <div className="row">
            {['LONG', 'SHORT'].map((d) => (
              <label key={d} className="pill" style={{ cursor: 'pointer' }}>
                <input type="checkbox"
                       checked={(cfg.directions || []).includes(d)}
                       onChange={(e) => patch('directions', e.target.checked
                         ? [...(cfg.directions || []), d]
                         : (cfg.directions || []).filter((x) => x !== d))} />
                {d}
              </label>
            ))}
          </div>

          <hr className="sep" />
          <div className="sub">
            The engine re-evaluates on every ingest, on every parameter change, and on the poll
            timer. Alerts are pushed to this page over WebSocket too, so an open tab rings
            immediately.
          </div>
          {analysis?.signal && (
            <div className={analysis.signal.trade ? 'ok' : 'note'} style={{ marginTop: 10 }}>
              Current state: score <b>{fmt(analysis.signal.score, 0)}</b>, tier{' '}
              <b>{analysis.signal.tier}</b>, direction <b>{analysis.signal.direction}</b>.{' '}
              {analysis.signal.trade
                ? (['A', 'B', 'C'].indexOf(analysis.signal.tier) <= ['A', 'B', 'C'].indexOf(cfg.min_tier)
                   && analysis.signal.score >= cfg.min_score
                   && cfg.directions.includes(analysis.signal.direction)
                    ? 'This setup would fire right now (subject to cooldown).'
                    : 'This setup is below your configured bar, so it will not fire.')
                : 'No trade constructible, so nothing can fire.'}
            </div>
          )}
        </Card>

        <Card title="Channels">
          {(cfg.channels || []).map((ch, i) => (
            <div key={i} style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 10, marginBottom: 8 }}>
              <div className="spread">
                <b style={{ fontSize: 13 }}>{CH_KIND[ch.type]?.label || ch.type}</b>
                <div className="row" style={{ gap: 6 }}>
                  <label className="pill">
                    <input type="checkbox" checked={ch.enabled}
                           onChange={() => {
                             const l = [...cfg.channels]; l[i] = { ...ch, enabled: !ch.enabled };
                             save({ ...cfg, channels: l });
                           }} /> on
                  </label>
                  <button className="btn ghost" style={{ padding: '3px 9px' }} disabled={!!testing}
                          onClick={() => test(ch)}>
                    {testing === ch.type + ch.url + ch.chat_id ? 'Sending…' : 'Test'}
                  </button>
                  <button className="btn ghost" style={{ padding: '3px 9px' }}
                          onClick={() => setEditing(i)}>Edit</button>
                  <button className="btn danger" style={{ padding: '3px 9px' }}
                          onClick={() => removeChannel(i)}>✕</button>
                </div>
              </div>
              <div className="sub mono" style={{ marginTop: 4 }}>
                {ch.url ? ch.url.slice(0, 72) : (ch.token ? 'bot •••••• / chat ' + ch.chat_id : 'journal only')}
              </div>
            </div>
          ))}

          <hr className="sep" />
          <ChannelForm
            key={editing === null ? 'new' : editing}
            initial={editing === null ? EMPTY : cfg.channels[editing]}
            isEdit={editing !== null}
            onCancel={() => setEditing(null)}
            onSave={saveChannel}
          />
        </Card>
      </div>

      <Card title="Dispatch log" right={<button className="btn ghost" style={{ padding: '3px 9px' }} onClick={refresh}>Refresh</button>}>
        <Table
          columns={[
            { key: 'created_at', label: 'When', render: (r) => <span className="mono">{new Date(r.created_at * 1000).toISOString().slice(5, 19).replace('T', ' ')}</span> },
            { key: 'direction', label: 'Signal', render: (r) => (r.direction ? <span className={r.direction === 'LONG' ? 'long' : 'short'}>{r.direction} {r.tier || ''}</span> : '—') },
            { key: 'score', label: 'Score', num: true, render: (r) => fmt(r.score, 0) },
            { key: 'channel', label: 'Channel' },
            { key: 'status', label: 'Status', render: (r) => <span className={r.status === 'sent' ? 'up' : 'down'}>{r.status}</span> },
            { key: 'detail', label: 'Detail', render: (r) => <span className="sub mono">{String(r.detail || '').slice(0, 80)}</span> },
          ]}
          rows={log}
          empty="Nothing dispatched yet. Send a test above to verify your channel."
          maxHeight={360}
        />
      </Card>
    </div>
  );
}

function ChannelForm({ initial, isEdit, onCancel, onSave }) {
  const [ch, setCh] = useState({ ...EMPTY, ...initial });
  const kind = CH_KIND[ch.type] || CH_KIND.webhook;
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <select style={{ width: 200 }} value={ch.type} onChange={(e) => setCh({ ...ch, type: e.target.value })}>
          {Object.entries(CH_KIND).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
      </div>
      <div className="sub" style={{ marginBottom: 8 }}>{kind.hint}</div>
      {kind.fields.includes('url') && (
        <div style={{ marginBottom: 8 }}>
          <label className="field">Webhook URL</label>
          <input type="text" placeholder="https://…" value={ch.url}
                 onChange={(e) => setCh({ ...ch, url: e.target.value })} />
        </div>
      )}
      {kind.fields.includes('token') && (
        <div style={{ marginBottom: 8 }}>
          <label className="field">Bot token</label>
          <input type="password" placeholder="123456:AA…" value={ch.token}
                 onChange={(e) => setCh({ ...ch, token: e.target.value })} />
        </div>
      )}
      {kind.fields.includes('chat_id') && (
        <div style={{ marginBottom: 8 }}>
          <label className="field">Chat ID</label>
          <input type="text" placeholder="-100…" value={ch.chat_id}
                 onChange={(e) => setCh({ ...ch, chat_id: e.target.value })} />
        </div>
      )}
      <div className="row">
        <button className="btn" onClick={() => onSave(ch)}>{isEdit ? 'Save channel' : 'Add channel'}</button>
        {isEdit && <button className="btn ghost" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}
