// Panel principal, cabecera, recorridos y navegacion entre vistas.
import { connectLive } from "./api.js";
import { $, esc, clock, byName, busArt, setBusFill, icons, seenText } from "./util.js";
import { DetailView } from "./detail.js";
import { ConfigView } from "./config.js";

const store = { state: null, linkUp: false };
let view = null;

// ------------------------------------------------------------ cabecera y avisos

function renderSummary(state) {
  const s = state.summary;
  $("#sum-buses").textContent = s.buses_online;
  $("#sum-passengers").textContent = s.passengers;
  $("#sum-recs").textContent = s.recommendations;
  if ($("#recs-dialog").open) renderRecsList(state);
}

function renderRecsList(state) {
  const recs = state.recommendations;
  const box = $("#recs-list");
  if (!recs.enabled) {
    box.innerHTML = `<p class="note">${esc(recs.notice)}</p>`;
    return;
  }
  if (!recs.items.length) {
    box.innerHTML = `<p class="rec-item">No hay recomendaciones por ahora. Aparecen cuando un bus va con muy pocas personas o casi lleno durante un rato.</p>`;
    return;
  }
  box.innerHTML = recs.items.map((r) => `
    <div class="rec-item">
      <a href="#/bus/${encodeURIComponent(r.bus_id)}" data-close>${esc(r.bus_name)}</a>
      <span class="route">${esc(r.route_name || "Sin recorrido")}</span>
      <span>${esc(r.message)}</span>
    </div>`).join("");
}

function openRecs() {
  if (!store.state) return;
  renderRecsList(store.state);
  $("#recs-dialog").showModal();
}

function renderLinkStatus() {
  const bar = $("#link-status");
  let text = null;
  if (!store.linkUp) {
    text = "Se perdió la conexión con el servidor. Reintentando…";
  } else if (store.state && !store.state.mqtt_connected) {
    text = "El servidor no está recibiendo datos de los buses. Revisa que el sistema esté encendido completo.";
  }
  bar.hidden = !text;
  if (text) bar.innerHTML = `<p><span class="pulse" aria-hidden="true"></span>${esc(text)}</p>`;
}

// ------------------------------------------------------------ franja de recorridos

const TAB_KEY = "bus-counter.tab";

function currentTab() {
  try { return localStorage.getItem(TAB_KEY) || "all"; } catch { return "all"; }
}

function setTab(tab) {
  try { localStorage.setItem(TAB_KEY, tab); } catch { /* sin almacenamiento */ }
}

function matchesTab(bus, tab) {
  if (tab === "all") return true;
  if (tab === "none") return bus.route_id == null;
  return String(bus.route_id) === tab;
}

function renderBand(mode) {
  const band = $("#band");
  if (mode !== "main") {
    band.dataset.signature = mode;
    band.innerHTML = `<div class="band-inner"><a class="tab band-back" href="#/">Volver a todos los buses</a></div>`;
    return;
  }
  const state = store.state;
  const buses = state ? state.buses : [];
  const tabs = [{ key: "all", label: "Todos" }]
    .concat((state ? state.routes : []).map((r) => ({ key: String(r.id), label: r.name })))
    .concat([{ key: "none", label: "Sin recorrido" }]);
  let active = currentTab();
  if (!tabs.some((t) => t.key === active)) active = "all";
  const signature = "main|" + tabs.map((t) => `${t.key}:${t.label}`).join("|");
  if (band.dataset.signature === signature) {
    // Mismas pestanas: solo se actualizan contadores y seleccion (no se pierde el foco)
    band.querySelectorAll("[data-tab]").forEach((btn) => {
      btn.setAttribute("aria-selected", String(btn.dataset.tab === active));
      btn.querySelector(".n").textContent = buses.filter((b) => matchesTab(b, btn.dataset.tab)).length;
    });
    return;
  }
  band.dataset.signature = signature;
  band.innerHTML = `<div class="band-inner" role="tablist">${tabs.map((t) => `
    <button type="button" class="tab" role="tab" data-tab="${t.key}" aria-selected="${t.key === active}">
      ${esc(t.label)} <span class="n">${buses.filter((b) => matchesTab(b, t.key)).length}</span>
    </button>`).join("")}</div>`;
}

$("#band").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-tab]");
  if (!btn) return;
  setTab(btn.dataset.tab);
  renderBand("main");
  if (view && view.refilter) view.refilter();
});

// ------------------------------------------------------------ vista principal

class MainView {
  constructor(root) {
    this.root = root;
    this.cards = new Map();
    root.innerHTML = `
      <h1 class="view-title" id="main-title">Buses</h1>
      <p class="lede">Cada tarjeta muestra cuántas personas lleva el bus ahora. Toca una para ver su detalle.</p>
      <div class="grid" id="cards"></div>
      <div class="empty" id="empty" hidden></div>`;
    this.grid = $("#cards", root);
    if (store.state) this.update(store.state);
  }

  refilter() { if (store.state) this.update(store.state); }

  update(state) {
    const tab = currentTab();
    const buses = state.buses.filter((b) => matchesTab(b, tab)).sort(byName);
    const ids = new Set(buses.map((b) => b.id));
    for (const [id, el] of this.cards) {
      if (!ids.has(id)) { el.remove(); this.cards.delete(id); }
    }
    buses.forEach((bus, i) => {
      let el = this.cards.get(bus.id);
      if (!el) {
        el = createCard(bus);
        this.cards.set(bus.id, el);
      }
      if (this.grid.children[i] !== el) this.grid.insertBefore(el, this.grid.children[i] || null);
      updateCard(el, bus);
    });
    const empty = $("#empty", this.root);
    empty.hidden = buses.length > 0;
    if (!buses.length) {
      empty.innerHTML = state.buses.length
        ? "<strong>No hay buses en este recorrido.</strong>Elige otro recorrido arriba o asígnalo en Configuración."
        : "<strong>Todavía no hay buses.</strong>Aparecen solos cuando un bus envía su primer dato.";
    }
  }
}

function createCard(bus) {
  const el = document.createElement("article");
  el.className = "bus";
  el.innerHTML = `
    <a class="bus-main" href="#/bus/${encodeURIComponent(bus.id)}">
      <div class="bus-head"><h2 class="bus-name"></h2><span class="size-tag"></span></div>
      ${busArt(bus.id)}
      <p class="count"><span class="now"></span><span class="cap"></span></p>
      <div class="state-row">
        <span class="level"><i class="swatch" aria-hidden="true"></i><span class="level-text"></span></span>
        <span class="conn"></span>
      </div>
      <p class="seen"></p>
    </a>
    <ul class="flags"></ul>
    <p class="rec" hidden>${icons.swap}<span></span></p>`;
  return el;
}

function updateCard(el, bus) {
  el.className = `bus lvl-${bus.level}${bus.online ? "" : " is-off"}`;
  $(".bus-name", el).textContent = bus.name;
  const tag = $(".size-tag", el);
  tag.textContent = bus.type_name;
  tag.classList.toggle("big", bus.size === "big");
  setBusFill($(".bus-art", el), bus.count, bus.capacity);
  $(".now", el).textContent = bus.count;
  $(".cap", el).textContent = `/ ${bus.capacity}`;
  $(".level", el).hidden = !bus.online;
  $(".level-text", el).textContent = bus.level_label;
  const conn = $(".conn", el);
  conn.textContent = bus.online ? "Conectado" : "Sin conexión";
  conn.classList.toggle("off", !bus.online);
  const seen = $(".seen", el);
  seen.dataset.seen = bus.last_seen ?? "";
  seen.dataset.prefix = bus.online ? "Actualizado" : "Último dato";
  seen.textContent = seenText(seen);
  $(".bus-main", el).setAttribute("aria-label",
    `${bus.name}, bus ${bus.type_name.toLowerCase()}, ${bus.count} de ${bus.capacity} personas, ` +
    (bus.online ? `${bus.level_label}, conectado` : "sin conexión"));

  // El aviso de sin conexion ya se ve en la tarjeta; aqui van los demas
  const flags = bus.alerts.filter((a) => a.kind !== "offline");
  const ul = $(".flags", el);
  ul.innerHTML = flags.map((a) => `<li class="${a.kind}">${esc(a.message)}</li>`).join("");
  ul.hidden = !flags.length;
  const rec = $(".rec", el);
  rec.hidden = !bus.recommendation;
  if (bus.recommendation) $("span", rec).textContent = bus.recommendation.message;
}

// ------------------------------------------------------------ navegacion

function route() {
  const hash = location.hash || "#/";
  if (view && view.destroy) view.destroy();
  const root = $("#view");
  const settings = $(".settings-link");
  settings.removeAttribute("aria-current");
  const m = hash.match(/^#\/bus\/(.+)$/);
  if (m) {
    renderBand("detail");
    view = new DetailView(root, decodeURIComponent(m[1]), store);
    document.title = "Detalle del bus · Contador de pasajeros";
  } else if (hash === "#/config") {
    renderBand("config");
    settings.setAttribute("aria-current", "page");
    view = new ConfigView(root, store);
    document.title = "Configuración · Contador de pasajeros";
  } else {
    renderBand("main");
    view = new MainView(root);
    document.title = "Contador de pasajeros";
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", () => { route(); $("#view").focus({ preventScroll: true }); });

// ------------------------------------------------------------ eventos globales

$("#open-recs").addEventListener("click", openRecs);
$("#sum-recs").addEventListener("click", openRecs);
$("#recs-dialog").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-close]") || ev.target === ev.currentTarget) $("#recs-dialog").close();
});

// "Actualizado hace X s" avanza solo, sin esperar al servidor
setInterval(() => {
  document.querySelectorAll("[data-seen]").forEach((el) => { el.textContent = seenText(el); });
}, 1000);

connectLive({
  onState(state) {
    clock.sync(state.server_time);
    store.state = state;
    renderSummary(state);
    renderLinkStatus();
    if (view instanceof MainView) renderBand("main");
    if (view && view.update) view.update(state);
  },
  onLink(up) {
    store.linkUp = up;
    renderLinkStatus();
  },
});

route();
