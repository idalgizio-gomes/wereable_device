// ============================================================
// BleInternal.h - declaracoes privadas partilhadas entre Ble.cpp e
// BleGattDump.cpp (ver Ble.h para a API publica do modulo).
// ============================================================
// Nao incluir fora de src/Ble/ - isto NAO e a interface publica do
// modulo BLE, so existe para ligar os dois .cpp deste modulo sem
// duplicar objetos/estado (ver PROJECT_STATUS.md, modularizacao
// 2026-09-07, para o porque desta divisao).
#pragma once

#include "Ble/Ble.h"

#include <bluefruit.h>
#include <cstdint>
#include <cstring>

// ------------------------------------------------------------
// Objetos GATT (servicos/characteristics) - definidos uma unica vez em
// Ble.cpp, usados tambem por BleGattDump.cpp (gattDumpTask,
// sendLiveSnapshot, publishDumpStatus).
// ------------------------------------------------------------
extern BLEService        wearableService;
extern BLECharacteristic aesKeyChar;
extern BLEService        currentTimeService;
extern BLECharacteristic currentTimeChar;
extern BLECharacteristic dumpCtrlChar;
extern BLECharacteristic dumpDataChar;
extern BLECharacteristic dumpStatusChar;
extern BLECharacteristic emergencyAlertChar;
extern BLECharacteristic emergencyProfileWriteChar;
extern BLECharacteristic emergencyProfileChar;
extern BLECharacteristic liveSnapshotChar;
extern BLEBas             batteryService;

// ------------------------------------------------------------
// Constantes de configuracao do "modo de dados" (streaming GATT) - ver
// comentario completo original junto de cada uma em BleGattDump.cpp.
// ------------------------------------------------------------
constexpr uint16_t kGattDumpTaskStackWords = 1280;
constexpr uint32_t kGattDumpInterPacketMs = 2;
constexpr uint8_t kGattDumpTxMaxRetries = 5;
constexpr uint32_t kGattDumpTxRetryDelayMs = 4;
constexpr uint32_t kGattDumpWindowMs = 1000;
constexpr uint32_t kImuRateHz = 52;
constexpr uint32_t kWindowTargetRecords = (kImuRateHz * kGattDumpWindowMs) / 1000U;
constexpr uint32_t kCatchUpBacklogRecords = kWindowTargetRecords * 10;
constexpr uint32_t kWindowCatchUpMultiplier = 3;
constexpr uint32_t kWindowCatchUpTargetRecords = kWindowTargetRecords * kWindowCatchUpMultiplier;
constexpr uint32_t kDumpStatusEveryRecords = 128;
constexpr uint32_t kGattDumpInterRecordMs = 0;
constexpr uint32_t kGattDumpWaitLogMs = 2000;
constexpr uint32_t kGattDumpIdleLogMs = 5000;
constexpr uint32_t kBleProvisionWaitLogMs = 5000;
constexpr uint32_t kProvisionCentralTimeoutMs = 15000;
constexpr bool kGattDumpVerboseLogs = false;
constexpr uint8_t kGattDumpChunkLen = 8;
constexpr uint8_t kDumpCtrlStart = 0x01;
constexpr uint8_t kDumpCtrlStop = 0x02;
constexpr uint8_t kDumpCtrlForceHr = 0x03;
constexpr uint16_t kForceHrDefaultSeconds = 15;
constexpr uint8_t kDumpCtrlResetReadings = 0x04;
constexpr uint8_t kDumpDataType = 0xA1;
constexpr uint8_t kDumpStatusType = 0xA2;
constexpr const char *kBleBuildTag = "BLE_GATT_DUMP_V1";

// ------------------------------------------------------------
// Formatos de pacote - ver comentario original completo junto de cada
// struct em BleGattDump.cpp (FullPlain) / Ble.cpp (EmergencyAlertPacket).
// ------------------------------------------------------------
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
  uint8_t pacing_index;
};

struct __attribute__((packed)) DumpDataPacket {
  uint8_t type;
  uint8_t frag_idx;
  uint8_t frag_total;
  uint8_t chunk_len;
  uint32_t rec_seq;
  uint32_t nonce;
  uint8_t chunk[kGattDumpChunkLen];
};

struct __attribute__((packed)) DumpStatusPacket {
  uint8_t type;
  uint8_t state;
  uint8_t reason;
  uint8_t data_loss_flag;
  uint32_t seq;
  uint32_t sent_records;
  uint32_t acked_records;
  uint32_t ring_count;
};

struct FullMappedRecord {
  uint32_t rec_seq;
  FullPlain payload;
};

struct __attribute__((packed)) EmergencyAlertPacket {
  uint8_t type;
  uint8_t reserved;
  uint16_t seq;
  uint32_t timestamp_utc;
};

static_assert(sizeof(FullPlain) == 39, "FullPlain v3 must have 39 bytes");
static_assert(sizeof(DumpDataPacket) == 20, "DumpDataPacket must have 20 bytes");
static_assert(sizeof(DumpStatusPacket) == 20, "DumpStatusPacket must have 20 bytes");
static_assert(sizeof(EmergencyAlertPacket) == 8, "EmergencyAlertPacket must have 8 bytes");

// Estados possiveis da maquina de estados do "dump" (streaming) de
// sensores - ver comentario original completo em BleGattDump.cpp.
enum DumpState : uint8_t {
  DUMP_IDLE = 0,
  DUMP_STREAMING = 1,
};

// ------------------------------------------------------------
// Estado partilhado da maquina de streaming - definido em
// BleGattDump.cpp, escrito tambem pelos callbacks BLE em Ble.cpp
// (dumpCtrlCallback, periphConnectCallback, periphDisconnectCallback).
// ------------------------------------------------------------
extern volatile DumpState s_dumpState;
extern volatile bool s_dumpStartRequested;
extern volatile bool s_dumpStopRequested;
extern volatile bool s_dumpPendingValid;
extern volatile bool s_dumpWindowImmediate;
extern volatile uint32_t s_dumpSentRecords;
extern volatile uint32_t s_dumpAckedRecords;
extern TaskHandle_t s_dumpTaskHandle;

// Ligado/desligado do "modo de dados" - definido em Ble.cpp (startBroadcast/
// stopBroadcast/isBroadcastActive), lido por gattDumpTask em BleGattDump.cpp.
extern volatile bool s_dataModeEnabled;

// Funcoes definidas em BleGattDump.cpp, chamadas a partir de Ble.cpp.
void cacheAesKey(const uint8_t *key, size_t len);
void publishDumpStatus(uint8_t state, uint8_t reason, uint32_t seq);
void gattDumpTask(void *arg);
