// Tests nativos de la logica de conteo:  pio test -e native
#include <unity.h>

#include "counter.h"

using namespace counter;

static const Config CFG = {150, 40, 3000};  // margen 150 mm, antirrebote 40 ms, timeout 3 s
static const int FREE = 1000;               // puerta vacia: 1 m hasta el panel
static const int BODY = 450;                // una persona cortando el haz

struct Sim {
  Door door{CFG};
  uint32_t t = 0;
  int ins = 0;
  int outs = 0;

  Sim() { door.setBaseline(1000, 1000); }

  // Mantiene una lectura durante ms milisegundos, leyendo cada 25 ms
  void hold(int a, int b, uint32_t ms) {
    for (uint32_t end = t + ms; t < end; t += 25) {
      Dir d = door.update(a, b, t);
      if (d == Dir::In) ++ins;
      if (d == Dir::Out) ++outs;
    }
  }
};

void setUp() {}
void tearDown() {}

void test_entrada_a_luego_b() {
  Sim s;
  s.hold(FREE, FREE, 200);
  s.hold(BODY, FREE, 150);
  s.hold(BODY, BODY, 150);
  s.hold(FREE, BODY, 150);
  s.hold(FREE, FREE, 200);
  TEST_ASSERT_EQUAL(1, s.ins);
  TEST_ASSERT_EQUAL(0, s.outs);
  TEST_ASSERT_TRUE(s.door.state() == State::Idle);
}

void test_salida_b_luego_a() {
  Sim s;
  s.hold(FREE, BODY, 150);
  s.hold(BODY, BODY, 150);
  s.hold(BODY, FREE, 150);
  s.hold(FREE, FREE, 200);
  TEST_ASSERT_EQUAL(0, s.ins);
  TEST_ASSERT_EQUAL(1, s.outs);
}

void test_paso_rapido_sin_lectura_de_ambos() {
  Sim s;
  s.hold(BODY, FREE, 100);
  s.hold(FREE, BODY, 100);
  s.hold(FREE, FREE, 200);
  TEST_ASSERT_EQUAL(1, s.ins);
}

void test_ambos_se_liberan_a_la_vez_cuenta() {
  Sim s;
  s.hold(BODY, FREE, 100);
  s.hold(BODY, BODY, 200);
  s.hold(FREE, FREE, 200);
  TEST_ASSERT_EQUAL(1, s.ins);
}

void test_se_devuelve_sin_cruzar_no_cuenta() {
  Sim s;
  s.hold(BODY, FREE, 300);
  s.hold(FREE, FREE, 300);
  s.hold(BODY, FREE, 100);
  s.hold(BODY, BODY, 100);
  s.hold(BODY, FREE, 100);  // retrocede
  s.hold(FREE, FREE, 300);
  TEST_ASSERT_EQUAL(0, s.ins + s.outs);
}

void test_rebote_corto_se_ignora() {
  Sim s;
  s.hold(BODY, FREE, 25);  // un solo ciclo: menos que el antirrebote
  s.hold(FREE, BODY, 25);
  s.hold(FREE, FREE, 300);
  TEST_ASSERT_EQUAL(0, s.ins + s.outs);
  TEST_ASSERT_TRUE(s.door.state() == State::Idle);
}

void test_lecturas_invalidas_mantienen_el_estado() {
  Sim s;
  s.hold(BODY, FREE, 150);
  s.hold(INVALID, INVALID, 100);  // RangeStatus 4: no cambia nada
  s.hold(BODY, BODY, 150);
  s.hold(FREE, INVALID, 100);
  s.hold(FREE, BODY, 100);
  s.hold(FREE, FREE, 200);
  TEST_ASSERT_EQUAL(1, s.ins);
}

void test_timeout_descarta_y_espera_puerta_libre() {
  Sim s;
  s.hold(BODY, FREE, 150);
  s.hold(BODY, BODY, 3500);  // alguien parado en la puerta
  TEST_ASSERT_TRUE(s.door.state() == State::WaitClear);
  s.hold(FREE, BODY, 150);
  s.hold(FREE, FREE, 200);
  TEST_ASSERT_EQUAL(0, s.ins + s.outs);
  s.hold(BODY, FREE, 150);  // la siguiente persona cuenta normal
  s.hold(FREE, BODY, 150);
  s.hold(FREE, FREE, 200);
  TEST_ASSERT_EQUAL(1, s.ins);
}

void test_varias_personas_seguidas() {
  Sim s;
  for (int i = 0; i < 5; ++i) {
    s.hold(BODY, FREE, 100);
    s.hold(BODY, BODY, 100);
    s.hold(FREE, BODY, 100);
    s.hold(FREE, FREE, 100);
  }
  for (int i = 0; i < 2; ++i) {
    s.hold(FREE, BODY, 100);
    s.hold(BODY, BODY, 100);
    s.hold(BODY, FREE, 100);
    s.hold(FREE, FREE, 100);
  }
  TEST_ASSERT_EQUAL(5, s.ins);
  TEST_ASSERT_EQUAL(2, s.outs);
}

void test_margen_cerca_de_la_base_no_interrumpe() {
  Sim s;
  s.hold(900, 900, 300);  // 100 mm menos que la base: dentro del margen de 150
  TEST_ASSERT_FALSE(s.door.blockedA());
  s.hold(840, 1000, 300);  // 160 mm menos: interrumpido
  TEST_ASSERT_TRUE(s.door.blockedA());
}

void test_sin_calibrar_nunca_interrumpe() {
  Door d(CFG);
  for (uint32_t t = 0; t < 500; t += 25) d.update(100, 100, t);
  TEST_ASSERT_FALSE(d.blockedA());
  TEST_ASSERT_TRUE(d.state() == State::Idle);
}

void test_promedio_de_calibracion() {
  int ok[] = {1000, 1010, 990, INVALID, 1000};
  TEST_ASSERT_EQUAL_UINT16(1000, averageBaseline(ok, 5));
  int bad[] = {INVALID, INVALID, INVALID, 1000};
  TEST_ASSERT_EQUAL_UINT16(0, averageBaseline(bad, 4));  // menos de la mitad validas
}

void test_tally_nunca_baja_de_cero_y_seq_crece() {
  Tally t;
  TEST_ASSERT_EQUAL_UINT32(1, t.apply(Dir::Out));
  TEST_ASSERT_EQUAL(0, t.count);
  t.apply(Dir::In);
  t.apply(Dir::In);
  TEST_ASSERT_EQUAL(2, t.count);
  TEST_ASSERT_EQUAL_UINT32(3, t.seq);
  t.set(15);
  t.reset();
  TEST_ASSERT_EQUAL(0, t.count);
  TEST_ASSERT_EQUAL_UINT32(3, t.seq);  // los comandos no cambian seq
  t.set(-4);
  TEST_ASSERT_EQUAL(0, t.count);
  TEST_ASSERT_EQUAL_UINT32(2, t.in);
  TEST_ASSERT_EQUAL_UINT32(1, t.out);
}

void test_cola_circular_descarta_el_mas_antiguo() {
  Ring<int, 3> r;
  for (int i = 1; i <= 5; ++i) r.push(i);
  TEST_ASSERT_EQUAL(3, (int)r.size());
  TEST_ASSERT_EQUAL_UINT32(2, r.dropped());
  TEST_ASSERT_EQUAL(3, r.front());
  r.pop();
  TEST_ASSERT_EQUAL(4, r.front());
  r.pop();
  r.pop();
  TEST_ASSERT_TRUE(r.empty());
  r.pop();  // no falla vacia
  TEST_ASSERT_TRUE(r.empty());
}

void test_cola_de_500_eventos_en_orden() {
  static Ring<Event, 500> r;
  for (uint32_t i = 1; i <= 500; ++i) r.push(Event{i, Dir::In, 0, i});
  uint32_t expected = 1;
  while (!r.empty()) {
    TEST_ASSERT_EQUAL_UINT32(expected++, r.front().seq);
    r.pop();
  }
  TEST_ASSERT_EQUAL_UINT32(501, expected);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_entrada_a_luego_b);
  RUN_TEST(test_salida_b_luego_a);
  RUN_TEST(test_paso_rapido_sin_lectura_de_ambos);
  RUN_TEST(test_ambos_se_liberan_a_la_vez_cuenta);
  RUN_TEST(test_se_devuelve_sin_cruzar_no_cuenta);
  RUN_TEST(test_rebote_corto_se_ignora);
  RUN_TEST(test_lecturas_invalidas_mantienen_el_estado);
  RUN_TEST(test_timeout_descarta_y_espera_puerta_libre);
  RUN_TEST(test_varias_personas_seguidas);
  RUN_TEST(test_margen_cerca_de_la_base_no_interrumpe);
  RUN_TEST(test_sin_calibrar_nunca_interrumpe);
  RUN_TEST(test_promedio_de_calibracion);
  RUN_TEST(test_tally_nunca_baja_de_cero_y_seq_crece);
  RUN_TEST(test_cola_circular_descarta_el_mas_antiguo);
  RUN_TEST(test_cola_de_500_eventos_en_orden);
  return UNITY_END();
}
