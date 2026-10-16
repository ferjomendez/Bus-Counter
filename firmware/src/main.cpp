// Contador de pasajeros para buses: ESP32 + 2x VL53L0X (GY-530).
//
// - Tarea de sensores (sensors.cpp): lee A y B cada SENSOR_PERIOD_MS y detecta pasos.
// - loop(): registra los pasos (NVS + cola), atiende la red (net.cpp) y la web (web.cpp).
#include <Arduino.h>

#include "app.h"
#include "net.h"
#include "sensors.h"
#include "storage.h"
#include "web.h"

App app;

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nContador de pasajeros, bus %s, firmware %s\n", BUS_ID, FW_VERSION);

  storageLoad(app.tally);
  Serial.printf("[conteo] Recuperado de memoria: %ld a bordo, %lu subidas, %lu bajadas, seq %lu\n",
                static_cast<long>(app.tally.count), static_cast<unsigned long>(app.tally.in),
                static_cast<unsigned long>(app.tally.out), static_cast<unsigned long>(app.tally.seq));

  sensorsBegin();
  netBegin();
  webBegin();
}

void loop() {
  counter::Dir d;
  while (sensorsNextPass(d)) {
    const uint32_t seq = app.tally.apply(d);
    storageSave(app.tally);
    app.queue.push(counter::Event{seq, d, unixTime(), millis()});
    app.count_dirty = true;
    Serial.printf("[conteo] %s. A bordo: %ld (seq %lu)\n", d == counter::Dir::In ? "Subio" : "Bajo",
                  static_cast<long>(app.tally.count), static_cast<unsigned long>(seq));
  }
  netLoop();
  webLoop();
  delay(2);
}
