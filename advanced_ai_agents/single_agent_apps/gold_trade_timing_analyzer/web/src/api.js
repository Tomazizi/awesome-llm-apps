/** Thin API client + WebSocket hub. All paths are relative so the app works
 *  behind any proxy, on any host, from one origin. */

const JSON_HEADERS = { 'Content-Type': 'application/json' };

async function req(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`${path}: non-JSON response (${res.status})`);
  }
  if (!res.ok) throw new Error(data?.detail || data?.message || `${path} failed (${res.status})`);
  return data;
}

export const api = {
  health: () => req('/api/health'),
  status: () => req('/api/status'),
  events: () => req('/api/events'),
  ingest: (payload) => req('/api/market/ingest', { method: 'POST', body: payload }),
  spot: (price, source, kind) => req('/api/spot', { method: 'POST', body: { price, source, kind } }),
  refresh: (body) => req('/api/market/refresh', { method: 'POST', body: body || {} }),
  clear: () => req('/api/market/clear', { method: 'POST' }),
  analyze: (body) => req('/api/analyze', { method: 'POST', body: body || {} }),
  analysis: () => req('/api/analysis'),
  paramsGet: () => req('/api/params'),
  paramsSet: (values) => req('/api/params', { method: 'POST', body: { values } }),
  backtest: (body) => req('/api/backtest', { method: 'POST', body: body || {} }),
  newsIngest: (feeds) => req('/api/news/ingest', { method: 'POST', body: { feeds } }),
  news: () => req('/api/news'),
  research: (body) => req('/api/research', { method: 'POST', body }),
  journal: (limit = 200) => req(`/api/journal?limit=${limit}`),
  journalGrade: () => req('/api/journal/grade', { method: 'POST' }),
  journalReset: () => req('/api/journal', { method: 'DELETE' }),
  alerts: () => req('/api/alerts'),
  alertsConfig: (cfg) => req('/api/alerts/config', { method: 'POST', body: cfg }),
  alertsTest: (channel) => req('/api/alerts/test', { method: 'POST', body: { channel } }),
  alertsCheck: () => req('/api/alerts/check', { method: 'POST' }),
};

/** WebSocket with auto-reconnect and subscription dispatch. */
export function connectWS(onMessage, onState) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${window.location.host}/ws`;
  let ws = null;
  let closed = false;
  let retry = 0;

  const open = () => {
    if (closed) return;
    onState?.('connecting');
    try {
      ws = new WebSocket(url);
    } catch {
      onState?.('error');
      schedule();
      return;
    }
    ws.onopen = () => { retry = 0; onState?.('open'); };
    ws.onclose = () => { onState?.('closed'); schedule(); };
    ws.onerror = () => onState?.('error');
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        onMessage?.(msg);
      } catch { /* ignore */ }
    };
  };

  const schedule = () => {
    if (closed) return;
    retry = Math.min(retry + 1, 6);
    setTimeout(open, 500 * 2 ** (retry - 1));
  };

  open();
  return {
    send: (obj) => { if (ws?.readyState === 1) ws.send(JSON.stringify(obj)); },
    close: () => { closed = true; try { ws?.close(); } catch { /* ignore */ } },
  };
}
