// Lectura de los dos VL53L0X en una tarea propia, para que la red nunca frene el conteo.
#include "sensors.h"

#include <Adafruit_VL53L0X.h>
#include <Wire.h>

#include "app.h"

static Adafruit_VL53L0X sensorA;
static Adafruit_VL53L0X sensorB;
static QueueHandle_t passQueue = nullptr;
static counter::Door door({MARGEN_MM, DEBOUNCE_MS, SEQUENCE_TIMEOUT_MS});
static int lastA = counter::INVALID;
static int lastB = counter::INVALID;

// Todos los VL53L0X parten en 0x29: se apagan ambos con XSHUT y se encienden de a uno
// para asignarles 0x30 (A) y 0x31 (B).
static bool initSensors() {
  pinMode(PIN_XSHUT_A, OUTPUT);
  pinMode(PIN_XSHUT_B, OUTPUT);
  digitalWrite(PIN_XSHUT_A, LOW);
  digitalWrite(PIN_XSHUT_B, LOW);
  delay(10);

  digitalWrite(PIN_XSHUT_A, HIGH);
  delay(10);
  if (!sensorA.begin(ADDR_A, false, &Wire, Adafruit_VL53L0X::VL53L0X_SENSE_LONG_RANGE)) {
    Serial.println("[sensores] No responde el sensor A. Revisa cableado y XSHUT (GPIO 25).");
    return false;
  }
  digitalWrite(PIN_XSHUT_B, HIGH);
  delay(10);
  if (!sensorB.begin(ADDR_B, false, &Wire, Adafruit_VL53L0X::VL53L0X_SENSE_LONG_RANGE)) {
    Serial.println("[sensores] No responde el sensor B. Revisa cableado y XSHUT (GPIO 26).");
    return false;
  }
  // Medicion continua: ambos sensores miden en paralelo y aqui solo se recoge el resultado
  // (si el periodo es menor que el tiempo de medicion del modo largo alcance, el sensor
  // mide tan rapido como puede, unos 33 ms)
  sensorA.startRangeContinuous(SENSOR_PERIOD_MS);
  sensorB.startRangeContinuous(SENSOR_PERIOD_MS);
  return true;
}

// Ultima lectura del sensor en mm, o INVALID si RangeStatus == 4 (fuera de rango o fallo).
// Si todavia no hay una medicion nueva se repite la anterior.
static int readMM(Adafruit_VL53L0X& s, int& last) {
  if (s.isRangeComplete()) {
    uint16_t mm = s.readRangeResult();
    // readRangeResult devuelve 0xFFFF si fallo la lectura I2C
    last = (s.readRangeStatus() == 4 || mm == 0xFFFF) ? counter::INVALID : static_cast<int>(mm);
  }
  return last;
}

static void calibrate() {
  app.sensors.calibrating = true;
  static int samplesA[CALIBRATION_SAMPLES];
  static int samplesB[CALIBRATION_SAMPLES];
  Serial.println("[sensores] Midiendo la puerta vacia. No pases por la puerta...");
  for (int i = 0; i < CALIBRATION_SAMPLES; ++i) {
    vTaskDelay(pdMS_TO_TICKS(40));  // ~una medicion nueva por sensor
    samplesA[i] = readMM(sensorA, lastA);
    samplesB[i] = readMM(sensorB, lastB);
  }
  uint16_t baseA = counter::averageBaseline(samplesA, CALIBRATION_SAMPLES);
  uint16_t baseB = counter::averageBaseline(samplesB, CALIBRATION_SAMPLES);
  if (baseA == 0) {
    Serial.printf("[sensores] AVISO: el sensor A no logro medir la puerta. Se usa %d mm.\n", FALLBACK_BASELINE_MM);
    baseA = FALLBACK_BASELINE_MM;
  }
  if (baseB == 0) {
    Serial.printf("[sensores] AVISO: el sensor B no logro medir la puerta. Se usa %d mm.\n", FALLBACK_BASELINE_MM);
    baseB = FALLBACK_BASELINE_MM;
  }
  door.setBaseline(baseA, baseB);
  app.sensors.base_a = baseA;
  app.sensors.base_b = baseB;
  Serial.printf("[sensores] Distancia base: A=%u mm, B=%u mm (margen %d mm)\n", baseA, baseB, MARGEN_MM);
  app.sensors.calibrating = false;
}

static void sensorTask(void*) {
  while (!initSensors()) {
    app.sensors.ok = false;
    vTaskDelay(pdMS_TO_TICKS(5000));  // reintenta: quizas se conecto despues
  }
  app.sensors.ok = true;
  Serial.println("[sensores] OK. Formato: A_mm, B_mm");
  calibrate();

  TickType_t wake = xTaskGetTickCount();
  for (;;) {
    if (app.sensors.recalibrate) {
      app.sensors.recalibrate = false;
      calibrate();
      wake = xTaskGetTickCount();
    }
    const int a = readMM(sensorA, lastA);
    const int b = readMM(sensorB, lastB);
    const counter::Dir d = door.update(a, b, millis());

    app.sensors.a_mm = a;
    app.sensors.b_mm = b;
    app.sensors.blocked_a = door.blockedA();
    app.sensors.blocked_b = door.blockedB();
    app.sensors.state = static_cast<uint8_t>(door.state());
#if SERIAL_STREAM
    Serial.printf("%d, %d\n", a, b);
#endif
    if (d != counter::Dir::None) xQueueSend(passQueue, &d, 0);
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(SENSOR_PERIOD_MS));
  }
}

void sensorsBegin() {
  Wire.begin(PIN_SDA, PIN_SCL);
  passQueue = xQueueCreate(64, sizeof(counter::Dir));
  // Nucleo 1 (el del loop de Arduino) con mas prioridad que el loop: la red y la web
  // corren en el loop y nunca bloquean la lectura de los sensores.
  xTaskCreatePinnedToCore(sensorTask, "sensores", 4096, nullptr, 2, nullptr, 1);
}

bool sensorsNextPass(counter::Dir& d) {
  return passQueue && xQueueReceive(passQueue, &d, 0) == pdTRUE;
}
