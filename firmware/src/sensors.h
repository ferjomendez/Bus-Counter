// Sensores VL53L0X y deteccion de pasos (corre en su propia tarea).
#pragma once

#include "counter.h"

void sensorsBegin();
// Entrega el siguiente paso detectado, si hay uno
bool sensorsNextPass(counter::Dir& d);
