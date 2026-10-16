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
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
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


# ------------------------------------------------------------------ .env

ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
BUS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def parse_env_lines(lines: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    return parse_env_lines(ENV_FILE.read_text(encoding="utf-8").splitlines())


def bus_ids(env: dict[str, str]) -> list[str]:
    ids = [b.strip() for b in env.get("BUS_IDS", "").split(",") if b.strip()]
    for b in ids:
        if not BUS_ID_RE.match(b):
            sys.exit(f"ID de bus invalido en .env: '{b}' (solo letras, numeros, - y _)")
    return ids


def ensure_env() -> dict[str, str]:
    """Crea .env si falta y rellena claves vacias o faltantes con valores aleatorios."""
    if not ENV_FILE.exists():
        info("Creando .env con claves aleatorias...")
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    changed = False
    for i, line in enumerate(lines):
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or line.lstrip().startswith("#"):
            continue
        if (key.endswith("_PASSWORD") or key.startswith("BUS_PASSWORD_")) and not value.strip():
            lines[i] = f"{key}={secrets.token_urlsafe(18)}"
            changed = True
    env = parse_env_lines(lines)
    for b in bus_ids(env):
        if f"BUS_PASSWORD_{b}" not in env:
            lines.append(f"BUS_PASSWORD_{b}={secrets.token_urlsafe(18)}")
            changed = True
    if changed:
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env = parse_env_lines(lines)
    return env


def detect_local_ip() -> str:
    """IP de este computador en la red local (sin enviar trafico)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def advertised_ip(env: dict[str, str]) -> str:
    return env.get("ADVERTISED_IP") or detect_local_ip()


# ------------------------------------------------------------------ certificados

BROKER_DIR = ROOT / "broker"
CERTS_DIR = BROKER_DIR / "certs"


def _write_key(path: Path, key) -> None:
    from cryptography.hazmat.primitives import serialization
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))
    if not IS_WINDOWS:
        path.chmod(0o600)


def _write_cert(path: Path, cert) -> None:
    from cryptography.hazmat.primitives import serialization
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def generate_ca(certs_dir: Path, common_name: str = "BusCounter CA") -> None:
    """CA propia: el ESP32, el servidor y el simulador validan al broker con ca.crt."""
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    certs_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(certs_dir / "ca.key", key)
    _write_cert(certs_dir / "ca.crt", cert)


def generate_server_cert(certs_dir: Path, ip: str) -> None:
    """Certificado del broker firmado por la CA, con la IP, 127.0.0.1 y localhost en el SAN."""
    import datetime as dt
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_key = serialization.load_pem_private_key((certs_dir / "ca.key").read_bytes(), None)
    ca_cert = x509.load_pem_x509_certificate((certs_dir / "ca.crt").read_bytes())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ips = {ip, "127.0.0.1"}
    san = [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)] + [x509.DNSName("localhost")]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(certs_dir / "server.key", key)
    _write_cert(certs_dir / "server.crt", cert)


def server_cert_ips(certs_dir: Path) -> set[str]:
    from cryptography import x509
    path = certs_dir / "server.crt"
    if not path.exists():
        return set()
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set()
    return {str(i) for i in san.get_values_for_type(x509.IPAddress)}


def ensure_certs(env: dict[str, str], force: bool = False, new_ca: bool = False) -> None:
    """Genera lo que falte. Conserva la CA salvo que se pida una nueva (new_ca)."""
    ip = advertised_ip(env)
    have_ca = (CERTS_DIR / "ca.crt").exists() and (CERTS_DIR / "ca.key").exists()
    if new_ca or not have_ca:
        info("Generando CA nueva (ca.crt). Si ya hay un ESP32 grabado, hay que reflashearlo.")
        generate_ca(CERTS_DIR)
        force = True
    if force or not (CERTS_DIR / "server.crt").exists() or ip not in server_cert_ips(CERTS_DIR):
        keep = "" if new_ca or not have_ca else " (se conserva la CA)"
        info(f"Generando certificado del servidor para {ip}, 127.0.0.1 y localhost{keep}.")
        generate_server_cert(CERTS_DIR, ip)


def cmd_certs(args: argparse.Namespace) -> int:
    reexec_in_venv()
    env = ensure_env()
    before = server_cert_ips(CERTS_DIR)
    ensure_certs(env, force=args.force, new_ca=args.ca)
    after = server_cert_ips(CERTS_DIR)
    if before == after and not args.force and not args.ca:
        print(f"Certificados al dia (IP {advertised_ip(env)}). Usa --force para regenerarlos.")
    print(f"Archivos en {CERTS_DIR}")
    return 0


# ------------------------------------------------------------------ usuarios del broker

PASSWD_FILE = BROKER_DIR / "passwd"


def write_passwd(env: dict[str, str], passwd_file: Path = PASSWD_FILE) -> None:
    """Regenera el archivo de usuarios de Mosquitto a partir de .env."""
    tool = find_mosquitto_tool("mosquitto_passwd")
    if not tool:
        sys.exit("No se encontro mosquitto_passwd. Ejecuta: python manage.py setup")
    users = [(env.get("MQTT_SERVER_USER", "servidor"), env["MQTT_SERVER_PASSWORD"])]
    users += [(b, env[f"BUS_PASSWORD_{b}"]) for b in bus_ids(env)]
    passwd_file.unlink(missing_ok=True)
    for i, (user, password) in enumerate(users):
        flags = ["-c", "-b"] if i == 0 else ["-b"]
        r = subprocess.run([str(tool), *flags, str(passwd_file), user, password],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"mosquitto_passwd fallo para {user}: {r.stderr.strip() or r.stdout.strip()}")


def prepare_broker() -> dict[str, str]:
    """Deja listos .env, certificados y usuarios. Devuelve la configuracion."""
    env = ensure_env()
    ensure_certs(env)
    if (not PASSWD_FILE.exists()) or PASSWD_FILE.stat().st_mtime < ENV_FILE.stat().st_mtime:
        info("Actualizando usuarios del broker (passwd)...")
        write_passwd(env)
    (BROKER_DIR / "data").mkdir(exist_ok=True)
    return env


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def broker_command() -> list[str]:
    exe = find_mosquitto_tool("mosquitto")
    if not exe:
        sys.exit("No se encontro Mosquitto. Ejecuta: python manage.py setup")
    return [str(exe), "-c", "mosquitto.conf", "-v"]


def cmd_broker(_args: argparse.Namespace) -> int:
    reexec_in_venv()
    env = prepare_broker()
    port = int(env.get("MQTT_PORT", "8883"))
    if port_in_use(port):
        sys.exit(f"El puerto {port} ya esta en uso. Hay otro broker encendido?")
    info(f"Broker MQTT en el puerto {port} (Ctrl+C para detener)")
    proc = subprocess.Popen(broker_command(), cwd=BROKER_DIR)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        return 0


# ------------------------------------------------------------------ servidor y arranque completo

SERVER_DIR = ROOT / "server"
SIM_DIR = ROOT / "simulator"


def server_command() -> list[str]:
    return [sys.executable, "-m", "app.main"]


def sim_command(extra: list[str] | None = None) -> list[str]:
    return [sys.executable, "-m", "simulator", *(extra or [])]


def wait_until(check, timeout_s: float, procs: list | None = None) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if check():
            return True
        if procs and any(p.poll() is not None for _, p in procs):
            return False
        time.sleep(0.3)
    return False


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def stop_all(procs: list) -> None:
    # Con Ctrl+C los procesos hijos ya reciben la senal; se espera un poco y luego se cierran
    for name, p in reversed(procs):
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        except KeyboardInterrupt:
            p.kill()


def tail(path: Path, n: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def cmd_server(_args: argparse.Namespace) -> int:
    reexec_in_venv()
    env = ensure_env()
    ensure_certs(env)
    port = int(env.get("SERVER_PORT", "8000"))
    if port_in_use(port):
        sys.exit(f"El puerto {port} ya esta en uso. Hay otro servidor encendido?")
    info(f"Servidor web en http://localhost:{port} (Ctrl+C para detener)")
    try:
        return subprocess.call(server_command(), cwd=SERVER_DIR)
    except KeyboardInterrupt:
        return 0


def cmd_start(args: argparse.Namespace) -> int:
    reexec_in_venv()
    env = prepare_broker()
    mqtt_port = int(env.get("MQTT_PORT", "8883"))
    web_port = int(env.get("SERVER_PORT", "8000"))
    for port, what in ((mqtt_port, "broker"), (web_port, "servidor web")):
        if port_in_use(port):
            sys.exit(f"El puerto {port} ({what}) ya esta en uso. Ya esta encendido Bus Counter en otra ventana?")
    if args.sim and not (SIM_DIR / "__main__.py").exists():
        warn("El simulador todavia no existe (fase 3). Se enciende sin simulador.")
        args.sim = False

    procs: list[tuple[str, subprocess.Popen]] = []
    broker_log_path = BROKER_DIR / "mosquitto.log"
    broker_log = open(broker_log_path, "w", encoding="utf-8")
    try:
        info("Encendiendo broker MQTT...")
        procs.append(("broker", subprocess.Popen(broker_command(), cwd=BROKER_DIR,
                                                 stdout=broker_log, stderr=subprocess.STDOUT)))
        if not wait_until(lambda: port_in_use(mqtt_port), 15, procs):
            print(tail(broker_log_path))
            raise RuntimeError("El broker no arranco. Arriba esta su registro.")

        info("Encendiendo servidor web...")
        procs.append(("servidor", subprocess.Popen(server_command(), cwd=SERVER_DIR)))
        if not wait_until(lambda: http_ok(f"http://127.0.0.1:{web_port}/api/health"), 30, procs):
            raise RuntimeError("El servidor web no arranco. Revisa los mensajes de arriba.")

        if args.sim:
            info("Encendiendo simulador...")
            procs.append(("simulador", subprocess.Popen(sim_command(), cwd=ROOT)))

        url = f"http://localhost:{web_port}"
        ip = advertised_ip(env)
        print()
        print("  Bus Counter esta encendido.")
        print(f"  En este computador:     {url}")
        print(f"  Desde otros equipos:    http://{ip}:{web_port}  (misma red WiFi)")
        print(f"  Broker para los buses:  {ip}:{mqtt_port}")
        print(f"  Registro del broker:    {broker_log_path}")
        print("  Para apagar: Ctrl+C")
        print()
        if not args.no_browser:
            webbrowser.open(url)

        while True:
            for name, p in procs:
                if p.poll() is not None:
                    raise RuntimeError(f"El proceso '{name}' se detuvo (codigo {p.returncode}).")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        info("Apagando...")
        stop_all(procs)
        return 0
    except RuntimeError as e:
        warn(str(e))
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        stop_all(procs)
        return 1
    finally:
        broker_log.close()


# ------------------------------------------------------------------ test

def cmd_test(args: argparse.Namespace) -> int:
    reexec_in_venv()
    return subprocess.call([sys.executable, "-m", "pytest", "-q", "server/tests", "broker/tests", *args.pytest_args], cwd=ROOT)


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
    s.add_argument("--force", action="store_true", help="regenera el certificado del servidor (conserva la CA)")
    s.add_argument("--ca", action="store_true", help="genera tambien una CA nueva (obliga a reflashear el ESP32)")
    s.set_defaults(func=cmd_certs)

    s = sub.add_parser("start", help="enciende broker + servidor")
    s.add_argument("--sim", action="store_true", help="enciende tambien el simulador")
    s.add_argument("--no-browser", action="store_true", help="no abre el navegador")
    s.set_defaults(func=cmd_start)

    sub.add_parser("broker", help="enciende solo el broker").set_defaults(func=cmd_broker)
    sub.add_parser("server", help="enciende solo el servidor").set_defaults(func=cmd_server)
    sub.add_parser("sim", help="enciende solo el simulador").set_defaults(func=not_yet("fase 3"))
    sub.add_parser("flash", help="graba el firmware").set_defaults(func=not_yet("fase 5"))

    s = sub.add_parser("test", help="ejecuta las pruebas")
    s.add_argument("pytest_args", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_test)
    return p


def main() -> int:
    if sys.version_info[:2] < MIN_PYTHON:
        sys.exit(f"Se necesita Python 3.10 o superior (tienes {platform.python_version()}).")
    sys.stdout.reconfigure(line_buffering=True)  # que los mensajes salgan al tiro aunque se redirija
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
