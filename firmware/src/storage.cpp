#include "storage.h"

#include <Preferences.h>

static Preferences prefs;
static const char* NS = "counter";

void storageLoad(counter::Tally& t) {
  prefs.begin(NS, true);
  t.count = prefs.getInt("count", 0);
  t.in = prefs.getUInt("in", 0);
  t.out = prefs.getUInt("out", 0);
  t.seq = prefs.getUInt("seq", 0);
  prefs.end();
}

// Se guarda en cada paso: seq debe sobrevivir a un reinicio para que el servidor
// no descarte eventos nuevos como duplicados. NVS reparte el desgaste de la flash.
void storageSave(const counter::Tally& t) {
  prefs.begin(NS, false);
  prefs.putInt("count", t.count);
  prefs.putUInt("in", t.in);
  prefs.putUInt("out", t.out);
  prefs.putUInt("seq", t.seq);
  prefs.end();
}
