// WiFi AP + STA, hora NTP y MQTT por TLS (QoS 1, Last Will, reconexion con espera creciente).
#include "net.h"

#include <ArduinoJson.h>
#include <WiFi.h>
#include <espMqttClient.h>
#include <time.h>

#include "app.h"
#include "storage.h"

// espMqttClient publica con QoS 1 y reintenta hasta recibir el PUBACK (PubSubClient
// solo publica con QoS 0). Sin tarea interna: todo corre en loop(), sin carreras.
static espMqttClientSecure mqtt(espMqttClientTypes::UseInternalTask::NO);

static const char* T_EVENT = "buses/" BUS_ID "/event";
static const char* T_COUNT = "buses/" BUS_ID "/count";
static const char* T_STATUS = "buses/" BUS_ID "/status";
static const char* T_CMD = "buses/" BUS_ID "/cmd";
static const char* WILL = "{\"state\":\"offline\"}";

static const uint32_t COUNT_EVERY_MS = 10000;
static const uint32_t STATUS_EVERY_MS = 30000;
static const uint32_t MAX_BACKOFF_MS = 60000;
static const int EVENTS_PER_LOOP = 10;

static bool mqttUp = false;
static bool wifiUp = false;
static bool timeOk = false;
static uint32_t ntpStartedAt = 0;
static bool ntpStarted = false;
static uint32_t lastTimeWarn = 0;
static uint32_t nextWifiTry = 0;
static uint32_t wifiBackoff = 1000;
static uint32_t nextMqttTry = 0;
static uint32_t mqttBackoff = 1000;
static uint32_t lastCount = 0;
static uint32_t lastStatus = 0;
static const char* statusText = "Iniciando";

uint32_t unixTime() {
  time_t t = time(nullptr);
  return t > 1700000000 ? static_cast<uint32_t>(t) : 0;
}

static bool due(uint32_t at) { return static_cast<int32_t>(millis() - at) >= 0; }

static void publishStatus() {
  char buf[128];
  snprintf(buf, sizeof(buf), "{\"state\":\"online\",\"rssi\":%d,\"fw\":\"%s\",\"up_ms\":%lu}",
           WiFi.RSSI(), FW_VERSION, static_cast<unsigned long>(millis()));
  if (mqtt.publish(T_STATUS, 1, true, buf)) lastStatus = millis();
}

static void publishCount() {
  char buf[128];
  snprintf(buf, sizeof(buf), "{\"seq\":%lu,\"count\":%ld,\"in\":%lu,\"out\":%lu,\"ts\":%lu}",
           static_cast<unsigned long>(app.tally.seq), static_cast<long>(app.tally.count),
           static_cast<unsigned long>(app.tally.in), static_cast<unsigned long>(app.tally.out),
           static_cast<unsigned long>(unixTime()));
  if (mqtt.publish(T_COUNT, 1, true, buf)) {
    lastCount = millis();
    app.count_dirty = false;
  }
}

static void flushEvents() {
  for (int i = 0; i < EVENTS_PER_LOOP && !app.queue.empty(); ++i) {
    const counter::Event& e = app.queue.front();
    char buf[112];
    snprintf(buf, sizeof(buf), "{\"seq\":%lu,\"dir\":\"%s\",\"ts\":%lu,\"up_ms\":%lu}",
             static_cast<unsigned long>(e.seq), e.dir == counter::Dir::In ? "in" : "out",
             static_cast<unsigned long>(e.ts), static_cast<unsigned long>(e.up_ms));
    if (!mqtt.publish(T_EVENT, 1, false, buf)) break;  // cola de salida llena: sigue despues
    app.queue.pop();
  }
}

// ------------------------------------------------------------------ callbacks MQTT

static void onConnect(bool sessionPresent) {
  mqttUp = true;
  mqttBackoff = 1000;
  Serial.printf("[mqtt] Conectado a %s:%d (sesion previa: %s)\n", MQTT_HOST, MQTT_PORT, sessionPresent ? "si" : "no");
  mqtt.subscribe(T_CMD, 1);
  publishStatus();
  app.count_dirty = true;
}

static void onDisconnect(espMqttClientTypes::DisconnectReason reason) {
  if (mqttUp) Serial.println("[mqtt] Se perdio la conexion con el servidor");
  mqttUp = false;
  Serial.printf("[mqtt] Sin conexion (motivo %u). Reintento en %lu s\n", static_cast<unsigned>(reason),
                static_cast<unsigned long>(mqttBackoff / 1000));
  nextMqttTry = millis() + mqttBackoff;
  mqttBackoff = min(mqttBackoff * 2, MAX_BACKOFF_MS);
}

static void onMessage(const espMqttClientTypes::MessageProperties&, const char* topic, const uint8_t* payload,
                      size_t len, size_t index, size_t total) {
  if (index != 0 || len != total || total > 200) return;  // los comandos son mensajes cortos
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) {
    Serial.printf("[cmd] Comando invalido en %s\n", topic);
    return;
  }
  const char* action = doc["action"] | "";
  if (strcmp(action, "reset") == 0) {
    app.tally.reset();
  } else if (strcmp(action, "set") == 0 && doc["count"].is<int>() && doc["count"].as<int>() >= 0) {
    app.tally.set(doc["count"].as<int>());
  } else {
    Serial.printf("[cmd] Comando desconocido: %.*s\n", static_cast<int>(len), payload);
    return;
  }
  storageSave(app.tally);
  app.count_dirty = true;
  lastCount = 0;  // publica el nuevo conteo de inmediato
  Serial.printf("[cmd] %s -> conteo %ld\n", action, static_cast<long>(app.tally.count));
}

// ------------------------------------------------------------------ API del modulo

void netBegin() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP("Bus-" BUS_ID, AP_PASSWORD);
  WiFi.setAutoReconnect(false);  // la reconexion la maneja netLoop con espera creciente
  Serial.printf("[wifi] Red propia: Bus-%s (pagina en http://%s)\n", BUS_ID, WiFi.softAPIP().toString().c_str());

#if TLS_SKIP_VERIFY
  Serial.println("[mqtt] ADVERTENCIA: TLS_SKIP_VERIFY=1, no se verifica el certificado del servidor");
  mqtt.setInsecure();
#else
  mqtt.setCACert(CA_CERT);
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCredentials(MQTT_USER, MQTT_PASSWORD);
  mqtt.setClientId(BUS_ID);
  mqtt.setCleanSession(false);
  mqtt.setKeepAlive(15);
  mqtt.setWill(T_STATUS, 1, true, WILL);
  mqtt.onConnect(onConnect);
  mqtt.onDisconnect(onDisconnect);
  mqtt.onMessage(onMessage);
}

void netLoop() {
  // WiFi
  if (WiFi.status() != WL_CONNECTED) {
    if (wifiUp) Serial.println("[wifi] Se perdio la red");
    wifiUp = false;
    statusText = "Buscando la red WiFi";
    if (due(nextWifiTry)) {
      Serial.printf("[wifi] Conectando a %s...\n", WIFI_SSID);
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      nextWifiTry = millis() + wifiBackoff;
      wifiBackoff = min(wifiBackoff * 2, MAX_BACKOFF_MS);
    }
    mqtt.loop();
    return;
  }
  if (!wifiUp) {
    wifiUp = true;
    wifiBackoff = 1000;
    Serial.printf("[wifi] Conectado. IP %s, senal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  }

  // Hora: la validacion del certificado TLS necesita la fecha correcta
  if (!timeOk) {
    if (!ntpStarted || millis() - ntpStartedAt > 30000) {
      configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);
      ntpStarted = true;
      ntpStartedAt = millis();
    }
    if (unixTime()) {
      timeOk = true;
      Serial.printf("[hora] Hora de internet OK (%lu)\n", static_cast<unsigned long>(unixTime()));
    } else {
      if (millis() - lastTimeWarn > 10000) {
        lastTimeWarn = millis();
        Serial.println("[hora] AVISO: aun no hay hora de internet. Sin ella no se puede validar el "
                       "certificado TLS; se sigue intentando. Revisa que la red tenga salida a internet.");
      }
#if !TLS_SKIP_VERIFY
      statusText = "Esperando la hora de internet";
      mqtt.loop();
      return;
#endif
    }
  }

  // MQTT
  if (!mqttUp) {
    statusText = "Conectando al servidor";
    if (mqtt.disconnected() && due(nextMqttTry)) {
      Serial.printf("[mqtt] Conectando a %s:%d...\n", MQTT_HOST, MQTT_PORT);
      nextMqttTry = millis() + 2000;  // onDisconnect fija la espera real si falla
      mqtt.connect();
    }
  } else {
    statusText = "Conectado al servidor";
    flushEvents();
    const uint32_t sinceCount = millis() - lastCount;
    if ((app.count_dirty && app.queue.empty() && sinceCount >= 1000) || sinceCount >= COUNT_EVERY_MS) {
      publishCount();
    }
    if (millis() - lastStatus >= STATUS_EVERY_MS) publishStatus();
  }
  mqtt.loop();
}

bool netMqttConnected() { return mqttUp; }

const char* netStatusText() { return statusText; }
