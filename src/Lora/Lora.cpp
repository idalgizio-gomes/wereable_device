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
  // O RF_SW e' um pino simples de controlo da antena (nao faz parte do
  // protocolo SPI do radio) — configura-se como saida digital normal,
  // ligado (HIGH) para permitir a transmissao/receção pela antena.
  pinMode(kPinRfSwitch, OUTPUT);
  digitalWrite(kPinRfSwitch, HIGH);

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
