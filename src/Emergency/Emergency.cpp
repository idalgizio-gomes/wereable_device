#include "Emergency/Emergency.h"

#include "Imu/Imu.h"
#include "Ble/Ble.h"
#include "Lora/Lora.h"
#include "Clock/Clock.h"

namespace Emergency {

namespace {

Config s_config;
uint8_t s_buttonPin = 0;

// estado do gesto SOS manual (cliques do botao)
bool s_lastButtonLow = false;
uint8_t s_clickCount = 0;
uint32_t s_lastClickMs = 0;
bool s_sosPending = false;
uint32_t s_sosConfirmDeadlineMs = 0;

// estado da deteção automatica (queda + inatividade)
bool s_lastFreefall = false;
bool s_fallWatchActive = false; // true entre a queda e o alerta/cancelamento
uint32_t s_fallDetectedMs = 0;
bool s_confirmedStillSinceFall = false; // true quando inactivity assentou (~3s) pela 1a vez apos a queda atual

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

  const bool clickEdge = nowLow && !s_lastButtonLow; // borda de descida = 1 clique
  s_lastButtonLow = nowLow;

  if (clickEdge) {
    if (s_sosPending) {
      Serial.println("[EMERGENCY] SOS pendente cancelado (novo clique durante confirmacao)");
      s_sosPending = false;
      s_clickCount = 0;
      return;
    }

    if ((nowMs - s_lastClickMs) > s_config.sosClickWindowMs) {
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

  // comparacao segura a overflow de millis() (~49.7 dias), mesmo padrao de Ppg.cpp/Ble.cpp
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

  if (sample.freefall && !s_lastFreefall) {
    Serial.println("[EMERGENCY] possivel queda detetada — a vigiar inatividade...");
    s_fallWatchActive = true;
    s_fallDetectedMs = nowMs;
    s_confirmedStillSinceFall = false;
  }
  s_lastFreefall = sample.freefall;

  if (!s_fallWatchActive) return;

  // so cancela por "!inactivity" DEPOIS de a termos visto assentar 1x desde a queda; inactivity so assenta a true apos ~3s parado (senao o alerta nunca disparava)
  if (sample.inactivity) {
    s_confirmedStillSinceFall = true;
  } else if (s_confirmedStillSinceFall) {
    Serial.println("[EMERGENCY] movimento retomado apos queda — vigilancia cancelada");
    s_fallWatchActive = false;
    return;
  }

  if (s_confirmedStillSinceFall &&
      (nowMs - s_fallDetectedMs) >= s_config.fallInactivityTimeoutMs) {
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
  s_confirmedStillSinceFall = false;

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
