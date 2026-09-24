#include "SharedI2cBus/SharedI2cBus.h"

#include <rtos.h>

namespace {
SemaphoreHandle_t s_mutex = nullptr;
}

namespace SharedI2cBus {

void init() {
  if (s_mutex == nullptr) {
    s_mutex = xSemaphoreCreateRecursiveMutex();
  }
}

void lock() {
  if (s_mutex == nullptr) init(); // rede de seguranca se algum chamador esquecer o init() em setup()
  xSemaphoreTakeRecursive(s_mutex, portMAX_DELAY);
}

void unlock() {
  xSemaphoreGiveRecursive(s_mutex);
}

}
