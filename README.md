# Contador de pasajeros para buses

Cuenta cuántas personas suben y bajan de cada bus, muestra en una página web cuántas van a
bordo y sugiere qué tipo de bus conviene enviar a cada recorrido.

## Encender el sistema

Copia una de estas líneas en **PowerShell** (menú Inicio, escribe "PowerShell") y presiona Enter.

**1. Descargar y encender**, en un computador que ya tiene git:

```powershell
git clone https://github.com/ferjomendez/Bus-Counter.git; cd Bus-Counter; powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Start
```

**2. Solo revisar e instalar lo que falta** (desde la carpeta del proyecto):

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Para ver qué está instalado sin instalar nada:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -CheckOnly
```

**3. Encender cuando ya está todo instalado** (desde la carpeta del proyecto):

```powershell
.\.venv\Scripts\python.exe manage.py start
```

**4. Computador limpio, sin git.** Descarga el instalador, instala lo necesario, deja el
proyecto en la carpeta `Bus-Counter` de tu usuario y lo enciende:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/ferjomendez/Bus-Counter/main/setup.ps1))) -Start
```

Al terminar se abre el navegador con el panel. Para apagar todo, vuelve a la ventana de
PowerShell y presiona **Ctrl+C**.

Opciones que se pueden agregar al final de las líneas 1, 2 y 4:

| Opción | Qué hace |
|---|---|
| `-Start` | Enciende el sistema al terminar la instalación. |
| `-Sim` | Junto con `-Start`, enciende también 6 buses de prueba. |
| `-CheckOnly` | Solo muestra qué está instalado. No instala ni cambia nada. |
| `-Firewall` | Permite que los buses y otros equipos de la red se conecten (pide permiso de administrador). Hazlo una vez en el computador servidor. |

El instalador solo instala lo que falta: Git, Python, Mosquitto y los paquetes del proyecto.
Puedes ejecutarlo las veces que quieras. Mosquitto pide permiso de administrador una sola vez.

## Ver el panel

- En el mismo computador: **http://localhost:8000**
- Desde un celular, tablet u otro computador conectado a la **misma red WiFi**: la dirección que
  aparece en PowerShell al encender, por ejemplo `http://192.168.1.84:8000`. Para que funcione,
  ejecuta una vez el instalador con `-Firewall`.

> **Importante:** el panel no pide usuario ni clave. Úsalo solo dentro de una red local de
> confianza (la del laboratorio o la de la casa), nunca expuesto a internet.

### Qué muestra

- **Arriba:** buses en servicio, pasajeros en total y recomendaciones activas. Toca las
  recomendaciones para ver la lista.
- **Pestañas celestes:** filtran por recorrido.
- **Cada tarjeta:** nombre del bus, si es Grande o Chico, un dibujo que se llena según la
  ocupación y cuántas personas lleva, por ejemplo "20 / 50". El estado se muestra con color y
  palabra:
  - verde, **Con espacio**
  - ámbar, **Casi lleno**
  - rojo, **Lleno**

  También indica si el bus está **Conectado** o **Sin conexión**. Cuando hay una sugerencia,
  aparece una franja negra, por ejemplo "Va con pocas personas. Conviene enviar un bus chico
  en este recorrido."
- **Al tocar un bus:** ocupación grande, gráfico de la última hora, día o semana, y lista de
  subidas y bajadas. En "Opciones avanzadas" se puede reiniciar o corregir el conteo.
- **Configuración:** capacidad de cada tipo de bus, nombre, tipo y recorrido de cada bus, y
  cuándo se hacen las recomendaciones.

## Cómo funciona

```
   BUS (uno por bus)                         COMPUTADOR SERVIDOR
 ┌──────────────────────┐                ┌──────────────────────────────────┐
 │ 2 sensores en puerta │                │  Mosquitto (broker MQTT)         │
 │        │             │   WiFi + TLS   │  puerto 8883, solo con clave     │
 │      ESP32  ─────────┼───────────────►│          │                       │
 │        │             │   MQTT 8883    │          ▼                       │
 │  página propia       │                │  Servidor web (FastAPI + SQLite) │
 │  192.168.4.1         │                │  puerto 8000                     │
 └──────────────────────┘                └──────────┬───────────────────────┘
                                                    │ página web en vivo
                                                    ▼
                                    Navegador: computador, tablet o celular
```

1. Cada bus tiene un **ESP32** con dos sensores de distancia en la puerta. Según qué sensor se
   interrumpe primero, sabe si la persona subió o bajó.
2. El ESP32 envía cada subida y bajada al servidor por **MQTT** cifrado con TLS. Cada bus tiene
   su propia clave y solo puede escribir en sus propios datos.
3. El **servidor** guarda todo, calcula la ocupación y las recomendaciones, y actualiza el panel
   al instante.
4. Si un bus pierde la señal, guarda lo que cuenta y lo envía al reconectarse.

### Las recomendaciones

- **Bus grande con pocas personas:** si durante los últimos 10 minutos nunca llevó más de 20
  personas, se sugiere enviar uno chico.
- **Bus chico casi lleno:** si durante los últimos 10 minutos estuvo siempre sobre el 90 % de
  su capacidad, se sugiere enviar uno grande.

Los números se cambian en Configuración. Un bus sin conexión, o con datos incompletos, no
recibe recomendaciones. Tampoco las recibe hasta tener datos de al menos la mitad de ese
período, así que tras encender hay que esperar unos 5 minutos.

## Probar sin buses reales

```powershell
.\.venv\Scripts\python.exe manage.py start --sim
```

Enciende 6 buses de prueba con distintos tipos y recorridos. Uno grande va casi vacío y uno
chico va lleno, para ver las recomendaciones. También se pueden provocar fallas mientras el
sistema está encendido, desde otra ventana de PowerShell:

```powershell
.\.venv\Scripts\python.exe manage.py sim --offline B04 --seq-gap B05 --suspicious B06
```

- `--offline` corta la señal de un bus.
- `--seq-gap` hace que se pierdan datos.
- `--suspicious` sube más gente de la que cabe.

Escribe `manage.py sim -h` para ver todas las opciones. Si el simulador ya está corriendo
con `start --sim`, usa una sola de las dos formas: cada bus simulado solo puede estar
conectado una vez.

## Agregar un bus real

1. **Darle un nombre en el sistema.** Abre el archivo `.env` de la carpeta del proyecto y agrega
   el ID del bus a `BUS_IDS`, por ejemplo `BUS_IDS=B01,B02,B03,B04,B05,B06,B07`. Apaga y vuelve
   a encender el sistema: la clave del bus se crea sola.
2. **Abrir el firewall** del computador servidor, una sola vez:
   `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Firewall`.
3. **Armar el equipo:** ESP32, dos sensores GY-530 y un regulador a 5 V, porque el bus entrega
   12 o 24 V. El cableado y el montaje están en [firmware/README.md](firmware/README.md).
4. **Grabar el ESP32:** conéctalo por USB y ejecuta
   `.\.venv\Scripts\python.exe manage.py flash`. La primera vez crea
   `firmware/include/config.h`. Edita en él el ID del bus y el nombre y clave del WiFi, y vuelve
   a ejecutar el comando.
5. **Calibrar** los sensores en la puerta (siguiente sección).
6. El bus aparece solo en el panel como "Bus B07", de tipo Grande. Cambia su nombre, tipo y
   recorrido en **Configuración**.

## Calibrar los sensores

1. Con el equipo encendido en la puerta, conecta el celular a la red WiFi `Bus-<ID>`, por
   ejemplo `Bus-B07`. La clave está en `config.h`, en `AP_PASSWORD`.
2. Abre **http://192.168.4.1/debug**. Se ven en vivo las distancias de los sensores A y B, la
   distancia de la puerta vacía y si cada sensor está "libre" o "INTERRUMPIDO".
3. Pasa varias veces subiendo y bajando. Al subir debe interrumpirse primero A y después B.
4. Si cuenta sin que pase nadie o no detecta a algunas personas, ajusta `MARGEN_MM` y los
   tiempos en `config.h` y vuelve a grabar. El detalle está en
   [firmware/README.md](firmware/README.md#calibrar-los-sensores).

La página principal del bus, **http://192.168.4.1**, muestra el conteo en grande y si el bus
está conectado al servidor.

## Si algo falla

| Qué pasa | Qué hacer |
|---|---|
| PowerShell dice que no puede ejecutar scripts | Usa la línea completa con `powershell -ExecutionPolicy Bypass -File .\setup.ps1`. No hace falta cambiar ninguna configuración de Windows. |
| El instalador dice que no encuentra **winget** | Instala o actualiza "App Installer" desde Microsoft Store, cierra PowerShell y vuelve a intentar. |
| "El puerto 8000 (u 8883) ya está en uso" | El sistema ya está encendido en otra ventana. Ciérrala con Ctrl+C o usa esa. |
| El panel no abre en otro equipo | Revisa que esté en la misma red WiFi, que uses la dirección que muestra PowerShell (no `localhost`) y que hayas ejecutado el instalador con `-Firewall`. |
| Aviso negro "Se perdió la conexión con el servidor" | El panel reintenta solo. Si no vuelve, revisa que la ventana de PowerShell siga abierta y sin errores. |
| Aviso "El servidor no está recibiendo datos de los buses" | El broker se detuvo. Apaga con Ctrl+C y vuelve a encender con `manage.py start`. |
| Un bus aparece **Sin conexión** | Revisa que el equipo tenga energía y WiFi. En su página `http://192.168.4.1` se ve el estado de la conexión. |
| El ESP32 no se conecta y el monitor serie dice "aún no hay hora de internet" | La red WiFi del bus necesita salida a internet para obtener la hora, que se usa para validar la conexión segura. Usa una red con internet. |
| Cambió la IP del computador servidor | `manage.py start` renueva solo el certificado. Vuelve a grabar los ESP32 con `manage.py flash` para que apunten a la nueva IP; la CA no cambia. Si quieres fijar la IP, ponla en `ADVERTISED_IP` en `.env`. |
| Un bus muestra "Faltan datos de este bus" | Se perdieron algunos mensajes. Se corrige solo con la siguiente actualización completa del bus, cada 10 segundos. |
| "El conteo supera lo que cabe en el bus" | Probablemente un sensor contó de más. Corrige el número en el detalle del bus, en "Opciones avanzadas", y revisa la calibración. |
| El conteo quedó mal al terminar el recorrido | En el detalle del bus, en "Opciones avanzadas", usa "Reiniciar conteo a 0" o "Fijar conteo". |

## Comandos

Todos se ejecutan desde la carpeta del proyecto con `.\.venv\Scripts\python.exe manage.py <comando>`.
En Linux y macOS usa `.venv/bin/python manage.py <comando>`; la primera vez,
`python3 manage.py setup`.

| Comando | Qué hace |
|---|---|
| `setup` | Instala lo que falte. En Windows usa `setup.ps1`. |
| `start` | Enciende broker y servidor, y abre el navegador. `--sim` agrega buses de prueba. |
| `broker` | Enciende solo el broker MQTT. |
| `server` | Enciende solo el servidor web. |
| `sim` | Enciende solo el simulador (`sim -h` muestra las opciones). |
| `certs` | Crea los certificados si faltan. `--force` renueva el del servidor y conserva la CA. `--ca` crea una CA nueva (obliga a regrabar los ESP32). |
| `flash` | Compila y graba el firmware en el ESP32. Opciones: `--port COM5`, `--monitor`, `--build-only`. |
| `test` | Corre las pruebas. `--firmware` corre las del firmware (necesita g++). |

El primer `start` crea solo el archivo `.env` con claves al azar, los certificados y los
usuarios del broker. Un clon nuevo funciona sin editar nada.

## Carpetas

```
broker/      configuración de Mosquitto, permisos (acl.txt) y certificados (certs/, fuera de git)
server/      servidor web: app/ (Python), static/ (panel), tests/
simulator/   buses de prueba
firmware/    programa del ESP32 (PlatformIO)
setup.ps1    instalador para Windows
manage.py    comandos para encender, probar y grabar
.env         claves y ajustes locales (se crea solo, fuera de git)
```

Los certificados, claves, `.env`, `passwd`, `config.h` y `secrets.h` nunca se suben a git.
