"""Estado de los buses sobre SQLite: procesa mensajes MQTT y arma las vistas para la API."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from typing import Callable

from . import logic
from .logic import CountState

log = logging.getLogger("bus_counter")

BUS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HISTORY_MAX_POINTS = 600


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class NeedsConfirmation(Exception):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class BusService:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], float] = time.time,
                 on_change: Callable[[], None] | None = None, retention_days: int = 30):
        self.conn = conn
        self.clock = clock
        self.on_change = on_change or (lambda: None)
        self.retention_days = retention_days
        self.lock = threading.RLock()
        self._last_sample: dict[str, tuple[float, int]] = {}

    # ------------------------------------------------------------ configuracion

    def settings(self) -> dict[str, int]:
        with self.lock:
            rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        values = dict(logic.DEFAULT_SETTINGS)
        for r in rows:
            if r["key"] in values:
                values[r["key"]] = int(r["value"])
        return values

    def types(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT id, name, capacity FROM bus_types ORDER BY id")]

    def routes(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT id, name FROM routes ORDER BY id")]

    def get_config(self) -> dict:
        types, settings = self.types(), self.settings()
        check = logic.check_config(types, settings)
        return {"bus_types": types, "routes": self.routes(), "settings": settings,
                "notices": check.notices, "warnings": check.confirm}

    def update_config(self, *, bus_types: list[dict] | None = None, routes: list[dict] | None = None,
                      settings: dict | None = None, confirm: bool = False) -> dict:
        with self.lock:
            cur_types = {t["id"]: t for t in self.types()}
            cur_routes = {r["id"]: r for r in self.routes()}
            new_types = {k: dict(v) for k, v in cur_types.items()}
            errors: list[str] = []
            for t in bus_types or []:
                if t.get("id") not in cur_types:
                    errors.append("Solo se pueden editar los dos tipos de bus existentes.")
                    continue
                if "name" in t:
                    new_types[t["id"]]["name"] = str(t["name"]).strip()
                if "capacity" in t:
                    new_types[t["id"]]["capacity"] = t["capacity"]
            new_routes = {k: dict(v) for k, v in cur_routes.items()}
            for r in routes or []:
                if r.get("id") not in cur_routes:
                    errors.append("Solo se pueden editar los recorridos existentes.")
                    continue
                name = str(r.get("name", "")).strip()
                if not name or len(name) > 60:
                    errors.append("Cada recorrido necesita un nombre de hasta 60 letras.")
                new_routes[r["id"]]["name"] = name
            new_settings = self.settings()
            for k, v in (settings or {}).items():
                if k not in new_settings:
                    errors.append(f"Ajuste desconocido: {k}")
                else:
                    new_settings[k] = v
            if errors:
                raise ConfigError(errors)
            check = logic.check_config(list(new_types.values()), new_settings)
            if check.errors:
                raise ConfigError(check.errors)
            # Solo se pide confirmar avisos nuevos, no los que el usuario ya acepto antes
            already = set(logic.check_config(list(cur_types.values()), self.settings()).confirm)
            pending = [m for m in check.confirm if m not in already]
            if pending and not confirm:
                raise NeedsConfirmation(pending)

            self.conn.execute("BEGIN")
            try:
                for t in new_types.values():
                    self.conn.execute("UPDATE bus_types SET name = ?, capacity = ? WHERE id = ?",
                                      (t["name"], t["capacity"], t["id"]))
                for r in new_routes.values():
                    self.conn.execute("UPDATE routes SET name = ? WHERE id = ?", (r["name"], r["id"]))
                for k, v in new_settings.items():
                    self.conn.execute("UPDATE settings SET value = ? WHERE key = ?", (str(v), k))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        self.on_change()
        return self.get_config()

    # ------------------------------------------------------------ entrada MQTT

    def handle_message(self, topic: str, payload: bytes, retained: bool = False) -> bool:
        """Procesa un mensaje de buses/<id>/<tipo>. Devuelve True si cambio algo."""
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "buses" or not BUS_ID_RE.match(parts[1]):
            return False
        bus_id, kind = parts[1], parts[2]
        handler = {"event": self._on_event, "count": self._on_count, "status": self._on_status}.get(kind)
        if handler is None:
            return False
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("no es un objeto JSON")
        except ValueError as e:
            log.warning("Mensaje invalido en %s: %s", topic, e)
            return False
        with self.lock:
            try:
                handler(bus_id, data, retained)
            except (KeyError, TypeError, ValueError) as e:
                log.warning("Mensaje con datos invalidos en %s: %s (%s)", topic, data, e)
                return False
        self.on_change()
        return True

    def _ensure_bus(self, bus_id: str) -> None:
        if self.conn.execute("SELECT 1 FROM buses WHERE id = ?", (bus_id,)).fetchone():
            return
        types = self.types()
        big_id, _ = logic.bus_sizes(types)
        type_id = big_id or max(types, key=lambda t: t["capacity"])["id"]
        self.conn.execute("INSERT INTO buses (id, name, type_id) VALUES (?, ?, ?)",
                          (bus_id, f"Bus {bus_id}", type_id))
        log.info("Bus nuevo registrado: %s", bus_id)

    def _mark_seen(self, bus_id: str, retained: bool) -> None:
        # Los mensajes retenidos pueden ser antiguos: no prueban que el bus este conectado
        if not retained:
            self.conn.execute("UPDATE buses SET last_seen = ?, state = 'online' WHERE id = ?",
                              (self.clock(), bus_id))

    def _load_state(self, bus_id: str) -> CountState:
        r = self.conn.execute("SELECT count, in_total, out_total, last_seq, snapshot_seq, incomplete "
                              "FROM buses WHERE id = ?", (bus_id,)).fetchone()
        return CountState(r["count"], r["in_total"], r["out_total"], r["last_seq"], r["snapshot_seq"],
                          bool(r["incomplete"]))

    def _save_state(self, bus_id: str, s: CountState) -> None:
        self.conn.execute("UPDATE buses SET count = ?, in_total = ?, out_total = ?, last_seq = ?, "
                          "snapshot_seq = ?, incomplete = ? WHERE id = ?",
                          (s.count, s.in_total, s.out_total, s.last_seq, s.snapshot_seq,
                           int(s.incomplete), bus_id))

    def _on_event(self, bus_id: str, data: dict, retained: bool) -> None:
        seq, direction = data["seq"], data["dir"]
        if not _is_int(seq) or seq < 0 or direction not in ("in", "out"):
            raise ValueError("seq o dir invalidos")
        ts = data.get("ts") if _is_int(data.get("ts")) else None
        up_ms = data.get("up_ms") if _is_int(data.get("up_ms")) else None
        self._ensure_bus(bus_id)
        self._mark_seen(bus_id, retained)
        cur = self.conn.execute("INSERT OR IGNORE INTO events (bus_id, seq, dir, ts, up_ms, received_at) "
                                "VALUES (?, ?, ?, ?, ?, ?)", (bus_id, seq, direction, ts, up_ms, self.clock()))
        if cur.rowcount == 0:
            return  # duplicado: se ignora sin error
        self._save_state(bus_id, logic.apply_event(self._load_state(bus_id), seq, direction))

    def _on_count(self, bus_id: str, data: dict, retained: bool) -> None:
        seq, count, in_total, out_total = data["seq"], data["count"], data["in"], data["out"]
        if not all(_is_int(v) and v >= 0 for v in (seq, count, in_total, out_total)):
            raise ValueError("valores del snapshot invalidos")
        self._ensure_bus(bus_id)
        self._mark_seen(bus_id, retained)
        later = self.conn.execute("SELECT seq, dir FROM events WHERE bus_id = ? AND seq > ?",
                                  (bus_id, seq)).fetchall()
        state = logic.rebuild_from_snapshot(seq, count, in_total, out_total,
                                            [(r["seq"], r["dir"]) for r in later])
        self._save_state(bus_id, state)

    def _on_status(self, bus_id: str, data: dict, retained: bool) -> None:
        state = data["state"]
        if state not in ("online", "offline"):
            raise ValueError("state invalido")
        self._ensure_bus(bus_id)
        if state == "offline":
            # Last Will (o aviso del propio bus): queda sin conexion de inmediato
            self.conn.execute("UPDATE buses SET state = 'offline' WHERE id = ?", (bus_id,))
            return
        rssi = data.get("rssi") if _is_int(data.get("rssi")) else None
        fw = str(data["fw"])[:32] if data.get("fw") is not None else None
        up_ms = data.get("up_ms") if _is_int(data.get("up_ms")) else None
        self.conn.execute("UPDATE buses SET rssi = COALESCE(?, rssi), fw = COALESCE(?, fw), "
                          "up_ms = COALESCE(?, up_ms) WHERE id = ?", (rssi, fw, up_ms, bus_id))
        self._mark_seen(bus_id, retained)

    # ------------------------------------------------------------ tareas periodicas

    def sweep_offline(self) -> int:
        """Marca sin conexion los buses que llevan mas de offline_timeout_s sin mensajes."""
        limit = self.clock() - self.settings()["offline_timeout_s"]
        with self.lock:
            n = self.conn.execute("UPDATE buses SET state = 'offline' WHERE state = 'online' "
                                  "AND (last_seen IS NULL OR last_seen < ?)", (limit,)).rowcount
        if n:
            self.on_change()
        return n

    def sample_counts(self) -> int:
        """Guarda muestras de conteo de los buses conectados (para graficos y recomendaciones)."""
        now = self.clock()
        n = 0
        with self.lock:
            for r in self.conn.execute("SELECT id, count FROM buses WHERE state = 'online'").fetchall():
                last = self._last_sample.get(r["id"])
                if last is None:
                    row = self.conn.execute("SELECT ts, count FROM count_samples WHERE bus_id = ? "
                                            "ORDER BY ts DESC LIMIT 1", (r["id"],)).fetchone()
                    last = (row["ts"], row["count"]) if row else (None, None)
                if logic.should_sample(last[0], last[1], now, r["count"]):
                    self.conn.execute("INSERT INTO count_samples (bus_id, ts, count) VALUES (?, ?, ?)",
                                      (r["id"], now, r["count"]))
                    last = (now, r["count"])
                    n += 1
                self._last_sample[r["id"]] = last
        return n

    def cleanup(self) -> None:
        """Borra muestras y eventos mas antiguos que retention_days."""
        cutoff = self.clock() - self.retention_days * 86400
        with self.lock:
            self.conn.execute("DELETE FROM count_samples WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM events WHERE received_at < ?", (cutoff,))

    # ------------------------------------------------------------ vistas

    def _bus_rows(self, bus_id: str | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT b.*, t.name AS type_name, t.capacity, r.name AS route_name FROM buses b "
               "JOIN bus_types t ON t.id = b.type_id LEFT JOIN routes r ON r.id = b.route_id")
        if bus_id is not None:
            return self.conn.execute(sql + " WHERE b.id = ?", (bus_id,)).fetchall()
        return self.conn.execute(sql + " ORDER BY b.id").fetchall()

    def _views(self, bus_id: str | None = None) -> list[dict]:
        now = self.clock()
        settings = self.settings()
        big_id, small_id = logic.bus_sizes(self.types())
        window_start = now - settings["recommend_window_min"] * 60 - logic.SAMPLE_MAX_GAP_S
        views = []
        with self.lock:
            for r in self._bus_rows(bus_id):
                size = "big" if r["type_id"] == big_id else "small" if r["type_id"] == small_id else None
                online = r["state"] == "online"
                samples = [(s["ts"], s["count"]) for s in self.conn.execute(
                    "SELECT ts, count FROM count_samples WHERE bus_id = ? AND ts >= ? ORDER BY ts",
                    (r["id"], window_start))]
                rec = logic.evaluate_recommendation(
                    size=size, capacity=r["capacity"], online=online, incomplete=bool(r["incomplete"]),
                    samples=samples, current_count=r["count"], now=now, settings=settings)
                level = logic.occupancy_level(r["count"], r["capacity"], settings)
                views.append({
                    "id": r["id"], "name": r["name"],
                    "type_id": r["type_id"], "type_name": r["type_name"], "capacity": r["capacity"],
                    "size": size, "route_id": r["route_id"], "route_name": r["route_name"],
                    "online": online, "last_seen": r["last_seen"], "rssi": r["rssi"], "fw": r["fw"],
                    "up_ms": r["up_ms"], "count": r["count"], "in": r["in_total"], "out": r["out_total"],
                    "last_seq": r["last_seq"], "incomplete": bool(r["incomplete"]),
                    "occupancy_pct": round(100 * r["count"] / r["capacity"]),
                    "level": level, "level_label": logic.LEVEL_LABELS[level],
                    "recommendation": rec,
                    "alerts": logic.bus_alerts(online=online, incomplete=bool(r["incomplete"]),
                                               count=r["count"], capacity=r["capacity"]),
                })
        return views

    def list_buses(self) -> list[dict]:
        return self._views()

    def get_bus(self, bus_id: str) -> dict | None:
        views = self._views(bus_id)
        return views[0] if views else None

    @staticmethod
    def _recommendations_from(views: list[dict], enabled: bool) -> dict:
        items = [{"bus_id": v["id"], "bus_name": v["name"], "route_id": v["route_id"],
                  "route_name": v["route_name"], **v["recommendation"]}
                 for v in views if v["recommendation"]]
        return {"enabled": enabled, "notice": None if enabled else logic.MSG_SAME_CAPACITY, "items": items}

    @staticmethod
    def _alerts_from(views: list[dict]) -> list[dict]:
        return [{"bus_id": v["id"], "bus_name": v["name"], **a} for v in views for a in v["alerts"]]

    def recommendations(self) -> dict:
        enabled = logic.bus_sizes(self.types())[0] is not None
        return self._recommendations_from(self._views(), enabled)

    def alerts(self) -> list[dict]:
        return self._alerts_from(self._views())

    def live_state(self) -> dict:
        """Todo lo que necesita la pantalla principal, en un solo mensaje."""
        views = self._views()
        enabled = logic.bus_sizes(self.types())[0] is not None
        recs = self._recommendations_from(views, enabled)
        online = [v for v in views if v["online"]]
        return {
            "type": "state",
            "server_time": self.clock(),
            "buses": views,
            "recommendations": recs,
            "alerts": self._alerts_from(views),
            "routes": self.routes(),
            "settings": self.settings(),
            "summary": {"buses_online": len(online),
                        "passengers": sum(v["count"] for v in online),
                        "recommendations": len(recs["items"])},
        }

    def update_bus(self, bus_id: str, *, name: str | None = None, type_id: int | None = None,
                   route_id: int | None = None, set_route: bool = False) -> dict | None:
        with self.lock:
            if not self.conn.execute("SELECT 1 FROM buses WHERE id = ?", (bus_id,)).fetchone():
                return None
            errors = []
            if name is not None and not (0 < len(name.strip()) <= 60):
                errors.append("El nombre del bus debe tener entre 1 y 60 letras.")
            if type_id is not None and not self.conn.execute(
                    "SELECT 1 FROM bus_types WHERE id = ?", (type_id,)).fetchone():
                errors.append("Ese tipo de bus no existe.")
            if set_route and route_id is not None and not self.conn.execute(
                    "SELECT 1 FROM routes WHERE id = ?", (route_id,)).fetchone():
                errors.append("Ese recorrido no existe.")
            if errors:
                raise ConfigError(errors)
            if name is not None:
                self.conn.execute("UPDATE buses SET name = ? WHERE id = ?", (name.strip(), bus_id))
            if type_id is not None:
                self.conn.execute("UPDATE buses SET type_id = ? WHERE id = ?", (type_id, bus_id))
            if set_route:
                self.conn.execute("UPDATE buses SET route_id = ? WHERE id = ?", (route_id, bus_id))
        self.on_change()
        return self.get_bus(bus_id)

    def history(self, bus_id: str, frm: float, to: float) -> list[dict]:
        """Muestras de conteo entre frm y to. Si son muchas, agrupa y deja el maximo de cada grupo."""
        with self.lock:
            rows = self.conn.execute("SELECT ts, count FROM count_samples WHERE bus_id = ? AND ts BETWEEN ? AND ? "
                                     "ORDER BY ts", (bus_id, frm, to)).fetchall()
        points = [(r["ts"], r["count"]) for r in rows]
        if len(points) > HISTORY_MAX_POINTS:
            bucket = (to - frm) / HISTORY_MAX_POINTS
            grouped: dict[int, tuple[float, int]] = {}
            for ts, c in points:
                k = int((ts - frm) // bucket)
                if k not in grouped or c > grouped[k][1]:
                    grouped[k] = (ts, c)
            points = [grouped[k] for k in sorted(grouped)]
        return [{"ts": ts, "count": c} for ts, c in points]

    def events(self, bus_id: str, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT seq, dir, ts, up_ms, received_at FROM events WHERE bus_id = ? "
                                     "ORDER BY received_at DESC, seq DESC LIMIT ?", (bus_id, limit)).fetchall()
        return [dict(r) for r in rows]
