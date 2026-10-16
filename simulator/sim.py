"""Simulador de N buses que publican por MQTT con TLS, igual que el ESP32.

Cada bus usa su propio usuario y clave (de .env), publica event/count/status, tiene
Last Will y responde a los comandos reset y set. Ademas se pueden forzar fallas:
un bus sin conexion, un salto de seq y un conteo sospechoso.
"""
from __future__ import annotations

import argparse
import json
import random
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = Path(__file__).resolve().parent / "state.json"
FW_VERSION = "sim-0.1.0"
COUNT_EVERY_S = 10
STATUS_EVERY_S = 30
OFFLINE_BUFFER = 500
TICK_S = 0.2

# Escenarios de los primeros 6 buses: tipo, recorrido y perfil de pasajeros.
#   normal: sube y baja gente con ocupacion variable
#   low:    bus grande con pocas personas (dispara "conviene un bus chico")
#   full:   bus chico que se llena y se mantiene lleno (dispara "conviene un bus grande")
SCENARIOS = [
    ("big", "UAI-Grecia", "normal"),
    ("big", "UAI-Dominicos", "low"),
    ("small", "UAI-Grecia", "full"),
    ("small", "UAI-Fernando Castillo Velasco", "normal"),
    ("big", "UAI-Fernando Castillo Velasco", "normal"),
    ("small", "UAI-Dominicos", "normal"),
]


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


# ------------------------------------------------------------------ fallas forzadas

@dataclass
class Fault:
    bus_id: str
    start_s: float
    duration_s: float


def parse_fault(text: str, default_start: float, default_duration: float) -> Fault:
    """Formato BUS[:inicio_s[:duracion_s]], por ejemplo B04:60:90."""
    parts = text.split(":")
    try:
        start = float(parts[1]) if len(parts) > 1 and parts[1] else default_start
        duration = float(parts[2]) if len(parts) > 2 and parts[2] else default_duration
    except ValueError:
        raise argparse.ArgumentTypeError(f"Formato invalido '{text}'. Usa BUS[:inicio_s[:duracion_s]]")
    return Fault(parts[0], start, duration)


# ------------------------------------------------------------------ bus simulado

@dataclass
class SimBus:
    bus_id: str
    password: str
    size: str            # 'big' o 'small'
    route: str
    profile: str         # 'normal', 'low' o 'full'
    capacity: int
    cfg: dict
    seq: int = 0
    count: int = 0
    in_total: int = 0
    out_total: int = 0
    client: mqtt.Client | None = None
    connected: bool = False
    started: float = field(default_factory=time.monotonic)
    next_stop_at: float = 0.0
    planned: deque = field(default_factory=deque)       # (momento, 'in'|'out')
    pending: deque = field(default_factory=lambda: deque(maxlen=OFFLINE_BUFFER))
    commands: deque = field(default_factory=deque)
    last_count_pub: float = 0.0
    last_status_pub: float = 0.0
    count_dirty: bool = True
    offline_until: float = 0.0
    hold_snapshots_until: float = 0.0
    suspicious_until: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    # -------------------------------------------------- conexion

    def up_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def connect(self) -> None:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{self.bus_id}",
                        clean_session=False, protocol=mqtt.MQTTv311)
        c.username_pw_set(self.bus_id, self.password)
        c.tls_set(ca_certs=self.cfg["ca_file"], tls_version=ssl.PROTOCOL_TLS_CLIENT)
        c.will_set(f"buses/{self.bus_id}/status", json.dumps({"state": "offline"}), qos=1, retain=True)
        c.reconnect_delay_set(1, 30)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.connect_async(self.cfg["host"], self.cfg["port"], keepalive=15)
        c.loop_start()
        self.client = c

    def _on_connect(self, client, _u, _f, rc, _p):
        if rc.is_failure:
            log(f"[{self.bus_id}] el broker rechazo la conexion: {rc}")
            return
        self.connected = True
        client.subscribe(f"buses/{self.bus_id}/cmd", qos=1)
        self.last_status_pub = 0.0  # publica online de inmediato
        self.count_dirty = True
        log(f"[{self.bus_id}] conectado")

    def _on_disconnect(self, _c, _u, _f, rc, _p):
        if self.connected:
            log(f"[{self.bus_id}] desconectado ({rc})")
        self.connected = False

    def _on_message(self, _c, _u, msg):
        try:
            self.commands.append(json.loads(msg.payload))
        except ValueError:
            log(f"[{self.bus_id}] comando invalido: {msg.payload!r}")

    def drop_connection(self) -> None:
        """Corta la conexion sin despedirse, como un bus que pierde la senal: el broker manda el Last Will."""
        if self.client is None:
            return
        c, self.client = self.client, None
        c.loop_stop()
        sock = c.socket()
        if sock is not None:
            sock.close()
        self.connected = False

    def close(self) -> None:
        """Apagado ordenado: avisa offline y se desconecta."""
        if self.client is None:
            return
        if self.connected:
            self.client.publish(f"buses/{self.bus_id}/status", json.dumps({"state": "offline"}),
                                qos=1, retain=True).wait_for_publish(3)
            self.client.disconnect()
        self.client.loop_stop()

    def publish(self, kind: str, payload: dict, retain: bool = False) -> bool:
        if not self.connected or self.client is None:
            return False
        info = self.client.publish(f"buses/{self.bus_id}/{kind}", json.dumps(payload), qos=1, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    # -------------------------------------------------- pasajeros

    def plan_stop(self, now: float) -> None:
        """Decide cuantas personas suben y bajan en la proxima parada."""
        cap, c = self.capacity, self.count
        if self.profile == "low":
            target = random.randint(3, 10)
            outs = random.randint(0, min(c, 3))
            remain = c - outs
            ins = max(0, min(target - remain, 15 - remain))
            ins_first = False                       # primero bajan: nunca supera el umbral
        elif self.profile == "full":
            floor = int(0.92 * cap) + 1             # se mantiene sobre el 90 %
            ins = random.randint(1, 4) if c >= floor else floor - c + random.randint(0, 2)
            ins = min(ins, max(0, cap - c))
            outs = random.randint(0, max(0, c + ins - floor))
            ins_first = True                        # primero suben: no baja del 90 %
        else:
            target = random.randint(int(0.3 * cap), int(0.8 * cap))
            outs = random.randint(0, max(0, int(0.35 * c)))
            ins = max(0, min(target - (c - outs) + random.randint(-2, 2), cap - (c - outs)))
            ins_first = False
        order = (["in"] * ins + ["out"] * outs) if ins_first else (["out"] * outs + ["in"] * ins)
        t = now + random.uniform(0.5, 1.5)
        for d in order:
            self.planned.append((t, d))
            t += random.uniform(0.8, 2.0)
        self.next_stop_at = t + random.uniform(15, 40)
        if ins or outs:
            log(f"[{self.bus_id}] parada: suben {ins}, bajan {outs}")

    def passenger(self, direction: str, publish: bool = True) -> None:
        with self.lock:
            self.seq += 1
            if direction == "in":
                self.count += 1
                self.in_total += 1
            else:
                self.count = max(0, self.count - 1)
                self.out_total += 1
            self.count_dirty = True
            ev = {"seq": self.seq, "dir": direction, "ts": int(time.time()), "up_ms": self.up_ms()}
        if not publish:
            return
        # Sin conexion (o con eventos atrasados en cola) se guarda para reenviar en orden
        if self.pending or not self.publish("event", ev):
            self.pending.append(ev)

    # -------------------------------------------------- comandos y publicaciones

    def handle_commands(self) -> None:
        while self.commands:
            cmd = self.commands.popleft()
            with self.lock:
                if cmd.get("action") == "reset":
                    self.count = 0
                elif cmd.get("action") == "set" and isinstance(cmd.get("count"), int) and cmd["count"] >= 0:
                    self.count = cmd["count"]
                else:
                    log(f"[{self.bus_id}] comando desconocido: {cmd}")
                    continue
                self.count_dirty = True
            log(f"[{self.bus_id}] comando {cmd} -> conteo {self.count}")
            self.last_count_pub = 0.0

    def tick(self, now: float) -> None:
        self.handle_commands()
        if self.offline_until and now >= self.offline_until:
            self.offline_until = 0.0
            log(f"[{self.bus_id}] recupera la senal y reconecta")
            self.connect()
        if not self.planned and now >= self.next_stop_at:
            self.plan_stop(now)
        while self.planned and self.planned[0][0] <= now:
            self.passenger(self.planned.popleft()[1])

        if not self.connected:
            return
        while self.pending:  # reenvia en orden lo guardado sin conexion
            if not self.publish("event", self.pending[0]):
                return
            self.pending.popleft()
        wall = time.time()
        if now >= self.hold_snapshots_until and (
                (self.count_dirty and now - self.last_count_pub >= 1) or now - self.last_count_pub >= COUNT_EVERY_S):
            with self.lock:
                snap = {"seq": self.seq, "count": self.count, "in": self.in_total, "out": self.out_total,
                        "ts": int(wall)}
            if self.publish("count", snap, retain=True):
                self.count_dirty = False
                self.last_count_pub = now
        if now - self.last_status_pub >= STATUS_EVERY_S:
            status = {"state": "online", "rssi": random.randint(-80, -55), "fw": FW_VERSION, "up_ms": self.up_ms()}
            if self.publish("status", status, retain=True):
                self.last_status_pub = now


# ------------------------------------------------------------------ servidor (configuracion inicial)

def api(server: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


def fetch_capacities(server: str) -> dict[str, dict] | None:
    """Devuelve {'big': tipo, 'small': tipo} segun la configuracion del servidor."""
    try:
        types = api(server, "GET", "/api/config")["bus_types"]
    except (OSError, ValueError, KeyError):
        return None
    ordered = sorted(types, key=lambda t: t["capacity"])
    return {"small": ordered[0], "big": ordered[-1]}


def configure_buses(server: str, buses: list[SimBus], stop: threading.Event) -> None:
    """Asigna nombre, tipo y recorrido a los buses que siguen con los valores por defecto."""
    pending = {b.bus_id: b for b in buses}
    while pending and not stop.is_set():
        try:
            types = fetch_capacities(server)
            routes = {r["name"]: r["id"] for r in api(server, "GET", "/api/config")["routes"]}
            known = {b["id"]: b for b in api(server, "GET", "/api/buses")}
        except (OSError, ValueError, KeyError):
            stop.wait(3)
            continue
        for bus_id, b in list(pending.items()):
            current = known.get(bus_id)
            if current is None:
                continue  # el servidor aun no lo registra
            if current["name"] == f"Bus {bus_id}" and current["route_id"] is None and types:
                n = int("".join(ch for ch in bus_id if ch.isdigit()) or 0)
                body = {"name": f"Bus {n}" if n else bus_id, "type_id": types[b.size]["id"],
                        "route_id": routes.get(b.route)}
                try:
                    api(server, "PUT", f"/api/buses/{bus_id}", body)
                    log(f"[{bus_id}] configurado en el servidor: {body['name']}, "
                        f"{types[b.size]['name']}, {b.route}")
                except (OSError, ValueError):
                    continue
            del pending[bus_id]
        stop.wait(2)


# ------------------------------------------------------------------ estado persistente

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(buses: list[SimBus]) -> None:
    data = {b.bus_id: {"seq": b.seq, "count": b.count, "in": b.in_total, "out": b.out_total} for b in buses}
    STATE_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="simulator", description="Simulador de buses para Bus Counter")
    p.add_argument("--buses", type=int, default=6, help="cantidad de buses (por defecto 6)")
    p.add_argument("--server", default=None, help="URL del servidor web (por defecto http://127.0.0.1:<SERVER_PORT>)")
    p.add_argument("--no-configure", action="store_true", help="no asigna tipo ni recorrido en el servidor")
    p.add_argument("--offline", action="append", default=[], metavar="BUS[:inicio[:duracion]]",
                   help="corta la conexion de un bus (Last Will). Por defecto a los 60 s, por 90 s")
    p.add_argument("--seq-gap", action="append", default=[], metavar="BUS[:inicio[:duracion]]",
                   help="pierde 3 eventos (salto de seq) y retiene snapshots. Por defecto a los 45 s, por 40 s")
    p.add_argument("--suspicious", action="append", default=[], metavar="BUS[:inicio[:duracion]]",
                   help="sube gente hasta pasar el 120 %% de la capacidad. Por defecto a los 30 s, por 120 s")
    p.add_argument("--reset-state", action="store_true", help="parte de cero (seq y conteos)")
    p.add_argument("--seed", type=int, default=None, help="semilla para repetir la misma simulacion")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.seed is not None:
        random.seed(args.seed)
    env = read_env()
    ids = [b.strip() for b in env.get("BUS_IDS", "").split(",") if b.strip()]
    if args.buses > len(ids):
        print(f"Se pidieron {args.buses} buses pero .env tiene {len(ids)} en BUS_IDS. "
              "Agrega IDs en BUS_IDS y vuelve a encender el broker (python manage.py start).")
        return 1
    ca = ROOT / "broker" / "certs" / "ca.crt"
    if not ca.exists():
        print("Falta broker/certs/ca.crt. Ejecuta primero: python manage.py start")
        return 1
    cfg = {"host": env.get("MQTT_HOST", "127.0.0.1"), "port": int(env.get("MQTT_PORT", "8883")),
           "ca_file": str(ca)}
    server = args.server or f"http://127.0.0.1:{env.get('SERVER_PORT', '8000')}"

    faults = {"offline": [parse_fault(f, 60, 90) for f in args.offline],
              "seq_gap": [parse_fault(f, 45, 40) for f in args.seq_gap],
              "suspicious": [parse_fault(f, 30, 120) for f in args.suspicious]}
    chosen = ids[:args.buses]
    for kind, items in faults.items():
        for f in items:
            if f.bus_id not in chosen:
                print(f"El bus {f.bus_id} de --{kind.replace('_', '-')} no esta en la simulacion ({', '.join(chosen)}).")
                return 1

    types = None
    for _ in range(10):
        types = fetch_capacities(server)
        if types:
            break
        time.sleep(1)
    caps = {"big": types["big"]["capacity"], "small": types["small"]["capacity"]} if types else {"big": 50, "small": 25}

    state = {} if args.reset_state else load_state()
    buses: list[SimBus] = []
    for i, bus_id in enumerate(chosen):
        size, route, profile = SCENARIOS[i] if i < len(SCENARIOS) else (
            random.choice(["big", "small"]), random.choice(SCENARIOS)[1], "normal")
        b = SimBus(bus_id, env.get(f"BUS_PASSWORD_{bus_id}", ""), size, route, profile, caps[size], cfg)
        s = state.get(bus_id)
        if s:
            b.seq, b.count, b.in_total, b.out_total = s["seq"], s["count"], s["in"], s["out"]
        else:
            # Sin estado guardado, el bus parte "en servicio" con algunos pasajeros
            b.count = {"low": random.randint(4, 8),
                       "full": random.randint(int(0.92 * b.capacity) + 1, b.capacity)}.get(
                profile, random.randint(int(0.3 * b.capacity), int(0.6 * b.capacity)))
            b.in_total = b.count
        b.next_stop_at = time.monotonic() + random.uniform(1, 8)
        buses.append(b)
        b.connect()
    log(f"Simulando {len(buses)} buses: " + ", ".join(f"{b.bus_id} ({b.profile})" for b in buses))

    stop = threading.Event()
    if not args.no_configure:
        threading.Thread(target=configure_buses, args=(server, buses, stop), daemon=True).start()

    by_id = {b.bus_id: b for b in buses}
    t0 = time.monotonic()
    done: set[int] = set()
    last_save = 0.0
    try:
        while True:
            now = time.monotonic()
            elapsed = now - t0
            for kind, items in faults.items():
                for f in items:
                    if id(f) in done or elapsed < f.start_s:
                        continue
                    done.add(id(f))
                    b = by_id[f.bus_id]
                    if kind == "offline":
                        log(f"[{b.bus_id}] FALLA: pierde la conexion por {f.duration_s:.0f} s (Last Will)")
                        b.drop_connection()
                        b.offline_until = now + f.duration_s
                    elif kind == "seq_gap":
                        log(f"[{b.bus_id}] FALLA: se pierden 3 eventos (salto de seq); "
                            f"snapshots retenidos por {f.duration_s:.0f} s")
                        for _ in range(3):
                            b.passenger("in", publish=False)
                        b.hold_snapshots_until = now + f.duration_s
                    else:
                        log(f"[{b.bus_id}] FALLA: conteo sospechoso por {f.duration_s:.0f} s")
                        b.suspicious_until = now + f.duration_s
                        target = int(1.3 * b.capacity) + 1
                        t = now
                        b.planned.clear()
                        for _ in range(max(0, target - b.count)):
                            t += random.uniform(0.3, 0.8)
                            b.planned.append((t, "in"))
                        b.next_stop_at = now + f.duration_s
            for b in buses:
                b.tick(now)
            if now - last_save >= 10:
                save_state(buses)
                last_save = now
            time.sleep(TICK_S)
    except KeyboardInterrupt:
        log("Deteniendo simulador...")
    finally:
        stop.set()
        save_state(buses)
        for b in buses:
            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
