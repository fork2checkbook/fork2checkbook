#include "BillStorage.h"

#include <Arduino.h>
#include <SPIFFS.h>

bool BillStorage::begin() {
  // Never format automatically: an unreadable partition may contain the only
  // copy of a bearer artifact. Recovery or explicit discard is a human action.
  mounted_ = SPIFFS.begin(false);
  if (!mounted_) {
    Serial.println(
        "storage: SPIFFS mount failed; artifacts blocked; no format attempted");
    return false;
  }

  Serial.printf("storage: SPIFFS mounted total=%u used=%u\n",
                static_cast<unsigned>(SPIFFS.totalBytes()),
                static_cast<unsigned>(SPIFFS.usedBytes()));
  return true;
}
