/**
 * Browser-side market data.
 *
 * The Python server may have no outbound internet (locked sandbox, corporate
 * network, some cloud tiers). The browser always does. So this module fetches
 * real market data directly and POSTs it to /api/market/ingest.
 *
 * Venues, in preference order:
 *   1. Binance / Coinbase / Kraken / Bitfinex  - tokenised gold (PAXG, XAUt).
 *      Real, liquid, exchange-traded, tight tracking of XAU/USD spot, and all
 *      send permissive CORS headers.
 *   2. Yahoo Finance through public CORS relays - the macro universe
 *      (GC=F, SI=F, DX-Y.NYB, ^TNX, ^VIX, ^GSPC, CL=F, HG=F, IEF, TIP).
 *
 * Everything is labelled with its provenance so the UI never implies a proxy
 * is spot, or that synthetic data is real.
 */

const RELAYS = [
  (u) => `https://api.allorigins.win/raw?url=${encodeURIComponent(u)}`,
  (u) => `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(u)}`,
];

const TF_BINANCE = { '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1h', '2h': '2h', '4h': '4h', '1d': '1d' };
const TF_YAHOO = { '5m': '5m', '15m': '15m', '30m': '30m', '1h': '60m', '2h': '60m', '4h': '60m', '1d': '1d' };
const TF_RANGE = { '5m': '60d', '15m': '60d', '30m': '60d', '1h': '730d', '2h': '730d', '4h': '730d', '1d': '5y' };
const TF_KRAKEN = { '5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '1d': 1440 };
const TF_CB = { '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400, '1d': 86400 };

export const UNIVERSE = [
  { key: 'XAGUSD', yahoo: 'SI=F', label: 'Silver' },
  { key: 'DXY', yahoo: 'DX-Y.NYB', label: 'Dollar index' },
  { key: 'US10Y', yahoo: '%5ETNX', label: 'US 10Y' },
  { key: 'VIX', yahoo: '%5EVIX', label: 'VIX' },
  { key: 'SPX', yahoo: '%5EGSPC', label: 'S&P 500' },
  { key: 'WTI', yahoo: 'CL=F', label: 'WTI crude' },
  { key: 'COPPER', yahoo: 'HG=F', label: 'Copper' },
  { key: 'IEF', yahoo: 'IEF', label: 'Nominal Treasuries' },
  { key: 'TIP', yahoo: 'TIP', label: 'TIPS' },
];

async function get(url, { timeout = 20000, as = 'text' } = {}) {
  const ac = new AbortController();
  const to = setTimeout(() => ac.abort(), timeout);
  try {
    const r = await fetch(url, { signal: ac.signal, cache: 'no-store', credentials: 'omit' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return as === 'json' ? await r.json() : await r.text();
  } finally {
    clearTimeout(to);
  }
}

const yahooUrl = (symbol, interval) =>
  `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=${TF_YAHOO[interval] || '60m'}&range=${TF_RANGE[interval] || '730d'}&includePrePost=false`;

function parseYahoo(json) {
  const res = json?.chart?.result?.[0];
  if (!res?.timestamp?.length) throw new Error('no data');
  const q = res.indicators.quote[0];
  const out = [];
  for (let i = 0; i < res.timestamp.length; i++) {
    const o = q.open?.[i], h = q.high?.[i], l = q.low?.[i], c = q.close?.[i];
    if (![o, h, l, c].every((v) => typeof v === 'number' && isFinite(v))) continue;
    out.push([res.timestamp[i] * 1000, o, h, l, c, q.volume?.[i] || 0]);
  }
  if (out.length < 30) throw new Error('too few bars');
  return out;
}

// ---------------------------------------------------------------------------
// Gold OHLCV
// ---------------------------------------------------------------------------

async function fromBinance(interval, limit = 1000) {
  const url = `https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=${TF_BINANCE[interval] || '1h'}&limit=${limit}`;
  const raw = await get(url, { as: 'json' });
  return raw.map((r) => [r[0], +r[1], +r[2], +r[3], +r[4], +r[5]]);
}

async function fromBitfinex(interval) {
  const raw = await get(`https://api-pub.bitfinex.com/v2/candles/trade:${TF_BINANCE[interval] || '1h'}:tXAUUSD/hist?limit=1000`, { as: 'json' });
  return raw.map((r) => [r[0], r[1], r[3], r[4], r[2], r[5]]).sort((a, b) => a[0] - b[0]);
}

async function fromKraken(interval) {
  const j = await get(`https://api.kraken.com/0/public/OHLC?pair=PAXGUSD&interval=${TF_KRAKEN[interval] || 60}`, { as: 'json' });
  if (j.error?.length) throw new Error(j.error[0]);
  const key = Object.keys(j.result || {}).find((k) => k !== 'last');
  return j.result[key].map((r) => [r[0] * 1000, +r[1], +r[2], +r[3], +r[4], +r[6]]);
}

async function fromCoinbase(interval) {
  const raw = await get(`https://api.exchange.coinbase.com/products/PAXG-USD/candles?granularity=${TF_CB[interval] || 3600}`, { as: 'json' });
  return raw.map((r) => [r[0] * 1000, +r[3], +r[2], +r[1], +r[4], +r[5]]).sort((a, b) => a[0] - b[0]);
}

export async function loadGold(interval, onProgress = () => {}) {
  const attempts = [
    ['binance:PAXGUSDT', fromBinance, 'proxy'],
    ['bitfinex:tXAUUSD', fromBitfinex, 'proxy'],
    ['kraken:PAXGUSD', fromKraken, 'proxy'],
    ['coinbase:PAXG-USD', fromCoinbase, 'proxy'],
  ];
  let lastErr;
  for (const [name, fn, kind] of attempts) {
    try {
      onProgress(`trying ${name}…`);
      const bars = await fn(interval);
      if (!bars || bars.length < 60) throw new Error('too few bars');
      return {
        bars, source: name, kind,
        note: 'Tokenised gold (PAXG/XAUt) — a real, liquid, exchange-traded proxy for XAU/USD spot.',
      };
    } catch (e) {
      onProgress(`${name} failed: ${e.message}`);
      lastErr = e;
    }
  }
  // fall back to Yahoo through a CORS relay: true COMEX front-month, not a proxy
  for (let i = 0; i < RELAYS.length; i++) {
    try {
      onProgress(`trying yahoo:GC=F via relay #${i + 1}…`);
      const text = await get(RELAYS[i](yahooUrl('GC=F', interval)));
      const bars = parseYahoo(JSON.parse(text));
      return { bars, source: `yahoo:GC=F (CORS relay #${i + 1})`, kind: 'real',
               note: 'COMEX front-month gold future (Yahoo Finance) via a public CORS relay.' };
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('no gold source reachable');
}

// ---------------------------------------------------------------------------
// Macro universe
// ---------------------------------------------------------------------------

export async function loadUniverse(interval, keys = null, onProgress = () => {}) {
  const wanted = UNIVERSE.filter((u) => !keys || keys.includes(u.key));
  const out = {};
  // direct first (works if the browser is not behind a CORS-stripping proxy)
  await Promise.all(wanted.map(async (u) => {
    for (let i = -1; i < RELAYS.length; i++) {
      const raw = i < 0 ? yahooUrl(u.yahoo, interval) : RELAYS[i](yahooUrl(u.yahoo, interval));
      try {
        onProgress(`${u.label}: ${i < 0 ? 'direct' : `relay #${i + 1}`}…`);
        const text = await get(raw, { timeout: 15000 });
        const bars = parseYahoo(JSON.parse(text));
        out[u.key] = { bars, source: `yahoo:${u.yahoo}${i < 0 ? '' : ` (relay #${i + 1})`}`,
                       kind: 'real', note: '' };
        return;
      } catch (e) { /* try next */ }
    }
    onProgress(`${u.label}: unavailable`);
  }));
  return out;
}

// ---------------------------------------------------------------------------
// Live spot
// ---------------------------------------------------------------------------

export async function loadSpot() {
  const attempts = [
    ['gold-api.com', async () => (await get('https://api.gold-api.com/price/XAU', { as: 'json', timeout: 8000 })).price, 'real'],
    ['Binance PAXG', async () => +(await get('https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT', { as: 'json', timeout: 8000 })).price, 'proxy'],
    ['Coinbase PAXG', async () => +(await get('https://api.coinbase.com/v2/prices/PAXG-USD/spot', { as: 'json', timeout: 8000 })).data.amount, 'proxy'],
    ['Kraken PAXG', async () => { const j = await get('https://api.kraken.com/0/public/Ticker?pair=PAXGUSD', { as: 'json', timeout: 8000 }); const k = Object.keys(j.result)[0]; return +j.result[k].c[0]; }, 'proxy'],
    ['CoinGecko PAXG', async () => (await get('https://api.coingecko.com/api/v3/simple/price?ids=pax-gold&vs_currencies=usd', { as: 'json', timeout: 8000 }))['pax-gold'].usd, 'proxy'],
  ];
  for (const [name, fn, kind] of attempts) {
    try {
      const price = await fn();
      if (price && isFinite(price) && price > 0) {
        return { price, source: name, kind, ts: new Date().toISOString() };
      }
    } catch (e) { /* next */ }
  }
  return { price: null, source: 'unavailable', kind: 'none', ts: null };
}

// ---------------------------------------------------------------------------
// News (RSS) — pulled the same way, then handed to the server for scoring
// ---------------------------------------------------------------------------

export const FEEDS = [
  ['Kitco News', 'https://www.kitco.com/rss/KitcoNewsRSS.xml'],
  ['FXStreet', 'https://www.fxstreet.com/rss/news'],
  ['Investing.com Metals', 'https://www.investing.com/rss/news_11.rss'],
  ['Mining.com', 'https://www.mining.com/feed/'],
  ['CNBC Economy', 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258'],
  ['MarketWatch', 'https://feeds.content.dowjones.io/public/rss/mw_topstories'],
  ['Trading Economics', 'https://tradingeconomics.com/rss/news.aspx'],
];

export async function loadFeeds(feeds = FEEDS) {
  const out = {};
  await Promise.all(feeds.map(async ([name, url]) => {
    for (let i = -1; i < RELAYS.length; i++) {
      const target = i < 0 ? url : RELAYS[i](url);
      try {
        const text = await get(target, { timeout: 12000 });
        if (text && text.length > 200) { out[name] = text; return; }
      } catch (e) { /* next */ }
    }
  }));
  return out;
}

/** Optional browser-side LLM call for providers that allow CORS. */
export async function llmFromBrowser({ provider, model, apiKey, system, user }) {
  const endpoints = {
    groq: 'https://api.groq.com/openai/v1/chat/completions',
    openrouter: 'https://openrouter.ai/api/v1/chat/completions',
    openai: 'https://api.openai.com/v1/chat/completions',
  };
  const url = endpoints[provider];
  if (!url) throw new Error(`${provider} cannot be called from the browser`);
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ model, messages: [{ role: 'system', content: system }, { role: 'user', content: user }], temperature: 0.3, max_tokens: 1400 }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j?.error?.message || `HTTP ${r.status}`);
  return j.choices?.[0]?.message?.content || '';
}
