"""Pruebas del servicio con SQLite en memoria y un reloj controlado."""
import json

import pytest

from app import db, logic
from app.service import BusService, ConfigError, NeedsConfirmation


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(clock):
    return BusService(db.connect(":memory:"), clock=clock)


def send(svc, bus, kind, retained=False, **data):
    return svc.handle_message(f"buses/{bus}/{kind}", json.dumps(data).encode(), retained=retained)


def event(svc, bus, seq, direction="in"):
    return send(svc, bus, "event", seq=seq, dir=direction, ts=1760500000, up_ms=1000 * seq)


def test_registro_automatico(svc):
    event(svc, "B01", 1)
    bus = svc.get_bus("B01")
    assert bus["name"] == "Bus B01" and bus["type_name"] == "Grande" and bus["route_id"] is None
    assert bus["online"] and bus["count"] == 1


def test_duplicado_se_ignora(svc):
    event(svc, "B01", 1)
    event(svc, "B01", 2)
    event(svc, "B01", 2)
    bus = svc.get_bus("B01")
    assert bus["count"] == 2 and bus["in"] == 2
    assert len(svc.events("B01")) == 2


def test_salto_de_seq_y_resincronizacion(svc):
    send(svc, "B01", "count", seq=10, count=5, **{"in": 8, "out": 3}, ts=0)
    event(svc, "B01", 11)
    event(svc, "B01", 14)  # se perdieron 12 y 13
    bus = svc.get_bus("B01")
    assert bus["incomplete"] and bus["count"] == 7
    assert "incomplete" in [a["kind"] for a in bus["alerts"]]
    # El snapshot del bus manda y limpia la marca
    send(svc, "B01", "count", seq=14, count=9, **{"in": 12, "out": 3}, ts=0)
    bus = svc.get_bus("B01")
    assert not bus["incomplete"] and bus["count"] == 9 and bus["in"] == 12


def test_snapshot_suma_eventos_ya_recibidos(svc):
    event(svc, "B01", 1)
    event(svc, "B01", 2)
    event(svc, "B01", 3)
    # Snapshot atrasado (seq 2) llega despues del evento 3: conteo = snapshot + evento 3
    send(svc, "B01", "count", seq=2, count=2, **{"in": 2, "out": 0}, ts=0)
    bus = svc.get_bus("B01")
    assert bus["count"] == 3 and bus["last_seq"] == 3 and not bus["incomplete"]


def test_offline_por_last_will(svc):
    send(svc, "B01", "status", state="online", rssi=-67, fw="0.1.0", up_ms=1000)
    assert svc.get_bus("B01")["online"]
    send(svc, "B01", "status", state="offline")
    bus = svc.get_bus("B01")
    assert not bus["online"] and bus["rssi"] == -67 and bus["fw"] == "0.1.0"


def test_offline_por_tiempo_sin_mensajes(svc, clock):
    send(svc, "B01", "status", state="online", rssi=-60, fw="0.1.0", up_ms=1)
    clock.t += 60
    assert svc.sweep_offline() == 0          # justo en el limite sigue conectado
    clock.t += 1
    assert svc.sweep_offline() == 1
    assert not svc.get_bus("B01")["online"]
    event(svc, "B01", 1)                     # vuelve a mandar datos
    assert svc.get_bus("B01")["online"]


def test_timeout_configurable(svc, clock):
    svc.update_config(settings={"offline_timeout_s": 120})
    send(svc, "B01", "status", state="online")
    clock.t += 100
    assert svc.sweep_offline() == 0


def test_mensaje_retenido_no_marca_conectado(svc):
    send(svc, "B01", "count", retained=True, seq=5, count=4, **{"in": 5, "out": 1}, ts=0)
    send(svc, "B01", "status", retained=True, state="online", rssi=-70, fw="0.1.0", up_ms=1)
    bus = svc.get_bus("B01")
    assert bus["count"] == 4 and not bus["online"] and bus["last_seen"] is None


def test_mensajes_invalidos_no_rompen(svc):
    assert not svc.handle_message("buses/B01/event", b"no es json")
    assert not send(svc, "B01", "event", seq="x", dir="in")
    assert not send(svc, "B01", "event", seq=1, dir="arriba")
    assert not svc.handle_message("buses/B01/otro", b"{}")
    assert not svc.handle_message("buses/B+1/event", b'{"seq":1,"dir":"in"}')
    assert svc.get_bus("B01") is None or svc.get_bus("B01")["count"] == 0


def test_muestras_y_recomendacion(svc, clock):
    send(svc, "B01", "count", seq=0, count=8, **{"in": 8, "out": 0}, ts=0)
    for _ in range(0, 11 * 60, 10):  # 11 minutos con 8 personas en un bus grande
        send(svc, "B01", "status", state="online")
        svc.sample_counts()
        clock.t += 10
    send(svc, "B01", "status", state="online")
    recs = svc.recommendations()
    assert recs["enabled"] and recs["items"][0]["kind"] == "to_small"
    # muestras: una por minuto si no cambia el conteo
    assert 10 <= len(svc.history("B01", clock.t - 3600, clock.t)) <= 12


def test_sin_recomendacion_si_la_ventana_no_tiene_datos(svc):
    send(svc, "B01", "count", seq=0, count=8, **{"in": 8, "out": 0}, ts=0)
    send(svc, "B01", "status", state="online")
    svc.sample_counts()
    assert svc.recommendations()["items"] == []


def test_conteo_sospechoso(svc):
    send(svc, "B01", "count", seq=0, count=61, **{"in": 61, "out": 0}, ts=0)
    assert [a["kind"] for a in svc.alerts()] == ["suspicious"]
    send(svc, "B01", "count", seq=0, count=60, **{"in": 60, "out": 0}, ts=0)
    assert svc.alerts() == []


def test_editar_bus_y_config(svc):
    event(svc, "B01", 1)
    bus = svc.update_bus("B01", name="Micro azul", type_id=2, route_id=3, set_route=True)
    assert (bus["name"], bus["type_name"], bus["size"], bus["route_name"]) == \
        ("Micro azul", "Chico", "small", "UAI-Fernando Castillo Velasco")
    assert svc.update_bus("B01", route_id=None, set_route=True)["route_id"] is None
    with pytest.raises(ConfigError):
        svc.update_bus("B01", type_id=99)
    assert svc.update_bus("B99", name="x") is None


def test_config_requiere_confirmacion(svc):
    with pytest.raises(NeedsConfirmation):
        svc.update_config(settings={"recommend_low_threshold": 30})
    assert svc.settings()["recommend_low_threshold"] == 20
    svc.update_config(settings={"recommend_low_threshold": 30}, confirm=True)
    assert svc.settings()["recommend_low_threshold"] == 30
    # Ya confirmado: otros cambios no vuelven a pedirlo, pero un valor nuevo si
    svc.update_config(routes=[{"id": 1, "name": "UAI-Grecia 2"}])
    with pytest.raises(NeedsConfirmation):
        svc.update_config(settings={"recommend_low_threshold": 35})


def test_config_errores_no_guardan_nada(svc):
    with pytest.raises(ConfigError):
        svc.update_config(routes=[{"id": 1, "name": "Nuevo"}], settings={"color_amber_pct": 95})
    assert svc.routes()[0]["name"] == "UAI-Grecia"
    with pytest.raises(ConfigError):
        svc.update_config(bus_types=[{"id": 3, "name": "Mediano", "capacity": 35}])


def test_capacidades_iguales_desactivan_recomendaciones(svc):
    cfg = svc.update_config(bus_types=[{"id": 2, "capacity": 50}])
    assert cfg["notices"] == [logic.MSG_SAME_CAPACITY]
    assert svc.recommendations() == {"enabled": False, "notice": logic.MSG_SAME_CAPACITY, "items": []}


def test_limpieza_borra_datos_antiguos(svc, clock):
    event(svc, "B01", 1)
    svc.sample_counts()
    clock.t += 31 * 86400
    svc.cleanup()
    assert svc.events("B01") == [] and svc.history("B01", 0, clock.t) == []
