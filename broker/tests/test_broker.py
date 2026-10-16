"""Prueba de aceptacion del broker (fase 1).

Levanta un Mosquitto temporal con la misma configuracion de TLS y ACL que el real,
pero en un puerto libre y con certificados y usuarios de prueba. Usa mosquitto_pub y
mosquitto_sub para demostrar que:
  - un bus publica en sus propios topicos,
  - un bus no puede publicar en los de otro bus ni leer sus comandos,
  - sin certificado valido, sin TLS o con clave incorrecta no se conecta.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import manage  # noqa: E402

TOOLS = {t: manage.find_mosquitto_tool(t) for t in manage.MOSQUITTO_TOOLS}
pytestmark = pytest.mark.skipif(not all(TOOLS.values()), reason="Mosquitto no esta instalado")

USERS = {"servidor": "clave-servidor", "B01": "clave-b01", "B02": "clave-b02"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    d = tmp_path_factory.mktemp("broker")
    manage.generate_ca(d / "certs")
    manage.generate_server_cert(d / "certs", "127.0.0.1")
    shutil.copyfile(manage.BROKER_DIR / "acl.txt", d / "acl.txt")
    env = {"MQTT_SERVER_USER": "servidor", "MQTT_SERVER_PASSWORD": USERS["servidor"],
           "BUS_IDS": "B01,B02", "BUS_PASSWORD_B01": USERS["B01"], "BUS_PASSWORD_B02": USERS["B02"]}
    manage.write_passwd(env, d / "passwd")

    # Misma configuracion que broker/mosquitto.conf, cambiando solo el puerto
    port = free_port()
    conf = (manage.BROKER_DIR / "mosquitto.conf").read_text(encoding="utf-8")
    conf = conf.replace("listener 8883", f"listener {port}")
    conf = conf.replace("persistence true", "persistence false")
    (d / "mosquitto.conf").write_text(conf, encoding="utf-8")

    # El log va a un archivo: un pipe sin leer se llena y bloquea al broker
    log = open(d / "broker.log", "w")
    proc = subprocess.Popen([str(TOOLS["mosquitto"]), "-c", "mosquitto.conf", "-v"], cwd=d,
                            stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 10
    while not manage.port_in_use(port):
        if proc.poll() is not None or time.time() > deadline:
            proc.kill()
            out = (d / "broker.log").read_text(errors="replace")
            pytest.fail(f"El broker no arranco:\n{out}")
        time.sleep(0.1)
    yield {"port": port, "dir": d}
    proc.terminate()
    proc.wait(5)
    log.close()


def client_args(b, user=None, cafile="default", host="localhost"):
    args = ["-h", host, "-p", str(b["port"]), "-V", "mqttv5"]
    if cafile == "default":
        cafile = b["dir"] / "certs" / "ca.crt"
    if cafile:
        args += ["--cafile", str(cafile)]
    if user:
        args += ["-u", user, "-P", USERS.get(user, "clave-mala")]
    return args


def pub(b, user, topic, msg, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(TOOLS["mosquitto_pub"]), *client_args(b, user, **kw),
                           "-t", topic, "-m", msg, "-q", "1"],
                          capture_output=True, text=True, timeout=15)


def start_sub(b, user, topic, wait_s=4) -> subprocess.Popen:
    p = subprocess.Popen([str(TOOLS["mosquitto_sub"]), *client_args(b, user), "-t", topic, "-v",
                          "-q", "1", "-W", str(wait_s)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)  # da tiempo a que se suscriba
    return p


def sub_output(p: subprocess.Popen) -> str:
    out, _ = p.communicate(timeout=15)
    return out


def test_bus_publica_en_sus_topicos(broker):
    s = start_sub(broker, "servidor", "buses/#")
    for t in ("event", "count", "status"):
        r = pub(broker, "B01", f"buses/B01/{t}", f"propio-{t}")
        assert r.returncode == 0, r.stderr
    out = sub_output(s)
    for t in ("event", "count", "status"):
        assert f"buses/B01/{t} propio-{t}" in out


def test_bus_no_publica_en_topicos_de_otro_bus(broker):
    s = start_sub(broker, "servidor", "buses/#")
    pub(broker, "B01", "buses/B02/event", "intruso-event")
    pub(broker, "B01", "buses/B02/count", "intruso-count")
    pub(broker, "B01", "buses/B01/cmd", "bus-no-manda-comandos")
    pub(broker, "B02", "buses/B02/event", "legitimo")
    out = sub_output(s)
    assert "buses/B02/event legitimo" in out  # el broker funciona
    assert "intruso" not in out
    assert "bus-no-manda-comandos" not in out


def test_bus_solo_lee_sus_comandos(broker):
    own = start_sub(broker, "B01", "buses/B01/cmd")
    other = start_sub(broker, "B01", "buses/B02/cmd")
    assert pub(broker, "servidor", "buses/B01/cmd", '{"action":"reset"}').returncode == 0
    assert pub(broker, "servidor", "buses/B02/cmd", '{"action":"set","count":5}').returncode == 0
    assert '{"action":"reset"}' in sub_output(own)
    assert '"set"' not in sub_output(other)


def test_rechaza_ca_no_confiable(broker, tmp_path):
    manage.generate_ca(tmp_path, "CA falsa")
    r = pub(broker, "B01", "buses/B01/event", "x", cafile=tmp_path / "ca.crt")
    assert r.returncode != 0


def test_rechaza_conexion_sin_tls(broker):
    r = pub(broker, "B01", "buses/B01/event", "x", cafile=None)
    assert r.returncode != 0


def test_rechaza_nombre_fuera_del_certificado(broker):
    # El certificado es para 127.0.0.1 y localhost; otro nombre no valida
    host = socket.gethostname()
    r = pub(broker, "B01", "buses/B01/event", "x", host=host)
    assert r.returncode != 0


def test_rechaza_clave_incorrecta_y_anonimo(broker):
    r = subprocess.run([str(TOOLS["mosquitto_pub"]), *client_args(broker), "-u", "B01", "-P", "mala",
                        "-t", "buses/B01/event", "-m", "x"], capture_output=True, text=True, timeout=15)
    assert r.returncode != 0
    r = pub(broker, None, "buses/B01/event", "x")
    assert r.returncode != 0
