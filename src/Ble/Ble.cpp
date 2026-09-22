// Ble.cpp - GATT services/UUIDs, provisioning (chave AES + Current Time), callbacks BLE. Streaming (cifra/task) esta em BleGattDump.cpp; estado partilhado em BleInternal.h.
#include "Ble/Ble.h"
#include "BleInternal.h"

#include "Display/Ui.h"
#include "Storage/Storage.h"
#include "QspiRingBuffer/QspiRingBuffer.h"
#include "Clock/Clock.h"
#include "Ppg/Ppg.h"

#include <bluefruit.h>
#include <rtos.h>

// UUIDs custom do wearable; currentTimeService/Char e batteryService usam UUIDs padrao Bluetooth SIG (0x2A2B, 0x180F/0x2A19). Ligacao externa (ver BleInternal.h) porque BleGattDump.cpp tambem acede.
BLEService        wearableService("12345678-1234-5678-1234-56789abcdef0");
BLECharacteristic aesKeyChar     ("abcd1234-5678-1234-5678-abcdef123456");
BLEService        currentTimeService(UUID16_SVC_CURRENT_TIME);
BLECharacteristic currentTimeChar(UUID16_CHR_CURRENT_TIME);
BLECharacteristic dumpCtrlChar   ("abcd1234-5678-1234-5678-abcdef200001");
BLECharacteristic dumpDataChar   ("abcd1234-5678-1234-5678-abcdef200002");
BLECharacteristic dumpStatusChar ("abcd1234-5678-1234-5678-abcdef200003");
BLECharacteristic emergencyAlertChar("abcd1234-5678-1234-5678-abcdef200004");
// perfil de emergencia: bridge escreve em WriteChar, dispositivo persiste e espelha em Char (so leitura)
BLECharacteristic emergencyProfileWriteChar("abcd1234-5678-1234-5678-abcdef200005");
BLECharacteristic emergencyProfileChar     ("abcd1234-5678-1234-5678-abcdef200006");
// snapshot "ao vivo" do ring buffer, paralelo ao dump historico (que entrega por ordem cronologica e pode ficar minutos atrasado)
BLECharacteristic liveSnapshotChar         ("abcd1234-5678-1234-5678-abcdef200007");
// estado da leitura de GNSS forcada via dumpCtrlChar; nao cifrado por AES-CTR, so encriptacao de link (mesmo padrao que dumpStatusChar)
BLECharacteristic gnssStatusChar           ("abcd1234-5678-1234-5678-abcdef200008");
// telemetria de latencia sensor->bridge (rec_seq + timestamp); metadados de timing apenas, sem dados clinicos
BLECharacteristic latencyProbeChar         ("abcd1234-5678-1234-5678-abcdef200009");
BLEBas batteryService; // Battery Service padrao (0x180F/0x2A19)

volatile bool s_dataModeEnabled = false; // lido tambem por gattDumpTask em BleGattDump.cpp
volatile bool s_gnssForceRequested = false; // consumido por Ble::consumeGnssForceRequest() no loop() de main.cpp

namespace {

volatile bool s_aesArrived = false;
volatile bool s_timestampArrived = false;
volatile uint32_t s_timestamp = 0;

uint16_t s_emergencyAlertSeq = 0;

bool isLeapYear(uint16_t y) {
  return ((y % 4U) == 0U) && (((y % 100U) != 0U) || ((y % 400U) == 0U));
}

uint8_t daysInMonth(uint16_t y, uint8_t m) {
  static const uint8_t days[12] = {31,28,31,30,31,30,31,31,30,31,30,31};
  if (m < 1 || m > 12) return 0;
  if (m == 2 && isLeapYear(y)) return 29;
  return days[m - 1];
}

// payload de "Current Time" (0x2A2B, 10 bytes: ano LE, mes, dia, h, m, s, dow, subsec, reason) -> epoch UTC; usa algoritmo de Howard Hinnant (eras de 400 anos)
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

} // namespace

// callbacks BLE (correm no contexto da stack Bluefruit ao escrever/ligar/desligar)

// so aceita a 1a escrita por dispositivo (chave ja em flash = ignora)
static void aesKeyCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                           uint8_t *data, uint16_t len) {
  (void)conn_hdl;
  (void)chr;

  if (Storage::hasAesKey()) {
    Serial.println("[BLE] AES already in flash, ignoring write");
    return;
  }

  // so os 3 comprimentos reais de AES (128/192/256); outros passavam na validacao antiga mas bloqueavam encryptRecord()
  if (len != 16 && len != 24 && len != 32) {
    Serial.println("[BLE] AES key invalid length (precisa 16, 24 ou 32 bytes)");
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

// escrita em Current Time (0x2A2B) sincroniza Clock; ja protegida por SECMODE_ENC_NO_MITM, por isso permitida mesmo em modo de dados
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

// pede start/stop do streaming; so tem efeito com s_dataModeEnabled ativo
static void dumpCtrlCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                             uint8_t *data, uint16_t len) {
  (void)chr;
  if (len < 1 || data == nullptr) return;

  // log sempre (aceite ou nao) para distinguir "write nunca chegou" de "chegou mas foi descartado"
  Serial.print("[BLEG][DUMP] write recebido cmd=0x");
  Serial.print(data[0], HEX);
  Serial.print(" len=");
  Serial.print(len);
  Serial.print(" s_dataModeEnabled=");
  Serial.println(s_dataModeEnabled ? "1" : "0");

  if (!s_dataModeEnabled) {
    Serial.println("[BLEG][DUMP] write descartado: modo de dados inativo");
    return;
  }

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
    // 1 comando pede FC forcada durante `seconds` + SpO2 medido de imediato
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

  if (cmd == kDumpCtrlForceGnss) {
    s_gnssForceRequested = true;
    Serial.println("[BLEG][DUMP] FORCE_GNSS pedido");
    return;
  }

  if (cmd == kDumpCtrlResetReadings) {
    // DESTRUTIVO E IRREVERSIVEL: apaga so o ring buffer (calibracao IMU e chave AES ficam intactas); format() usa mutex interno, seguro contra storageTask/gattDumpTask concorrentes
    s_dumpStopRequested = true;
    vTaskDelay(pdMS_TO_TICKS(100));
    const bool ok = QspiRingBuffer::format();
    Serial.print("[BLEG][DUMP] RESET_READINGS ok=");
    Serial.println(ok ? "1" : "0");
    return;
  }
}

// perfil de emergencia (JSON, do bridge): sem cifra AES propria, so encriptacao de link BLE; guarda em flash e espelha logo em emergencyProfileChar
static void emergencyProfileWriteCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                                           uint8_t *data, uint16_t len) {
  (void)conn_hdl;
  (void)chr;

  if (!Storage::saveEmergencyProfile(data, len)) {
    Serial.println("[BLE] failed to save emergency profile");
    return;
  }

  emergencyProfileChar.write(data, len);
  Serial.print("[BLE] emergency profile received and stored, len=");
  Serial.println(len);
}

// em modo de dados, streaming arranca automaticamente ao ligar
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

bool begin() {
  Clock::begin();
  Serial.print("[BLE] build tag: ");
  Serial.println(kBleBuildTag);

  Bluefruit.Periph.setConnectCallback(periphConnectCallback);
  Bluefruit.Periph.setDisconnectCallback(periphDisconnectCallback);

  // bond=1/mitm=0: pairing Just Works (Legacy, sem LESC — NRF_CRYPTOCELL nao definido nesta placa); MITM via Numeric Comparison fica para Fase B (BLE-003)
  Bluefruit.Security.setIOCaps(false, false, false);
  Bluefruit.Security.setMITM(false);

  wearableService.begin();

  // escrita exige SECMODE_ENC_NO_MITM (bonding); "so aceita 1a escrita" e camada extra em aesKeyCallback
  aesKeyChar.setProperties(CHR_PROPS_WRITE);
  aesKeyChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  aesKeyChar.setMaxLen(AES_KEY_MAX_LEN);
  aesKeyChar.setWriteCallback(aesKeyCallback);
  aesKeyChar.begin();

  dumpCtrlChar.setProperties(CHR_PROPS_WRITE | CHR_PROPS_WRITE_WO_RESP);
  dumpCtrlChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  dumpCtrlChar.setMaxLen(8);
  dumpCtrlChar.setWriteCallback(dumpCtrlCallback);
  dumpCtrlChar.begin();

  dumpDataChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_INDICATE);
  dumpDataChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  dumpDataChar.setFixedLen(sizeof(DumpDataPacket));
  dumpDataChar.begin();

  dumpStatusChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  dumpStatusChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  dumpStatusChar.setFixedLen(sizeof(DumpStatusPacket));
  dumpStatusChar.begin();

  liveSnapshotChar.setProperties(CHR_PROPS_NOTIFY);
  liveSnapshotChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  liveSnapshotChar.setFixedLen(sizeof(DumpDataPacket));
  liveSnapshotChar.begin();

  gnssStatusChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  gnssStatusChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  gnssStatusChar.setFixedLen(sizeof(GnssStatusPacket));
  gnssStatusChar.begin();

  latencyProbeChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  latencyProbeChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  latencyProbeChar.setFixedLen(sizeof(LatencyProbePacket));
  latencyProbeChar.begin();

  emergencyAlertChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  emergencyAlertChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  emergencyAlertChar.setFixedLen(sizeof(EmergencyAlertPacket));
  emergencyAlertChar.begin();

  emergencyProfileWriteChar.setProperties(CHR_PROPS_WRITE | CHR_PROPS_WRITE_WO_RESP);
  emergencyProfileWriteChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  emergencyProfileWriteChar.setMaxLen(EMERGENCY_PROFILE_MAX_LEN);
  emergencyProfileWriteChar.setWriteCallback(emergencyProfileWriteCallback);
  emergencyProfileWriteChar.begin();

  // sem setFixedLen(): tamanho variavel, o JSON varia por paciente
  emergencyProfileChar.setProperties(CHR_PROPS_READ);
  emergencyProfileChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  emergencyProfileChar.setMaxLen(EMERGENCY_PROFILE_MAX_LEN);
  emergencyProfileChar.begin();

  // pre-carrega com o que ja estiver em flash (ou "{}"), disponivel por leitura logo no arranque
  {
    uint8_t profBuf[EMERGENCY_PROFILE_MAX_LEN];
    size_t profLen = 0;
    if (Storage::hasEmergencyProfile() && Storage::loadEmergencyProfile(profBuf, sizeof(profBuf), profLen)) {
      emergencyProfileChar.write(profBuf, profLen);
    } else {
      emergencyProfileChar.write(reinterpret_cast<const uint8_t *>("{}"), 2);
    }
  }

  if (batteryService.begin() != ERROR_NONE) {
    Serial.println("[BLE] falha ao iniciar Battery Service (0x180F) — nivel de bateria nao sera publicado por BLE");
  }

  currentTimeService.begin();
  currentTimeChar.setProperties(CHR_PROPS_WRITE);
  currentTimeChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  currentTimeChar.setMaxLen(10);
  currentTimeChar.setWriteCallback(timestampCallback);
  currentTimeChar.begin();

  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addService(wearableService);
  Bluefruit.Advertising.addService(currentTimeService);
  // pacote principal (31 bytes) ja cheio com os 2 UUIDs, nome vai no scan response
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(160, 244);
  const bool provStartOk = Bluefruit.Advertising.start(0);
  Serial.print("[BLE] provisioning adv start=");
  Serial.println(provStartOk ? "OK" : "FAIL");
  Serial.print("[BLE] provisioning adv running=");
  Serial.println(Bluefruit.Advertising.isRunning() ? "1" : "0");

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

// espera bloqueante pela escrita da app no provisioning; central que fica ligado sem escrever ate kProvisionCentralTimeoutMs e desconectado a forca
static bool waitForProvisionWrite(volatile bool &arrivedFlag, const char *waitLabel) {
  uint32_t lastLog = 0;
  uint32_t connectedSinceMs = 0;
  bool wasConnected = false;

  while (!arrivedFlag) {
    const uint32_t now = millis();
    const bool isConnected = Bluefruit.connected() > 0;

    if (isConnected && !wasConnected) {
      connectedSinceMs = now; // novo central: prazo conta a partir de agora
    }
    wasConnected = isConnected;

    if (isConnected && (now - connectedSinceMs) >= kProvisionCentralTimeoutMs) {
      Serial.print("[BLE] central ligado sem completar '");
      Serial.print(waitLabel);
      Serial.println("' dentro do tempo limite -> a desconectar (pode nao ser o dashboard real)");
      uint16_t handles[2] = {0};
      const uint8_t connCount = Bluefruit.getConnectedHandles(handles, 2);
      for (uint8_t i = 0; i < connCount; i++) {
        Bluefruit.disconnect(handles[i]);
      }
      connectedSinceMs = now; // evita disconnect() em loop apertado antes do evento ser processado
      wasConnected = false;
    }

    if ((now - lastLog) >= kBleProvisionWaitLogMs) {
      lastLog = now;
      Serial.print("[BLE] wait ");
      Serial.print(waitLabel);
      Serial.print("... adv=");
      Serial.print(Bluefruit.Advertising.isRunning() ? "1" : "0");
      Serial.print(" connected=");
      Serial.println(Bluefruit.connected());
    }
    delay(100);
  }
  return true;
}

bool ensureAesKey() {
  if (Storage::hasAesKey()) { // chave ja persistida, nao precisa esperar por BLE
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

  Serial.println("[BLE] waiting for AES key via BLE...");
  uiMessage("Receber", "AES key");
  waitForProvisionWrite(s_aesArrived, "AES");

  uiMessage("AES key", "recebida");
  delay(1200);
  return true;
}

bool ensureTimeSync() {
  s_timestampArrived = false; // forca sync novo, descarta timestamp de arranque anterior
  s_timestamp = 0;
  Clock::invalidate();

  Serial.println("[BLE] waiting for Current Time (0x2A2B) via BLE...");
  uiMessage("Pedir", "Hora e Data");
  waitForProvisionWrite(s_timestampArrived, "TIME");

  // transicao provisioning -> modo de dados: fecha ligacoes e para este advertising antes de startBroadcast()
  Bluefruit.Advertising.restartOnDisconnect(false);

  uint16_t handles[2] = {0}; // Bluefruit.begin(2,0) em main.cpp reserva no maximo 2 ligacoes
  const uint8_t connCount = Bluefruit.getConnectedHandles(handles, 2);
  if (connCount > 0) {
    for (uint8_t i = 0; i < connCount; i++) {
      Bluefruit.disconnect(handles[i]);
    }
    Serial.print("[BLE] disconnected centrals after time sync: ");
    Serial.println(connCount);
    const uint32_t t0 = millis(); // espera evento real de disconnect no stack
    while (Bluefruit.connected() > 0 && (millis() - t0) < 3000) {
      delay(20);
    }
    Serial.print("[BLE] connected after wait: ");
    Serial.println(Bluefruit.connected());
  }

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
  // modo apenas GATT: reconstroi advertising do zero para nao sobrar config do provisioning
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
  // s_dumpState reposto aqui de forma otimista; gattDumpTask tambem o repoe ao processar o stop
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

bool consumeGnssForceRequest() {
  if (!s_gnssForceRequested) return false;
  s_gnssForceRequested = false;
  return true;
}

void publishGnssStatus(bool fix, uint8_t siv, uint32_t timestampMs, int32_t latitude, int32_t longitude, int32_t altitudeMm) {
  GnssStatusPacket pkt{};
  pkt.type = kGnssStatusType;
  pkt.fix = fix ? 1 : 0;
  pkt.siv = siv;
  pkt.reserved = 0;
  pkt.timestamp_ms = timestampMs;
  pkt.latitude = latitude;
  pkt.longitude = longitude;
  pkt.altitude_mm = altitudeMm;

  gnssStatusChar.write(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  if (Bluefruit.connected() > 0) {
    (void)gnssStatusChar.notify(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  }
}

void publishLatencyProbe(uint32_t recSeq, uint64_t epochMs) {
  LatencyProbePacket pkt{};
  pkt.type = kLatencyProbeType;
  pkt.reserved[0] = 0;
  pkt.reserved[1] = 0;
  pkt.reserved[2] = 0;
  pkt.rec_seq = recSeq;
  pkt.epoch_ms = epochMs;

  latencyProbeChar.write(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  if (Bluefruit.connected() > 0) {
    (void)latencyProbeChar.notify(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  }
}

void updateBatteryLevel(uint8_t percent) {
  if (percent > 100) percent = 100; // BLEBas.write()/notify() esperam 0-100

  batteryService.write(percent); // notify() so tem efeito com ligacao ativa
  if (Bluefruit.connected() > 0) {
    (void)batteryService.notify(percent);
  }
}

} // namespace Ble
