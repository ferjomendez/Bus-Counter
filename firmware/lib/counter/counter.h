// Logica de conteo de pasajeros, sin dependencias de hardware.
// Se compila igual en el ESP32 y en el computador (tests nativos de PlatformIO).
//
// Montaje: dos sensores en el mismo lado del marco, separados en el sentido de paso.
// A mira hacia afuera del bus y B hacia adentro. Quien entra corta primero A y luego B;
// quien sale corta primero B y luego A.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace counter {

// Lectura invalida del sensor (por ejemplo RangeStatus == 4): se ignora
constexpr int INVALID = -1;

enum class Dir : uint8_t { None, In, Out };

// Estados de la puerta
//   Idle:      reposo, ambos sensores libres
//   First:     un solo sensor activado (el que se activo primero)
//   Both:      ambos activados
//   Last:      el primero ya se libero y queda activado el segundo
//   WaitClear: secuencia descartada por tiempo; espera a que ambos se liberen
enum class State : uint8_t { Idle, First, Both, Last, WaitClear };

const char* stateName(State s);

struct Config {
  uint16_t margin_mm;     // interrumpido si la distancia < base - margin_mm
  uint32_t debounce_ms;   // tiempo que una lectura debe mantenerse para cambiar de estado
  uint32_t timeout_ms;    // secuencias mas largas que esto se descartan
};

// Antirrebote de un sensor: el estado estable cambia solo si la lectura cruda
// se mantiene distinta durante debounce_ms.
class Debouncer {
 public:
  bool update(bool raw, uint32_t now_ms, uint32_t debounce_ms);
  bool stable() const { return stable_; }
  void reset() { stable_ = false; candidate_ = false; since_ = 0; }

 private:
  bool stable_ = false;
  bool candidate_ = false;
  uint32_t since_ = 0;
};

class Door {
 public:
  explicit Door(const Config& cfg) : cfg_(cfg) {}

  void setConfig(const Config& cfg) { cfg_ = cfg; }
  const Config& config() const { return cfg_; }

  // Distancia base de la puerta vacia, por sensor (0 = sin calibrar: nunca interrumpido)
  void setBaseline(uint16_t a_mm, uint16_t b_mm);
  uint16_t baselineA() const { return base_a_; }
  uint16_t baselineB() const { return base_b_; }

  // Procesa una lectura de ambos sensores (mm o INVALID). Devuelve el sentido
  // cuando se completa una secuencia, o Dir::None.
  Dir update(int a_mm, int b_mm, uint32_t now_ms);

  State state() const { return state_; }
  bool blockedA() const { return deb_a_.stable(); }
  bool blockedB() const { return deb_b_.stable(); }
  void reset();

 private:
  bool rawBlocked(int mm, uint16_t base, bool previous) const;

  Config cfg_;
  uint16_t base_a_ = 0;
  uint16_t base_b_ = 0;
  bool raw_a_ = false;
  bool raw_b_ = false;
  Debouncer deb_a_;
  Debouncer deb_b_;
  State state_ = State::Idle;
  bool first_is_a_ = true;
  uint32_t started_ms_ = 0;
};

// Promedio de las lecturas validas (ignora INVALID y 0). Devuelve 0 si hay menos
// de la mitad de lecturas validas.
uint16_t averageBaseline(const int* samples, size_t n);

// Conteo acumulado. El conteo nunca baja de 0 y seq crece con cada paso.
struct Tally {
  int32_t count = 0;
  uint32_t in = 0;
  uint32_t out = 0;
  uint32_t seq = 0;

  // Registra un paso y devuelve su numero de secuencia
  uint32_t apply(Dir d);
  // Comandos del servidor: no cambian seq
  void reset() { count = 0; }
  void set(int32_t value) { count = value < 0 ? 0 : value; }
};

struct Event {
  uint32_t seq;
  Dir dir;
  uint32_t ts;      // hora UNIX (0 si no hay hora valida)
  uint32_t up_ms;   // milisegundos desde el arranque
};

// Cola circular para guardar eventos sin conexion. Si se llena, se descarta el
// mas antiguo (el servidor lo detecta como salto de seq y se resincroniza con el
// siguiente snapshot).
template <typename T, size_t N>
class Ring {
 public:
  void push(const T& item) {
    if (size_ == N) {
      head_ = (head_ + 1) % N;
      --size_;
      ++dropped_;
    }
    items_[(head_ + size_) % N] = item;
    ++size_;
  }
  bool empty() const { return size_ == 0; }
  size_t size() const { return size_; }
  size_t capacity() const { return N; }
  uint32_t dropped() const { return dropped_; }
  const T& front() const { return items_[head_]; }
  void pop() {
    if (size_ == 0) return;
    head_ = (head_ + 1) % N;
    --size_;
  }

 private:
  T items_[N];
  size_t head_ = 0;
  size_t size_ = 0;
  uint32_t dropped_ = 0;
};

}  // namespace counter
