// ============================================================
// Ble.cpp - Implementacao do modulo BLE (ver Ble.h para a visao geral)
// ============================================================
// Este ficheiro:
//   1) Declara os servicos/characteristics GATT (UUIDs) usados para
//      falar com a app do telemovel.
//   2) Implementa a logica de troca da chave AES e de sincronizacao de
//      hora/data (Current Time) durante o "provisioning" inicial.
//   3) Implementa o "modo de dados": uma tarefa FreeRTOS (gattDumpTask)
//      que le registos de sensores (IMU/PPG) de um ring buffer em QSPI
//      flash e envia-os por notificacoes BLE, fragmentados em pacotes
//      pequenos (porque o MTU do BLE é limitado).
//   4) Implementa o arranque/paragem do advertising ("anunciar-se" para
//      poder ser encontrado/ligado por um telemovel) para cada modo.
#include "Ble/Ble.h"

#include "Display/Ui.h"
#include "Storage/Storage.h"
#include "QspiRingBuffer/QspiRingBuffer.h"
#include "Clock/Clock.h"
#include "Ppg/Ppg.h"

#include <bluefruit.h>
#include <rtos.h>
#include <cstring>

// UUIDs dos servicos e characteristics GATT expostos pelo wearable.
// - wearableService: servico "guarda-chuva" custom do dispositivo, usado
//   tanto no provisioning (chave AES) como no modo de dados (dump).
// - aesKeyChar: characteristic de escrita onde a app envia a chave AES
//   partilhada, usada para cifrar/decifrar dados sensiveis.
// - currentTimeService/currentTimeChar: servico e characteristic PADRAO
//   do Bluetooth SIG ("Current Time", UUID16 0x2A2B) usados para a app
//   enviar a data/hora atual ao dispositivo.
// - dumpCtrlChar: characteristic de escrita para a app pedir
//   inicio/paragem da transmissao de dados dos sensores.
// - dumpDataChar: characteristic de notificacao pela qual os pacotes de
//   dados dos sensores (fragmentados) sao enviados ao telemovel.
// - dumpStatusChar: characteristic de notificacao/leitura com o estado
//   atual da transmissao (streaming/idle, contagens, motivo do estado).
static BLEService        wearableService("12345678-1234-5678-1234-56789abcdef0");
static BLECharacteristic aesKeyChar     ("abcd1234-5678-1234-5678-abcdef123456");
static BLEService        currentTimeService(UUID16_SVC_CURRENT_TIME);
static BLECharacteristic currentTimeChar(UUID16_CHR_CURRENT_TIME);
static BLECharacteristic dumpCtrlChar   ("abcd1234-5678-1234-5678-abcdef200001");
static BLECharacteristic dumpDataChar   ("abcd1234-5678-1234-5678-abcdef200002");
static BLECharacteristic dumpStatusChar ("abcd1234-5678-1234-5678-abcdef200003");
// emergencyAlertChar: notificacao dedicada a alertas de emergencia (SOS
// manual ou queda+inatividade), separada de dumpStatusChar para nao
// misturar semanticas (estado do streaming vs. um evento critico raro) e
// para nao ter de alterar o formato ja fixo do DumpStatusPacket existente.
static BLECharacteristic emergencyAlertChar("abcd1234-5678-1234-5678-abcdef200004");

namespace {

// ------------------------------------------------------------
// Constantes de configuracao do "modo de dados" (streaming GATT)
// ------------------------------------------------------------
// O dispositivo envia os registos de sensores em "janelas" periodicas
// (por omissao 1 segundo), tentando enviar ate kWindowTargetRecords
// registos por janela (baseado na taxa de amostragem do IMU). Cada
// registo é demasiado grande para um unico pacote BLE, por isso é
// fragmentado em pedacos de kGattDumpChunkLen bytes.
// *** OTIMIZACAO DE RAM (2a ronda, com dados reais de hardware) ***:
// reduzido de 2560 para 1280 words (-5120 bytes / -7168 bytes face ao
// valor original de 3072). Justificacao: captura real de
// uxTaskGetStackHighWaterMark() em 2026-07-03 (ver DEBUG_STACK_WATERMARKS
// em main.cpp e PROJECT_STATUS.md) mostrou apenas ~107 words realmente
// usadas de 2560 reservadas (free=2453/2560, ~96% livre) durante ~30s de
// streaming BLE ativo. 1280 words mantem ainda ~11x de margem sobre esse
// uso observado (1280-107=1173 words livres esperadas) — folga generosa
// mesmo sendo esta a task que chama para dentro da pilha BLE/SoftDevice
// da Nordic (Bluefruit.*), cuja profundidade de chamadas internas e mais
// dificil de estimar so por inspecao de codigo. Ainda por confirmar em
// hardware real com este novo valor — reativar DEBUG_STACK_WATERMARKS e
// validar que free_words continua confortavel acima de 0 (ver
// dumpTaskStackHighWaterMarkWords() em Ble.h).
constexpr uint16_t kGattDumpTaskStackWords = 1280;
// Atraso (ms) entre cada FRAGMENTO BLE enviado (ver sendDumpPendingRecord).
// *** AJUSTE DE ESTABILIDADE BLE ***: estava a 0 (sem atraso nenhum), o que
// gera ate ~208 notificacoes/seg (52 registos/seg x ate 4 fragmentos cada) —
// em testes reais isto sobrecarregou a pilha BLE do lado do central (PC com
// Windows) e causou desconexoes repetidas pouco depois de o streaming
// comecar. 2ms/fragmento reduz o pico para um maximo teorico de ~500
// fragmentos/seg, dando folga a pilha BLE do central sem comprometer o
// ritmo necessario (208/seg) para acompanhar a taxa real do IMU.
//
// IMPORTANTE: este valor regula APENAS o ritmo entre a placa e quem se
// liga diretamente por BLE (o bridge local ou uma app/telemovel) — e
// completamente independente de qualquer servidor externo que receba os
// dados depois disso. A partir do momento em que um registo chega ao
// bridge/app via BLE, o reenvio para um servidor externo (HTTP/WebSocket/
// fila, etc.) e responsabilidade exclusiva desse bridge/app, que o pode
// atrasar, colocar em fila ou agrupar como precisar — o firmware nunca
// espera por essa segunda etapa nem e afetado pela velocidade dela.
constexpr uint32_t kGattDumpInterPacketMs = 2;
constexpr uint32_t kGattDumpWindowMs = 1000; // envio continuo: 1 segundo
constexpr uint32_t kImuRateHz = 52;
constexpr uint32_t kWindowTargetRecords = (kImuRateHz * kGattDumpWindowMs) / 1000U; // 52
constexpr uint32_t kDumpStatusEveryRecords = 128;
constexpr uint32_t kGattDumpInterRecordMs = 0;
constexpr uint32_t kGattDumpCoopEveryRecords = 16;
constexpr uint32_t kGattDumpCoopDelayMs = 1;
constexpr uint32_t kGattDumpWaitLogMs = 2000;
constexpr uint32_t kGattDumpIdleLogMs = 5000;
constexpr uint32_t kBleProvisionWaitLogMs = 5000;
constexpr bool kGattDumpVerboseLogs = false;
constexpr uint8_t kGattDumpChunkLen = 12;

constexpr uint16_t kRecTypeImuPpgV1 = 0x1001;
constexpr uint8_t kDumpCtrlStart = 0x01;
constexpr uint8_t kDumpCtrlStop = 0x02;
// Pede uma medicao de FC "forcada" (ver Ppg::requestManualHr): bytes[1..2]
// (uint16 little-endian, opcional) indicam a duracao em segundos; se a
// app nao enviar esses bytes, usa-se kForceHrDefaultSeconds.
constexpr uint8_t kDumpCtrlForceHr = 0x03;
constexpr uint16_t kForceHrDefaultSeconds = 15;
// Apaga TODOS os registos de leituras guardados no ring buffer da flash
// externa (ver QspiRingBuffer::format()). Nao afeta a calibracao do IMU
// nem a chave AES (essas ficam noutro sistema de ficheiros - Storage -
// e nao sao tocadas por este comando). E' destrutivo e irreversivel: a
// app/dashboard deve confirmar explicitamente com o utilizador antes de
// enviar este comando (ver popup de aviso no dashboard).
constexpr uint8_t kDumpCtrlResetReadings = 0x04;
constexpr uint8_t kDumpDataType = 0xA1;
constexpr uint8_t kDumpStatusType = 0xA2;

// Layout binario (com "packed" para nao haver padding do compilador)
// do payload de um registo IMU+PPG tal como esta guardado no ring
// buffer QSPI (produzido por outro modulo, ex. sensores).
struct __attribute__((packed)) ImuPpgPayloadV1 {
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
  uint32_t steps;
  uint8_t ff;
  uint8_t inact;
  int16_t spo2;
  int16_t hr;
};

// Registo "completo" (com timestamp) tal como é enviado para a app,
// em texto simples (nao cifrado) — o nome "Plain" distingue-o de uma
// eventual versao cifrada com AES.
struct __attribute__((packed)) FullPlain {
  uint32_t ts;
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
  uint32_t steps;
  uint8_t ff;
  uint8_t inact;
  int16_t spo2;
  int16_t hr;
};

// Um "fragmento" de um FullPlain enviado via notify() na characteristic
// dumpDataChar. Como um FullPlain (38 bytes) pode nao caber num unico
// pacote BLE, é dividido em ate N fragmentos de kGattDumpChunkLen bytes;
// frag_idx/frag_total permitem a app remontar o registo do lado dela.
struct __attribute__((packed)) DumpDataPacket {
  uint8_t type;
  uint8_t frag_idx;
  uint8_t frag_total;
  uint8_t chunk_len;
  uint32_t rec_seq;
  uint8_t chunk[kGattDumpChunkLen];
};

// Pacote de estado enviado periodicamente (e sob pedido) na
// characteristic dumpStatusChar, para a app saber se a transmissao esta
// ativa/parada, quantos registos ja foram enviados/confirmados, e o
// motivo do ultimo evento de estado (ver os valores literais passados a
// publishDumpStatus() ao longo do ficheiro, ex.: 1=start, 4=sem dados,
// 5=stop por comando, 6=falha no envio, 7=desconectado, 8=falha no pop).
struct __attribute__((packed)) DumpStatusPacket {
  uint8_t type;
  uint8_t state;
  uint8_t reason;
  uint8_t reserved;
  uint32_t seq;
  uint32_t sent_records;
  uint32_t acked_records;
};

// Resultado de mapear um Record generico do ring buffer para o formato
// FullPlain especifico usado pelo BLE (inclui o numero de sequencia
// original do ring buffer, usado para tracking/ack).
struct FullMappedRecord {
  uint32_t rec_seq;
  FullPlain payload;
};

// Pacote enviado na characteristic emergencyAlertChar sempre que o modulo
// Emergency confirma um SOS manual ou uma queda+inatividade prolongada.
// 'type' usa os valores de EmergencyAlertType (Ble.h); 'seq' incrementa a
// cada alerta enviado (permite a app detetar alertas repetidos/perdidos).
struct __attribute__((packed)) EmergencyAlertPacket {
  uint8_t type;
  uint8_t reserved;
  uint16_t seq;
  uint32_t timestamp_utc;
};

static_assert(sizeof(FullPlain) == 38, "FullPlain v2 must have 38 bytes");
static_assert(sizeof(DumpDataPacket) == 20, "DumpDataPacket must have 20 bytes");
static_assert(sizeof(DumpStatusPacket) == 16, "DumpStatusPacket must have 16 bytes");
static_assert(sizeof(EmergencyAlertPacket) == 8, "EmergencyAlertPacket must have 8 bytes");

// Flags "volatile" porque sao escritas dentro de callbacks BLE (que
// correm no contexto/tarefa da stack Bluefruit) e lidas no loop
// principal ou noutra tarefa (gattDumpTask) — evita que o compilador
// otimize leituras assumindo que o valor nao muda "sozinho".
static volatile bool s_aesArrived = false;
static volatile bool s_timestampArrived = false;
static volatile uint32_t s_timestamp = 0;

// Copia em RAM da chave AES atualmente ativa (para acesso rapido sem
// tocar na flash a cada operacao de cifra/decifra).
static uint8_t s_aesKey[AES_KEY_MAX_LEN] = {0};
static constexpr const char *kBleBuildTag = "BLE_GATT_DUMP_V1";

// Estados possiveis da maquina de estados do "dump" (streaming) de
// sensores: DUMP_IDLE = parado/a espera de comando ou ligacao;
// DUMP_STREAMING = a enviar registos ativamente.
enum DumpState : uint8_t {
  DUMP_IDLE = 0,
  DUMP_STREAMING = 1,
};

// Estado partilhado da maquina de streaming, manipulado tanto pelos
// callbacks BLE (pedidos de start/stop, ligar/desligar) como pela
// tarefa gattDumpTask que efetivamente envia os dados.
static volatile DumpState s_dumpState = DUMP_IDLE;
static volatile bool s_dumpStartRequested = false;
static volatile bool s_dumpStopRequested = false;
static TaskHandle_t s_dumpTaskHandle = nullptr;
static uint32_t s_dumpSentRecords = 0;
static uint32_t s_dumpAckedRecords = 0;
static volatile bool s_dataModeEnabled = false;
// Permite forcar o envio imediato da proxima janela, sem esperar pelo
// intervalo normal (kGattDumpWindowMs) — atualmente nao é ligada a true
// em nenhum ponto do ficheiro, mas fica disponivel para esse fim.
static volatile bool s_dumpWindowImmediate = false;

// "Registo pendente": o proximo registo já lido do ring buffer mas
// ainda nao confirmado como enviado com sucesso. Guardar aqui evita
// perder o registo (e o seu numero de sequencia) se o envio falhar a
// meio e precisar de ser tentado novamente.
static bool s_dumpPendingValid = false;
static uint32_t s_dumpPendingSeq = 0;
static FullPlain s_dumpPendingSample = {};

// Monta e envia (via write local + notify, se ligado) um pacote de
// estado do streaming para a app, para que esta saiba em que fase o
// dispositivo esta e quantos registos ja foram processados.
void publishDumpStatus(uint8_t state, uint8_t reason, uint32_t seq) {
  DumpStatusPacket st{};
  st.type = kDumpStatusType;
  st.state = state;
  st.reason = reason;
  st.reserved = 0;
  st.seq = seq;
  st.sent_records = s_dumpSentRecords;
  st.acked_records = s_dumpAckedRecords;

  dumpStatusChar.write(reinterpret_cast<const uint8_t *>(&st), sizeof(st));
  if (Bluefruit.connected() > 0) {
    (void)dumpStatusChar.notify(reinterpret_cast<const uint8_t *>(&st), sizeof(st));
  }
}

// Contador de sequencia dos alertas de emergencia — ver EmergencyAlertPacket.
static uint16_t s_emergencyAlertSeq = 0;

// Helpers de calendario usados apenas para validar/converter a data
// recebida via BLE (nao ha biblioteca de data/hora disponivel aqui).
bool isLeapYear(uint16_t y) {
  return ((y % 4U) == 0U) && (((y % 100U) != 0U) || ((y % 400U) == 0U));
}

uint8_t daysInMonth(uint16_t y, uint8_t m) {
  static const uint8_t days[12] = {31,28,31,30,31,30,31,31,30,31,30,31};
  if (m < 1 || m > 12) return 0;
  if (m == 2 && isLeapYear(y)) return 29;
  return days[m - 1];
}

// Converte o payload bruto da characteristic padrao "Current Time"
// (Bluetooth SIG, UUID 0x2A2B) para um timestamp UTC em segundos desde
// 1970-01-01 (epoch), fazendo tambem validacao dos campos recebidos.
// Formato dos 10 bytes: ano (2 bytes little-endian), mes, dia, hora,
// minuto, segundo, dia-da-semana, sub-segundo, motivo-de-ajuste.
// O calculo do numero de dias usa o algoritmo classico de Howard
// Hinnant (baseado em "eras" de 400 anos) para converter uma data do
// calendario gregoriano em dias desde a epoch, sem depender de
// bibliotecas de data/hora do sistema.
bool ctsToEpochUtc(const uint8_t *data, uint16_t len, uint32_t &outEpoch) {
  if (len != 10 || data == nullptr) return false;

  const uint16_t year = (uint16_t)data[0] | ((uint16_t)data[1] << 8);
  const uint8_t month = data[2];
  const uint8_t day = data[3];
  const uint8_t hour = data[4];
  const uint8_t minute = data[5];
  const uint8_t second = data[6];

  if (year < 1970U || year > 2099U) return false;
  if (month < 1U || month > 12U) return false;
  if (day < 1U || day > daysInMonth(year, month)) return false;
  if (hour > 23U || minute > 59U || second > 59U) return false;

  int y = (int)year;
  const unsigned m = (unsigned)month;
  const unsigned d = (unsigned)day;
  y -= (m <= 2U);
  const int era = (y >= 0) ? (y / 400) : ((y - 399) / 400);
  const unsigned yoe = (unsigned)(y - era * 400); // [0, 399]
  const int mp = (int)m + ((m > 2U) ? -3 : 9);
  const unsigned doy = (153U * (unsigned)mp + 2U) / 5U + d - 1U;
  const unsigned doe = yoe * 365U + yoe / 4U - yoe / 100U + doy;
  const int64_t days = (int64_t)era * 146097LL + (int64_t)doe - 719468LL;
  if (days < 0) return false;

  const uint64_t sec =
      (uint64_t)days * 86400ULL + (uint64_t)hour * 3600ULL +
      (uint64_t)minute * 60ULL + (uint64_t)second;
  if (sec == 0ULL || sec > 0xFFFFFFFFULL) return false;

  outEpoch = (uint32_t)sec;
  return true;
}

// Copia a chave AES recebida (de BLE ou de flash) para o buffer em RAM
// usado pelo resto do firmware, garantindo que bytes nao usados ficam a
// zero (por exemplo se a chave for mais curta que AES_KEY_MAX_LEN).
void cacheAesKey(const uint8_t *key, size_t len) {
  if (len > AES_KEY_MAX_LEN) len = AES_KEY_MAX_LEN;
  memset(s_aesKey, 0, sizeof(s_aesKey));
  memcpy(s_aesKey, key, len);
}

// Converte um registo generico do ring buffer QSPI (formato interno,
// com "type" e "payload" opacos) para o formato FullPlain especifico
// de IMU+PPG usado pelo BLE. Rejeita registos de outro tipo ou com
// tamanho insuficiente (protecao contra dados corrompidos/inesperados).
bool mapRingRecordToFull(const QspiRingBuffer::Record &rec, FullMappedRecord &out) {
  if (rec.type != kRecTypeImuPpgV1) {
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP][BUF] skip type=0x");
      Serial.println(rec.type, HEX);
    }
    return false;
  }

  if (rec.len < sizeof(ImuPpgPayloadV1)) {
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP][BUF] skip short-len=");
      Serial.print(rec.len);
      Serial.print(" expected>=");
      Serial.println(sizeof(ImuPpgPayloadV1));
    }
    return false;
  }

  const ImuPpgPayloadV1 *p = reinterpret_cast<const ImuPpgPayloadV1 *>(rec.payload);

  out.rec_seq = rec.seq;
  out.payload.ts = rec.timestamp;
  out.payload.ax = p->ax;
  out.payload.ay = p->ay;
  out.payload.az = p->az;
  out.payload.gx = p->gx;
  out.payload.gy = p->gy;
  out.payload.gz = p->gz;
  out.payload.steps = p->steps;
  out.payload.ff = p->ff ? 1 : 0;
  out.payload.inact = p->inact ? 1 : 0;
  out.payload.spo2 = p->spo2;
  out.payload.hr = p->hr;

  if (kGattDumpVerboseLogs) {
    Serial.print("[BLEG][DUMP][MAP] seq=");
    Serial.print(out.rec_seq);
    Serial.print(" ts=");
    Serial.print(out.payload.ts);
    Serial.print(" a[g]=");
    Serial.print(out.payload.ax, 3);
    Serial.print(",");
    Serial.print(out.payload.ay, 3);
    Serial.print(",");
    Serial.print(out.payload.az, 3);
    Serial.print(" g[dps]=");
    Serial.print(out.payload.gx, 2);
    Serial.print(",");
    Serial.print(out.payload.gy, 2);
    Serial.print(",");
    Serial.print(out.payload.gz, 2);
    Serial.print(" steps=");
    Serial.print(out.payload.steps);
    Serial.print(" ff=");
    Serial.print(out.payload.ff ? 1 : 0);
    Serial.print(" inact=");
    Serial.print(out.payload.inact ? 1 : 0);
    Serial.print(" spo2=");
    Serial.print(out.payload.spo2);
    Serial.print(" hr=");
    Serial.println(out.payload.hr);
  }

  return true;
}

// Tenta obter (sem remover ainda) o proximo registo IMU+PPG valido do
// ring buffer, saltando ate 4 entradas invalidas/de outro tipo — estas
// sao removidas (pop) para nao bloquear o dump indefinidamente num
// registo que nunca vai passar na validacao de mapRingRecordToFull.
bool peekImuPpgRecord(FullMappedRecord &out) {
  QspiRingBuffer::Record rec{};
  for (int i = 0; i < 4; i++) {
    if (!QspiRingBuffer::peek(rec)) return false;

    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP][BUF] peek seq=");
      Serial.print(rec.seq);
      Serial.print(" ts=");
      Serial.print(rec.timestamp);
      Serial.print(" type=0x");
      Serial.print(rec.type, HEX);
      Serial.print(" len=");
      Serial.println(rec.len);
    }

    if (mapRingRecordToFull(rec, out)) {
      return true;
    }

    // Remove entradas antigas/invalidas para nao bloquear o dump.
    QspiRingBuffer::Record discard{};
    if (!QspiRingBuffer::pop(discard)) return false;
  }
  return false;
}

// Le (peek, sem remover) o proximo registo do ring buffer e guarda-o
// como "pendente", para so ser removido do buffer depois de confirmado
// o envio bem-sucedido (ver sendDumpPendingRecord + o pop no chamador).
bool prepareDumpPendingRecord() {
  FullMappedRecord mapped{};
  if (!peekImuPpgRecord(mapped)) return false;

  s_dumpPendingSeq = mapped.rec_seq;
  s_dumpPendingSample = mapped.payload;
  s_dumpPendingValid = true;
  return true;
}

// Envia o registo pendente atual (s_dumpPendingSample) por BLE,
// fragmentado em varios pacotes DumpDataPacket porque o registo (38
// bytes) normalmente nao cabe inteiro num unico payload de notify().
// Se qualquer fragmento falhar a enviar (ex.: fila de notificacoes
// cheia, desconexao a meio), aborta e devolve false — o registo
// continua "pendente" e sera reenviado na proxima iteracao.
bool sendDumpPendingRecord() {
  if (!s_dumpPendingValid) return false;
  if (Bluefruit.connected() == 0) return false;

  const uint8_t *sample = reinterpret_cast<const uint8_t *>(&s_dumpPendingSample);
  constexpr size_t kSampleLen = sizeof(FullPlain);
  const uint8_t fragTotal = (uint8_t)((kSampleLen + kGattDumpChunkLen - 1) / kGattDumpChunkLen);

  if (kGattDumpVerboseLogs) {
    Serial.print("[BLEG][TX] rec_seq=");
    Serial.print(s_dumpPendingSeq);
    Serial.print(" frags=");
    Serial.println((int)fragTotal);
  }

  for (uint8_t fragIdx = 0; fragIdx < fragTotal; fragIdx++) {
    const size_t offset = (size_t)fragIdx * kGattDumpChunkLen;
    const size_t remain = kSampleLen - offset;
    const uint8_t chunkLen = (uint8_t)((remain > kGattDumpChunkLen) ? kGattDumpChunkLen : remain);

    DumpDataPacket pkt{};
    pkt.type = kDumpDataType;
    pkt.frag_idx = fragIdx;
    pkt.frag_total = fragTotal;
    pkt.chunk_len = chunkLen;
    pkt.rec_seq = s_dumpPendingSeq;
    memcpy(pkt.chunk, sample + offset, chunkLen);

    const uint8_t *rawPkt = reinterpret_cast<const uint8_t *>(&pkt);
    if (!dumpDataChar.notify(rawPkt, sizeof(pkt))) {
      Serial.print("[BLEG][TX] FAIL rec_seq=");
      Serial.print(s_dumpPendingSeq);
      Serial.print(" frag=");
      Serial.println((int)fragIdx + 1);
      return false;
    }
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][TX] rec_seq=");
      Serial.print(s_dumpPendingSeq);
      Serial.print(" frag=");
      Serial.print((int)fragIdx + 1);
      Serial.print("/");
      Serial.print((int)fragTotal);
      Serial.print(" chunk_len=");
      Serial.println((int)chunkLen);
    }
    if (kGattDumpInterPacketMs > 0) {
      vTaskDelay(pdMS_TO_TICKS(kGattDumpInterPacketMs));
    }
  }

  if (kGattDumpVerboseLogs) {
    Serial.print("[BLEG][TX] SENT rec_seq=");
    Serial.println(s_dumpPendingSeq);
  }
  return true;
}

// Tarefa FreeRTOS de fundo (baixa prioridade) que implementa a maquina
// de estados do streaming de dados por GATT. Corre indefinidamente e é
// pilotada por flags partilhadas (s_dumpStartRequested/StopRequested)
// escritas pelos callbacks BLE (dumpCtrlCallback, periphConnectCallback,
// periphDisconnectCallback). Em resumo, o ciclo é:
//   1) Esperar um pedido de start (comando da app ou auto-start ao
//      ligar, se s_dataModeEnabled) e uma ligacao BLE ativa.
//   2) Ao entrar em DUMP_STREAMING, aguardar por "janelas" periodicas
//      (kGattDumpWindowMs) e, em cada janela, tentar enviar ate
//      kWindowTargetRecords registos do ring buffer, um de cada vez,
//      confirmando (pop) cada um só depois de enviado com sucesso.
//   3) Se for pedida paragem, desconectar, ou o envio falhar, volta a
//      DUMP_IDLE e publica um pacote de estado com o motivo.
// Os logs (Serial) neste ciclo sao intencionalmente throttled (via
// lastWaitLogMs/lastIdleLogMs) para nao inundar a consola em cada
// iteracao do loop.
void gattDumpTask(void *arg) {
  (void)arg;
  Serial.println("[BLEG][DUMP] task started");
  uint32_t lastWindowMs = 0;
  uint32_t lastWaitLogMs = 0;
  uint32_t lastIdleLogMs = 0;

  while (true) {
    // Pedido de paragem (comando da app, ou desconexao) tem prioridade:
    // repoe tudo para o estado inativo antes de continuar o ciclo.
    if (s_dumpStopRequested) {
      s_dumpStopRequested = false;
      s_dumpStartRequested = false;
      s_dumpState = DUMP_IDLE;
      s_dumpPendingValid = false;
      s_dumpWindowImmediate = false;
      lastWindowMs = 0;
      lastWaitLogMs = 0;
      lastIdleLogMs = 0;
      publishDumpStatus(DUMP_IDLE, 5, 0);
      Serial.println("[BLEG][DUMP] stopped by command");
    }

    // Ainda sem pedido de start: fica em espera passiva, so acordando
    // periodicamente para verificar de novo (polling leve, 50ms).
    if (!s_dumpStartRequested) {
      const uint32_t now = millis();
      if (s_dataModeEnabled && (now - lastIdleLogMs) >= kGattDumpIdleLogMs) {
        lastIdleLogMs = now;
        Serial.print("[BLEG][DUMP] idle wait connection adv=");
        Serial.print(Bluefruit.Advertising.isRunning() ? "1" : "0");
        Serial.print(" connected=");
        Serial.println(Bluefruit.connected());
      }
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    // Foi pedido start mas ja nao ha nenhuma ligacao ativa (ex.: o
    // telemovel desligou-se entretanto): aborta e volta a ficar idle.
    if (Bluefruit.connected() == 0) {
      s_dumpStartRequested = false;
      s_dumpState = DUMP_IDLE;
      s_dumpPendingValid = false;
      s_dumpWindowImmediate = false;
      lastWindowMs = 0;
      lastWaitLogMs = 0;
      lastIdleLogMs = 0;
      publishDumpStatus(DUMP_IDLE, 7, 0);
      Serial.println("[BLEG][DUMP] aborted: disconnected");
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    if (s_dumpState == DUMP_IDLE) {
      s_dumpState = DUMP_STREAMING;
      lastWindowMs = millis();
      lastWaitLogMs = lastWindowMs;
      Serial.print("[BLEG][DUMP] timer armed, first window in ");
      Serial.print(kGattDumpWindowMs / 1000);
      Serial.println("s");
    }

    // Uma "janela" de envio so arranca quando o intervalo configurado
    // (kGattDumpWindowMs) tiver passado, ou se foi forcada
    // imediatamente (s_dumpWindowImmediate). Caso contrario, so espera.
    const uint32_t now = millis();
    const bool dueByImmediate = s_dumpWindowImmediate;
    const bool dueByInterval = (lastWindowMs != 0) && ((now - lastWindowMs) >= kGattDumpWindowMs);
    if (!dueByImmediate && !dueByInterval) {
      if ((now - lastWaitLogMs) >= kGattDumpWaitLogMs) {
        uint32_t elapsed = 0;
        if (lastWindowMs != 0) elapsed = now - lastWindowMs;
        const uint32_t remainMs = (elapsed < kGattDumpWindowMs) ? (kGattDumpWindowMs - elapsed) : 0;
        Serial.print("[BLEG][DUMP] wait next window in ");
        Serial.print(remainMs / 1000);
        Serial.print("s ring_count=");
        Serial.println(QspiRingBuffer::count());
        lastWaitLogMs = now;
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    s_dumpWindowImmediate = false;
    lastWindowMs = now;
    lastWaitLogMs = now;
    // Estes prints corriam sem controlo a cada janela (1x/segundo durante
    // qualquer transferencia ativa), competindo por CPU/USB com as tasks
    // de IMU/PPG em tempo real. Passam a seguir o mesmo interruptor
    // kGattDumpVerboseLogs usado no resto do ficheiro — false por defeito.
    const uint32_t ringBefore = QspiRingBuffer::count();
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP] window tick ms=");
      Serial.print(now);
      Serial.print(" ring_count_before=");
      Serial.println(ringBefore);
    }

    uint32_t targetRecords = ringBefore;
    if (targetRecords > kWindowTargetRecords) {
      targetRecords = kWindowTargetRecords;
    }
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP] target_records=");
      Serial.print(targetRecords);
      Serial.print(" (52Hz x 1s = ");
      Serial.print(kWindowTargetRecords);
      Serial.println(")");
    }

    if (targetRecords == 0) {
      publishDumpStatus(DUMP_STREAMING, 4, 0);
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

    // Envia ate targetRecords registos nesta janela, um de cada vez:
    // le (peek) -> envia (notify fragmentado) -> so entao remove (pop)
    // do ring buffer. Isto garante que um registo nunca é perdido caso
    // o envio falhe a meio (fica pendente para a proxima tentativa).
    uint32_t sentInWindow = 0;
    for (uint32_t i = 0; i < targetRecords; i++) {
      if (s_dumpStopRequested || Bluefruit.connected() == 0) {
        publishDumpStatus(DUMP_STREAMING, 7, s_dumpPendingSeq);
        break;
      }

      if (!s_dumpPendingValid) {
        if (!prepareDumpPendingRecord()) {
          publishDumpStatus(DUMP_STREAMING, 4, 0);
          break;
        }
      }

      if (!sendDumpPendingRecord()) {
        publishDumpStatus(DUMP_STREAMING, 6, s_dumpPendingSeq);
        break;
      }

      QspiRingBuffer::Record discard{};
      if (!QspiRingBuffer::pop(discard)) {
        publishDumpStatus(DUMP_STREAMING, 8, s_dumpPendingSeq);
        break;
      }
      s_dumpSentRecords++;
      s_dumpAckedRecords++;
      s_dumpPendingValid = false;
      sentInWindow++;

      if ((sentInWindow % kDumpStatusEveryRecords) == 0) {
        publishDumpStatus(DUMP_STREAMING, 2, discard.seq);
      }

      // Cede o processador cooperativamente de vez em quando (yield),
      // para nao monopolizar o CPU e deixar outras tarefas correrem,
      // mesmo quando nao ha atraso configurado entre registos.
      if (kGattDumpInterRecordMs > 0) {
        vTaskDelay(pdMS_TO_TICKS(kGattDumpInterRecordMs));
      } else if ((sentInWindow % 64U) == 0U) {
        vTaskDelay(0);

      // else if ((sentInWindow % kGattDumpCoopEveryRecords) == 0U) {
      //   if (kGattDumpCoopDelayMs > 0) {
      //     vTaskDelay(pdMS_TO_TICKS(kGattDumpCoopDelayMs));
      //   } else {
      //     vTaskDelay(0);
      //   }
      }
    }

    if (sentInWindow > 0 && Bluefruit.connected() > 0) {
      publishDumpStatus(DUMP_STREAMING, 2, s_dumpPendingSeq);
    }
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP] window sent=");
      Serial.print(sentInWindow);
      Serial.print(" ring_count_after=");
      Serial.println(QspiRingBuffer::count());
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

} // namespace

// ============================================================
// Callbacks BLE (correm no contexto/tarefa da stack Bluefruit sempre
// que o telemovel escreve numa characteristic ou liga/desliga)
// ============================================================

// Chamado quando a app escreve na characteristic aesKeyChar, isto é,
// quando envia a chave AES partilhada durante o "provisioning". So
// aceita a escrita uma unica vez por dispositivo: se ja existir uma
// chave guardada em flash, ignora silenciosamente novas escritas (para
// nao permitir que qualquer ligacao troque a chave depois de definida).
static void aesKeyCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                           uint8_t *data, uint16_t len) {
  (void)conn_hdl;
  (void)chr;

  if (Storage::hasAesKey()) {
    Serial.println("[BLE] AES already in flash, ignoring write");
    return;
  }

  if (len < AES_KEY_MIN_LEN || len > AES_KEY_MAX_LEN) {
    Serial.println("[BLE] AES key invalid length");
    return;
  }

  if (!Storage::saveAesKey(data, len)) {
    Serial.println("[BLE] failed to save AES key");
    return;
  }

  cacheAesKey(data, len);
  s_aesArrived = true;
  Serial.println("[BLE] AES key received and stored");
}

// Chamado quando a app escreve na characteristic padrao "Current Time"
// (0x2A2B), tipicamente logo apos a ligacao, para sincronizar a hora do
// dispositivo com a do telemovel. Valida e converte o payload para
// epoch UTC e publica o resultado no modulo Clock.
static void timestampCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                              uint8_t *data, uint16_t len) {
  (void)conn_hdl;
  (void)chr;

  if (len != 10) {
    Serial.print("[BLE] invalid current-time len: ");
    Serial.println(len);
    return;
  }

  uint32_t ts = 0;
  if (!ctsToEpochUtc(data, len, ts)) {
    Serial.println("[BLE] invalid current-time payload");
    return;
  }

  if (ts == 0) {
    Serial.println("[BLE] invalid current-time value: 0");
    return;
  }

  s_timestamp = ts;
  s_timestampArrived = true;
  Clock::setUtc(s_timestamp);
  Serial.print("[BLE] timestamp received: ");
  Serial.println(s_timestamp);
}

// Chamado quando a app escreve na characteristic dumpCtrlChar para
// pedir explicitamente o inicio (kDumpCtrlStart) ou a paragem
// (kDumpCtrlStop) do streaming de dados dos sensores. So tem efeito
// quando o dispositivo ja esta em modo de dados (s_dataModeEnabled),
// isto é, depois de startBroadcast() ter sido chamado.
static void dumpCtrlCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                             uint8_t *data, uint16_t len) {
  (void)chr;
  if (len < 1 || data == nullptr) return;
  if (!s_dataModeEnabled) return;

  const uint8_t cmd = data[0];
  if (cmd == kDumpCtrlStart) {
    (void)conn_hdl;
    s_dumpStartRequested = true;
    s_dumpStopRequested = false;
    s_dumpPendingValid = false;
    s_dumpWindowImmediate = false;
    s_dumpState = DUMP_IDLE;
    s_dumpSentRecords = 0;
    s_dumpAckedRecords = 0;
    Serial.println("[BLEG][DUMP] START");
    publishDumpStatus(DUMP_STREAMING, 1, 0);
    return;
  }

  if (cmd == kDumpCtrlStop) {
    s_dumpStopRequested = true;
    Serial.println("[BLEG][DUMP] STOP");
    return;
  }

  if (cmd == kDumpCtrlForceHr) {
    // Um unico comando/botao pede as duas leituras "agora": a FC fica
    // em streaming forcado durante `seconds` (pode demorar alguns
    // segundos a estabilizar um valor fiavel) e o SpO2 e' medido de
    // imediato na proxima iteracao da task (medicao unica, ~seg a mais).
    uint16_t seconds = kForceHrDefaultSeconds;
    if (len >= 3) {
      seconds = static_cast<uint16_t>(data[1]) | (static_cast<uint16_t>(data[2]) << 8);
    }
    Ppg::requestManualHr(static_cast<uint32_t>(seconds) * 1000UL);
    Ppg::requestManualSpo2();
    Serial.print("[BLEG][DUMP] FORCE_HR+SPO2 segundos=");
    Serial.println(seconds);
    return;
  }

  if (cmd == kDumpCtrlResetReadings) {
    // *** DESTRUTIVO E IRREVERSIVEL *** — ver aviso junto de
    // kDumpCtrlResetReadings. Apaga apenas os registos de leituras
    // (ring buffer); calibracao do IMU e chave AES ficam intactas.
    //
    // AVISO DE CONCORRENCIA: gattDumpTask (le/remove) e storageTask em
    // main.cpp (escreve) tambem acedem ao ring buffer. Pedir a paragem
    // do streaming e esperar um pouco reduz a janela de corrida com o
    // leitor, mas nao elimina a corrida com quem escreve — uma correcao
    // completa exigiria sincronizacao (mutex/secao critica) dentro do
    // proprio QspiRingBuffer, fora do ambito deste comando pontual.
    s_dumpStopRequested = true;
    vTaskDelay(pdMS_TO_TICKS(100));
    const bool ok = QspiRingBuffer::format();
    Serial.print("[BLEG][DUMP] RESET_READINGS ok=");
    Serial.println(ok ? "1" : "0");
    return;
  }
}

// Chamado pela stack Bluefruit sempre que um central (telemovel) se
// liga ao dispositivo. Se estivermos em modo de dados, o streaming
// arranca automaticamente ao ligar (nao é preciso a app enviar o
// comando de start explicitamente); durante o provisioning nao ha nada
// a fazer aqui alem de registar a ligacao.
static void periphConnectCallback(uint16_t conn_hdl) {
  Serial.print("[BLE] connected conn_hdl=");
  Serial.println(conn_hdl);

  if (!s_dataModeEnabled) {
    Serial.println("[BLE] provisioning link");
    return;
  }

  s_dumpStartRequested = true; // automatico
  s_dumpStopRequested = false;
  s_dumpPendingValid = false;
  s_dumpWindowImmediate = false;
  s_dumpState = DUMP_IDLE;
  Serial.println("[BLEG][DUMP] auto START");
  publishDumpStatus(DUMP_STREAMING, 1, 0);
}

// Chamado pela stack Bluefruit quando a ligacao ao central é perdida
// (app fechou, saiu de alcance, etc.). Repoe imediatamente o estado do
// streaming para inativo, para nao continuar "a pensar" que esta a
// enviar dados sem ninguem do outro lado.
static void periphDisconnectCallback(uint16_t conn_hdl, uint8_t reason) {
  (void)conn_hdl;
  Serial.print("[BLE] disconnected reason=0x");
  Serial.println(reason, HEX);
  s_dumpStartRequested = false;
  s_dumpStopRequested = false;
  s_dumpPendingValid = false;
  s_dumpWindowImmediate = false;
  s_dumpState = DUMP_IDLE;
}

namespace Ble {

// Ver documentacao completa em Ble.h. Aqui a implementacao segue,
// passo a passo, a ordem: registar callbacks de ligacao -> criar o
// servico/characteristics do wearable -> criar o servico padrao de
// hora -> configurar e arrancar o advertising de provisioning -> criar
// a tarefa de streaming (ainda inativa nesta fase).
bool begin() {
  Clock::begin();
  Serial.print("[BLE] build tag: ");
  Serial.println(kBleBuildTag);

  // Estes callbacks disparam sempre que um telemovel se liga/desliga,
  // independentemente do modo (provisioning ou dados).
  Bluefruit.Periph.setConnectCallback(periphConnectCallback);
  Bluefruit.Periph.setDisconnectCallback(periphDisconnectCallback);

  wearableService.begin();

  // Characteristic de escrita para a app enviar a chave AES. Permissao
  // SECMODE_OPEN (sem exigir pairing/bonding BLE) porque a protecao
  // real esta na logica: so aceita a primeira escrita (ver
  // aesKeyCallback), guardando-a de imediato em flash.
  aesKeyChar.setProperties(CHR_PROPS_WRITE);
  aesKeyChar.setPermission(SECMODE_OPEN, SECMODE_OPEN);
  aesKeyChar.setMaxLen(AES_KEY_MAX_LEN);
  aesKeyChar.setWriteCallback(aesKeyCallback);
  aesKeyChar.begin();

  // Characteristic de controlo (start/stop) do streaming; aceita
  // escrita com e sem resposta (WRITE_WO_RESP) para reduzir latencia
  // do lado da app ao pedir o inicio da transmissao.
  dumpCtrlChar.setProperties(CHR_PROPS_WRITE | CHR_PROPS_WRITE_WO_RESP);
  dumpCtrlChar.setPermission(SECMODE_OPEN, SECMODE_OPEN);
  dumpCtrlChar.setMaxLen(8);
  dumpCtrlChar.setWriteCallback(dumpCtrlCallback);
  dumpCtrlChar.begin();

  // Characteristic apenas de notificacao/indicacao: o dispositivo
  // "empurra" os pacotes de dados para a app, que nunca escreve aqui
  // (por isso SECMODE_NO_ACCESS na escrita). Tamanho fixo porque todos
  // os pacotes DumpDataPacket tem o mesmo tamanho.
  dumpDataChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_INDICATE);
  dumpDataChar.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  dumpDataChar.setFixedLen(sizeof(DumpDataPacket));
  dumpDataChar.begin();

  // Characteristic de estado: pode ser lida sob pedido ou recebida via
  // notify sempre que o estado do streaming muda.
  dumpStatusChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  dumpStatusChar.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  dumpStatusChar.setFixedLen(sizeof(DumpStatusPacket));
  dumpStatusChar.begin();

  // Characteristic dedicada a alertas de emergencia (SOS manual ou
  // queda+inatividade prolongada) — ver Emergency.h. So o dispositivo
  // escreve aqui (app nunca escreve, daí SECMODE_NO_ACCESS), e o valor
  // fica disponivel por leitura mesmo que nao haja ligacao ativa no
  // momento exato do alerta (a app pode ler ao reconectar-se).
  emergencyAlertChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  emergencyAlertChar.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  emergencyAlertChar.setFixedLen(sizeof(EmergencyAlertPacket));
  emergencyAlertChar.begin();

  // Servico/characteristic padrao do Bluetooth SIG para sincronizacao
  // de hora (0x2A2B). Usar o UUID standard permite, em teoria, que
  // qualquer app compativel com BLE "Current Time" consiga escrever
  // aqui, embora neste projeto seja a app dedicada que o faz.
  currentTimeService.begin();
  currentTimeChar.setProperties(CHR_PROPS_WRITE);
  currentTimeChar.setPermission(SECMODE_OPEN, SECMODE_OPEN);
  currentTimeChar.setMaxLen(10);
  currentTimeChar.setWriteCallback(timestampCallback);
  currentTimeChar.begin();

  // Advertising inicial ("provisioning"): anuncia os dois servicos
  // (wearable + current time) para que a app consiga encontrar e
  // ligar-se ao dispositivo antes de haver chave AES/hora definidas.
  // restartOnDisconnect(true) garante que volta a anunciar-se
  // automaticamente se a ligacao cair nesta fase.
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addService(wearableService);
  Bluefruit.Advertising.addService(currentTimeService);
  // O pacote de advertising principal (31 bytes) já vai cheio com os dois
  // UUIDs de serviço acima, por isso o nome ("Wearable", definido em
  // Bluefruit.setName() no main.cpp) vai no "scan response" — um segundo
  // pacote que apps como o nRF Connect também leem automaticamente ao
  // fazer scan. Sem isto, o dispositivo aparecia sem nome nas apps de
  // scan BLE, tornando-o impossível de identificar no meio de outros
  // dispositivos próximos.
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(160, 244);
  const bool provStartOk = Bluefruit.Advertising.start(0);
  Serial.print("[BLE] provisioning adv start=");
  Serial.println(provStartOk ? "OK" : "FAIL");
  Serial.print("[BLE] provisioning adv running=");
  Serial.println(Bluefruit.Advertising.isRunning() ? "1" : "0");

  // Cria a tarefa de streaming uma unica vez; ela fica em espera
  // passiva (DUMP_IDLE, sem pedido de start) ate startBroadcast() ser
  // chamado mais tarde e uma ligacao ser estabelecida em modo de dados.
  if (s_dumpTaskHandle == nullptr) {
    BaseType_t ok = xTaskCreate(
        gattDumpTask,
        "ble_gatt_dump_task",
        kGattDumpTaskStackWords,
        nullptr,
        TASK_PRIO_LOW,
        &s_dumpTaskHandle);
    if (ok != pdPASS) {
      s_dumpTaskHandle = nullptr;
      Serial.println("[BLEG][DUMP] failed to create task");
    }
  }

  Serial.println("[BLE] provisioning service active");
  return true;
}

bool ensureAesKey() {
  // Caminho rapido: ja existe uma chave persistida em flash de um
  // provisioning anterior — nao é preciso esperar por BLE outra vez.
  if (Storage::hasAesKey()) {
    uint8_t buf[AES_KEY_MAX_LEN] = {0};
    size_t n = 0;
    if (Storage::loadAesKey(buf, sizeof(buf), n)) {
      cacheAesKey(buf, n);
      Serial.println("[BLE] AES key loaded from flash");
      uiMessage("AES key", "recebida");
      delay(1200);
      return true;
    }
  }

  // Sem chave em flash: bloqueia aqui (busy-wait com delay) ate a app
  // se ligar e escrever a chave na characteristic aesKeyChar — o
  // aesKeyCallback (registado em begin()) e quem marca s_aesArrived.
  Serial.println("[BLE] waiting for AES key via BLE...");
  uiMessage("Receber", "AES key");
  uint32_t lastLog = 0;

  while (!s_aesArrived) {
    const uint32_t now = millis();
    if ((now - lastLog) >= kBleProvisionWaitLogMs) {
      lastLog = now;
      Serial.print("[BLE] wait AES... adv=");
      Serial.print(Bluefruit.Advertising.isRunning() ? "1" : "0");
      Serial.print(" connected=");
      Serial.println(Bluefruit.connected());
    }
    delay(100);
  }

  uiMessage("AES key", "recebida");
  delay(1200);
  return true;
}

bool ensureTimeSync() {
  // Forca sync novo nesta fase de arranque: mesmo que ja tenha havido
  // um timestamp anterior (de um arranque passado), este é descartado
  // para garantir que ficamos com a hora atual e nao uma desatualizada.
  s_timestampArrived = false;
  s_timestamp = 0;
  Clock::invalidate();

  // Bloqueia ate a app se ligar e escrever a hora atual na
  // characteristic "Current Time" — timestampCallback marca
  // s_timestampArrived quando isso acontece.
  Serial.println("[BLE] waiting for Current Time (0x2A2B) via BLE...");
  uiMessage("Pedir", "Hora e Data");
  uint32_t lastLog = 0;

  while (!s_timestampArrived) {
    const uint32_t now = millis();
    if ((now - lastLog) >= kBleProvisionWaitLogMs) {
      lastLog = now;
      Serial.print("[BLE] wait TIME... adv=");
      Serial.print(Bluefruit.Advertising.isRunning() ? "1" : "0");
      Serial.print(" connected=");
      Serial.println(Bluefruit.connected());
    }
    delay(100);
  }

  // A partir daqui comeca a transicao do modo "provisioning" para o
  // "modo de dados": ja temos chave AES e hora sincronizada, por isso
  // fechamos as ligacoes atuais e paramos este advertising, para que
  // startBroadcast() (chamado depois pelo main.cpp) possa arrancar um
  // advertising limpo e dedicado ao modo de dados.
  //
  // Congela auto-restart do advertising de provisioning antes da transicao.
  Bluefruit.Advertising.restartOnDisconnect(false);

  // Fecha ligacoes BLE do provisioning para libertar stack/roles
  // antes de entrar no modo de dados por GATT.
  uint16_t handles[8] = {0};
  const uint8_t connCount = Bluefruit.getConnectedHandles(handles, 8);
  if (connCount > 0) {
    for (uint8_t i = 0; i < connCount; i++) {
      Bluefruit.disconnect(handles[i]);
    }
    Serial.print("[BLE] disconnected centrals after time sync: ");
    Serial.println(connCount);
    // Espera evento real de disconnect no stack.
    const uint32_t t0 = millis();
    while (Bluefruit.connected() > 0 && (millis() - t0) < 3000) {
      delay(20);
    }
    Serial.print("[BLE] connected after wait: ");
    Serial.println(Bluefruit.connected());
  }

  // Para explicitamente o advertising de provisioning.
  const bool stopOk = Bluefruit.Advertising.stop();
  Serial.print("[BLE] provisioning adv stop=");
  Serial.println(stopOk ? "OK" : "FAIL");
  delay(100);
  Serial.print("[BLE] provisioning adv running=");
  Serial.println(Bluefruit.Advertising.isRunning() ? "1" : "0");

  uiMessage("Hora e Data", "recebida");
  delay(1000);
  return true;
}

uint32_t timestamp() {
  return s_timestamp;
}

bool hasTimestamp() {
  return s_timestampArrived;
}

bool startBroadcast() {
  // Modo apenas GATT: sem broadcast de manufacturer data. Reconstroi o
  // advertising do zero (stop + clearData) para garantir que nao
  // sobram dados/servicos configurados na fase de provisioning.
  (void)Bluefruit.Advertising.stop();
  delay(30);
  Bluefruit.Advertising.clearData();
  Bluefruit.ScanResponse.clearData();
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addService(wearableService);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(160, 244);
  if (!Bluefruit.Advertising.start(0)) {
    s_dataModeEnabled = false;
    Serial.println("[BLE] failed to start GATT advertising");
    return false;
  }

  s_dataModeEnabled = true;
  Serial.print("[BLE] GATT-only mode active (auto dump ON CONNECT, window=");
  Serial.print(kGattDumpWindowMs / 1000);
  Serial.print("s, target=");
  Serial.print(kWindowTargetRecords);
  Serial.println(" rec/window)");
  Serial.print("[BLE] advRunning=");
  Serial.print(Bluefruit.Advertising.isRunning() ? "1" : "0");
  Serial.print(" connected=");
  Serial.print(Bluefruit.connected());
  Serial.print(" dumpTask=");
  Serial.println((s_dumpTaskHandle != nullptr) ? "1" : "0");
  return true;
}

void stopBroadcast() {
  // Sinaliza a gattDumpTask para parar (via s_dumpStopRequested) e para
  // o advertising do modo de dados. Note que s_dumpState so é reposto
  // aqui de forma otimista; a tarefa tambem o repoe ao processar o
  // pedido de stop, para lidar com corridas entre esta chamada e o loop.
  s_dataModeEnabled = false;
  s_dumpStartRequested = false;
  s_dumpStopRequested = true;
  s_dumpPendingValid = false;
  s_dumpState = DUMP_IDLE;
  (void)Bluefruit.Advertising.stop();
  Serial.println("[BLE] GATT adv stopped");
}

bool isBroadcastActive() {
  return s_dataModeEnabled && Bluefruit.Advertising.isRunning();
}

// *** DIAGNOSTICO TEMPORARIO (otimizacao de RAM) *** — ver Ble.h.
uint32_t dumpTaskStackHighWaterMarkWords() {
  if (s_dumpTaskHandle == nullptr) return 0;
  return static_cast<uint32_t>(uxTaskGetStackHighWaterMark(s_dumpTaskHandle));
}

void notifyEmergencyAlert(uint8_t alertType, uint32_t timestampUtc) {
  EmergencyAlertPacket pkt{};
  pkt.type = alertType;
  pkt.reserved = 0;
  pkt.seq = ++s_emergencyAlertSeq;
  pkt.timestamp_utc = timestampUtc;

  emergencyAlertChar.write(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  if (Bluefruit.connected() > 0) {
    (void)emergencyAlertChar.notify(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  }

  Serial.print("[BLE] alerta de emergencia enviado, tipo=");
  Serial.print(alertType);
  Serial.print(" seq=");
  Serial.println(pkt.seq);
}

} // namespace Ble
