#include "Emergency/Emergency.h"

#include "Imu/Imu.h"
#include "Ble/Ble.h"
#include "Lora/Lora.h"
#include "Clock/Clock.h"

namespace Emergency {

namespace {

Config s_config;
uint8_t s_buttonPin = 0;

// --- Estado do gesto SOS manual (cliques do botão) ---------------------
bool s_lastButtonLow = false;   // Último valor lido do botão (LOW = premido).
uint8_t s_clickCount = 0;
uint32_t s_lastClickMs = 0;
bool s_sosPending = false;
uint32_t s_sosConfirmDeadlineMs = 0;

// --- Estado da deteção automática (queda + inatividade) -----------------
bool s_lastFreefall = false;    // Último valor de freefall lido do IMU.
bool s_fallWatchActive = false; // true entre a queda e o alerta/cancelamento.
uint32_t s_fallDetectedMs = 0;

// Dispara o alerta pelos dois canais decididos com o utilizador (BLE +
// LoRa, quando disponível) e regista em série para diagnóstico.
void raiseAlert(uint8_t alertType, const char *reasonLabel) {
  const uint32_t ts = Clock::nowUtc();

  Serial.print("[EMERGENCY] ALERTA disparado (" );
  Serial.print(reasonLabel);
  Serial.print("), tipo=");
  Serial.println(alertType);

  Ble::notifyEmergencyAlert(alertType, ts);

  if (Lora::isReady()) {
    const char *msg = (alertType == Ble::kEmergencyAlertSosManual)
                           ? "CareWear ALERTA: SOS manual"
                           : "CareWear ALERTA: queda + inatividade";
    (void)Lora::sendTest(msg);
  } else {
    Serial.println("[EMERGENCY] LoRa nao disponivel — alerta enviado so por BLE");
  }
}

void updateSosGesture() {
  const bool nowLow = (digitalRead(s_buttonPin) == LOW);
  const uint32_t nowMs = millis();

  // Deteta a borda de descida (transição solto -> premido) — é o que
  // conta como "um clique", evitando contar o mesmo toque várias vezes
  // enquanto o botão continua premido.
  const bool clickEdge = nowLow && !s_lastButtonLow;
  s_lastButtonLow = nowLow;

  if (clickEdge) {
    if (s_sosPending) {
      // Um novo clique enquanto o SOS está pendente de confirmação
      // cancela-o — dá ao utilizador uma forma simples de desfazer um
      // gesto acidental (ver comentário em Emergency.h/Config).
      Serial.println("[EMERGENCY] SOS pendente cancelado (novo clique durante confirmacao)");
      s_sosPending = false;
      s_clickCount = 0;
      return;
    }

    if ((nowMs - s_lastClickMs) > s_config.sosClickWindowMs) {
      // Passou tempo demais desde o último clique — recomeça a contagem.
      s_clickCount = 0;
    }
    s_lastClickMs = nowMs;
    s_clickCount++;

    if (s_clickCount >= s_config.sosClickCount) {
      Serial.print("[EMERGENCY] gesto SOS detetado (" );
      Serial.print(s_clickCount);
      Serial.println(" cliques) — a aguardar confirmacao...");
      s_sosPending = true;
      s_sosConfirmDeadlineMs = nowMs + s_config.sosConfirmDelayMs;
      s_clickCount = 0;
    }
  }

  // Comparação segura a overflow de millis() (idêntica ao padrão já usado
  // em Ppg.cpp/Ble.cpp): "nowMs >= s_sosConfirmDeadlineMs" direto falharia
  // silenciosamente se millis() desse a volta (~49.7 dias) enquanto uma
  // confirmação de SOS estivesse pendente, atrasando-a até ao próximo
  // overflow em vez dos poucos segundos configurados.
  if (s_sosPending &&
      static_cast<int32_t>(nowMs - s_sosConfirmDeadlineMs) >= 0) {
    s_sosPending = false;
    raiseAlert(Ble::kEmergencyAlertSosManual, "SOS manual confirmado");
  }
}

void updateFallDetection() {
  Imu::Sample sample{};
  if (!Imu::getLatestSample(sample)) return;

  const uint32_t nowMs = millis();

  // Borda de subida do freefall: início de uma possível queda. Arranca
  // (ou reinicia) o período de vigilância de inatividade.
  if (sample.freefall && !s_lastFreefall) {
    Serial.println("[EMERGENCY] possivel queda detetada — a vigiar inatividade...");
    s_fallWatchActive = true;
    s_fallDetectedMs = nowMs;
  }
  s_lastFreefall = sample.freefall;

  if (!s_fallWatchActive) return;

  if (!sample.inactivity) {
    // O utilizador voltou a mexer-se antes do timeout — cancela a
    // vigilância. Não é necessariamente "falsa queda": pode ter sido uma
    // queda real da qual a pessoa se levantou sozinha, o que também não
    // deve gerar um alerta automático (decisão de implementação,
    // documentada para validação futura do utilizador).
    Serial.println("[EMERGENCY] movimento retomado apos queda — vigilancia cancelada");
    s_fallWatchActive = false;
    return;
  }

  if ((nowMs - s_fallDetectedMs) >= s_config.fallInactivityTimeoutMs) {
    s_fallWatchActive = false;
    raiseAlert(Ble::kEmergencyAlertFallInactivity, "queda + inatividade prolongada");
  }
}

} // namespace

void begin(uint8_t buttonPin) {
  s_buttonPin = buttonPin;
  pinMode(s_buttonPin, INPUT_PULLUP);
  s_lastButtonLow = (digitalRead(s_buttonPin) == LOW);
  s_config = Config{};
  s_clickCount = 0;
  s_lastClickMs = 0;
  s_sosPending = false;
  s_lastFreefall = false;
  s_fallWatchActive = false;

  Serial.println("[EMERGENCY] modulo inicializado (SOS manual + queda/inatividade)");
}

void setConfig(const Config &cfg) {
  s_config = cfg;
}

const Config &config() {
  return s_config;
}

void update() {
  updateSosGesture();
  updateFallDetection();
}

void triggerTestAlert() {
  Serial.println("[EMERGENCY] disparo de teste forcado via serie");
  raiseAlert(Ble::kEmergencyAlertSosManual, "teste forcado (serie)");
}

} // namespace Emergency
