"""Reglas de negocio puras (sin base de datos ni red), para poder probarlas facilmente."""
from __future__ import annotations

from dataclasses import dataclass, replace

DEFAULT_SETTINGS: dict[str, int] = {
    "recommend_low_threshold": 20,
    "recommend_window_min": 10,
    "recommend_full_pct": 90,
    "color_amber_pct": 60,
    "color_red_pct": 90,
    "offline_timeout_s": 60,
}

# Rango permitido de cada ajuste (minimo, maximo)
SETTING_LIMITS: dict[str, tuple[int, int]] = {
    "recommend_low_threshold": (0, 1000),
    "recommend_window_min": (1, 240),
    "recommend_full_pct": (1, 100),
    "color_amber_pct": (1, 100),
    "color_red_pct": (1, 100),
    "offline_timeout_s": (10, 3600),
}

# Nombres para mostrar en mensajes de error
SETTING_LABELS: dict[str, str] = {
    "recommend_low_threshold": "Pocas personas",
    "recommend_window_min": "Minutos a evaluar",
    "recommend_full_pct": "Porcentaje para sugerir bus grande",
    "color_amber_pct": "Porcentaje de 'Casi lleno'",
    "color_red_pct": "Porcentaje de 'Lleno'",
    "offline_timeout_s": "Segundos sin señal",
}

SAMPLE_MIN_INTERVAL_S = 10     # como maximo una muestra cada 10 s
SAMPLE_KEEPALIVE_S = 60        # si no cambia, una muestra por minuto
SAMPLE_MAX_GAP_S = 90          # mas separacion que esto entre muestras = hueco sin datos
SUSPICIOUS_FACTOR = 1.2        # conteo mayor al 120 % de la capacidad

MSG_TO_SMALL = "Va con pocas personas. Conviene enviar un bus chico en este recorrido."
MSG_TO_BIG = "Va casi lleno. Conviene enviar un bus grande en este recorrido."
MSG_SAME_CAPACITY = ("Los dos tipos de bus tienen la misma capacidad, "
                     "por eso las recomendaciones están desactivadas.")


# ------------------------------------------------------------------ conteo

@dataclass(frozen=True)
class CountState:
    count: int = 0
    in_total: int = 0
    out_total: int = 0
    last_seq: int | None = None       # mayor seq conocido (evento o snapshot)
    snapshot_seq: int | None = None   # seq del ultimo snapshot aplicado
    incomplete: bool = False


def apply_event(state: CountState, seq: int, direction: str) -> CountState:
    """Aplica un evento nuevo (no duplicado) al estado.

    Los eventos con seq <= snapshot_seq ya estan incluidos en el snapshot y no cambian
    el conteo. Si el seq salta respecto al ultimo conocido, se marca 'datos incompletos'.
    """
    if state.snapshot_seq is not None and seq <= state.snapshot_seq:
        return state
    incomplete = state.incomplete
    if state.last_seq is None:
        # Primer dato del bus sin snapshot: si no parte en 1, nos perdimos eventos
        if seq > 1:
            incomplete = True
    elif seq > state.last_seq + 1:
        incomplete = True
    if direction == "in":
        count, in_total, out_total = state.count + 1, state.in_total + 1, state.out_total
    else:
        count, in_total, out_total = max(0, state.count - 1), state.in_total, state.out_total + 1
    last_seq = seq if state.last_seq is None else max(state.last_seq, seq)
    return replace(state, count=count, in_total=in_total, out_total=out_total,
                   last_seq=last_seq, incomplete=incomplete)


def rebuild_from_snapshot(seq: int, count: int, in_total: int, out_total: int,
                          later_events: list[tuple[int, str]]) -> CountState:
    """Conteo actual = snapshot + eventos posteriores ya recibidos (el snapshot manda).

    Limpia 'datos incompletos', salvo que los eventos posteriores tengan huecos.
    """
    state = CountState(count=max(0, count), in_total=in_total, out_total=out_total,
                       last_seq=seq, snapshot_seq=seq, incomplete=False)
    for ev_seq, direction in sorted(later_events):
        if ev_seq > seq:
            state = apply_event(state, ev_seq, direction)
    return state


def should_sample(last_ts: float | None, last_count: int | None, now: float, count: int) -> bool:
    """Muestra para graficos: cada 10 s como maximo si cambia, cada minuto si no."""
    if last_ts is None:
        return True
    elapsed = now - last_ts
    if count != last_count:
        return elapsed >= SAMPLE_MIN_INTERVAL_S
    return elapsed >= SAMPLE_KEEPALIVE_S


# ------------------------------------------------------------------ ocupacion

def occupancy_level(count: int, capacity: int, settings: dict[str, int]) -> str:
    pct = 100 * count / capacity if capacity > 0 else 0
    if pct >= settings["color_red_pct"]:
        return "red"
    if pct >= settings["color_amber_pct"]:
        return "amber"
    return "green"


LEVEL_LABELS = {"green": "Con espacio", "amber": "Casi lleno", "red": "Lleno"}


# ------------------------------------------------------------------ recomendaciones

@dataclass(frozen=True)
class WindowStats:
    covered_s: float
    max_count: int
    min_count: int


def window_stats(samples: list[tuple[float, int]], current_count: int, now: float,
                 window_s: float, max_gap_s: float = SAMPLE_MAX_GAP_S) -> WindowStats | None:
    """Resume la ventana [now - window_s, now] a partir de las muestras (ts, conteo).

    Una muestra vale hasta la siguiente si estan a menos de max_gap_s; si no, ese tramo
    se considera sin datos. El conteo actual cuenta como una muestra en 'now'.
    """
    start = now - window_s
    points = sorted((t, c) for t, c in samples if t <= now)
    points.append((now, current_count))
    covered = 0.0
    values: list[int] = []
    for (t0, c0), (t1, c1) in zip(points, points[1:]):
        if t1 <= start:
            continue
        if t1 - t0 <= max_gap_s:
            covered += t1 - max(t0, start)
            values.append(c0)
        elif t0 >= start:
            values.append(c0)
    values.append(current_count)
    if covered <= 0:
        return None
    return WindowStats(covered_s=covered, max_count=max(values), min_count=min(values))


def bus_sizes(types: list[dict]) -> tuple[int | None, int | None]:
    """Devuelve (id del tipo grande, id del tipo chico) segun la capacidad.

    Si las capacidades son iguales devuelve (None, None): recomendaciones desactivadas.
    """
    ordered = sorted(types, key=lambda t: t["capacity"])
    if len(ordered) < 2 or ordered[0]["capacity"] == ordered[-1]["capacity"]:
        return None, None
    return ordered[-1]["id"], ordered[0]["id"]


def evaluate_recommendation(*, size: str | None, capacity: int, online: bool, incomplete: bool,
                            samples: list[tuple[float, int]], current_count: int, now: float,
                            settings: dict[str, int]) -> dict | None:
    """Evalua un bus. size es 'big', 'small' o None (recomendaciones desactivadas).

    Devuelve {'kind': 'to_small'|'to_big', 'message': ...} o None.
    """
    if size is None or not online or incomplete:
        return None
    window_s = settings["recommend_window_min"] * 60
    stats = window_stats(samples, current_count, now, window_s)
    if stats is None or stats.covered_s < window_s / 2:
        return None
    if size == "big" and stats.max_count <= settings["recommend_low_threshold"]:
        return {"kind": "to_small", "message": MSG_TO_SMALL}
    if size == "small" and stats.min_count * 100 >= settings["recommend_full_pct"] * capacity:
        return {"kind": "to_big", "message": MSG_TO_BIG}
    return None


# ------------------------------------------------------------------ alertas

def bus_alerts(*, online: bool, incomplete: bool, count: int, capacity: int) -> list[dict]:
    alerts = []
    if not online:
        alerts.append({"kind": "offline", "message": "Sin conexión: no se reciben datos de este bus."})
    if incomplete:
        alerts.append({"kind": "incomplete",
                       "message": "Faltan datos de este bus. El conteo puede no ser exacto "
                                  "hasta la próxima actualización completa."})
    if capacity > 0 and count > SUSPICIOUS_FACTOR * capacity:
        alerts.append({"kind": "suspicious",
                       "message": f"El conteo ({count}) supera lo que cabe en el bus ({capacity}). "
                                  "Revisa el sensor o corrige el conteo."})
    return alerts


# ------------------------------------------------------------------ validacion de configuracion

@dataclass
class ConfigCheck:
    errors: list[str]
    confirm: list[str]      # avisos que el usuario debe confirmar antes de guardar
    notices: list[str]      # avisos informativos


def check_config(types: list[dict], settings: dict[str, int]) -> ConfigCheck:
    """Valida tipos de bus y ajustes ya combinados (valores actuales + cambios)."""
    errors: list[str] = []
    confirm: list[str] = []
    notices: list[str] = []
    for t in types:
        if not str(t.get("name", "")).strip():
            errors.append("Cada tipo de bus necesita un nombre.")
        if not isinstance(t.get("capacity"), int) or not 1 <= t["capacity"] <= 500:
            errors.append(f"La capacidad de '{t.get('name')}' debe ser un número entre 1 y 500.")
    for key, (lo, hi) in SETTING_LIMITS.items():
        v = settings.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            errors.append(f"'{SETTING_LABELS[key]}' debe ser un número entero entre {lo} y {hi}.")
    if errors:
        return ConfigCheck(errors, confirm, notices)

    if settings["color_amber_pct"] >= settings["color_red_pct"]:
        errors.append("El porcentaje de 'Casi lleno' debe ser menor que el de 'Lleno'.")
    big_id, small_id = bus_sizes(types)
    if big_id is None:
        notices.append(MSG_SAME_CAPACITY)
    else:
        small = next(t for t in types if t["id"] == small_id)
        if settings["recommend_low_threshold"] > small["capacity"]:
            confirm.append(
                f"El umbral de pocas personas ({settings['recommend_low_threshold']}) es mayor que la "
                f"capacidad del bus {small['name']} ({small['capacity']}): esas personas ya no cabrían "
                "en un bus chico.")
    return ConfigCheck(errors, confirm, notices)
