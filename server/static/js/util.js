// Utilidades compartidas: formato, dibujo del bus, dialogos y avisos.

export const $ = (sel, root = document) => root.querySelector(sel);

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// Hora del servidor, para que "hace 5 s" no dependa del reloj del celular
export const clock = {
  offset: 0,
  sync(serverTime) { this.offset = serverTime - Date.now() / 1000; },
  now() { return Date.now() / 1000 + this.offset; },
};

export function ago(epoch) {
  if (epoch == null) return null;
  const s = Math.max(0, Math.round(clock.now() - epoch));
  if (s < 60) return `hace ${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `hace ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.floor(h / 24);
  return d === 1 ? "hace 1 día" : `hace ${d} días`;
}

// Texto de "Actualizado hace X" a partir de data-seen y data-prefix
export function seenText(el) {
  const t = el.dataset.seen;
  if (!t) return "Sin datos todavía";
  return `${el.dataset.prefix || "Actualizado"} ${ago(Number(t))}`;
}

const timeFmt = new Intl.DateTimeFormat("es-CL", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const dayTimeFmt = new Intl.DateTimeFormat("es-CL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

export function clockTime(epoch) {
  const d = new Date(epoch * 1000);
  const today = new Date();
  return d.toDateString() === today.toDateString() ? timeFmt.format(d) : dayTimeFmt.format(d);
}

export function signalWords(rssi) {
  if (rssi == null) return "Sin dato";
  const word = rssi >= -60 ? "Excelente" : rssi >= -70 ? "Buena" : rssi >= -80 ? "Regular" : "Débil";
  return `${word} (${rssi} dBm)`;
}

export function byName(a, b) {
  return a.name.localeCompare(b.name, "es", { numeric: true, sensitivity: "base" });
}

// ------------------------------------------------------------ dibujo del bus

const BODY_X = 6;
const BODY_W = 230;

// Bus de perfil que se llena de color desde atras hacia adelante segun la ocupacion
export function busArt(id) {
  const clip = `clip-${id.replace(/[^\w-]/g, "")}`;
  return `
  <svg class="bus-art" viewBox="0 0 240 88" aria-hidden="true" focusable="false">
    <defs><clipPath id="${clip}"><path d="M18 6 H206 Q232 6 234 28 L236 60 Q236 68 228 68 H12 Q6 68 6 60 V18 Q6 6 18 6 Z"/></clipPath></defs>
    <rect class="fill" x="${BODY_X}" y="6" width="0" height="62" clip-path="url(#${clip})"/>
    <path class="shell" d="M18 6 H206 Q232 6 234 28 L236 60 Q236 68 228 68 H12 Q6 68 6 60 V18 Q6 6 18 6 Z"/>
    <rect class="pane" x="18" y="14" width="24" height="22" rx="2"/>
    <rect class="pane" x="48" y="14" width="24" height="22" rx="2"/>
    <rect class="pane" x="78" y="14" width="24" height="22" rx="2"/>
    <rect class="pane" x="108" y="14" width="24" height="22" rx="2"/>
    <rect class="pane" x="138" y="14" width="24" height="22" rx="2"/>
    <rect class="pane" x="172" y="14" width="22" height="48" rx="2"/>
    <path class="pane" d="M202 14 H220 Q227 14 228 26 L229 36 H202 Z"/>
    <circle class="wheel" cx="50" cy="72" r="12"/><circle class="hub" cx="50" cy="72" r="4.5"/>
    <circle class="wheel" cx="190" cy="72" r="12"/><circle class="hub" cx="190" cy="72" r="4.5"/>
  </svg>`;
}

export function setBusFill(svg, count, capacity) {
  const pct = capacity > 0 ? Math.min(1, count / capacity) : 0;
  svg.querySelector(".fill").setAttribute("width", (BODY_W * pct).toFixed(1));
}

// ------------------------------------------------------------ iconos

export const icons = {
  swap: `<svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22"><path d="M4 8h13l-3.5-3.5M20 16H7l3.5 3.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  up: `<svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  down: `<svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20"><path d="M12 5v14M5.5 12.5 12 19l6.5-6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

// ------------------------------------------------------------ dialogos y avisos

export function confirmDialog({ title, text, ok = "Confirmar", danger = false }) {
  const dlg = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  const okBtn = $("#confirm-ok");
  okBtn.textContent = ok;
  okBtn.className = danger ? "btn btn-primary btn-danger-fill" : "btn btn-primary";
  dlg.returnValue = "cancel";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
  });
}

let toastTimer;
export function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("err", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 7000 : 4000);
}
