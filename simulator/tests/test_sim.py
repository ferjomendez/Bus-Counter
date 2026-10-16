"""Garantias de los perfiles del simulador de las que depende la demo."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from simulator.sim import SimBus  # noqa: E402


def run_stops(profile, capacity, start, stops=300):
    random.seed(1)
    b = SimBus("B99", "x", "big", "UAI-Grecia", profile, capacity, cfg={})
    b.count = start
    seen = []
    for _ in range(stops):
        b.plan_stop(0.0)
        while b.planned:
            b.passenger(b.planned.popleft()[1], publish=False)
            seen.append(b.count)
    return b, seen


def test_bus_con_pocas_personas_nunca_supera_15():
    _, seen = run_stops("low", 50, 6)
    assert max(seen) <= 15


def test_bus_lleno_no_baja_del_90_por_ciento_ni_supera_capacidad():
    _, seen = run_stops("full", 25, 24)
    assert min(seen) >= 23 and max(seen) <= 25


def test_bus_normal_respeta_capacidad_y_seq_crece():
    b, seen = run_stops("normal", 50, 20)
    assert 0 <= min(seen) and max(seen) <= 50
    assert b.seq == b.in_total + b.out_total  # un seq por cada paso
