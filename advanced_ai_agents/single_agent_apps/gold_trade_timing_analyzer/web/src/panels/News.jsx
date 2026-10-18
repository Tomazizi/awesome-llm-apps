/** News scan + research agent. The lexicon score is deterministic and always
 *  available; the LLM brief is optional and only nudges the blend. */
import React, { useState } from 'react';
import { Card, KPI, Bar, Table, Badge, fmt, fmtPct, shortTs } from '../ui.jsx';
import { loadFeeds, llmFromBrowser } from '../marketData.js';

const PROVIDERS = [
  ['groq', 'Groq', 'llama-3.3-70b-versatile', true],
  ['openrouter', 'OpenRouter', 'anthropic/claude-3.5-sonnet', true],
  ['openai', 'OpenAI', 'gpt-4o-mini', true],
  ['ollama', 'Ollama (local)', 'llama3.1', false],
];

export default function News({ news, setNews, api, toast, analysis }) {
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState('groq');
  const [model, setModel] = useState('llama-3.3-70b-versatile');
  const [key, setKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [brief, setBrief] = useState(null);

  const scan = async () => {
    setBusy(true);
    try {
      const feeds = await loadFeeds();
      if (!Object.keys(feeds).length) {
        toast('No news feed reachable from this browser. The engine still works — it just runs without a news component.', true);
        return;
      }
      const r = await api.newsIngest(feeds);
      setNews(r.news);
      toast(`Scanned ${r.news.n} headlines from ${Object.keys(feeds).length} feeds.`);
    } catch (e) {
      toast(`News scan failed: ${e.message}`, true);
    } finally {
      setBusy(false);
    }
  };

  const research = async () => {
    setBusy(true);
    try {
      let payload = null;
      if (PROVIDERS.find((p) => p[0] === provider)?.[3]) {
        if (!key) { toast('Enter an API key for the browser-side call, or leave it blank to use the server.', true); setBusy(false); return; }
        const ctx = buildContext(analysis);
        const headlines = (news?.headlines || []).slice(0, 30)
          .map((h, i) => `${i + 1}. [${h.source}] ${h.title} -- ${(h.summary || '').slice(0, 180)}`).join('\n');
        const text = await llmFromBrowser({
          provider, model, apiKey: key,
          system: SYSTEM_PROMPT,
          user: `QUANTITATIVE CONTEXT (engine output, last completed bar)\n${ctx}\n\nHEADLINES (most recent first)\n${headlines}\n\nProduce the JSON brief now. Bias must respect the sign convention: positive = bullish XAU/USD.`,
        });
        payload = { text, json: safeJson(text) };
      }
      const r = await api.research({ provider, model, api_key: key, base_url: baseUrl || null, payload });
      if (r.ok) {
        setBrief(r.brief);
        setNews((n) => (n ? { ...n, ...r.bias } : n));
        toast('Research brief generated.');
      } else {
        toast(`Research failed: ${r.error}`, true);
      }
    } catch (e) {
      toast(`Research failed: ${e.message}`, true);
    } finally {
      setBusy(false);
    }
  };

  const n = news || {};
  const headlines = n.headlines || [];
  const events = n.events || [];

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Card
        title="News scan"
        right={<button className="btn" disabled={busy} onClick={scan}>{busy ? 'Scanning…' : 'Scan feeds'}</button>}
      >
        <div className="grid g4">
          <KPI label="Headlines" value={n.n ?? 0} sub={n.source || 'not scanned'} />
          <KPI label="Bias" value={fmt(n.bias)}
               color={(n.bias ?? 0) > 0 ? 'var(--green)' : (n.bias ?? 0) < 0 ? 'var(--red)' : undefined}
               sub="−1 bearish · +1 bullish" />
          <KPI label="Confidence" value={fmtPct(n.confidence)} sub="lexicon coverage" />
          <KPI label="Event penalty" value={fmt(n.event_penalty ?? 1)}
               color={(n.event_penalty ?? 1) < 1 ? 'var(--orange)' : undefined}
               sub="multiplies the ETS" />
        </div>
        <div className="sub" style={{ marginTop: 10 }}>
          The browser fetches the RSS feeds (the server often cannot), then the backend parses and
          scores them with a recency-weighted lexicon. A scheduled-event penalty is applied to the
          composite score, never used as a trade reason on its own.
        </div>
      </Card>

      <div className="grid g2" style={{ alignItems: 'start' }}>
        <Card title="Event detection">
          {events.length === 0 && <div className="sub">Nothing scanned yet.</div>}
          {events.map((e, i) => (
            <Bar key={i} label={e.event} value={e.impact * 100} max={100}
                 color={e.avg_score >= 0 ? 'var(--green)' : 'var(--red)'}
                 right={`${e.mentions} · ${fmt(e.avg_score, 2)}`} />
          ))}
        </Card>

        <Card title="Research agent">
          <div className="grid g2" style={{ gap: 8 }}>
            <div>
              <label className="field">Provider</label>
              <select value={provider} onChange={(e) => {
                setProvider(e.target.value);
                const p = PROVIDERS.find((x) => x[0] === e.target.value);
                if (p) setModel(p[2]);
              }}>
                {PROVIDERS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select>
            </div>
            <div>
              <label className="field">Model</label>
              <input type="text" value={model} onChange={(e) => setModel(e.target.value)} />
            </div>
          </div>
          {provider === 'ollama' ? (
            <div style={{ marginTop: 8 }}>
              <label className="field">Local base URL</label>
              <input type="text" placeholder="http://localhost:11434/v1" value={baseUrl}
                     onChange={(e) => setBaseUrl(e.target.value)} />
            </div>
          ) : (
            <div style={{ marginTop: 8 }}>
              <label className="field">API key {PROVIDERS.find((p) => p[0] === provider)?.[3] ? '(called from this browser, never stored server-side)' : ''}</label>
              <input type="password" value={key} onChange={(e) => setKey(e.target.value)} />
            </div>
          )}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn" disabled={busy} onClick={research}>Generate brief</button>
          </div>
          <div className="sub" style={{ marginTop: 8 }}>
            The brief is a JSON object constrained by the system prompt: macro regime, catalyst
            grading, event risk with position guidance, a bias in [−1, 1] and an explicit
            contradictions list. Its bias is blended 65/35 into the lexicon score weighted by its
            own stated confidence — it nudges, it never overrides.
          </div>
        </Card>
      </div>

      {brief?.json && <Brief brief={brief} />}

      {brief?.raw && !brief?.json && (
        <Card title="Raw model output">
          <pre className="mono" style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>{brief.raw}</pre>
        </Card>
      )}

      <Card title={`Headlines (${headlines.length})`}>
        <Table
          columns={[
            { key: 'ts', label: 'When', render: (r) => <span className="mono">{shortTs(r.ts)}</span> },
            { key: 'source', label: 'Source' },
            { key: 'title', label: 'Headline', render: (r) => (r.link
              ? <a href={r.link} target="_blank" rel="noreferrer" style={{ color: 'var(--text)' }}>{r.title}</a>
              : r.title) },
            { key: 'score', label: 'Score', num: true,
              render: (r) => <span className={r.score >= 0 ? 'up' : 'down'}>{fmt(r.score, 2)}</span> },
            { key: 'events', label: 'Events', render: (r) => <span className="sub">{r.events || '—'}</span> },
          ]}
          rows={headlines}
          empty="Scan feeds to load headlines."
          maxHeight={520}
        />
      </Card>
    </div>
  );
}

function Brief({ brief }) {
  const j = brief.json;
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Card title="Macro regime">
        <div style={{ fontSize: 15, lineHeight: 1.5 }}>{j.macro_regime}</div>
        <div className="row" style={{ marginTop: 10 }}>
          <Badge kind="real">bias {fmt(j.bias, 2)}</Badge>
          <Badge kind="proxy">confidence {fmtPct(j.confidence)}</Badge>
        </div>
      </Card>
      {(j.narrative || []).length > 0 && (
        <Card title="Narrative">
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
            {(j.narrative || []).map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        </Card>
      )}
      <div className="grid g2" style={{ alignItems: 'start' }}>
        {(j.catalysts || []).length > 0 && (
          <Card title="Catalysts">
            <Table
              columns={[
                { key: 'headline', label: 'Headline' },
                { key: 'direction', label: 'Dir', render: (r) => <span className={r.direction === 'bullish' ? 'up' : r.direction === 'bearish' ? 'down' : ''}>{r.direction}</span> },
                { key: 'magnitude', label: 'Mag', num: true, render: (r) => fmt(r.magnitude, 2) },
                { key: 'novelty', label: 'Novel', num: true, render: (r) => fmt(r.novelty, 2) },
              ]}
              rows={j.catalysts}
              maxHeight={240}
            />
          </Card>
        )}
        {(j.event_risk || []).length > 0 && (
          <Card title="Event risk">
            <Table
              columns={[
                { key: 'event', label: 'Event' },
                { key: 'timing', label: 'Timing' },
                { key: 'impact', label: 'Impact', num: true, render: (r) => fmt(r.impact, 2) },
                { key: 'guidance', label: 'Guidance', render: (r) => <span className={
                  /stand aside/.test(r.guidance) ? 'down' : /reduce/.test(r.guidance) ? '' : 'up'}>{r.guidance}</span> },
              ]}
              rows={j.event_risk}
              maxHeight={240}
            />
          </Card>
        )}
      </div>
      <div className="grid g2" style={{ alignItems: 'start' }}>
        {(j.contradictions || []).length > 0 && (
          <Card title="What could make the signal wrong">
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
              {j.contradictions.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </Card>
        )}
        {(j.what_would_change_my_mind || []).length > 0 && (
          <Card title="What would change the view">
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
              {j.what_would_change_my_mind.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ helpers */

const SYSTEM_PROMPT = `You are a sell-side precious-metals strategist and macro trader. You are given live gold (XAU/USD) market structure readings from a quantitative engine plus the current news flow. You are rigorous, numerate and explicitly uncertain. You never invent data: if something is not supplied, say so. You always answer with a single JSON object and nothing else.

The JSON schema is:
{
  "macro_regime": "one short sentence naming the dominant driver of gold right now",
  "narrative": ["3 to 5 bullets, each <= 25 words, each tied to supplied data or headlines"],
  "catalysts": [{"headline": "<exact headline text>", "direction": "bullish|bearish|neutral", "magnitude": <0.0-1.0>, "novelty": <0.0-1.0>, "why": "<<= 15 words>"}],
  "event_risk": [{"event": "<name>", "timing": "<unknown|today|this week>", "impact": <0.0-1.0>, "guidance": "stand aside|reduce size|trade through"}],
  "bias": <float -1.0 to 1.0, bullish gold positive>,
  "confidence": <float 0.0 to 1.0>,
  "contradictions": ["reasons the engine's technical signal could be wrong, <= 20 words each"],
  "what_would_change_my_mind": ["<= 20 words each"]
}`;

function buildContext(analysis) {
  if (!analysis?.signal) return '(no analysis yet)';
  const s = analysis.signal;
  const m = analysis.models || {};
  return [
    `Last bar: ${s.bar} UTC`,
    `Close: ${(analysis.candles || []).slice(-1)[0]?.[4] ?? '—'} | Live spot: ${analysis.meta?.spot ?? '—'}`,
    `ATR(14): ${fmt(s.atr)}`,
    `ETS long / short: ${fmt(s.score_long, 1)} / ${fmt(s.score_short, 1)}`,
    `tCISD long / short: ${fmt(m.tcisd?.long, 0)} / ${fmt(m.tcisd?.short, 0)}`,
    `Fibonacci long / short: ${fmt(m.fib?.long, 0)} / ${fmt(m.fib?.short, 0)}`,
    `Market-neutral z: ${fmt(m.market_neutral?.z, 2)} | fair-value z: ${fmt(m.market_neutral?.fv_z, 2)}`,
    `Residual half-life: ${fmt(m.market_neutral?.halflife, 1)} bars | Hurst: ${fmt(m.market_neutral?.hurst)} | ADF t: ${fmt(m.market_neutral?.adf_t)}`,
    `Driver R2: ${fmt(m.market_neutral?.r2)} | betas: ${JSON.stringify(m.market_neutral?.betas || {})}`,
    `SSMT divergence: ${fmt(m.ssmt?.div, 2)} | relative strength: ${fmt(m.ssmt?.rel_strength, 2)}`,
    `Exhaustion up / down: ${fmt(m.exhaustion?.up, 0)} / ${fmt(m.exhaustion?.down, 0)}`,
    `Reversal risk: ${fmtPct(m.reversal?.risk)} (out-of-sample AUC ${fmt(m.reversal?.auc, 3)})`,
  ].map((l) => `- ${l}`).join('\n');
}

function safeJson(text) {
  try {
    const m = String(text).match(/\{[\s\S]*\}/);
    return m ? JSON.parse(m[0]) : null;
  } catch {
    return null;
  }
}
