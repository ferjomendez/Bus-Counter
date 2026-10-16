// Detalle de un bus: medidor, grafico, subidas y bajadas, y opciones avanzadas.
import { api } from "./api.js";
import { $, esc, clockTime, signalWords, seenText, icons, confirmDialog, toast } from "./util.js";

const RANGES = {
  hour: { first: "Última", label: "hora", seconds: 3600, step: 10 * 60 },
  day: { first: "Último", label: "día", seconds: 86400, step: 3 * 3600 },
  week: { first: "Última", label: "semana", seconds: 7 * 86400, step: 86400 },
};
const REFRESH_MS = 10000;

const hourFmt = new Intl.DateTimeFormat("es-CL", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const weekFmt = new Intl.DateTimeFormat("es-CL", { weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

export class DetailView {
  constructor(root, busId, store) {
    this.root = root;
    this.busId = busId;
    this.store = store;
    this.range = "hour";
    this.chart = null;
    this.lastRefresh = 0;
    this.built = false;
    this.alive = true;
    root.innerHTML = `<p class="lede">Cargando…</p>`;
    if (store.state) this.update(store.state);
  }

  destroy() {
    this.alive = false;
    if (this.chart) this.chart.destroy();
  }

  bus(state) {
    return state.buses.find((b) => b.id === this.busId);
  }

  update(state) {
    const bus = this.bus(state);
    if (!bus) {
      this.built = false;
      this.root.innerHTML = `
        <div class="empty"><strong>No encontramos este bus.</strong>Puede que se haya quitado. Vuelve al panel para ver los buses actuales.</div>`;
      return;
    }
    if (!this.built) this.build(bus);
    this.fill(bus, state);
    if (Date.now() - this.lastRefresh > REFRESH_MS) this.refreshData(bus);
  }

  build(bus) {
    this.built = true;
    this.root.innerHTML = `
      <div class="detail-head">
        <h1 class="view-title" id="d-name"></h1>
        <span class="size-tag" id="d-type"></span>
        <span class="seen" id="d-route"></span>
      </div>
      <div class="detail-grid">
        <section class="gauge-card" id="d-gauge-card" aria-label="Ocupación actual">
          <svg class="gauge" viewBox="0 0 240 146" role="img" aria-labelledby="d-gauge-label">
            <title id="d-gauge-label"></title>
            <path class="track" d="M20 124 A100 100 0 0 1 220 124" fill="none" stroke-width="22" pathLength="100"/>
            <path class="arc" d="M20 124 A100 100 0 0 1 220 124" fill="none" stroke-width="22" pathLength="100"
                  stroke-dasharray="100" stroke-dashoffset="100"/>
            <text class="big" x="120" y="112" text-anchor="middle" id="d-count">0</text>
            <text class="small" x="120" y="142" text-anchor="middle" id="d-cap"></text>
          </svg>
          <div class="state-row">
            <span class="level"><i class="swatch" aria-hidden="true"></i><span id="d-level"></span></span>
            <span class="conn" id="d-conn"></span>
          </div>
          <p class="seen" id="d-seen"></p>
          <div class="inout">
            <div>Total de subidas<b id="d-in">0</b></div>
            <div>Total de bajadas<b id="d-out">0</b></div>
          </div>
          <ul class="flags" id="d-flags" style="padding:0"></ul>
          <p class="rec" id="d-rec" hidden>${icons.swap}<span></span></p>
        </section>
        <section class="chart-card" aria-labelledby="d-chart-title">
          <div class="chart-top">
            <h2 id="d-chart-title">Personas a bordo</h2>
            <div class="segmented" role="group" aria-label="Período del gráfico">
              ${Object.entries(RANGES).map(([k, r]) =>
                `<button type="button" data-range="${k}" aria-pressed="${k === this.range}"><span class="wide-only">${r.first} </span>${r.label}</button>`).join("")}
            </div>
          </div>
          <div class="chart-box"><canvas id="d-chart" aria-label="Gráfico de personas a bordo"></canvas></div>
          <p class="seen" id="d-chart-empty" hidden>Aún no hay datos en este período. El gráfico se completa mientras el bus está conectado.</p>
        </section>
      </div>
      <div class="lower-grid">
        <section class="chart-card" aria-labelledby="d-events-title">
          <div class="chart-top"><h2 id="d-events-title">Subidas y bajadas recientes</h2></div>
          <ul class="events" id="d-events"></ul>
        </section>
        <details class="advanced" id="d-advanced">
          <summary>Opciones avanzadas</summary>
          <div class="advanced-body">
            <dl class="facts">
              <dt>Señal WiFi</dt><dd id="d-rssi"></dd>
              <dt>Versión del equipo</dt><dd id="d-fw"></dd>
              <dt>Identificador</dt><dd>${esc(bus.id)}</dd>
            </dl>
            <p class="help">Usa estas opciones solo si el conteo quedó mal, por ejemplo al terminar el recorrido con el bus vacío.</p>
            <div class="cmd-row">
              <button type="button" class="btn btn-danger" id="d-reset">Reiniciar conteo a 0</button>
            </div>
            <div class="cmd-row">
              <div class="field">
                <label for="d-set-value">Fijar conteo en</label>
                <div class="suffix-wrap">
                  <input class="input input-num" id="d-set-value" type="number" min="0" max="1000" inputmode="numeric">
                  <span>personas</span>
                </div>
              </div>
              <button type="button" class="btn" id="d-set">Fijar conteo</button>
            </div>
          </div>
        </details>
      </div>`;

    this.root.querySelector(".segmented").addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-range]");
      if (!btn) return;
      this.range = btn.dataset.range;
      this.root.querySelectorAll("[data-range]").forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.range === this.range)));
      this.loadHistory();
    });
    $("#d-reset", this.root).addEventListener("click", () => this.sendReset());
    $("#d-set", this.root).addEventListener("click", () => this.sendSet());
  }

  fill(bus, state) {
    const card = $("#d-gauge-card", this.root);
    card.className = `gauge-card lvl-${bus.level}${bus.online ? "" : " is-off"}`;
    $("#d-name", this.root).textContent = bus.name;
    const tag = $("#d-type", this.root);
    tag.textContent = bus.type_name;
    tag.classList.toggle("big", bus.size === "big");
    $("#d-route", this.root).textContent = bus.route_name || "Sin recorrido asignado";
    const pct = Math.min(100, bus.occupancy_pct);
    $(".arc", card).setAttribute("stroke-dashoffset", String(100 - pct));
    $("#d-count", this.root).textContent = bus.count;
    $("#d-cap", this.root).textContent = `de ${bus.capacity} personas (${bus.occupancy_pct} %)`;
    $("#d-gauge-label", this.root).textContent = `${bus.count} de ${bus.capacity} personas`;
    $(".level", card).hidden = !bus.online;
    $("#d-level", this.root).textContent = bus.level_label;
    const conn = $("#d-conn", this.root);
    conn.textContent = bus.online ? "Conectado" : "Sin conexión";
    conn.classList.toggle("off", !bus.online);
    const seen = $("#d-seen", this.root);
    seen.dataset.seen = bus.last_seen ?? "";
    seen.dataset.prefix = bus.online ? "Actualizado" : "Último dato";
    seen.textContent = seenText(seen);
    $("#d-in", this.root).textContent = bus.in;
    $("#d-out", this.root).textContent = bus.out;
    const flags = bus.alerts.filter((a) => a.kind !== "offline");
    const ul = $("#d-flags", this.root);
    ul.innerHTML = flags.map((a) => `<li class="${a.kind}">${esc(a.message)}</li>`).join("");
    ul.hidden = !flags.length;
    const rec = $("#d-rec", this.root);
    rec.hidden = !bus.recommendation;
    if (bus.recommendation) $("span", rec).textContent = bus.recommendation.message;
    $("#d-rssi", this.root).textContent = signalWords(bus.rssi);
    $("#d-fw", this.root).textContent = bus.fw || "Sin dato";
    this.capacity = bus.capacity;
    this.count = bus.count;
    this.settings = state.settings;
  }

  refreshData() {
    this.lastRefresh = Date.now();
    this.loadHistory();
    this.loadEvents();
  }

  async loadEvents() {
    try {
      const events = await api(`/api/buses/${encodeURIComponent(this.busId)}/events?limit=30`);
      if (!this.alive) return;
      const ul = $("#d-events", this.root);
      ul.innerHTML = events.length
        ? events.map((e) => `
          <li class="${e.dir}">
            <span class="dir">${e.dir === "in" ? icons.up : icons.down}${e.dir === "in" ? "Subió" : "Bajó"}</span>
            <time>${clockTime(e.received_at)}</time>
          </li>`).join("")
        : `<li>Todavía no hay subidas ni bajadas registradas.</li>`;
    } catch { /* se reintenta en la proxima actualizacion */ }
  }

  async loadHistory() {
    const now = Date.now() / 1000;
    const span = RANGES[this.range].seconds;
    let points;
    try {
      points = await api(`/api/buses/${encodeURIComponent(this.busId)}/history?from=${now - span}&to=${now}`);
    } catch { return; }
    if (!this.alive) return;
    $("#d-chart-empty", this.root).hidden = points.length > 0;
    // El conteo actual se extiende hasta ahora para que la linea llegue al borde derecho
    if (points.length && this.count != null) points.push({ ts: now, count: this.count });
    this.drawChart(points, now - span, now);
  }

  drawChart(points, from, to) {
    const Chart = window.Chart;
    if (!Chart) return;
    const css = getComputedStyle(document.documentElement);
    const celeste = css.getPropertyValue("--celeste").trim();
    const fmt = this.range === "week" ? weekFmt : hourFmt;
    const data = points.map((p) => ({ x: p.ts * 1000, y: p.count }));
    const cap = this.capacity || 0;
    const capLine = [{ x: from * 1000, y: cap }, { x: to * 1000, y: cap }];
    if (this.chart) {
      this.chart.data.datasets[0].data = data;
      this.chart.data.datasets[1].data = capLine;
      this.chart.options.scales.x.min = from * 1000;
      this.chart.options.scales.x.max = to * 1000;
      this.chart.options.scales.x.ticks.callback = (v) => fmt.format(new Date(v));
      this.chart.options.scales.x.ticks.stepSize = RANGES[this.range].step * 1000;
      this.chart.update("none");
      return;
    }
    Chart.defaults.font.family = css.getPropertyValue("--font");
    Chart.defaults.font.size = 14;
    Chart.defaults.color = css.getPropertyValue("--ink-2").trim();
    this.chart = new Chart($("#d-chart", this.root), {
      type: "line",
      data: {
        datasets: [
          { label: "Personas a bordo", data, stepped: "before", borderColor: celeste, borderWidth: 3,
            backgroundColor: "rgba(77, 175, 214, 0.16)", fill: "origin", pointRadius: 0, pointHitRadius: 8 },
          { label: "Capacidad", data: capLine, borderColor: "#000000", borderWidth: 1.5, borderDash: [6, 5],
            pointRadius: 0, fill: false },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, parsing: true,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: {
          x: { type: "linear", min: from * 1000, max: to * 1000, grid: { display: false },
               ticks: { maxTicksLimit: 8, stepSize: RANGES[this.range].step * 1000,
                        callback: (v) => fmt.format(new Date(v)) } },
          y: { beginAtZero: true, suggestedMax: cap, ticks: { precision: 0 }, grid: { color: "#e3e9ed" } },
        },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 18, usePointStyle: false } },
          tooltip: {
            callbacks: {
              title: (items) => items.length ? clockTime(items[0].parsed.x / 1000) : "",
              label: (item) => item.datasetIndex === 0 ? ` ${item.parsed.y} personas` : ` Capacidad: ${item.parsed.y}`,
            },
          },
        },
      },
    });
  }

  async sendReset() {
    const name = $("#d-name", this.root).textContent;
    const ok = await confirmDialog({
      title: "¿Reiniciar el conteo a 0?",
      text: `${name} quedará con 0 personas. Hazlo solo si el bus está vacío.`,
      ok: "Reiniciar conteo", danger: true,
    });
    if (ok) this.sendCommand({ action: "reset" }, "Conteo reiniciado. El bus lo confirmará en unos segundos.");
  }

  async sendSet() {
    const input = $("#d-set-value", this.root);
    const value = Number(input.value);
    if (input.value === "" || !Number.isInteger(value) || value < 0 || value > 1000) {
      input.setAttribute("aria-invalid", "true");
      toast("Escribe un número entero de personas entre 0 y 1000.", true);
      input.focus();
      return;
    }
    input.removeAttribute("aria-invalid");
    const name = $("#d-name", this.root).textContent;
    const ok = await confirmDialog({
      title: `¿Fijar el conteo en ${value}?`,
      text: `${name} quedará con ${value} personas a bordo.`,
      ok: "Fijar conteo",
    });
    if (ok) this.sendCommand({ action: "set", count: value }, `Conteo fijado en ${value}. El bus lo confirmará en unos segundos.`);
  }

  async sendCommand(body, okMessage) {
    try {
      await api(`/api/buses/${encodeURIComponent(this.busId)}/cmd`, { method: "POST", body });
      toast(okMessage);
    } catch (e) {
      toast(e.message, true);
    }
  }
}
