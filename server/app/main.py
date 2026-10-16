"""Servidor web: API REST, WebSocket en vivo y frontend estatico."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config import AppConfig
from .service import BusService, ConfigError, NeedsConfirmation

log = logging.getLogger("bus_counter")

SWEEP_EVERY_S = 5
SAMPLE_EVERY_S = 10
CLEANUP_EVERY_S = 3600
WS_MIN_INTERVAL_S = 0.3
WS_KEEPALIVE_S = 5


class BusUpdate(BaseModel):
    name: str | None = None
    type_id: int | None = None
    route_id: int | None = None


class Command(BaseModel):
    action: Literal["reset", "set"]
    count: int | None = Field(default=None, ge=0, le=1000)


class TypeUpdate(BaseModel):
    id: int
    name: str | None = None
    capacity: int | None = None


class RouteUpdate(BaseModel):
    id: int
    name: str


class ConfigUpdate(BaseModel):
    bus_types: list[TypeUpdate] | None = None
    routes: list[RouteUpdate] | None = None
    settings: dict[str, int] | None = None
    confirm: bool = False


class Hub:
    """Clientes WebSocket y aviso de cambios desde cualquier hilo (MQTT, API)."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.dirty = asyncio.Event()

    def notify(self) -> None:
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.dirty.set)


def create_app(cfg: AppConfig | None = None, enable_mqtt: bool = True) -> FastAPI:
    cfg = cfg or AppConfig.from_env()
    hub = Hub()
    conn = db.connect(cfg.db_path)
    service = BusService(conn, on_change=hub.notify, retention_days=cfg.retention_days)
    bridge = None
    if enable_mqtt:
        from .mqtt_bridge import MqttBridge
        bridge = MqttBridge(cfg, service, on_change=hub.notify)

    async def periodic():
        last_sample = last_cleanup = 0.0
        while True:
            now = time.time()
            await run_in_threadpool(service.sweep_offline)
            if now - last_sample >= SAMPLE_EVERY_S:
                await run_in_threadpool(service.sample_counts)
                last_sample = now
            if now - last_cleanup >= CLEANUP_EVERY_S:
                await run_in_threadpool(service.cleanup)
                last_cleanup = now
            await asyncio.sleep(SWEEP_EVERY_S)

    async def broadcaster():
        while True:
            try:
                await asyncio.wait_for(hub.dirty.wait(), timeout=WS_KEEPALIVE_S)
            except asyncio.TimeoutError:
                pass
            hub.dirty.clear()
            if hub.clients:
                state = await run_in_threadpool(live_state)
                for ws in list(hub.clients):
                    try:
                        await ws.send_json(state)
                    except Exception:
                        hub.clients.discard(ws)
            await asyncio.sleep(WS_MIN_INTERVAL_S)

    def live_state() -> dict:
        state = service.live_state()
        state["mqtt_connected"] = bridge.connected if bridge else False
        return state

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.loop = asyncio.get_running_loop()
        hub.dirty = asyncio.Event()
        tasks = [asyncio.create_task(periodic()), asyncio.create_task(broadcaster())]
        if bridge:
            bridge.start()
        yield
        for t in tasks:
            t.cancel()
        if bridge:
            bridge.stop()
        conn.close()

    app = FastAPI(title="Bus Counter", lifespan=lifespan)
    app.state.service = service
    app.state.bridge = bridge

    def bus_or_404(bus_id: str) -> dict:
        bus = service.get_bus(bus_id)
        if bus is None:
            raise HTTPException(404, "No existe ese bus.")
        return bus

    @app.exception_handler(ConfigError)
    async def config_error(_req, exc: ConfigError):
        return JSONResponse(status_code=422, content={"detail": exc.errors})

    @app.exception_handler(NeedsConfirmation)
    async def needs_confirmation(_req, exc: NeedsConfirmation):
        return JSONResponse(status_code=409, content={"needs_confirmation": True, "detail": exc.messages})

    @app.get("/api/health")
    def health():
        return {"ok": True, "mqtt_connected": bridge.connected if bridge else False}

    @app.get("/api/buses")
    def list_buses():
        return service.list_buses()

    @app.get("/api/buses/{bus_id}")
    def get_bus(bus_id: str):
        return bus_or_404(bus_id)

    @app.put("/api/buses/{bus_id}")
    def update_bus(bus_id: str, body: BusUpdate):
        bus = service.update_bus(bus_id, name=body.name, type_id=body.type_id, route_id=body.route_id,
                                 set_route="route_id" in body.model_fields_set)
        if bus is None:
            raise HTTPException(404, "No existe ese bus.")
        return bus

    @app.get("/api/buses/{bus_id}/history")
    def history(bus_id: str, frm: float | None = Query(None, alias="from"), to: float | None = None):
        bus_or_404(bus_id)
        to = to or time.time()
        frm = frm if frm is not None else to - 3600
        if frm >= to:
            raise HTTPException(422, "'from' debe ser anterior a 'to'.")
        return service.history(bus_id, frm, to)

    @app.get("/api/buses/{bus_id}/events")
    def events(bus_id: str, limit: int = Query(50, ge=1, le=500)):
        bus_or_404(bus_id)
        return service.events(bus_id, limit)

    @app.post("/api/buses/{bus_id}/cmd", status_code=202)
    def command(bus_id: str, body: Command):
        bus_or_404(bus_id)
        payload: dict = {"action": body.action}
        if body.action == "set":
            if body.count is None:
                raise HTTPException(422, "Falta el número de personas.")
            payload["count"] = body.count
        if not bridge or not bridge.publish_cmd(bus_id, payload):
            raise HTTPException(503, "No hay conexión con el broker. Intenta de nuevo en unos segundos.")
        return {"sent": payload}

    @app.get("/api/recommendations")
    def recommendations():
        return service.recommendations()

    @app.get("/api/alerts")
    def alerts():
        return service.alerts()

    @app.get("/api/config")
    def get_config():
        return service.get_config()

    @app.put("/api/config")
    def put_config(body: ConfigUpdate):
        return service.update_config(
            bus_types=[t.model_dump(exclude_none=True) for t in body.bus_types] if body.bus_types else None,
            routes=[r.model_dump() for r in body.routes] if body.routes else None,
            settings=body.settings, confirm=body.confirm)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        hub.clients.add(ws)
        try:
            await ws.send_json(await run_in_threadpool(live_state))
            while True:
                await ws.receive_text()  # el cliente no manda nada; esto detecta el cierre
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    if cfg.static_dir.exists():
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="static")
    return app


def run() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = AppConfig.from_env()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    run()
