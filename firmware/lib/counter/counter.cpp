#include "counter.h"

namespace counter {

const char* stateName(State s) {
  switch (s) {
    case State::Idle: return "reposo";
    case State::First: return "uno activado";
    case State::Both: return "ambos activados";
    case State::Last: return "solo el segundo";
    case State::WaitClear: return "esperando puerta libre";
  }
  return "?";
}

bool Debouncer::update(bool raw, uint32_t now_ms, uint32_t debounce_ms) {
  if (raw == stable_) {
    candidate_ = stable_;
    return stable_;
  }
  if (raw != candidate_) {
    candidate_ = raw;
    since_ = now_ms;
  }
  if (now_ms - since_ >= debounce_ms) stable_ = candidate_;
  return stable_;
}

void Door::setBaseline(uint16_t a_mm, uint16_t b_mm) {
  base_a_ = a_mm;
  base_b_ = b_mm;
  reset();
}

void Door::reset() {
  state_ = State::Idle;
  raw_a_ = raw_b_ = false;
  deb_a_.reset();
  deb_b_.reset();
}

bool Door::rawBlocked(int mm, uint16_t base, bool previous) const {
  if (mm == INVALID || mm <= 0) return previous;  // lectura invalida: se mantiene lo anterior
  if (base == 0 || base <= cfg_.margin_mm) return false;
  return mm < static_cast<int>(base - cfg_.margin_mm);
}

Dir Door::update(int a_mm, int b_mm, uint32_t now_ms) {
  raw_a_ = rawBlocked(a_mm, base_a_, raw_a_);
  raw_b_ = rawBlocked(b_mm, base_b_, raw_b_);
  const bool a = deb_a_.update(raw_a_, now_ms, cfg_.debounce_ms);
  const bool b = deb_b_.update(raw_b_, now_ms, cfg_.debounce_ms);

  // "first" es el sensor que se activo primero y "second" el otro
  const bool first = first_is_a_ ? a : b;
  const bool second = first_is_a_ ? b : a;

  if (state_ != State::Idle && state_ != State::WaitClear && now_ms - started_ms_ > cfg_.timeout_ms) {
    state_ = State::WaitClear;  // alguien se quedo en la puerta: se descarta la secuencia
  }

  switch (state_) {
    case State::Idle:
      if (a && b) {
        state_ = State::WaitClear;  // ambos a la vez: no se sabe el sentido
      } else if (a || b) {
        first_is_a_ = a;
        started_ms_ = now_ms;
        state_ = State::First;
      }
      break;

    case State::First:
      if (first && second) state_ = State::Both;
      else if (!first && second) state_ = State::Last;  // paso muy rapido, sin lectura de ambos
      else if (!first) state_ = State::Idle;            // se devolvio sin cruzar
      break;

    case State::Both:
      if (!first && !second) {
        state_ = State::Idle;  // se liberaron ambos en la misma lectura: cuenta igual
        return first_is_a_ ? Dir::In : Dir::Out;
      }
      if (!first) state_ = State::Last;
      else if (!second) state_ = State::First;  // retrocedio
      break;

    case State::Last:
      if (!second && !first) {
        state_ = State::Idle;
        return first_is_a_ ? Dir::In : Dir::Out;
      }
      if (first) state_ = State::Both;  // volvio a activar el primero
      break;

    case State::WaitClear:
      if (!a && !b) state_ = State::Idle;
      break;
  }
  return Dir::None;
}

uint16_t averageBaseline(const int* samples, size_t n) {
  uint32_t sum = 0;
  size_t valid = 0;
  for (size_t i = 0; i < n; ++i) {
    if (samples[i] > 0) {
      sum += static_cast<uint32_t>(samples[i]);
      ++valid;
    }
  }
  if (valid == 0 || valid * 2 < n) return 0;
  return static_cast<uint16_t>(sum / valid);
}

uint32_t Tally::apply(Dir d) {
  if (d == Dir::None) return seq;
  ++seq;
  if (d == Dir::In) {
    ++count;
    ++in;
  } else {
    if (count > 0) --count;
    ++out;
  }
  return seq;
}

}  // namespace counter
