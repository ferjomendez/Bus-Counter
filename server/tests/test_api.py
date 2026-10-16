"""Pruebas de la API (sin MQTT)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    cfg = AppConfig(db_path=tmp_path / "test.db", static_dir=tmp_path / "static")
    app = create_app(cfg, enable_mqtt=False)
    with TestClient(app) as c:
        app.state.service.handle_message("buses/B01/event", json.dumps({"seq": 1, "dir": "in"}).encode())
        yield c


def test_listar_y_detalle(client):
    buses = client.get("/api/buses").json()
    assert [b["id"] for b in buses] == ["B01"]
    assert client.get("/api/buses/B01").json()["count"] == 1
    assert client.get("/api/buses/B99").status_code == 404


def test_editar_bus(client):
    r = client.put("/api/buses/B01", json={"name": "Bus 1", "route_id": 2})
    assert r.status_code == 200 and r.json()["route_name"] == "UAI-Dominicos"
    r = client.put("/api/buses/B01", json={"route_id": None})
    assert r.json()["route_id"] is None and r.json()["name"] == "Bus 1"
    assert client.put("/api/buses/B01", json={"type_id": 7}).status_code == 422


def test_historial_y_eventos(client):
    assert client.get("/api/buses/B01/events?limit=5").json()[0]["dir"] == "in"
    assert client.get("/api/buses/B01/history").status_code == 200
    assert client.get("/api/buses/B01/history?from=10&to=5").status_code == 422


def test_comando_sin_broker_responde_503(client):
    assert client.post("/api/buses/B01/cmd", json={"action": "reset"}).status_code == 503
    assert client.post("/api/buses/B01/cmd", json={"action": "set"}).status_code == 422
    assert client.post("/api/buses/B01/cmd", json={"action": "apagar"}).status_code == 422


def test_config_confirmacion_y_validacion(client):
    r = client.put("/api/config", json={"settings": {"recommend_low_threshold": 30}})
    assert r.status_code == 409 and r.json()["needs_confirmation"]
    r = client.put("/api/config", json={"settings": {"recommend_low_threshold": 30}, "confirm": True})
    assert r.status_code == 200 and r.json()["settings"]["recommend_low_threshold"] == 30
    r = client.put("/api/config", json={"settings": {"color_amber_pct": 95}})
    assert r.status_code == 422
    r = client.put("/api/config", json={"routes": [{"id": 1, "name": "UAI-Grecia Norte"}]})
    assert r.json()["routes"][0]["name"] == "UAI-Grecia Norte"


def test_recomendaciones_y_alertas(client):
    assert client.get("/api/recommendations").json()["enabled"] is True
    assert client.get("/api/alerts").status_code == 200


def test_websocket_envia_estado(client):
    with client.websocket_connect("/ws") as ws:
        state = ws.receive_json()
        assert state["type"] == "state" and state["summary"]["buses_online"] == 1
        assert state["buses"][0]["id"] == "B01"
