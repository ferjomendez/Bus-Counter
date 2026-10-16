"""Conexion MQTT del servidor (usuario 'servidor') por TLS, con reconexion automatica."""
from __future__ import annotations

import json
import logging
import ssl

import paho.mqtt.client as mqtt

from .config import AppConfig
from .service import BusService

log = logging.getLogger("bus_counter.mqtt")

SUBSCRIPTIONS = [("buses/+/event", 1), ("buses/+/count", 1), ("buses/+/status", 1)]


class MqttBridge:
    def __init__(self, cfg: AppConfig, service: BusService, on_change=None):
        self.cfg = cfg
        self.service = service
        self.on_change = on_change or (lambda: None)
        self.connected = False
        # Sesion persistente (clean_session=False): el broker guarda los mensajes QoS 1
        # que lleguen mientras el servidor esta apagado y los entrega al reconectar.
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bus-counter-server",
                                  clean_session=False, protocol=mqtt.MQTTv311)
        self.client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        self.client.tls_set(ca_certs=str(cfg.ca_file), tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        log.info("Conectando a MQTT %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
        self.client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def publish_cmd(self, bus_id: str, payload: dict) -> bool:
        if not self.connected:
            return False
        info = self.client.publish(f"buses/{bus_id}/cmd", json.dumps(payload), qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            log.error("MQTT rechazo la conexion: %s", reason_code)
            return
        log.info("MQTT conectado")
        self.connected = True
        client.subscribe(SUBSCRIPTIONS)
        self.on_change()

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties):
        if self.connected:
            log.warning("MQTT desconectado (%s). Reintentando...", reason_code)
        self.connected = False
        self.on_change()

    def _on_message(self, _client, _userdata, msg):
        try:
            self.service.handle_message(msg.topic, msg.payload, retained=bool(msg.retain))
        except Exception:  # un mensaje malo no debe botar la conexion
            log.exception("Error procesando %s", msg.topic)
