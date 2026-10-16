"""Genera firmware/include/secrets.h a partir de broker/certs/ca.crt y del archivo .env.

Se ejecuta solo antes de cada compilacion (extra_scripts de PlatformIO) y tambien se
puede correr a mano:  python firmware/scripts/gen_secrets.py

secrets.h contiene el certificado de la CA, la clave MQTT del bus (segun BUS_ID de
config.h) y la IP del servidor. No se sube a git.
"""
import os
import re
import socket
import sys
from pathlib import Path

try:
    Import("env")  # noqa: F821  (definido por PlatformIO/SCons)
    FIRMWARE = Path(env["PROJECT_DIR"])  # noqa: F821
except NameError:
    FIRMWARE = Path(__file__).resolve().parents[1]

ROOT = FIRMWARE.parent
CA_FILE = ROOT / "broker" / "certs" / "ca.crt"
ENV_FILE = ROOT / ".env"
CONFIG_H = FIRMWARE / "include" / "config.h"
CONFIG_EXAMPLE = FIRMWARE / "include" / "config.example.h"
SECRETS_H = FIRMWARE / "include" / "secrets.h"


def fail(msg: str) -> None:
    print(f"\n[secrets.h] ERROR: {msg}\n", file=sys.stderr)
    sys.exit(1)


def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def c_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> None:
    if not CA_FILE.exists():
        fail("falta broker/certs/ca.crt. Ejecuta primero: python manage.py certs")
    if not CONFIG_H.exists():
        fail("falta firmware/include/config.h. Copia config.example.h como config.h y editalo "
             "(python manage.py flash lo hace solo).")
    m = re.search(r'^\s*#define\s+BUS_ID\s+"([^"]+)"', CONFIG_H.read_text(encoding="utf-8"), re.M)
    if not m:
        fail('config.h debe tener una linea como: #define BUS_ID "B01"')
    bus_id = m.group(1)

    env = read_env()
    ids = [b.strip() for b in env.get("BUS_IDS", "").split(",") if b.strip()]
    password = env.get(f"BUS_PASSWORD_{bus_id}", "")
    if bus_id not in ids or not password:
        fail(f"el bus {bus_id} no esta en .env. Agregalo a BUS_IDS y ejecuta "
             "python manage.py start una vez para crear su clave.")
    server_ip = env.get("ADVERTISED_IP") or local_ip()

    ca = CA_FILE.read_text(encoding="ascii").strip()
    content = (
        "// Generado por firmware/scripts/gen_secrets.py. No editar ni subir a git.\n"
        "#pragma once\n\n"
        f"#define SECRET_MQTT_PASSWORD {c_string(password)}\n"
        f"#define SECRET_SERVER_IP {c_string(server_ip)}\n\n"
        f'static const char CA_CERT[] = R"PEM(\n{ca}\n)PEM";\n'
    )
    old = SECRETS_H.read_text(encoding="utf-8") if SECRETS_H.exists() else ""
    if old != content:  # solo se reescribe si cambio, para no recompilar de mas
        SECRETS_H.write_text(content, encoding="utf-8")
        print(f"[secrets.h] generado para {bus_id} (servidor {server_ip})")


main()
