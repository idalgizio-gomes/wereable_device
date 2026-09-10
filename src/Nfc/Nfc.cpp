// Nfc.cpp - placeholder "falha segura": nao toca em UICR.NFCPINS/NFCT nem em P0.09/P0.10, so fixa a API ate a antena estar confirmada (ver Nfc.h)
#include "Nfc/Nfc.h"

namespace Nfc {

namespace {
bool s_ready = false;
} // namespace

bool begin() {
  // UICR.NFCPINS e irreversivel por software (retira P0.09/P0.10 do GPIO ate reprogramar o UICR) — nao ativar sem antena confirmada
  Serial.println("[NFC] preparacao apenas — antena nao confirmada no hardware, NFC nao inicializado (ver PROJECT_STATUS.md)");
  s_ready = false;
  return false;
}

void update() {
}

bool isReady() {
  return s_ready;
}

} // namespace Nfc
