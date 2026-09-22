// Gnss.h - modulo GPS/GNSS (CAM-M8Q, u-blox, I2C). Mesmo padrao de Imu.h:
// begin() uma vez, depois startTask() poe uma task FreeRTOS a fazer polling.
// Ainda nao ligado ao payload BLE (protocolo em aberto).

#ifndef GNSS_H_
#define GNSS_H_

#include <Arduino.h>

namespace Gnss {

  // Amostra (snapshot) da ultima posicao lida do modulo GNSS.
  struct Sample {
    uint32_t timestamp_ms; // Instante (millis()) em que a amostra foi lida.
    bool fix;               // true se o modulo tem uma posicao valida (fix).
    uint8_t siv;             // Satellites In View usados na solucao atual.
    int32_t latitude;       // graus * 10^7 (so valido se fix==true).
    int32_t longitude;      // graus * 10^7 (so valido se fix==true).
    int32_t altitude_mm;    // mm acima do elipsoide (so valido se fix==true).
  };

  // Tenta Wire e Wire1. Chamar uma vez, antes de startTask().
  bool begin();

  // Cria a task "gnss_task" (idempotente). Requer begin() com sucesso.
  bool startTask();

  // Pede paragem; a task sai e termina sozinha (nunca de fora).
  void stopTask();

  bool isTaskRunning();

  // Devolve false (sem alterar 'out') se a task nao estiver a correr.
  bool getLatestSample(Sample &out);

  // Barramento onde o modulo foi encontrado, ou "N/A".
  const char *busName();
}

#endif
