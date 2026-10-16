// Configuracion: tipos de bus, buses y recorridos, y recomendaciones.
import { api } from "./api.js";
import { $, esc, byName, confirmDialog, toast } from "./util.js";

// Mismos rangos que valida el servidor
const SETTINGS = [
  { key: "recommend_low_threshold", label: "Pocas personas", unit: "personas", min: 0, max: 1000,
    help: "Si un bus grande lleva esta cantidad de personas o menos durante todo el período evaluado, se sugiere cambiarlo por uno chico." },
  { key: "recommend_window_min", label: "Minutos a evaluar", unit: "minutos", min: 1, max: 240,
    help: "Cuánto tiempo se observa cada bus antes de sugerir un cambio. Más minutos dan sugerencias más seguras, pero más lentas." },
  { key: "recommend_full_pct", label: "Bus chico casi lleno", unit: "% de ocupación", min: 1, max: 100,
    help: "Si un bus chico se mantiene sobre este porcentaje durante todo el período evaluado, se sugiere uno grande." },
  { key: "color_amber_pct", label: "Casi lleno desde", unit: "% de ocupación", min: 1, max: 100,
    help: "Desde este porcentaje la tarjeta del bus se ve ámbar y dice \"Casi lleno\"." },
  { key: "color_red_pct", label: "Lleno desde", unit: "% de ocupación", min: 1, max: 100,
    help: "Desde este porcentaje la tarjeta se ve roja y dice \"Lleno\". Debe ser mayor que el de casi lleno." },
  { key: "offline_timeout_s", label: "Tiempo sin señal", unit: "segundos", min: 10, max: 3600,
    help: "Si no llegan datos de un bus durante este tiempo, se muestra como sin conexión." },
];

const SAME_CAPACITY = "Los dos tipos tienen la misma capacidad: las recomendaciones quedarán desactivadas.";

export class ConfigView {
  constructor(root, store) {
    this.root = root;
    this.store = store;
    root.innerHTML = `
      <h1 class="view-title">Configuración</h1>
      <p class="lede">Los cambios se aplican a todas las pantallas apenas los guardas.</p>
      <div id="cfg-body"><p class="lede">Cargando…</p></div>`;
    this.load();
  }

  async load() {
    try {
      const [config, buses] = await Promise.all([api("/api/config"), api("/api/buses")]);
      this.config = config;
      this.buses = buses.sort(byName);
      this.render();
    } catch (e) {
      $("#cfg-body", this.root).innerHTML = `<p class="errors">${esc(e.message)}</p>`;
    }
  }

  render() {
    const { bus_types: types, routes, settings, notices } = this.config;
    const bigId = [...types].sort((a, b) => b.capacity - a.capacity)[0].id;
    $("#cfg-body", this.root).innerHTML = `
      <section class="panel" id="p-types" aria-labelledby="h-types">
        <h2 id="h-types">Tipos de bus</h2>
        <p class="help">Nombre y cantidad de personas que caben en cada tipo. El de mayor capacidad se considera el bus grande.</p>
        ${types.map((t) => `
          <div class="type-row" data-type="${t.id}">
            <div class="field">
              <label for="t-name-${t.id}">Nombre ${t.id === bigId ? "del bus grande" : "del bus chico"}</label>
              <input class="input" id="t-name-${t.id}" name="name" maxlength="40" value="${esc(t.name)}">
            </div>
            <div class="field">
              <label for="t-cap-${t.id}">Capacidad</label>
              <div class="suffix-wrap">
                <input class="input input-num" id="t-cap-${t.id}" name="capacity" type="number" min="1" max="500" inputmode="numeric" value="${t.capacity}">
                <span>personas</span>
              </div>
            </div>
          </div>`).join("")}
        <p class="note" id="types-note" ${notices.length ? "" : "hidden"}>${esc(notices[0] || SAME_CAPACITY)}</p>
        <div class="panel-actions">
          <button type="button" class="btn btn-primary" id="save-types">Guardar tipos de bus</button>
          <span class="msg" id="msg-types" role="status"></span>
        </div>
      </section>

      <section class="panel" id="p-buses" aria-labelledby="h-buses">
        <h2 id="h-buses">Buses y recorridos</h2>
        <p class="help">Ponle a cada bus un nombre fácil de reconocer, su tipo y el recorrido que hace. Los buses nuevos aparecen aquí solos cuando envían su primer dato.</p>
        ${this.buses.length ? this.buses.map((b) => `
          <div class="bus-row" data-bus="${esc(b.id)}">
            <span class="bus-id" title="Identificador del equipo">${esc(b.id)}</span>
            <div class="field">
              <label for="b-name-${esc(b.id)}">Nombre</label>
              <input class="input" id="b-name-${esc(b.id)}" name="name" maxlength="60" value="${esc(b.name)}">
            </div>
            <div class="field">
              <label for="b-type-${esc(b.id)}">Tipo</label>
              <select class="select" id="b-type-${esc(b.id)}" name="type_id">
                ${types.map((t) => `<option value="${t.id}" ${t.id === b.type_id ? "selected" : ""}>${esc(t.name)}</option>`).join("")}
              </select>
            </div>
            <div class="field">
              <label for="b-route-${esc(b.id)}">Recorrido</label>
              <select class="select" id="b-route-${esc(b.id)}" name="route_id">
                <option value="" ${b.route_id == null ? "selected" : ""}>Sin recorrido</option>
                ${routes.map((r) => `<option value="${r.id}" ${r.id === b.route_id ? "selected" : ""}>${esc(r.name)}</option>`).join("")}
              </select>
            </div>
          </div>`).join("") : `<div class="empty"><strong>Todavía no hay buses.</strong>Aparecen aquí solos cuando un bus envía su primer dato.</div>`}
        <h3 class="subhead">Nombres de los recorridos</h3>
        <div class="form-grid">
          ${routes.map((r) => `
            <div class="field" data-route="${r.id}">
              <label for="r-name-${r.id}">Recorrido ${r.id}</label>
              <input class="input" id="r-name-${r.id}" maxlength="60" value="${esc(r.name)}">
            </div>`).join("")}
        </div>
        <div class="panel-actions">
          <button type="button" class="btn btn-primary" id="save-buses">Guardar buses y recorridos</button>
          <span class="msg" id="msg-buses" role="status"></span>
        </div>
      </section>

      <section class="panel" id="p-recs" aria-labelledby="h-recs">
        <h2 id="h-recs">Recomendaciones y colores</h2>
        <p class="help">Define cuándo se sugiere cambiar el tipo de bus y cuándo una tarjeta cambia de color.</p>
        <div class="form-grid">
          ${SETTINGS.map((s) => `
            <div class="field">
              <label for="s-${s.key}">${s.label}</label>
              <div class="suffix-wrap">
                <input class="input input-num" id="s-${s.key}" data-key="${s.key}" type="number" min="${s.min}" max="${s.max}" inputmode="numeric" value="${settings[s.key]}" aria-describedby="h-${s.key}">
                <span>${s.unit}</span>
              </div>
              <p class="help" id="h-${s.key}">${esc(s.help)}</p>
            </div>`).join("")}
        </div>
        <div class="panel-actions">
          <button type="button" class="btn btn-primary" id="save-recs">Guardar recomendaciones</button>
          <span class="msg" id="msg-recs" role="status"></span>
        </div>
      </section>`;

    const body = $("#cfg-body", this.root);
    body.addEventListener("input", (ev) => {
      if (ev.target.closest("#p-types")) this.checkTypes();
      ev.target.removeAttribute("aria-invalid");
    });
    $("#save-types", body).addEventListener("click", () => this.saveTypes());
    $("#save-buses", body).addEventListener("click", () => this.saveBuses());
    $("#save-recs", body).addEventListener("click", () => this.saveRecs());
  }

  readTypes() {
    return [...this.root.querySelectorAll("[data-type]")].map((row) => ({
      id: Number(row.dataset.type),
      name: $("[name=name]", row).value.trim(),
      capacity: Number($("[name=capacity]", row).value),
    }));
  }

  checkTypes() {
    const types = this.readTypes();
    const note = $("#types-note", this.root);
    note.hidden = !(types.length === 2 && types[0].capacity === types[1].capacity);
    note.textContent = SAME_CAPACITY;
  }

  // Envia la configuracion; si el servidor pide confirmar, pregunta y reintenta
  async putConfig(body, msgId) {
    const msg = $(msgId, this.root);
    msg.className = "msg";
    msg.textContent = "Guardando…";
    try {
      let result;
      try {
        result = await api("/api/config", { method: "PUT", body });
      } catch (e) {
        if (e.status !== 409) throw e;
        const ok = await confirmDialog({
          title: "¿Guardar de todas formas?",
          text: (e.data.detail || []).join(" "),
          ok: "Guardar igual",
        });
        if (!ok) { msg.textContent = "No se guardó."; return null; }
        result = await api("/api/config", { method: "PUT", body: { ...body, confirm: true } });
      }
      msg.className = "msg ok";
      msg.textContent = "Cambios guardados.";
      return result;
    } catch (e) {
      msg.className = "msg err";
      msg.textContent = e.message;
      return null;
    }
  }

  async saveTypes() {
    const types = this.readTypes();
    for (const t of types) {
      if (!t.name) return this.fail("#msg-types", `#t-name-${t.id}`, "Cada tipo de bus necesita un nombre.");
      if (!Number.isInteger(t.capacity) || t.capacity < 1 || t.capacity > 500) {
        return this.fail("#msg-types", `#t-cap-${t.id}`, "La capacidad debe ser un número entero entre 1 y 500.");
      }
    }
    const result = await this.putConfig({ bus_types: types }, "#msg-types");
    if (result) { this.config = result; toast("Tipos de bus guardados."); }
  }

  async saveBuses() {
    const msg = $("#msg-buses", this.root);
    const routes = [...this.root.querySelectorAll("[data-route]")].map((f) => ({
      id: Number(f.dataset.route), name: $("input", f).value.trim(),
    }));
    const emptyRoute = routes.find((r) => !r.name);
    if (emptyRoute) return this.fail("#msg-buses", `#r-name-${emptyRoute.id}`, "Cada recorrido necesita un nombre.");
    const rows = [...this.root.querySelectorAll("[data-bus]")];
    for (const row of rows) {
      if (!$("[name=name]", row).value.trim()) {
        return this.fail("#msg-buses", `#b-name-${CSS.escape(row.dataset.bus)}`, "Cada bus necesita un nombre.");
      }
    }
    msg.className = "msg";
    msg.textContent = "Guardando…";
    try {
      const routesChanged = routes.some((r) => this.config.routes.find((o) => o.id === r.id).name !== r.name);
      if (routesChanged) this.config = await api("/api/config", { method: "PUT", body: { routes } });
      for (const row of rows) {
        const old = this.buses.find((b) => b.id === row.dataset.bus);
        const name = $("[name=name]", row).value.trim();
        const typeId = Number($("[name=type_id]", row).value);
        const routeVal = $("[name=route_id]", row).value;
        const routeId = routeVal === "" ? null : Number(routeVal);
        if (name !== old.name || typeId !== old.type_id || routeId !== old.route_id) {
          const updated = await api(`/api/buses/${encodeURIComponent(old.id)}`, {
            method: "PUT", body: { name, type_id: typeId, route_id: routeId },
          });
          Object.assign(old, updated);
        }
      }
      msg.className = "msg ok";
      msg.textContent = "Cambios guardados.";
      toast("Buses y recorridos guardados.");
    } catch (e) {
      msg.className = "msg err";
      msg.textContent = e.message;
    }
  }

  async saveRecs() {
    const settings = {};
    for (const s of SETTINGS) {
      const input = $(`#s-${s.key}`, this.root);
      const v = Number(input.value);
      if (input.value === "" || !Number.isInteger(v) || v < s.min || v > s.max) {
        return this.fail("#msg-recs", `#s-${s.key}`, `"${s.label}" debe ser un número entero entre ${s.min} y ${s.max}.`);
      }
      settings[s.key] = v;
    }
    if (settings.color_amber_pct >= settings.color_red_pct) {
      return this.fail("#msg-recs", "#s-color_amber_pct", "\"Casi lleno desde\" debe ser menor que \"Lleno desde\".");
    }
    const result = await this.putConfig({ settings }, "#msg-recs");
    if (result) { this.config = result; toast("Recomendaciones guardadas."); }
  }

  fail(msgId, inputSel, text) {
    const msg = $(msgId, this.root);
    msg.className = "msg err";
    msg.textContent = text;
    const input = $(inputSel, this.root);
    if (input) { input.setAttribute("aria-invalid", "true"); input.focus(); }
    return null;
  }
}
