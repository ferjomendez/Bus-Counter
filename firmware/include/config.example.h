// Configuracion del contador de pasajeros.
//
// Copia este archivo como config.h (python manage.py flash lo hace solo la primera vez)
// y edita al menos BUS_ID y los datos del WiFi. config.h no se sube a git.
//
// La clave MQTT del bus y la IP del servidor se toman de secrets.h, que se genera
// solo al compilar a partir de .env y broker/certs/ca.crt.
#pragma once

#include "secrets.h"

// ------------------------------------------------------------------ identificacion

// ID del bus: debe estar en BUS_IDS del archivo .env del servidor (B01, B02, ...)
#define BUS_ID "B01"
#define FW_VERSION "0.1.0"

// ------------------------------------------------------------------ WiFi

// Red a la que se conecta el bus (internet o la red del computador servidor)
#define WIFI_SSID "nombre-de-la-red"
#define WIFI_PASSWORD "clave-de-la-red"

// Red propia del bus para ver la pagina local en 192.168.4.1 (SSID: Bus-<ID>)
// La clave debe tener al menos 8 caracteres.
#define AP_PASSWORD "buscounter"

// ------------------------------------------------------------------ servidor MQTT

#define MQTT_HOST SECRET_SERVER_IP   // IP del computador servidor (o escribela entre comillas)
#define MQTT_PORT 8883
#define MQTT_USER BUS_ID
#define MQTT_PASSWORD SECRET_MQTT_PASSWORD

// Hora por internet: necesaria para validar el certificado TLS
#define NTP_SERVER_1 "pool.ntp.org"
#define NTP_SERVER_2 "time.google.com"

// ADVERTENCIA: solo para pruebas de desarrollo. Con 1 el bus NO verifica el
// certificado del servidor y cualquiera en la red podria hacerse pasar por el.
// Dejalo siempre en 0 en uso real.
#define TLS_SKIP_VERIFY 0

// ------------------------------------------------------------------ sensores y calibracion
// Valores iniciales: ajustalos mirando la pagina /debug con el bus en la puerta real.

// Un sensor esta interrumpido cuando mide menos que (distancia base - MARGEN_MM)
#define MARGEN_MM 150
// Tiempo que una lectura debe mantenerse para aceptarla (evita rebotes)
#define DEBOUNCE_MS 40
// Una secuencia que dura mas que esto se descarta (alguien parado en la puerta)
#define SEQUENCE_TIMEOUT_MS 3000
// Lecturas por sensor para medir la distancia base de la puerta vacia al arrancar
#define CALIBRATION_SAMPLES 40
// Si la calibracion falla (pocas lecturas validas) se usa esta distancia base
#define FALLBACK_BASELINE_MM 1000
// Cada cuanto se leen los sensores (20 a 30 ms)
#define SENSOR_PERIOD_MS 25

// 1 = imprime "A_mm, B_mm" por Serial en cada lectura (util para calibrar)
#define SERIAL_STREAM 1

// ------------------------------------------------------------------ pines

#define PIN_SDA 21
#define PIN_SCL 22
#define PIN_XSHUT_A 25
#define PIN_XSHUT_B 26
#define ADDR_A 0x30
#define ADDR_B 0x31
