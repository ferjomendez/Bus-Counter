// Comunicacion con el servidor: API REST y conexion en vivo (WebSocket).

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function messageFrom(data, status) {
  const d = data && data.detail;
  if (Array.isArray(d)) {
    return d.map((x) => (typeof x === "string" ? x : "Hay un valor que no es válido.")).join(" ");
  }
  if (typeof d === "string") return d;
  return `El servidor respondió con un error (${status}).`;
}

export async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("No hay conexión con el servidor. Revisa que esté encendido.", 0, null);
  }
  let data = null;
  try { data = await res.json(); } catch { /* respuesta sin cuerpo */ }
  if (!res.ok) throw new ApiError(messageFrom(data, res.status), res.status, data);
  return data;
}

// Conexion en vivo con reconexion automatica y espera creciente
export function connectLive({ onState, onLink }) {
  let delay = 1000;
  let ws;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { delay = 1000; onLink(true); };
    ws.onmessage = (ev) => {
      try { onState(JSON.parse(ev.data)); } catch { /* mensaje ignorado */ }
    };
    ws.onclose = () => {
      onLink(false);
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 10000);
    };
    ws.onerror = () => ws.close();
  };
  open();
}
