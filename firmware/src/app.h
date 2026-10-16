// Estado compartido entre los modulos del firmware.
#pragma once

#include <Arduino.h>

#include "config.h"
#include "counter.h"

// Capacidad de la cola de eventos guardados sin conexion (minimo 500)
constexpr size_t OFFLINE_QUEUE_SIZE = 500;

struct SensorSnapshot {
  // Escritos por la tarea de sensores, leidos por la web (valores de 32 bits: lectura atomica)
  volatile int a_mm = counter::INVALID;
  volatile int b_mm = counter::INVALID;
  volatile uint16_t base_a = 0;
  volatile uint16_t base_b = 0;
  volatile bool blocked_a = false;
  volatile bool blocked_b = false;
  volatile uint8_t state = 0;
  volatile bool ok = false;           // sensores iniciados
  volatile bool calibrating = false;
  volatile bool recalibrate = false;  // pedido desde la pagina /debug
};

struct App {
  counter::Tally tally;                                    // solo se modifica en loop()
  counter::Ring<counter::Event, OFFLINE_QUEUE_SIZE> queue; // eventos por enviar
  SensorSnapshot sensors;
  bool count_dirty = true;
};

extern App app;

// Hora UNIX valida (0 si aun no hay NTP)
uint32_t unixTime();
