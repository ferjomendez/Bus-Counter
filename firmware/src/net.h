// WiFi (AP + STA), hora NTP y MQTT por TLS.
#pragma once

void netBegin();
void netLoop();
bool netMqttConnected();
// Estado en palabras para la pagina local
const char* netStatusText();
