# Firmware del contador (ESP32)

Cada bus lleva un ESP32 con dos sensores de distancia VL53L0X (placas GY-530) en la puerta.
El ESP32 cuenta quién sube y quién baja, muestra el conteo en su propia página web y lo
envía al servidor por MQTT con TLS.

## Materiales

- 1 ESP32 (placa de desarrollo tipo "ESP32 DevKit").
- 2 placas GY-530 (sensor VL53L0X).
- **Regulador a 5 V para el bus.** El bus entrega 12 o 24 V y eso quema el ESP32. Usa un
  convertidor reductor (step-down / "buck") de 12-24 V a 5 V de al menos 1 A, por ejemplo uno
  de los cargadores USB para auto. Conecta su salida de 5 V al pin `5V` (o `VIN`) del ESP32, o
  directo por el cable USB.
- Un panel claro o reflectante para pegar en el lado opuesto de la puerta.

## Cableado

Ambos GY-530 comparten los mismos cables de datos:

| GY-530 | ESP32 |
|---|---|
| VIN | 3V3 |
| GND | GND |
| SDA | GPIO 21 |
| SCL | GPIO 22 |
| XSHUT del sensor A | GPIO 25 |
| XSHUT del sensor B | GPIO 26 |
| GPIO1 | sin conectar |

Al encender, el ESP32 apaga ambos sensores con XSHUT y los enciende de a uno para darles
direcciones distintas (A = 0x30, B = 0x31), porque de fábrica todos usan 0x29.

## Montaje

```
        lado de afuera del bus
   ─────────────────────────────────
   [A]  ──────── haz ────────►  |  panel claro
    │ 10 a 15 cm                 |  o reflectante
   [B]  ──────── haz ────────►  |  (lado opuesto,
   ─────────────────────────────────  ~1 m)
        lado de adentro del bus
```

- Ambos sensores van en el **mismo lado** del marco, apuntando al lado opuesto.
- Separados 10 a 15 cm en el sentido en que pasa la gente: **A hacia afuera, B hacia adentro**.
- Quien sube corta primero A y luego B. Quien baja corta primero B y luego A.
- Los sensores usan el modo de largo alcance (la puerta mide cerca de 1 m).

## Configurar y grabar

1. Conecta el ESP32 por USB. En Windows se necesita el driver CP210x o CH340, según la placa.
2. En la carpeta del proyecto ejecuta:
   ```powershell
   .\.venv\Scripts\python.exe manage.py flash
   ```
   La primera vez crea `firmware/include/config.h` y se detiene.
3. Abre `firmware/include/config.h` y cambia:
   - `BUS_ID`: el ID del bus, por ejemplo `"B01"`. Debe estar en `BUS_IDS` del archivo `.env`.
   - `WIFI_SSID` y `WIFI_PASSWORD`: la red a la que se conecta el bus.
4. Vuelve a ejecutar `manage.py flash`. Compila, genera `secrets.h` y graba el ESP32.
   Con `--monitor` se queda mostrando los mensajes del equipo y con `--port COM5` eliges el puerto.

`secrets.h` se genera solo con el certificado `broker/certs/ca.crt`, la clave del bus y la
IP del servidor, tomadas de `.env`. No se edita a mano ni se sube a git.

Si después se regenera la CA (`manage.py certs --ca`), hay que volver a grabar todos los buses.
Si solo cambia la IP del servidor, basta con volver a grabar (la CA se conserva).

## La red WiFi

El ESP32 hace dos cosas a la vez:

- **Crea su propia red** `Bus-<ID>` (clave `AP_PASSWORD` en `config.h`). Conectándose a ella con
  un celular se abre `http://192.168.4.1` y se ve el conteo en grande.
- **Se conecta como cliente** a la red de `WIFI_SSID` para llegar al servidor.

Nota: el ESP32 usa un solo canal de radio. Cuando se conecta a la red cliente, su red propia
cambia a ese canal y los celulares conectados pueden desconectarse un momento.

## Hora y TLS

Para validar el certificado del servidor, el ESP32 necesita la fecha correcta, que obtiene por
internet (NTP) antes de conectarse. Si la red no tiene salida a internet, el monitor serie
muestra un aviso cada 10 s y el equipo sigue intentando. Mientras tanto cuenta igual, guarda
los eventos y los envía al conectarse.

`TLS_SKIP_VERIFY` en `config.h` desactiva la verificación del certificado. Úsalo **solo para
pruebas**: con esa opción cualquiera en la red podría hacerse pasar por el servidor.

## Calibrar los sensores

Los valores iniciales de `config.h` no están calibrados. Hay que ajustarlos en la puerta real:

1. Con el ESP32 encendido en la puerta, conéctate a la red `Bus-<ID>` y abre
   `http://192.168.4.1/debug`.
2. La página muestra en vivo la distancia de A y B, la distancia base (puerta vacía), el
   umbral y el estado de la puerta. También se ve por el monitor serie como `A_mm, B_mm`.
3. Pasa varias veces en ambos sentidos y revisa:
   - Con la puerta vacía, ambos sensores deben decir "libre" y medir parecido a la base.
   - Al pasar, cada sensor debe quedar "INTERRUMPIDO", primero A y luego B al subir.
4. Ajusta en `config.h` y vuelve a grabar:
   - `MARGEN_MM`: cuánto tiene que bajar la distancia para contar como interrumpido.
     Súbelo si cuenta sin que pase nadie y bájalo si no detecta a algunas personas.
   - `DEBOUNCE_MS`: súbelo si hay conteos dobles por lecturas que saltan.
   - `SEQUENCE_TIMEOUT_MS`: tiempo máximo para cruzar la puerta.
   - `CALIBRATION_SAMPLES`: lecturas para medir la puerta vacía al arrancar.
5. El botón "Volver a medir la puerta vacía" repite la calibración sin reiniciar.
   La calibración automática se hace al encender, así que la puerta debe estar despejada en
   ese momento.

## Qué guarda el equipo

- El conteo, las subidas, las bajadas y el número de secuencia (`seq`) quedan en la memoria no
  volátil (NVS). Un corte de luz no los borra.
- Sin conexión, los eventos se guardan en una cola de 500 en RAM y se envían en orden al
  reconectar. Si la cola se llena se descartan los más antiguos y el servidor se resincroniza
  con el siguiente conteo completo. Si el equipo se reinicia sin conexión, los eventos en cola
  se pierden, pero el conteo no, porque está en NVS.

## Tests de la lógica

La lógica de conteo (`lib/counter`) no depende del hardware y se prueba en el computador:

```powershell
.\.venv\Scripts\python.exe manage.py test --firmware
```

Necesita un compilador C++ (g++) instalado en el computador.
