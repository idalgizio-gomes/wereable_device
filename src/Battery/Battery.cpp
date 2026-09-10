// Ver Battery.h para proveniencia do pinout/formulas e o que falta validar em hardware.
#include "Battery/Battery.h"

#include <math.h> // lroundf()

namespace Battery {

namespace {

constexpr uint32_t kAdcSettleDelayMs = 10; // tempo de assentamento apos VBAT_ENABLE=LOW, margem conservadora (ver Battery.h)

constexpr float kAdcMvPerLsb = 0.73242188f; // AR_INTERNAL_3_0 (3.0V/4096) — igual ao exemplo Adafruit adc_vbat.ino

constexpr float kBatteryDividerRatio = 3.0f; // divisor Seeed Sense Plus, ~1/3 documentado sem valores exatos — ESTIMATIVA, por calibrar

Reading s_latest = {};

// curva por troços (nao linear) para Li-Po 1S; pontos de referencia genericos, nao medidos nesta bateria
uint8_t voltageToPercent(float mv) {
  struct Point {
    float mv;
    uint8_t pct;
  };
  static const Point kCurve[] = {
      {3200.0f, 0},   {3300.0f, 5},   {3500.0f, 10},  {3600.0f, 15},
      {3650.0f, 20},  {3700.0f, 30},  {3730.0f, 40},  {3760.0f, 50},
      {3795.0f, 60},  {3830.0f, 70},  {3870.0f, 80},  {3910.0f, 85},
      {3980.0f, 90},  {4080.0f, 95},  {4200.0f, 100},
  };
  constexpr size_t n = sizeof(kCurve) / sizeof(kCurve[0]);

  if (mv <= kCurve[0].mv) return kCurve[0].pct;
  if (mv >= kCurve[n - 1].mv) return kCurve[n - 1].pct;

  for (size_t i = 1; i < n; i++) {
    if (mv <= kCurve[i].mv) {
      const Point &a = kCurve[i - 1];
      const Point &b = kCurve[i];
      const float t = (mv - a.mv) / (b.mv - a.mv);
      const float pct = a.pct + t * (static_cast<float>(b.pct) - static_cast<float>(a.pct));
      return static_cast<uint8_t>(lroundf(pct));
    }
  }
  return kCurve[n - 1].pct; // inalcancavel, guarda defensiva
}

} // namespace

bool begin() {
  // replica o default do BSP (initVariant()): percurso desativado (HIGH) em repouso
  pinMode(VBAT_ENABLE, OUTPUT);
  digitalWrite(VBAT_ENABLE, HIGH);
  pinMode(PIN_VBAT, INPUT);

  s_latest = Reading{};

  Serial.println("[BATTERY] modulo inicializado (ADC em PIN_VBAT/P0.31, ver Battery.h para proveniencia do pinout)");
  return true;
}

bool sample(Reading &out) {
  digitalWrite(VBAT_ENABLE, LOW); // nunca deixar em HIGH durante carregamento (ver Battery.h)
  delay(kAdcSettleDelayMs);

  analogReference(AR_INTERNAL_3_0); // default da placa e' 3.6V/10-bit, nao usar aqui
  analogReadResolution(12);
  delay(1);

  const uint16_t raw = analogRead(PIN_VBAT);

  analogReference(AR_DEFAULT); // repoe defaults para nao afetar outras leituras analogicas
  analogReadResolution(10);

  digitalWrite(VBAT_ENABLE, HIGH);

  const float adcMv = static_cast<float>(raw) * kAdcMvPerLsb;
  const float battMv = adcMv * kBatteryDividerRatio;

  out.raw_adc = raw;
  out.voltage_mv = static_cast<uint16_t>(lroundf(battMv));
  out.percent = voltageToPercent(battMv);
  out.timestamp_ms = millis();
  out.valid = true;

  s_latest = out;
  return true;
}

const Reading &latest() {
  return s_latest;
}

} // namespace Battery
