"""Configuracion del servidor: lee .env de la raiz del proyecto; las variables de entorno mandan."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


@dataclass
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 8883
    mqtt_user: str = "servidor"
    mqtt_password: str = ""
    ca_file: Path = field(default_factory=lambda: ROOT / "broker" / "certs" / "ca.crt")
    db_path: Path = field(default_factory=lambda: ROOT / "server" / "data" / "bus_counter.db")
    static_dir: Path = field(default_factory=lambda: ROOT / "server" / "static")
    retention_days: int = 30

    @classmethod
    def from_env(cls) -> "AppConfig":
        env = read_env_file(ROOT / ".env")
        env.update({k: v for k, v in os.environ.items() if k in env or k.startswith(("MQTT_", "SERVER_", "DB_"))})
        cfg = cls()
        cfg.host = env.get("SERVER_HOST") or cfg.host
        cfg.port = int(env.get("SERVER_PORT") or cfg.port)
        cfg.mqtt_host = env.get("MQTT_HOST") or cfg.mqtt_host
        cfg.mqtt_port = int(env.get("MQTT_PORT") or cfg.mqtt_port)
        cfg.mqtt_user = env.get("MQTT_SERVER_USER") or cfg.mqtt_user
        cfg.mqtt_password = env.get("MQTT_SERVER_PASSWORD", "")
        if env.get("DB_PATH"):
            cfg.db_path = _resolve(env["DB_PATH"])
        return cfg
