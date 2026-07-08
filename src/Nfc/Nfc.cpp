// ============================================================================
// Nfc.cpp
// ----------------------------------------------------------------------------
// Implementacao do modulo NFC (ver Nfc.h para o aviso completo sobre o
// estado de preparacao e as perguntas em aberto — antena nao confirmada).
//
// Esta versao e deliberadamente um placeholder "falha segura": nao inclui
// nenhuma biblioteca de NFC, nao toca em UICR.NFCPINS nem em registos do
// periferico NFCT, e nao configura pinMode nenhum em P0.09/P0.10. Existe
// para fixar a API (begin/update/isReady) e o ponto de integracao em
// main.cpp, exatamente como foi feito para o LoRa antes de o pinout
// exato estar confirmado — mas aqui um passo atras, porque nem a
// existencia da antena esta confirmada ainda (ver PROJECT_STATUS.md,
// secção "NFC", para as perguntas registadas ao utilizador).
// ============================================================================

#include "Nfc/Nfc.h"

namespace Nfc {

namespace {
bool s_ready = false;
} // namespace

bool begin() {
  // Nao ativar UICR.NFCPINS nem configurar o periferico NFCT enquanto a
  // antena nao estiver confirmada no esquematico da placa (ver aviso em
  // Nfc.h) — essa alteracao nao e reversivel por software e retira
  // P0.09/P0.10 do modo GPIO permanentemente ate o UICR ser reprogramado.
  Serial.println("[NFC] preparacao apenas — antena nao confirmada no hardware, NFC nao inicializado (ver PROJECT_STATUS.md)");
  s_ready = false;
  return false;
}

void update() {
  // Nada a fazer enquanto begin() nao inicializar hardware real.
}

bool isReady() {
  return s_ready;
}

} // namespace Nfc
