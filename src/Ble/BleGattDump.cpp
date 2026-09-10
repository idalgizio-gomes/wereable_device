// BleGattDump.cpp - "modo de dados": cifra AES-CTR, nonces persistentes, tarefa FreeRTOS de streaming GATT. Estado partilhado com Ble.cpp em BleInternal.h.
#include "BleInternal.h"

#include "Storage/Storage.h"
#include "QspiRingBuffer/QspiRingBuffer.h"
#include "Ppg/Ppg.h"
#include "ImuPpgPayload.h"

#include <rtos.h>

#include <AES.h>
#include <CTR.h>

// estado partilhado (ver BleInternal.h), lido/escrito tambem pelos callbacks BLE em Ble.cpp
volatile DumpState s_dumpState = DUMP_IDLE;
volatile bool s_dumpStartRequested = false;
volatile bool s_dumpStopRequested = false;
volatile bool s_dumpPendingValid = false;
volatile bool s_dumpWindowImmediate = false;
volatile uint32_t s_dumpSentRecords = 0;
volatile uint32_t s_dumpAckedRecords = 0;
TaskHandle_t s_dumpTaskHandle = nullptr;

namespace {

static uint8_t s_aesKey[AES_KEY_MAX_LEN] = {0};
static size_t s_aesKeyLen = 0; // 16, 24 ou 32 (AES-128/192/256)

// registo lido do ring buffer mas ainda nao confirmado enviado (s_dumpPendingValid e' extern, ver BleInternal.h)
static uint32_t s_dumpPendingSeq = 0;
static FullPlain s_dumpPendingSample = {};
static uint32_t s_dumpPendingNonce = 0;

// nonces alocados em RAM por lotes: 1 escrita de flash a cada kNonceBatchSize registos, em vez de 1 por registo (~52/seg esgotaria a flash em dias); resto do lote perde-se se desligar a meio, o que e' seguro (nunca repetir nonce > aproveitar todo o lote)
constexpr uint64_t kNonceBatchSize = 65536; // ~21 min de streaming continuo a 52Hz por escrita de flash
static uint64_t s_nonceNext = 0;
static uint64_t s_nonceReservedUntil = 0;
static bool s_nonceBatchInitialized = false;

// so os 32 bits baixos do contador (64 bits) viajam no pacote -> a 52 reg/seg esgota em ~2.6 anos; ao esgotar, para o streaming em vez de repetir nonce (quebra de seguranca do CTR)
constexpr uint64_t kMaxNonceValue = 0xFFFFFFFFULL;
static bool s_nonceKeyExhaustedWarned = false;
static bool s_nonceCounterCorruptWarned = false;

void warnNonceExhausted() {
  if (s_nonceKeyExhaustedWarned) return;
  s_nonceKeyExhaustedWarned = true;
  Serial.println("[BLEG] AVISO CRITICO: espaco de nonces AES-CTR desta chave "
                  "esgotado (2^32 registos cifrados) - streaming de dados "
                  "parado para nunca reutilizar um nonce com a mesma chave. "
                  "Reprovisionar o dispositivo com uma chave AES nova "
                  "(requer apagar a flash interna, ver aesKeyCallback) para "
                  "retomar o streaming.");
}

// reserva (1 escrita de flash) o proximo lote de kNonceBatchSize nonces
bool reserveNonceBatch() {
  uint64_t current = 0;
  bool corrupted = false;
  Storage::counter_load(current, &corrupted);
  // ficheiro corrompido falha fechado (nao reutiliza nonces); ficheiro ausente (1o arranque) comeca do zero
  if (corrupted) {
    if (!s_nonceCounterCorruptWarned) {
      s_nonceCounterCorruptWarned = true;
      Serial.println("[BLEG] AVISO CRITICO: contador persistente de nonce AES-CTR "
                      "corrompido (magic/checksum invalido) - streaming de dados "
                      "parado para nunca arriscar reutilizar um nonce com a mesma "
                      "chave. Reprovisionar o dispositivo com uma chave AES nova "
                      "para retomar o streaming.");
    }
    return false;
  }
  if (current > kMaxNonceValue) {
    warnNonceExhausted();
    return false;
  }
  const uint64_t newBoundary = current + kNonceBatchSize;
  if (!Storage::counter_save(newBoundary)) return false;
  s_nonceNext = current;
  s_nonceReservedUntil = newBoundary;
  s_nonceBatchInitialized = true;
  return true;
}

// proximo nonce nunca usado; so toca a flash quando o lote esgota
bool allocateNonce(uint64_t &outNonce) {
  if (!s_nonceBatchInitialized || s_nonceNext >= s_nonceReservedUntil) {
    if (!reserveNonceBatch()) return false;
  }
  if (s_nonceNext > kMaxNonceValue) {
    warnNonceExhausted();
    return false;
  }
  outNonce = s_nonceNext;
  s_nonceNext++;
  return true;
}

// CTR (nao CBC/GCM): sem padding, decifra fragmento a fragmento. IV = [nonce 32-bit BE | 0x00000000 | contador de bloco 8 bytes], setCounterSize(8)
bool encryptRecord(uint32_t nonce, const uint8_t *plain, uint8_t *cipher, size_t len) {
  uint8_t iv[16] = {0};
  iv[0] = (uint8_t)(nonce >> 24);
  iv[1] = (uint8_t)(nonce >> 16);
  iv[2] = (uint8_t)(nonce >> 8);
  iv[3] = (uint8_t)(nonce);
  constexpr size_t kCounterSizeBytes = 8;

  if (s_aesKeyLen == 16) {
    CTR<AES128> ctr;
    if (!ctr.setKey(s_aesKey, 16)) return false;
    if (!ctr.setIV(iv, sizeof(iv))) return false;
    ctr.setCounterSize(kCounterSizeBytes);
    ctr.encrypt(cipher, plain, len);
    return true;
  }
  if (s_aesKeyLen == 24) {
    CTR<AES192> ctr;
    if (!ctr.setKey(s_aesKey, 24)) return false;
    if (!ctr.setIV(iv, sizeof(iv))) return false;
    ctr.setCounterSize(kCounterSizeBytes);
    ctr.encrypt(cipher, plain, len);
    return true;
  }
  if (s_aesKeyLen == 32) {
    CTR<AES256> ctr;
    if (!ctr.setKey(s_aesKey, 32)) return false;
    if (!ctr.setIV(iv, sizeof(iv))) return false;
    ctr.setCounterSize(kCounterSizeBytes);
    ctr.encrypt(cipher, plain, len);
    return true;
  }
  Serial.print("[BLEG] encryptRecord: comprimento de chave AES invalido: ");
  Serial.println((unsigned)s_aesKeyLen);
  return false;
}

// registo generico do ring buffer -> FullPlain; rejeita tipo/tamanho errados
bool mapRingRecordToFull(const QspiRingBuffer::Record &rec, FullMappedRecord &out) {
  if (rec.type != kImuPpgRecordTypeV1) {
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

  const ImuPpgPayloadV1 *src = reinterpret_cast<const ImuPpgPayloadV1 *>(rec.payload);

  out.rec_seq = rec.seq;
  out.payload.ts = rec.timestamp;
  out.payload.ax = src->ax;
  out.payload.ay = src->ay;
  out.payload.az = src->az;
  out.payload.gx = src->gx;
  out.payload.gy = src->gy;
  out.payload.gz = src->gz;
  out.payload.steps = src->steps;
  out.payload.ff = src->ff ? 1 : 0;
  out.payload.inact = src->inact ? 1 : 0;
  out.payload.spo2 = src->spo2;
  out.payload.hr = src->hr;
  out.payload.pacing_index = src->pacing_index;

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

// proximo registo IMU+PPG valido (sem remover), salta ate 4 entradas invalidas
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

    if (!QspiRingBuffer::advanceTail()) return false;
  }
  return false;
}

// peek + guarda como "pendente"; so removido do buffer apos envio confirmado
bool prepareDumpPendingRecord() {
  FullMappedRecord mapped{};
  if (!peekImuPpgRecord(mapped)) return false;

  uint64_t nonce64 = 0;
  if (!allocateNonce(nonce64)) {
    Serial.println("[BLEG][DUMP] falha ao reservar lote de nonces (Storage::counter_save) — adia registo");
    return false;
  }

  s_dumpPendingSeq = mapped.rec_seq;
  s_dumpPendingSample = mapped.payload;
  s_dumpPendingNonce = (uint32_t)(nonce64 & 0xFFFFFFFFULL);
  s_dumpPendingValid = true;
  return true;
}

// envia o registo pendente cifrado, fragmentado em pacotes DumpDataPacket; se um fragmento falhar, aborta e fica pendente para reenvio
bool sendDumpPendingRecord() {
  if (!s_dumpPendingValid) return false;
  if (Bluefruit.connected() == 0) return false;

  const uint8_t *sample = reinterpret_cast<const uint8_t *>(&s_dumpPendingSample);
  constexpr size_t kSampleLen = sizeof(FullPlain);

  uint8_t cipherBuf[kSampleLen];
  if (!encryptRecord(s_dumpPendingNonce, sample, cipherBuf, kSampleLen)) {
    Serial.print("[BLEG][TX] FAIL cifra rec_seq=");
    Serial.println(s_dumpPendingSeq);
    return false;
  }

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
    pkt.nonce = s_dumpPendingNonce;
    memcpy(pkt.chunk, cipherBuf + offset, chunkLen);

    const uint8_t *rawPkt = reinterpret_cast<const uint8_t *>(&pkt);
    bool sent = dumpDataChar.notify(rawPkt, sizeof(pkt));
    uint8_t retries = 0;
    while (!sent && retries < kGattDumpTxMaxRetries) {
      vTaskDelay(pdMS_TO_TICKS(kGattDumpTxRetryDelayMs));
      sent = dumpDataChar.notify(rawPkt, sizeof(pkt));
      retries++;
    }
    if (!sent) {
      Serial.print("[BLEG][TX] FAIL rec_seq=");
      Serial.print(s_dumpPendingSeq);
      Serial.print(" frag=");
      Serial.print((int)fragIdx + 1);
      Serial.print(" apos ");
      Serial.print((int)retries);
      Serial.println(" retries");
      return false;
    }
    if (kGattDumpVerboseLogs && retries > 0) {
      Serial.print("[BLEG][TX] recuperado apos ");
      Serial.print((int)retries);
      Serial.print(" retries rec_seq=");
      Serial.println(s_dumpPendingSeq);
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

// "fotografia" do registo mais recente sem consumir o ring buffer, mesmo contador de nonce que o caminho historico; sem retries (proximo tick ~1s manda mais recente)
void sendLiveSnapshot() {
  if (Bluefruit.connected() == 0) return;

  QspiRingBuffer::Record rec{};
  if (!QspiRingBuffer::peekLatest(rec)) return; // buffer ainda vazio

  FullMappedRecord mapped{};
  if (!mapRingRecordToFull(rec, mapped)) return;

  // registo mais recente do ring buffer e' quase sempre so IMU; substitui hr/spo2 pela ultima leitura direta do Ppg
  Ppg::Metrics ppgLatest{};
  if (Ppg::getLatest(ppgLatest)) {
    if (ppgLatest.hr_valid) {
      long hrRounded = lroundf(ppgLatest.hr_bpm);
      if (hrRounded < INT16_MIN) hrRounded = INT16_MIN;
      if (hrRounded > INT16_MAX) hrRounded = INT16_MAX;
      mapped.payload.hr = (int16_t)hrRounded;
    }
    if (ppgLatest.spo2_valid) {
      long spo2Clamped = ppgLatest.spo2_value;
      if (spo2Clamped < INT16_MIN) spo2Clamped = INT16_MIN;
      if (spo2Clamped > INT16_MAX) spo2Clamped = INT16_MAX;
      mapped.payload.spo2 = (int16_t)spo2Clamped;
    }
  }

  uint64_t nonce64 = 0;
  if (!allocateNonce(nonce64)) return;
  const uint32_t nonce = (uint32_t)(nonce64 & 0xFFFFFFFFULL);

  const uint8_t *sample = reinterpret_cast<const uint8_t *>(&mapped.payload);
  constexpr size_t kSampleLen = sizeof(FullPlain);
  uint8_t cipherBuf[kSampleLen];
  if (!encryptRecord(nonce, sample, cipherBuf, kSampleLen)) return;

  const uint8_t fragTotal = (uint8_t)((kSampleLen + kGattDumpChunkLen - 1) / kGattDumpChunkLen);
  for (uint8_t fragIdx = 0; fragIdx < fragTotal; fragIdx++) {
    const size_t offset = (size_t)fragIdx * kGattDumpChunkLen;
    const size_t remain = kSampleLen - offset;
    const uint8_t chunkLen = (uint8_t)((remain > kGattDumpChunkLen) ? kGattDumpChunkLen : remain);

    DumpDataPacket pkt{};
    pkt.type = kDumpDataType;
    pkt.frag_idx = fragIdx;
    pkt.frag_total = fragTotal;
    pkt.chunk_len = chunkLen;
    pkt.rec_seq = mapped.rec_seq;
    pkt.nonce = nonce;
    memcpy(pkt.chunk, cipherBuf + offset, chunkLen);

    const uint8_t *rawPkt = reinterpret_cast<const uint8_t *>(&pkt);
    if (!liveSnapshotChar.notify(rawPkt, sizeof(pkt))) {
      if (kGattDumpVerboseLogs) {
        Serial.print("[BLEG][LIVE] FAIL frag=");
        Serial.print((int)fragIdx + 1);
        Serial.print("/");
        Serial.println((int)fragTotal);
      }
      return; // sem retry de proposito
    }
  }
  if (kGattDumpVerboseLogs) {
    Serial.print("[BLEG][LIVE] SENT rec_seq=");
    Serial.println(mapped.rec_seq);
  }
}

} // namespace

// copia a chave AES para o buffer em RAM da cifra (bytes nao usados a zero); chamada por Ble::ensureAesKey() e aesKeyCallback() em Ble.cpp
void cacheAesKey(const uint8_t *key, size_t len) {
  if (len > AES_KEY_MAX_LEN) len = AES_KEY_MAX_LEN;
  memset(s_aesKey, 0, sizeof(s_aesKey));
  memcpy(s_aesKey, key, len);
  s_aesKeyLen = len;
}

// monta e envia (write local + notify, se ligado) o estado do streaming; chamada tambem pelos callbacks BLE em Ble.cpp
void publishDumpStatus(uint8_t state, uint8_t reason, uint32_t seq) {
  DumpStatusPacket st{};
  st.type = kDumpStatusType;
  st.state = state;
  st.reason = reason;
  const uint32_t ringCountNow = QspiRingBuffer::count(); // reaproveitado abaixo para nao adquirir o mutex do ring buffer 2x
  if (QspiRingBuffer::droppedByErase() > 0) {
    st.data_loss_flag = 2; // já a substituir dados
  } else {
    const uint32_t cap = QspiRingBuffer::capacity();
    const bool nearFull = cap > 0 && (static_cast<float>(ringCountNow) / cap) >= QspiRingBuffer::kRingBufferNearFullThreshold;
    st.data_loss_flag = nearFull ? 1 : 0;
  }
  st.seq = seq;
  st.sent_records = s_dumpSentRecords;
  st.acked_records = s_dumpAckedRecords;
  st.ring_count = ringCountNow;

  dumpStatusChar.write(reinterpret_cast<const uint8_t *>(&st), sizeof(st));
  if (Bluefruit.connected() > 0) {
    (void)dumpStatusChar.notify(reinterpret_cast<const uint8_t *>(&st), sizeof(st));
  }
}

// tarefa FreeRTOS de fundo: maquina de estados do streaming GATT, pilotada por flags partilhadas (ver BleInternal.h) escritas pelos callbacks BLE
void gattDumpTask(void *arg) {
  (void)arg;
  Serial.println("[BLEG][DUMP] task started");
  uint32_t lastWindowMs = 0;
  uint32_t lastWaitLogMs = 0;
  uint32_t lastIdleLogMs = 0;

  while (true) {
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
    lastWaitLogMs = now;
    const uint32_t ringBefore = QspiRingBuffer::count();
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP] window tick ms=");
      Serial.print(now);
      Serial.print(" ring_count_before=");
      Serial.println(ringBefore);
    }

    const uint32_t windowRecordCap = (ringBefore > kCatchUpBacklogRecords)
        ? kWindowCatchUpTargetRecords
        : kWindowTargetRecords;
    const bool inCatchUp = (windowRecordCap == kWindowCatchUpTargetRecords);

    uint32_t targetRecords = ringBefore;
    if (targetRecords > windowRecordCap) {
      targetRecords = windowRecordCap;
    }
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP] target_records=");
      Serial.print(targetRecords);
      Serial.print(inCatchUp ? " (catch-up, cap=" : " (cap=");
      Serial.print(windowRecordCap);
      Serial.println(")");
    }

    if (targetRecords == 0) {
      lastWindowMs = now;
      publishDumpStatus(DUMP_STREAMING, 4, 0);
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }

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

      if (!QspiRingBuffer::advanceTail()) {
        publishDumpStatus(DUMP_STREAMING, 8, s_dumpPendingSeq);
        break;
      }
      s_dumpSentRecords++;
      s_dumpAckedRecords++;
      s_dumpPendingValid = false;
      sentInWindow++;

      if ((sentInWindow % kDumpStatusEveryRecords) == 0) {
        publishDumpStatus(DUMP_STREAMING, 2, s_dumpPendingSeq);
      }

      if (kGattDumpInterRecordMs > 0) {
        vTaskDelay(pdMS_TO_TICKS(kGattDumpInterRecordMs));
      } else if ((sentInWindow % 64U) == 0U) {
        vTaskDelay(0);
      }
    }

    lastWindowMs = millis();

    if (sentInWindow > 0 && Bluefruit.connected() > 0) {
      publishDumpStatus(DUMP_STREAMING, 2, s_dumpPendingSeq);
    }
    if (kGattDumpVerboseLogs) {
      Serial.print("[BLEG][DUMP] window sent=");
      Serial.print(sentInWindow);
      Serial.print(" ring_count_after=");
      Serial.println(QspiRingBuffer::count());
    }

    sendLiveSnapshot();

    vTaskDelay(pdMS_TO_TICKS(50));
  }
}
