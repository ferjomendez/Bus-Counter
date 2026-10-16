// Conteo persistente en memoria no volatil (NVS), para no perderlo al reiniciar.
#pragma once

#include "counter.h"

void storageLoad(counter::Tally& t);
void storageSave(const counter::Tally& t);
