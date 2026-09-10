// Clock.cpp - RTC2 como cronometro (32.768kHz); hora = hora_base(setUtc) + ticks RTC2 desde a base / freq. Sem RTC calendario de hardware.
#include "Clock/Clock.h"

#include <nrf.h>
#include <rtos.h>
#include <stdio.h>

namespace {

constexpr uint32_t kRtcMask24 = 0x00FFFFFFUL; // RTC2 so tem 24 bits de contador
constexpr uint32_t kRtcFreqHz = 32768UL;

bool s_started = false;        // true depois de begin()
bool s_valid = false;          // true depois de setUtc() bem sucedido
uint32_t s_epochBase = 0;      // ultimo epoch UTC recebido por BLE
uint32_t s_lastCounter = 0;    // valor do RTC2 no instante de s_epochBase
uint64_t s_ticksSinceSet = 0;  // ticks acumulados desde s_epochBase

uint32_t rtcCounter24() {
  return (NRF_RTC2->COUNTER & kRtcMask24);
}

// subtracao modulo 2^24 lida com wrap-around do contador de 24 bits; chamar sempre dentro de secao critica
void updateTicksLocked() {
  const uint32_t nowCtr = rtcCounter24();
  const uint32_t delta = (nowCtr - s_lastCounter) & kRtcMask24;
  s_ticksSinceSet += delta;
  s_lastCounter = nowCtr;
}

// epoch -> ano/mes/dia/h/m/s; usa civil_from_days (Howard Hinnant) para dias->data gregoriana
void epochToDateTime(uint32_t epoch, int &year, int &month, int &day,
                     int &hour, int &minute, int &second) {
  uint32_t remainingSec = epoch;
  second = (int)(remainingSec % 60U);
  remainingSec /= 60U;
  minute = (int)(remainingSec % 60U);
  remainingSec /= 60U;
  hour = (int)(remainingSec % 24U);
  const uint32_t daysSince1970 = remainingSec / 24U;

  int64_t z = (int64_t)daysSince1970 + 719468LL; // desloca epoca para 0000-03-01
  const int64_t era = (z >= 0 ? z : z - 146096LL) / 146097LL;
  const uint32_t doe = (uint32_t)(z - era * 146097LL);  // [0, 146096]
  const uint32_t yoe = (doe - doe / 1460U + doe / 36524U - doe / 146096U) / 365U;
  int y = (int)(yoe) + (int)(era * 400LL);
  const uint32_t doy = doe - (365U * yoe + yoe / 4U - yoe / 100U);
  const uint32_t mp = (5U * doy + 2U) / 153U;
  const uint32_t d = doy - (153U * mp + 2U) / 5U + 1U;
  const int m = (int)mp + ((mp < 10U) ? 3 : -9);
  y += (m <= 2);

  year = y;
  month = m;
  day = (int)d;
}

} // namespace

namespace Clock {

bool begin() {
  if (s_started) return true; // idempotente: reiniciar o RTC2 desalinharia a hora ja sincronizada

  NRF_RTC2->TASKS_STOP = 1;
  NRF_RTC2->TASKS_CLEAR = 1;
  NRF_RTC2->PRESCALER = 0; // 32.768 kHz
  NRF_RTC2->EVTENCLR = 0xFFFFFFFFUL;
  NRF_RTC2->INTENCLR = 0xFFFFFFFFUL; // lido por polling, nao por interrupcao
  NRF_RTC2->TASKS_START = 1;

  s_lastCounter = rtcCounter24();
  s_ticksSinceSet = 0;
  s_valid = false;
  s_started = true;

  Serial.println("[CLOCK] RTC2 inicializado");
  return true;
}

void setUtc(uint32_t epochUtc) {
  if (!s_started) begin();

  taskENTER_CRITICAL(); // nowUtc() pode ler ao mesmo tempo que redefinimos
  s_epochBase = epochUtc;
  s_lastCounter = rtcCounter24();
  s_ticksSinceSet = 0;
  s_valid = true;
  taskEXIT_CRITICAL();
}

void invalidate() {
  taskENTER_CRITICAL();
  s_valid = false;
  s_epochBase = 0;
  s_ticksSinceSet = 0;
  if (s_started) {
    s_lastCounter = rtcCounter24();
  }
  taskEXIT_CRITICAL();
}

bool isValid() {
  bool valid = false;
  taskENTER_CRITICAL();
  valid = s_valid;
  taskEXIT_CRITICAL();
  return valid;
}

uint32_t nowUtc() {
  if (!s_started) begin();

  uint32_t epoch = 0;
  taskENTER_CRITICAL();
  if (s_valid) {
    updateTicksLocked();
    const uint32_t elapsedSec = (uint32_t)(s_ticksSinceSet / kRtcFreqHz);
    epoch = s_epochBase + elapsedSec;
  }
  // s_valid=false -> epoch fica 0, sentinela de "ainda nao sincronizado"
  taskEXIT_CRITICAL();

  return epoch;
}

bool formatTime(char *out, size_t outLen) {
  if (out == nullptr || outLen < 9) return false; // "HH:MM:SS\0"
  const uint32_t epoch = nowUtc();
  if (epoch == 0) return false;

  int y = 0, m = 0, d = 0, hh = 0, mm = 0, ss = 0;
  epochToDateTime(epoch, y, m, d, hh, mm, ss);
  (void)y; (void)m; (void)d;
  snprintf(out, outLen, "%02d:%02d:%02d", hh, mm, ss);
  return true;
}

bool formatDate(char *out, size_t outLen) {
  if (out == nullptr || outLen < 11) return false; // "DD/MM/YYYY\0"
  const uint32_t epoch = nowUtc();
  if (epoch == 0) return false;

  int y = 0, m = 0, d = 0, hh = 0, mm = 0, ss = 0;
  epochToDateTime(epoch, y, m, d, hh, mm, ss);
  (void)hh; (void)mm; (void)ss;
  snprintf(out, outLen, "%02d/%02d/%04d", d, m, y);
  return true;
}

bool formatDateTime(char *out, size_t outLen) {
  if (out == nullptr || outLen < 20) return false; // "DD/MM/YYYY HH:MM:SS\0"
  const uint32_t epoch = nowUtc();
  if (epoch == 0) return false;

  int y = 0, m = 0, d = 0, hh = 0, mm = 0, ss = 0;
  epochToDateTime(epoch, y, m, d, hh, mm, ss);
  snprintf(out, outLen, "%02d/%02d/%04d %02d:%02d:%02d", d, m, y, hh, mm, ss);
  return true;
}

} // namespace Clock
