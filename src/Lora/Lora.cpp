// ============================================================================
// Lora.cpp
// ----------------------------------------------------------------------------
// Implementacao do modulo LoRa (ver Lora.h para a visao geral, a API publica
// e — MUITO IMPORTANTE — o aviso completo sobre o nivel de confianca de cada
// pino: NSS/NRST sao ainda uma HIPOTESE por confirmar no esquematico).
//
// Este ficheiro contem:
//   - O mapeamento de pinos entre o MCU (XIAO nRF52840 Sense Plus) e o
//     radio Wio-SX1262, centralizado num unico sitio para ser facil de
//     corrigir quando a zona NRST/SPI_NSS do esquematico for confirmada.
//   - Os parametros de radio LoRa (frequencia, largura de banda, spreading
//     factor, coding rate, sync word, potencia) escolhidos para a banda
//     ISM europeia de 868 MHz (Portugal) em modo ponto-a-ponto simples.
//   - begin(): inicializacao "falha segura" — se o radio nao responder
//     (ex.: pino NSS errado), regista o codigo de erro do RadioLib e
//     devolve false SEM travar nem atrasar o resto do arranque; nada no
//     firmware depende do LoRa ter inicializado (validado em hardware
//     real a 2026-07-03: NSS=AD3 falhou com RADIOLIB_ERR_CHIP_NOT_FOUND
//     e o resto do sistema — BLE/IMU/PPG/storage — arrancou normalmente).
//   - sendTest(): transmissao unica de teste, bloqueante e sem qualquer
//     confirmacao do recetor, usada apenas para validar deteccao +
//     transmissao numa mesma sessao. NAO faz parte de nenhuma logica de
//     emergencia ainda (essa esta desenhada mas por implementar — ver a
//     seccao "Detecao de emergencia" em PROJECT_STATUS.md).
// ============================================================================

#include "Lora/Lora.h"

#include <RadioLib.h>
#include <cstring>

namespace Lora {

namespace {

// ------------------------------------------------------------
// PINOUT — ver aviso completo em Lora.h sobre o nivel de confianca de
// cada ligacao. Resumido aqui para ficarem todos os numeros num sitio
// so, faceis de corrigir se a hipotese de NRST/NSS estiver errada.
// ------------------------------------------------------------
constexpr uint8_t kPinRfSwitch = A2;   // RF_SW — controlo da antena (confianca alta)
constexpr uint8_t kPinDio1     = D7;   // DIO1 — interrupcao de eventos do radio (confianca alta)
constexpr uint8_t kPinBusy     = D8;   // BUSY — indica quando o radio esta ocupado (confianca alta)
constexpr uint8_t kPinNss      = A3;   // SPI_NSS / chip-select — HIPOTESE, confianca baixa (ver Lora.h)
// NRST nao esta ligado a nenhum pino do MCU nesta hipotese — assume-se
// reset passivo no proprio modulo. RADIOLIB_NC diz ao RadioLib para nao
// tentar controlar nenhum pino de reset.

// Parametros da rede LoRa. 868 MHz e a banda ISM licenciada para uso
// livre na Europa/Portugal (nos EUA seria 915 MHz — NAO usar aqui).
constexpr float kFrequencyMHz = 868.0f;
constexpr float kBandwidthKHz = 125.0f;
constexpr uint8_t kSpreadingFactor = 9;   // Compromisso alcance/velocidade; 7=rapido/curto alcance, 12=lento/longo alcance.
constexpr uint8_t kCodingRate = 7;        // 4/7 — tolerancia a erros vs. overhead.
constexpr uint8_t kSyncWord = 0x12;       // Valor "privado" (nao o publico 0x34 do LoRaWAN) — rede ponto-a-ponto propria.
constexpr int8_t kTxPowerDbm = 14;        // Potencia de transmissao moderada; ajustar depois consoante testes de alcance.

SX1262 s_radio = new Module(kPinNss, kPinDio1, RADIOLIB_NC, kPinBusy);
bool s_ready = false;

} // namespace

bool begin() {
  // IMPORTANTE (bug encontrado e corrigido em 2026-07-03): o RF_SW e'
  // partilhado entre a antena BLE (2.4GHz) e a antena LoRa (868MHz) nesta
  // placa. Comutar este pino ANTES de confirmar que o radio LoRa
  // realmente inicializou corta fisicamente o BLE, mesmo que a pilha BLE
  // continue "a pensar" que esta a anunciar-se normalmente (foi visto em
  // hardware real: LED do BLE pisca e depois apaga assim que initLora()
  // corre a seguir a initBleDataLink()). Por isso o RF_SW so e' tocado
  // DEPOIS de confirmarmos sucesso — se a inicializacao falhar, o pino
  // fica no estado por omissao (nao configurado) e o BLE continua a usar
  // a antena normalmente.
  Serial.println("[LORA] a inicializar SX1262...");
  const int16_t state = s_radio.begin(kFrequencyMHz, kBandwidthKHz, kSpreadingFactor,
                                       kCodingRate, kSyncWord, kTxPowerDbm);

  if (state != RADIOLIB_ERR_NONE) {
    // Nao trava nada — so regista o erro. Um valor negativo tipico aqui
    // (ex.: RADIOLIB_ERR_CHIP_NOT_FOUND) indica que o pino de NSS (ou
    // outro) provavelmente esta errado — ver aviso de confianca em
    // Lora.h antes de mudar outra coisa que nao seja esse pino.
    Serial.print("[LORA] falha ao inicializar, codigo=");
    Serial.println(state);
    s_ready = false;
    return false;
  }

  // So chegamos aqui se o radio LoRa respondeu corretamente ao SPI — so
  // agora e' seguro comutar a antena partilhada para o caminho LoRa.
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
  if (state != RADIOLIB_ERR_NONE) {
    Serial.print("[LORA] falha no envio, codigo=");
    Serial.println(state);
    return false;
  }

  Serial.println("[LORA] envio concluido");
  return true;
}

} // namespace Lora
