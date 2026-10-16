// Pagina local del bus (192.168.4.1): conteo grande y pagina /debug para calibrar.
#include "web.h"

#include <WebServer.h>

#include "app.h"
#include "net.h"

static WebServer server(80);

static const char PAGE_HOME[] PROGMEM = R"HTML(<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Contador del bus</title>
<style>
body{margin:0;font-family:system-ui,sans-serif;background:#fff;color:#000;text-align:center}
header{background:#000;color:#fff;padding:14px;border-bottom:8px solid #4dafd6;font-size:1.2rem}
#count{font-size:38vw;font-weight:800;line-height:1;margin:4vh 0 0}
@media(min-width:700px){#count{font-size:16rem}}
.row{display:flex;justify-content:center;gap:2rem;font-size:1.4rem;margin:1rem}
.row b{display:block;font-size:2.4rem}
#srv{display:inline-block;margin:1rem;padding:.6rem 1rem;border-radius:6px;font-weight:700}
.ok{background:#e3f4ea;color:#14663a}.bad{background:#fdeced;color:#a11d24}
a{color:#1a6e92}
</style></head><body>
<header>Bus <span id="bus"></span></header>
<p id="count">–</p><p>personas a bordo</p>
<div class="row"><div>Subieron<b id="in">–</b></div><div>Bajaron<b id="out">–</b></div></div>
<p id="srv">…</p>
<p><a href="/debug">Calibrar sensores</a></p>
<script>
async function tick(){try{const r=await fetch('/api/count');const d=await r.json();
bus.textContent=d.bus;count.textContent=d.count;document.getElementById('in').textContent=d.in;
out.textContent=d.out;srv.textContent=d.server_text;srv.className=d.server_ok?'ok':'bad';}
catch(e){srv.textContent='Sin conexión con el equipo';srv.className='bad';}}
tick();setInterval(tick,1000);
</script></body></html>)HTML";

static const char PAGE_DEBUG[] PROGMEM = R"HTML(<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Calibrar sensores</title>
<style>
body{margin:0;font-family:system-ui,sans-serif;color:#000}
header{background:#000;color:#fff;padding:14px;border-bottom:8px solid #4dafd6}
main{padding:16px;max-width:720px;margin:auto}
.s{border:1px solid #cfd8de;border-radius:6px;padding:14px;margin:12px 0}
.s h2{margin:0 0 6px}.mm{font-size:3rem;font-weight:800}
.bar{height:22px;background:#eef2f4;border-radius:4px;position:relative;overflow:hidden}
.bar i{position:absolute;left:0;top:0;bottom:0;background:#4dafd6}
.bar u{position:absolute;top:0;bottom:0;width:3px;background:#000}
.on{color:#a11d24;font-weight:700}
button{font-size:1.1rem;padding:12px 18px;border:2px solid #000;background:#fff;border-radius:6px}
code{background:#eef2f4;padding:2px 6px}
</style></head><body>
<header>Calibración de sensores · Bus <span id="bus"></span></header>
<main>
<p>Pasa por la puerta y mira cómo cambian las distancias. Una línea negra marca el umbral:
bajo ella el sensor queda <b>interrumpido</b>. Ajusta <code>MARGEN_MM</code> y los tiempos en
<code>config.h</code> si hace falta.</p>
<div class="s"><h2>Sensor A (afuera)</h2><div class="mm" id="a">–</div>
<div class="bar"><i id="ab"></i><u id="at"></u></div><p>Base: <span id="ba"></span> mm · <span id="sa"></span></p></div>
<div class="s"><h2>Sensor B (adentro)</h2><div class="mm" id="b">–</div>
<div class="bar"><i id="bb"></i><u id="bt"></u></div><p>Base: <span id="bbase"></span> mm · <span id="sb"></span></p></div>
<p>Margen: <b id="m"></b> mm · Estado de la puerta: <b id="st"></b></p>
<p>Conteo: <b id="c"></b> (subieron <span id="i"></span>, bajaron <span id="o"></span>)</p>
<button id="cal">Volver a medir la puerta vacía</button>
<p id="msg"></p>
</main>
<script>
const MAX=2000;function pct(v){return Math.max(0,Math.min(100,100*v/MAX))+'%'}
function show(k,v,base,blocked,margin){const el=document.getElementById(k);el.textContent=v<0?'inválida':v+' mm';
document.getElementById(k+'b').style.width=v<0?'0':pct(v);document.getElementById(k+'t').style.left=pct(base-margin);
const s=document.getElementById('s'+k);s.textContent=blocked?'INTERRUMPIDO':'libre';s.className=blocked?'on':'';}
async function tick(){try{const d=await (await fetch('/api/debug')).json();
bus.textContent=d.bus;show('a',d.a,d.base_a,d.blocked_a,d.margin);show('b',d.b,d.base_b,d.blocked_b,d.margin);
ba.textContent=d.base_a;bbase.textContent=d.base_b;m.textContent=d.margin;st.textContent=d.calibrating?'midiendo puerta vacía…':d.state;
c.textContent=d.count;i.textContent=d.in;o.textContent=d.out;}catch(e){}}
cal.onclick=async()=>{msg.textContent='Deja la puerta despejada…';await fetch('/api/calibrate',{method:'POST'});setTimeout(()=>msg.textContent='',4000)};
tick();setInterval(tick,250);
</script></body></html>)HTML";

static void sendJson(const String& body) {
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", body);
}

static void handleCount() {
  String s;
  s.reserve(220);
  s += "{\"bus\":\"" BUS_ID "\",\"count\":";
  s += app.tally.count;
  s += ",\"in\":";
  s += app.tally.in;
  s += ",\"out\":";
  s += app.tally.out;
  s += ",\"seq\":";
  s += app.tally.seq;
  s += ",\"pending\":";
  s += (unsigned)app.queue.size();
  s += ",\"server_ok\":";
  s += netMqttConnected() ? "true" : "false";
  s += ",\"server_text\":\"";
  s += netStatusText();
  s += "\"}";
  sendJson(s);
}

static void handleDebug() {
  const SensorSnapshot& d = app.sensors;
  String s;
  s.reserve(300);
  s += "{\"bus\":\"" BUS_ID "\",\"a\":";
  s += d.a_mm;
  s += ",\"b\":";
  s += d.b_mm;
  s += ",\"base_a\":";
  s += d.base_a;
  s += ",\"base_b\":";
  s += d.base_b;
  s += ",\"margin\":";
  s += MARGEN_MM;
  s += ",\"blocked_a\":";
  s += d.blocked_a ? "true" : "false";
  s += ",\"blocked_b\":";
  s += d.blocked_b ? "true" : "false";
  s += ",\"state\":\"";
  s += counter::stateName(static_cast<counter::State>(d.state));
  s += "\",\"calibrating\":";
  s += d.calibrating ? "true" : "false";
  s += ",\"count\":";
  s += app.tally.count;
  s += ",\"in\":";
  s += app.tally.in;
  s += ",\"out\":";
  s += app.tally.out;
  s += "}";
  sendJson(s);
}

void webBegin() {
  server.on("/", HTTP_GET, [] { server.send_P(200, "text/html; charset=utf-8", PAGE_HOME); });
  server.on("/debug", HTTP_GET, [] { server.send_P(200, "text/html; charset=utf-8", PAGE_DEBUG); });
  server.on("/api/count", HTTP_GET, handleCount);
  server.on("/api/debug", HTTP_GET, handleDebug);
  server.on("/api/calibrate", HTTP_POST, [] {
    app.sensors.recalibrate = true;
    sendJson("{\"ok\":true}");
  });
  server.onNotFound([] { server.send(404, "text/plain; charset=utf-8", "No existe"); });
  server.begin();
}

void webLoop() { server.handleClient(); }
