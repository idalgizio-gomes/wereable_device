// =============================================================================
// Clock.cpp
// -----------------------------------------------------------------------------
// Implementação do módulo Clock (ver Clock.h para a documentação da API
// pública). A estratégia é: usar o periférico RTC2 apenas como um
// "cronómetro" de alta precisão (conta ticks a 32.768 kHz) e guardar em
// variáveis a última hora UTC recebida por BLE + o instante do RTC2 nesse
// momento. A hora atual é sempre calculada como:
//     hora_base + (ticks do RTC2 desde a base) / frequência
// Isto evita depender de um RTC calendário de hardware (que o nRF52840
// não tem) e mantém a lógica simples e barata em CPU.
// =============================================================================

#include "Clock/Clock.h"

#include <nrf.h>
#include <rtos.h>
#include <stdio.h>

namespace {

// O contador do RTC2 tem apenas 24 bits (conta de 0 a 0xFFFFFF e depois
// dá a volta/"overflow"), por isso todas as leituras são mascaradas com
// kRtcMask24 para simular esse comportamento de 24 bits mesmo em
// variáveis de 32 bits.
constexpr uint32_t kRtcMask24 = 0x00FFFFFFUL;
// Frequência do LFCLK que alimenta o RTC2: 32.768 kHz (cristal típico de
// baixo consumo), ou seja, 32768 ticks = 1 segundo.
constexpr uint32_t kRtcFreqHz = 32768UL;

bool s_started = false;        // true depois de begin() ter corrido
bool s_valid = false;          // true depois de haver um setUtc() bem sucedido
uint32_t s_epochBase = 0;      // último epoch UTC recebido por BLE
uint32_t s_lastCounter = 0;    // valor do RTC2 no instante de s_epochBase (ou última atualização)
uint64_t s_ticksSinceSet = 0;  // total de ticks acumulados desde s_epochBase

// Lê o contador atual do RTC2, já limitado a 24 bits (o tamanho real do
// registo de hardware).
uint32_t rtcCounter24() {
  return (NRF_RTC2->COUNTER & kRtcMask24);
}

// Atualiza s_ticksSinceSet com os ticks que passaram desde a última
// leitura. Usa subtração módulo 2^24 (via kRtcMask24) para lidar
// corretamente com o "wrap-around" do contador de 24 bits — mesmo que o
// RTC2 dê a volta entre duas chamadas, o delta calculado continua correto.
// Deve ser chamada sempre dentro de uma secção crítica (ver chamadores).
void updateTicksLocked() {
  const uint32_t nowCtr = rtcCounter24();
  const uint32_t delta = (nowCtr - s_lastCounter) & kRtcMask24;
  s_ticksSinceSet += delta;
  s_lastCounter = nowCtr;
}

// Converte um epoch Unix (segundos desde 1970-01-01 UTC) nos seus
// componentes de calendário civil (ano, mês, dia, hora, minuto, segundo).
// O cálculo hora/minuto/segundo é aritmética simples (resto/divisão por
// 60 e por 24). A conversão de "dias desde 1970" para ano/mês/dia usa o
// algoritmo civil_from_days (de Howard Hinnant), que é uma fórmula
// conhecida e testada para converter dias em datas do calendário
// gregoriano sem precisar de tabelas de dias-por-mês nem tratar bissextos
// como caso especial.
void epochToDateTime(uint32_t epoch, int &year, int &month, int &day,
                     int &hour, int &minute, int &second) {
  uint32_t t = epoch;
  second = (int)(t % 60U);
  t /= 60U;
  minute = (int)(t % 60U);
  t /= 60U;
  hour = (int)(t % 24U);
  const uint32_t daysSince1970 = t / 24U;

  // Algoritmo civil_from_days: desloca a época para 0000-03-01 (719468
  // dias antes de 1970-01-01) para que os anos bissextos caiam sempre no
  // fim do "ano civil deslocado", simplificando as contas.
  int64_t z = (int64_t)daysSince1970 + 719468LL;
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
  // Idempotente: se já foi inicializado antes, não reinicia o RTC2 (o
  // que reiniciaria o contador e desalinhava a hora já sincronizada).
  if (s_started) return true;

  // Configuração "de raiz" do periférico: para, limpa o contador, usa o
  // prescaler 0 (sem divisão extra, ou seja, corre à frequência do
  // LFCLK = 32.768 kHz) e desliga eventos/interrupções que não são
  // usados por este módulo (aqui o RTC2 é lido por polling, não por
  // interrupção).
  NRF_RTC2->TASKS_STOP = 1;
  NRF_RTC2->TASKS_CLEAR = 1;
  NRF_RTC2->PRESCALER = 0; // 32.768 kHz
  NRF_RTC2->EVTENCLR = 0xFFFFFFFFUL;
  NRF_RTC2->INTENCLR = 0xFFFFFFFFUL;
  NRF_RTC2->TASKS_START = 1;

  s_lastCounter = rtcCounter24();
  s_ticksSinceSet = 0;
  s_valid = false; // ainda não houve nenhuma sincronização por BLE
  s_started = true;

  Serial.println("[CLOCK] RTC2 inicializado");
  return true;
}

void setUtc(uint32_t epochUtc) {
  // Garante que o RTC2 já está a correr antes de usarmos o seu contador
  // como referência.
  if (!s_started) begin();

  // Zona crítica: várias tarefas/ISRs podem ler a hora (nowUtc()) ao
  // mesmo tempo que esta função a está a redefinir, por isso as
  // variáveis partilhadas só podem ser alteradas com interrupções
  // desligadas, para evitar leituras inconsistentes.
  taskENTER_CRITICAL();
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
  // Só faz sentido reler o contador do RTC2 se ele já tiver sido
  // inicializado; caso contrário deixamos s_lastCounter como está,
  // pois begin() irá defini-lo mais tarde.
  if (s_started) {
    s_lastCounter = rtcCounter24();
  }
  taskEXIT_CRITICAL();
}

bool isValid() {
  // Leitura protegida por secção crítica pela mesma razão de setUtc():
  // s_valid pode estar a ser alterado por outra tarefa neste preciso
  // momento.
  bool v = false;
  taskENTER_CRITICAL();
  v = s_valid;
  taskEXIT_CRITICAL();
  return v;
}

uint32_t nowUtc() {
  if (!s_started) begin();

  uint32_t epoch = 0;
  taskENTER_CRITICAL();
  if (s_valid) {
    // Atualiza o total de ticks decorridos e converte para segundos
    // inteiros; a hora atual é a hora-base mais estes segundos.
    updateTicksLocked();
    const uint32_t elapsedSec = (uint32_t)(s_ticksSinceSet / kRtcFreqHz);
    epoch = s_epochBase + elapsedSec;
  }
  // Se s_valid for false, "epoch" fica 0 — este é o valor "sentinela"
  // que sinaliza ao chamador que ainda não há hora sincronizada.
  taskEXIT_CRITICAL();

  return epoch;
}

bool formatTime(char *out, size_t outLen) {
  // outLen mínimo de 9 cobre "HH:MM:SS\0" (8 caracteres + terminador).
  if (out == nullptr || outLen < 9) return false;
  const uint32_t epoch = nowUtc();
  if (epoch == 0) return false; // relógio ainda não sincronizado

  int y = 0, m = 0, d = 0, hh = 0, mm = 0, ss = 0;
  epochToDateTime(epoch, y, m, d, hh, mm, ss);
  (void)y; (void)m; (void)d; // não usados aqui, só precisamos da hora
  snprintf(out, outLen, "%02d:%02d:%02d", hh, mm, ss);
  return true;
}

bool formatDate(char *out, size_t outLen) {
  // outLen mínimo de 11 cobre "DD/MM/YYYY\0" (10 caracteres + terminador).
  if (out == nullptr || outLen < 11) return false;
  const uint32_t epoch = nowUtc();
  if (epoch == 0) return false; // relógio ainda não sincronizado

  int y = 0, m = 0, d = 0, hh = 0, mm = 0, ss = 0;
  epochToDateTime(epoch, y, m, d, hh, mm, ss);
  (void)hh; (void)mm; (void)ss; // não usados aqui, só precisamos da data
  snprintf(out, outLen, "%02d/%02d/%04d", d, m, y);
  return true;
}

bool formatDateTime(char *out, size_t outLen) {
  // outLen mínimo de 20 cobre "DD/MM/YYYY HH:MM:SS\0" (19 caracteres +
  // terminador).
  if (out == nullptr || outLen < 20) return false;
  const uint32_t epoch = nowUtc();
  if (epoch == 0) return false; // relógio ainda não sincronizado

  int y = 0, m = 0, d = 0, hh = 0, mm = 0, ss = 0;
  epochToDateTime(epoch, y, m, d, hh, mm, ss);
  snprintf(out, outLen, "%02d/%02d/%04d %02d:%02d:%02d", d, m, y, hh, mm, ss);
  return true;
}

} // namespace Clock
