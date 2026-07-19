// Battery.cpp - Implementacao do modulo Battery (ver Battery.h para a
// proveniencia completa do pinout/formulas e o que fica por validar em
// hardware real).
#include "Battery/Battery.h"

#include <math.h> // lroundf()

namespace Battery {

namespace {

// Tempo (ms) de espera depois de ativar VBAT_ENABLE (LOW) antes de ler o
// ADC, para deixar a tensao no pino assentar. O exemplo oficial da
// Adafruit (adc_vbat.ino, ver Battery.h) usa so 1ms, mas esse exemplo le
// diretamente o pino VBAT sem um mux/enable intermedio — aqui ha um passo
// extra (ativar o divisor via VBAT_ENABLE) sem tempo de assentamento
// documentado por Seeed para esta variante, por isso usa-se uma margem
// mais conservadora. Ainda por afinar com dados reais (ver Battery.h).
constexpr uint32_t kAdcSettleDelayMs = 10;

// 3.0V de gama do ADC (referencia AR_INTERNAL_3_0) a dividir por 4096
// niveis (resolucao de 12 bits) = mV por LSB. Valor e formula tal como no
// exemplo oficial Adafruit adc_vbat.ino (ver citacao completa em
// Battery.h) — reaproveitado tal e qual porque a familia de ADC (SAADC do
// nRF52840) e' a mesma, independentemente do desenho do divisor resistivo
// especifico de cada placa.
constexpr float kAdcMvPerLsb = 0.73242188f;

// Ratio do divisor resistivo entre a bateria e o pino PIN_VBAT nesta
// variante (Sense Plus): documentado publicamente pela Seeed apenas como
// "aproximadamente 1/3" (ver wiki.seeedstudio.com/battery_charging_considerations/,
// citado em Battery.h), sem os valores exatos das resistencias. Multiplicar
// por 3.0 aqui e' portanto uma ESTIMATIVA, nao um valor calibrado — ver
// aviso "por validar em hardware real" em Battery.h. Ajustar esta constante
// depois de comparar sample().voltage_mv com um multimetro real.
constexpr float kBatteryDividerRatio = 3.0f;

Reading s_latest = {};

// Converte uma tensao de bateria (mV) numa estimativa 0-100% de carga,
// usando uma curva por troços lineares que aproxima a curva de descarga
// tipica (em repouso, sem carga) de uma celula Li-Po unica. Deliberadamente
// NAO e um mapeamento linear simples entre 3.0V-4.2V: a tensao de uma
// Li-Po cai muito mais depressa perto dos extremos (quase cheia / quase
// vazia) do que a meio da descarga, onde fica bastante estavel — um mapa
// linear simples subestimaria fortemente a carga real a meio da descarga
// e sobrestimaria perto dos extremos. Os pontos abaixo sao valores de
// referencia amplamente citados para Li-Po de 1 celula (ex.: usados em
// varios projetos/bibliotecas open-source de "fuel gauge" por ADC simples)
// — NAO foram medidos na bateria especifica deste projeto, por isso
// continuam a ser uma aproximacao, nunca uma leitura de precisao.
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
  return kCurve[n - 1].pct; // inalcancavel na pratica, guarda defensiva.
}

} // namespace

bool begin() {
  // Replica o comportamento por omissao do proprio BSP (ver initVariant()
  // em variant.cpp, citado em Battery.h): percurso de leitura desativado
  // (HIGH) em repouso, so ativado (LOW) durante sample(). pinMode()/
  // digitalWrite() aqui sao idempotentes com o que initVariant() ja fez
  // antes do setup() — repetido explicitamente para o estado inicial deste
  // modulo nao depender silenciosamente de esse detalhe do core.
  pinMode(VBAT_ENABLE, OUTPUT);
  digitalWrite(VBAT_ENABLE, HIGH);
  pinMode(PIN_VBAT, INPUT);

  s_latest = Reading{};

  Serial.println("[BATTERY] modulo inicializado (ADC em PIN_VBAT/P0.31, ver Battery.h para proveniencia do pinout)");
  return true;
}

bool sample(Reading &out) {
  // Ativa o divisor so durante esta leitura (ver aviso de seguranca em
  // Battery.h: nunca deixar VBAT_ENABLE em HIGH durante o carregamento e'
  // o estado perigoso — desativado por omissao, ativado so quando
  // necessario, e' o padrao mais seguro independentemente do estado de
  // carregamento no momento).
  digitalWrite(VBAT_ENABLE, LOW);
  delay(kAdcSettleDelayMs);

  // Referencia/resolucao explicitas (ver adc_vbat.ino, citado em
  // Battery.h) — nao assumir o default da placa, que e' 3.6V/10-bit.
  analogReference(AR_INTERNAL_3_0);
  analogReadResolution(12);
  delay(1); // deixa o ADC assentar apos mudar referencia/resolucao.

  const uint16_t raw = analogRead(PIN_VBAT);

  // Repoe as definicoes por omissao do ADC, para nao afetar silenciosamente
  // qualquer outra leitura analogica que possa vir a existir no resto do
  // firmware (nao ha nenhuma no momento, mas e' o mesmo cuidado tomado no
  // exemplo oficial da Adafruit).
  analogReference(AR_DEFAULT);
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
