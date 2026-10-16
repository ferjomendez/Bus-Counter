"""Pruebas de las reglas puras: conteo, muestras, recomendaciones, alertas y validacion."""
from app import logic
from app.logic import CountState, apply_event, rebuild_from_snapshot

S = dict(logic.DEFAULT_SETTINGS)  # umbral 20, ventana 10 min, 90 %, colores 60/90, offline 60 s
NOW = 100_000.0
WINDOW = S["recommend_window_min"] * 60


def flat(count, start=NOW - WINDOW, end=NOW, step=30):
    """Muestras cada 'step' segundos con un conteo constante."""
    t, out = start, []
    while t < end:
        out.append((t, count))
        t += step
    return out


def rec(size, samples, current, capacity=50, online=True, incomplete=False, settings=S):
    return logic.evaluate_recommendation(size=size, capacity=capacity, online=online, incomplete=incomplete,
                                         samples=samples, current_count=current, now=NOW, settings=settings)


# ------------------------------------------------------------------ conteo

def test_eventos_suman_y_restan():
    s = CountState(last_seq=0, snapshot_seq=0)
    s = apply_event(s, 1, "in")
    s = apply_event(s, 2, "in")
    s = apply_event(s, 3, "out")
    assert (s.count, s.in_total, s.out_total, s.last_seq, s.incomplete) == (1, 2, 1, 3, False)


def test_conteo_nunca_baja_de_cero():
    s = apply_event(CountState(last_seq=0, snapshot_seq=0), 1, "out")
    assert s.count == 0 and s.out_total == 1


def test_salto_de_seq_marca_incompleto():
    s = apply_event(CountState(count=5, last_seq=10, snapshot_seq=10), 13, "in")
    assert s.incomplete and s.count == 6 and s.last_seq == 13


def test_primer_evento_sin_snapshot():
    assert not apply_event(CountState(), 1, "in").incomplete
    assert apply_event(CountState(), 512, "in").incomplete  # nos perdimos los anteriores


def test_evento_ya_incluido_en_snapshot_no_cambia_conteo():
    s = CountState(count=7, in_total=10, out_total=3, last_seq=13, snapshot_seq=13)
    assert apply_event(s, 12, "in") == s


def test_evento_atrasado_llena_hueco_sin_quitar_marca():
    s = apply_event(CountState(last_seq=1, snapshot_seq=1), 3, "in")  # falta el 2
    s = apply_event(s, 2, "in")
    assert s.count == 2 and s.last_seq == 3 and s.incomplete  # solo un snapshot limpia la marca


def test_snapshot_mas_eventos_posteriores():
    s = rebuild_from_snapshot(100, 20, 50, 30, [(101, "in"), (102, "in"), (103, "out")])
    assert (s.count, s.in_total, s.out_total, s.last_seq, s.incomplete) == (21, 52, 31, 103, False)


def test_snapshot_ignora_eventos_anteriores_y_limpia_marca():
    s = rebuild_from_snapshot(100, 20, 50, 30, [(99, "in"), (100, "out")])
    assert (s.count, s.last_seq, s.incomplete) == (20, 100, False)


def test_snapshot_con_hueco_posterior_sigue_incompleto():
    s = rebuild_from_snapshot(100, 20, 50, 30, [(102, "in")])
    assert s.incomplete and s.count == 21


def test_snapshot_negativo_se_recorta_a_cero():
    assert rebuild_from_snapshot(5, -3, 0, 3, []).count == 0


def test_should_sample():
    assert logic.should_sample(None, None, NOW, 3)
    assert not logic.should_sample(NOW - 5, 2, NOW, 3)     # cambio, pero hace menos de 10 s
    assert logic.should_sample(NOW - 10, 2, NOW, 3)        # cambio y pasaron 10 s
    assert not logic.should_sample(NOW - 59, 3, NOW, 3)    # sin cambio, menos de un minuto
    assert logic.should_sample(NOW - 60, 3, NOW, 3)        # sin cambio, un minuto


# ------------------------------------------------------------------ ventana

def test_ventana_completa():
    st = logic.window_stats(flat(10), 12, NOW, WINDOW)
    assert st.covered_s == WINDOW and st.max_count == 12 and st.min_count == 10


def test_muestra_previa_a_la_ventana_cuenta_desde_el_inicio():
    samples = [(NOW - WINDOW - 30, 40)] + flat(10, start=NOW - WINDOW + 30)
    st = logic.window_stats(samples, 10, NOW, WINDOW)
    assert st.covered_s == WINDOW and st.max_count == 40


def test_huecos_largos_no_cuentan_como_cubiertos():
    samples = [(NOW - WINDOW, 5), (NOW - 60, 5)]  # 9 minutos sin datos
    st = logic.window_stats(samples, 5, NOW, WINDOW)
    assert st.covered_s == 60
    # La ultima muestra tambien debe estar cerca de 'now' para cubrir ese tramo
    assert logic.window_stats([(NOW - 120, 5)], 5, NOW, WINDOW) is None


def test_sin_muestras_solo_el_conteo_actual():
    assert logic.window_stats([], 5, NOW, WINDOW) is None


# ------------------------------------------------------------------ recomendaciones

def test_grande_con_pocas_personas_recomienda_chico():
    r = rec("big", flat(15), 15)
    assert r == {"kind": "to_small", "message": logic.MSG_TO_SMALL}


def test_grande_en_el_limite_exacto_del_umbral():
    assert rec("big", flat(20), 20)["kind"] == "to_small"   # <= umbral
    assert rec("big", flat(20), 21) is None                 # el maximo supera el umbral


def test_grande_con_un_pico_en_la_ventana_no_recomienda():
    samples = flat(10)
    samples[5] = (samples[5][0], 35)
    assert rec("big", samples, 10) is None


def test_chico_casi_lleno_recomienda_grande():
    # 90 % de 25 = 22,5: el minimo debe ser >= 22,5, o sea 23
    assert rec("small", flat(23), 23, capacity=25) == {"kind": "to_big", "message": logic.MSG_TO_BIG}
    assert rec("small", flat(22), 23, capacity=25) is None


def test_chico_al_100_por_ciento():
    s = dict(S, recommend_full_pct=100)
    assert rec("small", flat(25), 25, capacity=25, settings=s)["kind"] == "to_big"
    assert rec("small", flat(24), 25, capacity=25, settings=s) is None


def test_no_evalua_offline_incompleto_ni_sin_tipo():
    assert rec("big", flat(5), 5, online=False) is None
    assert rec("big", flat(5), 5, incomplete=True) is None
    assert rec(None, flat(5), 5) is None


def test_ventana_cubierta_menos_de_la_mitad_no_evalua():
    half = WINDOW / 2
    assert rec("big", flat(5, start=NOW - half + 30), 5) is None      # menos de la mitad
    assert rec("big", flat(5, start=NOW - half), 5)["kind"] == "to_small"  # justo la mitad


def test_chico_con_pocas_personas_no_recomienda_nada():
    assert rec("small", flat(2), 2, capacity=25) is None


def test_bus_sizes():
    big, small = logic.bus_sizes([{"id": 1, "capacity": 50}, {"id": 2, "capacity": 25}])
    assert (big, small) == (1, 2)
    big, small = logic.bus_sizes([{"id": 1, "capacity": 20}, {"id": 2, "capacity": 40}])
    assert (big, small) == (2, 1)  # el grande es el de mayor capacidad, no el id 1
    assert logic.bus_sizes([{"id": 1, "capacity": 30}, {"id": 2, "capacity": 30}]) == (None, None)


# ------------------------------------------------------------------ ocupacion y alertas

def test_niveles_de_color():
    assert logic.occupancy_level(29, 50, S) == "green"
    assert logic.occupancy_level(30, 50, S) == "amber"   # 60 %
    assert logic.occupancy_level(45, 50, S) == "red"     # 90 %


def test_alertas():
    kinds = lambda **kw: [a["kind"] for a in logic.bus_alerts(**kw)]
    assert kinds(online=True, incomplete=False, count=60, capacity=50) == []          # 120 % exacto
    assert kinds(online=True, incomplete=False, count=61, capacity=50) == ["suspicious"]
    assert kinds(online=False, incomplete=True, count=0, capacity=50) == ["offline", "incomplete"]


# ------------------------------------------------------------------ validacion

TYPES = [{"id": 1, "name": "Grande", "capacity": 50}, {"id": 2, "name": "Chico", "capacity": 25}]


def test_config_valida():
    c = logic.check_config(TYPES, S)
    assert c.errors == [] and c.confirm == [] and c.notices == []


def test_colores_ambar_debe_ser_menor_que_rojo():
    assert logic.check_config(TYPES, dict(S, color_amber_pct=90, color_red_pct=90)).errors


def test_umbral_mayor_que_capacidad_chico_pide_confirmacion():
    c = logic.check_config(TYPES, dict(S, recommend_low_threshold=26))
    assert c.errors == [] and len(c.confirm) == 1
    assert logic.check_config(TYPES, dict(S, recommend_low_threshold=25)).confirm == []


def test_capacidades_iguales_desactiva_con_aviso():
    types = [dict(TYPES[0], capacity=30), dict(TYPES[1], capacity=30)]
    c = logic.check_config(types, S)
    assert c.errors == [] and c.notices == [logic.MSG_SAME_CAPACITY]


def test_valores_fuera_de_rango():
    assert logic.check_config(TYPES, dict(S, recommend_window_min=0)).errors
    assert logic.check_config([dict(TYPES[0], capacity=0), TYPES[1]], S).errors
    assert logic.check_config(TYPES, dict(S, offline_timeout_s=True)).errors
