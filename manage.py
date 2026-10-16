#!/usr/bin/env python3
"""CLI de Bus Counter.

Uso:  python manage.py <comando> [opciones]

  setup   instala dependencias (en Windows llama a setup.ps1)
  certs   genera certificados TLS (--force para regenerar)
  start   enciende broker + servidor (--sim agrega el simulador)
  broker  enciende solo el broker MQTT
  server  enciende solo el servidor web
  sim     enciende solo el simulador
  flash   compila y graba el firmware en el ESP32
  test    ejecuta las pruebas

Este archivo solo usa la libreria estandar en el nivel superior para poder
ejecutarse con cualquier Python >= 3.10, incluso antes de crear el .venv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
LOCAL_CONFIG = ROOT / "local_config.json"
REQUIREMENTS = ROOT / "requirements.txt"
MIN_PYTHON = (3, 10)
MOSQUITTO_TOOLS = ("mosquitto", "mosquitto_passwd", "mosquitto_pub", "mosquitto_sub")


# ------------------------------------------------------------------ utilidades

def info(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"AVISO: {msg}", flush=True)


def load_local_config() -> dict:
    if LOCAL_CONFIG.exists():
        try:
            return json.loads(LOCAL_CONFIG.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            warn("local_config.json esta danado; se ignora.")
    return {}


def save_local_config(cfg: dict) -> None:
    LOCAL_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def mosquitto_candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    cfg_dir = load_local_config().get("mosquitto_dir")
    if cfg_dir:
        dirs.append(Path(cfg_dir))
    found = shutil.which("mosquitto")
    if found:
        dirs.append(Path(found).parent)
    if IS_WINDOWS:
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                dirs.append(Path(os.environ[env]) / "mosquitto")
    else:
        dirs += [Path(p) for p in ("/usr/sbin", "/usr/bin", "/usr/local/sbin", "/usr/local/bin",
                                   "/opt/homebrew/sbin", "/opt/homebrew/bin")]
    return dirs


def find_mosquitto_tool(name: str) -> Path | None:
    """Busca un ejecutable de Mosquitto (mosquitto, mosquitto_passwd, ...)."""
    exe = name + (".exe" if IS_WINDOWS else "")
    for d in mosquitto_candidate_dirs():
        p = d / exe
        if p.exists():
            return p
    found = shutil.which(name)
    return Path(found) if found else None


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def reexec_in_venv() -> None:
    """Si no se esta usando el .venv, vuelve a ejecutar este comando con su Python."""
    if in_venv():
        return
    if not VENV_PYTHON.exists():
        sys.exit("No existe .venv. Ejecuta primero:  python manage.py setup")
    code = subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(code)


# ------------------------------------------------------------------ setup

def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest().upper()


def setup_posix(check_only: bool) -> int:
    """Equivalente a setup.ps1 para Linux y macOS."""
    rows: list[tuple[str, str, str]] = []
    failed = False

    ver = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info[:2] >= MIN_PYTHON:
        rows.append(("Python", ver, "ya estaba"))
    else:
        rows.append(("Python", ver, "falta (se necesita >= 3.10)"))
        failed = True

    git = shutil.which("git")
    if git:
        out = subprocess.run([git, "--version"], capture_output=True, text=True).stdout.split()
        rows.append(("Git", out[-1] if out else "?", "ya estaba"))
    else:
        rows.append(("Git", "-", "falta (instalar con apt o brew)"))
        failed = True

    missing = [t for t in MOSQUITTO_TOOLS if not find_mosquitto_tool(t)]
    if missing and not check_only:
        if platform.system() == "Darwin" and shutil.which("brew"):
            cmd = ["brew", "install", "mosquitto"]
        elif shutil.which("apt-get"):
            cmd = ["sudo", "apt-get", "install", "-y", "mosquitto", "mosquitto-clients"]
        else:
            cmd = None
        if cmd:
            ans = input(f"Falta Mosquitto. Ejecutar '{' '.join(cmd)}'? (s/n) ").strip().lower()
            if ans.startswith(("s", "y")):
                subprocess.call(cmd)
                missing = [t for t in MOSQUITTO_TOOLS if not find_mosquitto_tool(t)]
                state = "instalada" if not missing else "fallo"
            else:
                state = "omitido por el usuario"
        else:
            state = "falta (instalar manualmente)"
    else:
        state = "ya estaba" if not missing else "falta"
    if missing:
        failed = True
    rows.append(("Mosquitto", "-" if missing else "ok", state))
    if not missing:
        cfg = load_local_config()
        cfg["mosquitto_dir"] = str(find_mosquitto_tool("mosquitto").parent)
        if not check_only:
            save_local_config(cfg)

    stamp = VENV_DIR / "requirements.sha256"
    if not VENV_PYTHON.exists():
        if check_only:
            rows.append(("Entorno .venv", "-", "falta"))
            failed = True
        else:
            info("Creando entorno virtual .venv...")
            venv.create(VENV_DIR, with_pip=True)
            rows.append(("Entorno .venv", ver, "instalada"))
    else:
        rows.append(("Entorno .venv", "-", "ya estaba"))

    if VENV_PYTHON.exists():
        current = stamp.read_text().strip() if stamp.exists() else ""
        if current == requirements_hash():
            rows.append(("Paquetes Python", "requirements.txt", "ya estaba"))
        elif check_only:
            rows.append(("Paquetes Python", "requirements.txt", "falta"))
            failed = True
        else:
            info("Instalando paquetes de Python en .venv...")
            py = str(VENV_PYTHON)
            subprocess.call([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
            rc = subprocess.call([py, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)])
            if rc == 0:
                stamp.write_text(requirements_hash())
                rows.append(("Paquetes Python", "requirements.txt", "instalada"))
            else:
                rows.append(("Paquetes Python", "requirements.txt", "fallo"))
                failed = True

    print()
    print(f"{'Dependencia':<18} {'Version':<18} Estado")
    for name, version, st in rows:
        print(f"{name:<18} {version:<18} {st}")
    return 1 if failed else 0


def cmd_setup(args: argparse.Namespace) -> int:
    if IS_WINDOWS:
        ps_args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "setup.ps1")]
        if args.check_only:
            ps_args.append("-CheckOnly")
        return subprocess.call(ps_args)
    return setup_posix(args.check_only)


# ------------------------------------------------------------------ test

def cmd_test(args: argparse.Namespace) -> int:
    reexec_in_venv()
    return subprocess.call([sys.executable, "-m", "pytest", "-q", "server/tests", *args.pytest_args], cwd=ROOT)


# ------------------------------------------------------------------ pendientes

def not_yet(phase: str):
    def run(_args: argparse.Namespace) -> int:
        print(f"Este comando se completa en la {phase}.")
        return 1
    return run


# ------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="manage.py", description="Herramientas de Bus Counter")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="instala dependencias")
    s.add_argument("--check-only", action="store_true", help="solo reporta, no instala")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("certs", help="genera certificados TLS")
    s.add_argument("--force", action="store_true", help="regenera el certificado del servidor")
    s.set_defaults(func=not_yet("fase 1"))

    s = sub.add_parser("start", help="enciende broker + servidor")
    s.add_argument("--sim", action="store_true", help="enciende tambien el simulador")
    s.set_defaults(func=not_yet("fase 2"))

    sub.add_parser("broker", help="enciende solo el broker").set_defaults(func=not_yet("fase 1"))
    sub.add_parser("server", help="enciende solo el servidor").set_defaults(func=not_yet("fase 2"))
    sub.add_parser("sim", help="enciende solo el simulador").set_defaults(func=not_yet("fase 3"))
    sub.add_parser("flash", help="graba el firmware").set_defaults(func=not_yet("fase 5"))

    s = sub.add_parser("test", help="ejecuta las pruebas")
    s.add_argument("pytest_args", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_test)
    return p


def main() -> int:
    if sys.version_info[:2] < MIN_PYTHON:
        sys.exit(f"Se necesita Python 3.10 o superior (tienes {platform.python_version()}).")
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
