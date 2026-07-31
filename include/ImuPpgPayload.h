// =============================================================================
// ImuPpgPayload
// -----------------------------------------------------------------------------
// Layout binario (packed, sem espacos de alinhamento do compilador) de uma
// amostra combinada de IMU + PPG, exatamente como e gravada num registo do
// QspiRingBuffer (produtor: storageTask em main.cpp) e depois lida de volta
// para ser cifrada e enviada por BLE (consumidor: Ble.cpp).
//
// Extraida para aqui (2026-07-31) porque esta struct estava definida de
// forma independente em main.cpp e em Ble.cpp — identica em layout (mesma
// ordem e tipos de campos, por isso o reinterpret_cast entre os dois lados
// sempre funcionou), mas com o mesmo campo com nomes diferentes em cada
// copia (`hr_x10` em main.cpp vs `hr` em Ble.cpp, guardando o mesmo valor:
// BPM ja arredondado, nao x10 — o nome "_x10" era enganador). O nome
// canonico aqui e `hr`. Qualquer alteracao a este layout tem de manter em
// sincronia o produtor (main.cpp) e o consumidor (Ble.cpp) e, do lado do
// bridge, FULL_PLAIN_STRUCT em bridge/ble_bridge.py — agora so ha um sitio
// no firmware a editar.
// =============================================================================

#ifndef IMU_PPG_PAYLOAD_H_
#define IMU_PPG_PAYLOAD_H_

#include <Arduino.h>

struct __attribute__((packed)) ImuPpgPayloadV1 {
  float ax;             // aceleracao no eixo X (g)
  float ay;             // aceleracao no eixo Y (g)
  float az;             // aceleracao no eixo Z (g)
  float gx;             // velocidade angular no eixo X (graus/s)
  float gy;             // velocidade angular no eixo Y (graus/s)
  float gz;             // velocidade angular no eixo Z (graus/s)
  uint32_t steps;       // contagem acumulada de passos, calculada pelo modulo Imu
  uint8_t ff;           // 1 se foi detetado um evento de queda livre (free-fall) nesta amostra
  uint8_t inact;        // 1 se o dispositivo esta atualmente considerado "inativo" (parado)
  int16_t spo2;         // ultima leitura de saturacao de oxigenio (%), 0 se nao houver leitura nova
  int16_t hr;           // ultima frequencia cardiaca em bpm, 0 se nao houver leitura nova
  uint8_t pacing_index; // indice 0-100 de "pacing"/curvas apertadas via giroscopio (ver Imu::Sample::pacing_index)
};

#endif
