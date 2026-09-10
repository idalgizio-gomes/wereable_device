// Lora.cpp - radio Wio-SX1262. Ver Lora.h para o aviso de confianca do pinout (NSS/NRST sao hipotese por confirmar no esquematico).
#include "Lora/Lora.h"

#include <RadioLib.h>
#include <cstring>

namespace Lora {

namespace {

constexpr uint8_t kPinRfSwitch = A2;   // RF_SW — confianca alta
constexpr uint8_t kPinDio1     = D7;   // confianca alta
constexpr uint8_t kPinBusy     = D8;   // confianca alta
constexpr uint8_t kPinNss      = A3;   // HIPOTESE, confianca baixa (ver Lora.h)
// NRST nao ligado a nenhum pino (reset passivo no modulo); RADIOLIB_NC

constexpr float kFrequencyMHz = 868.0f; // banda ISM Europa/Portugal (EUA seria 915MHz)
constexpr float kBandwidthKHz = 125.0f;
constexpr uint8_t kSpreadingFactor = 9;   // 7=rapido/curto, 12=lento/longo alcance
constexpr uint8_t kCodingRate = 7;        // 4/7
constexpr uint8_t kSyncWord = 0x12;       // "privado" (nao o publico 0x34 do LoRaWAN)
constexpr int8_t kTxPowerDbm = 14;

SX1262 s_radio = new Module(kPinNss, kPinDio1, RADIOLIB_NC, kPinBusy);
bool s_ready = false;

} // namespace

bool begin() {
  // RF_SW e partilhado entre antena BLE e LoRa: so comutar DEPOIS de confirmar init, senao corta o BLE (bug real corrigido 2026-07-03)
  Serial.println("[LORA] a inicializar SX1262...");
  const int16_t state = s_radio.begin(kFrequencyMHz, kBandwidthKHz, kSpreadingFactor,
                                       kCodingRate, kSyncWord, kTxPowerDbm);

  if (state != RADIOLIB_ERR_NONE) {
    Serial.print("[LORA] falha ao inicializar, codigo=");
    Serial.println(state); // codigo negativo (ex. RADIOLIB_ERR_CHIP_NOT_FOUND) sugere pino NSS errado
    s_ready = false;
    return false;
  }

  pinMode(kPinRfSwitch, OUTPUT);
  digitalWrite(kPinRfSwitch, HIGH);

  Serial.println("[LORA] SX1262 inicializado com sucesso");
  s_ready = true;
  return true;
}

bool isReady() {
  return s_ready;
}

bool sendTest(const char *message) {
  if (!s_ready || message == nullptr) return false;

  Serial.print("[LORA] a enviar teste: ");
  Serial.println(message);

  const int16_t state = s_radio.transmit(reinterpret_cast<const uint8_t *>(message),
                                          strlen(message));

  digitalWrite(kPinRfSwitch, LOW); // devolve o RF switch ao BLE sempre, sucesso ou falha (bug corrigido: ficava preso em LoRa)

  if (state != RADIOLIB_ERR_NONE) {
    Serial.print("[LORA] falha no envio, codigo=");
    Serial.println(state);
    return false;
  }

  Serial.println("[LORA] envio concluido");
  return true;
}

} // namespace Lora
