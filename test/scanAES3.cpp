#include <Arduino.h>
#include <NimBLEDevice.h>
#include <mbedtls/aes.h>

#include <cstdio>
#include <cstring>
#include <string>

#define SCAN_TIME 8

// Company ID do emissor (igual a src/Ble/Ble.cpp -> kCompanyId)
static constexpr uint16_t kCompanyId = 0x1234;
static constexpr bool kVerboseRxLogs = true;
static constexpr bool kLogTargetManufacturer = true;

// Opcional: filtrar por MAC do advertising (pode mudar se o emissor usar endereco privado).
static constexpr bool kFilterByAdvMac = false;
const char *trustedAdvMacStr = "ea:9c:6f:8b:9e:fe";

// MAC usado para construir o IV no emissor (Bluefruit.getAddr() em formato humano).
// Se estiver vazio, o codigo tenta usar a MAC de advertising do pacote recebido.
const char *txMacForIvStr = "ea:9c:6f:8b:9e:fe";

const uint8_t aesKey[32] = {
    0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88,
    0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00,
    0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80,
    0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0, 0xF0, 0x00};

// Formato emissor (src/Ble/Ble.cpp):
// mfg = company(2) + frame
// frame short: [type=0x01][ctr_le_u64][cipher(10)]
// frame full v1: [type=0x02][ctr_le_u64][cipher(16)]
// frame full v2: [type=0x02][ctr_le_u64][cipher(18)] -> fragmentado
static constexpr size_t kShortCipherLen = 10;
static constexpr size_t kFullCipherLenV1 = 16;
static constexpr size_t kFullCipherLenV2 = 18;
static constexpr size_t kShortFrameLen = 1 + 8 + kShortCipherLen;
static constexpr size_t kFullFrameLenV1 = 1 + 8 + kFullCipherLenV1;
static constexpr size_t kFullFrameLenV2 = 1 + 8 + kFullCipherLenV2;

struct __attribute__((packed)) ShortPlain {
  uint8_t mac[6];
  uint32_t ts;
};

struct __attribute__((packed)) FullPlain {
  uint32_t rec_seq;
  uint32_t rec_ts;
  int16_t spo2;
  int16_t hr;
  uint16_t steps16;
  uint8_t flags;
  uint8_t reserved;
};

struct __attribute__((packed)) FullFragPlainV2 {
  uint8_t frag_idx;
  uint8_t frag_total;
  uint8_t chunk[16];
};

struct __attribute__((packed)) FullPayloadV2 {
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

static_assert(sizeof(ShortPlain) == 10, "ShortPlain deve ter 10 bytes");
static_assert(sizeof(FullPlain) == 16, "FullPlain deve ter 16 bytes");
static_assert(sizeof(FullFragPlainV2) == 18, "FullFragPlainV2 deve ter 18 bytes");
static_assert(sizeof(FullPayloadV2) == 38, "FullPayloadV2 deve ter 38 bytes");

NimBLEScan *pBLEScan = nullptr;
static int scanCycle = 0;
static uint8_t g_txMacForIv[6] = {0};
static bool g_hasTxMacForIv = false;
static uint8_t g_trustedAdvMac[6] = {0};
static bool g_hasTrustedAdvMac = false;
static uint32_t g_cycleTargetPackets = 0;
static uint32_t g_cycleTargetShort = 0;
static uint32_t g_cycleTargetFull = 0;

struct FullV2RxState {
  bool active = false;
  uint8_t expectedTotal = 0;
  uint8_t receivedMask = 0;
  uint8_t data[sizeof(FullPayloadV2)] = {0};
  uint32_t lastMs = 0;
};

static FullV2RxState g_fullV2Rx;

bool parseMacString(const char *s, uint8_t out[6]) {
  if (s == nullptr || s[0] == '\0') return false;
  unsigned int b0, b1, b2, b3, b4, b5;
  if (sscanf(s, "%2x:%2x:%2x:%2x:%2x:%2x",
             &b0, &b1, &b2, &b3, &b4, &b5) != 6) {
    return false;
  }
  out[0] = (uint8_t)b0;
  out[1] = (uint8_t)b1;
  out[2] = (uint8_t)b2;
  out[3] = (uint8_t)b3;
  out[4] = (uint8_t)b4;
  out[5] = (uint8_t)b5;
  return true;
}

bool macStringEqualsBytes(const std::string &macStr, const uint8_t macBytes[6]) {
  uint8_t parsed[6] = {0};
  if (!parseMacString(macStr.c_str(), parsed)) return false;
  return memcmp(parsed, macBytes, 6) == 0;
}

uint64_t readLeU64(const uint8_t *p) {
  uint64_t v = 0;
  for (int i = 0; i < 8; i++) {
    v |= ((uint64_t)p[i] << (8 * i));
  }
  return v;
}

void buildIv(uint8_t iv[16], const uint8_t mac[6], uint8_t frameType, uint64_t ctr) {
  memset(iv, 0, 16);
  memcpy(iv, mac, 6);
  for (int i = 0; i < 8; i++) {
    iv[6 + i] = (uint8_t)((ctr >> (8 * i)) & 0xFF);
  }
  iv[14] = frameType;
  iv[15] = 0xA5;
}

bool decryptAESCTR(const uint8_t *cipher, size_t length, const uint8_t ivIn[16], uint8_t *plainOut) {
  mbedtls_aes_context aes;
  uint8_t iv[16];
  memcpy(iv, ivIn, sizeof(iv));
  size_t nc_off = 0;
  uint8_t stream_block[16] = {0};

  mbedtls_aes_init(&aes);
  const int keyRc = mbedtls_aes_setkey_enc(&aes, aesKey, 256);
  if (keyRc != 0) {
    mbedtls_aes_free(&aes);
    return false;
  }

  const int ctrRc = mbedtls_aes_crypt_ctr(
      &aes, length, &nc_off, iv, stream_block, cipher, plainOut);
  mbedtls_aes_free(&aes);
  return ctrRc == 0;
}

bool plausibleEpoch(uint32_t ts) {
  // Jan/2023 .. Jan/2030
  return (ts >= 1672531200UL) && (ts <= 1893456000UL);
}

void printMacBytes(const uint8_t mac[6]) {
  Serial.printf("%02X:%02X:%02X:%02X:%02X:%02X",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

void printHexBytes(const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; i++) {
    if (data[i] < 0x10) Serial.print('0');
    Serial.print(data[i], HEX);
    if (i + 1 < len) Serial.print(' ');
  }
}

void resetFullV2Rx() {
  g_fullV2Rx.active = false;
  g_fullV2Rx.expectedTotal = 0;
  g_fullV2Rx.receivedMask = 0;
  memset(g_fullV2Rx.data, 0, sizeof(g_fullV2Rx.data));
  g_fullV2Rx.lastMs = 0;
}

void handleFullV2Fragment(const FullFragPlainV2 &frag, const std::string &advMac,
                          int rssi, uint64_t ctr) {
  if (frag.frag_total == 0 || frag.frag_total > 8) {
    Serial.printf("[SCAN] full-v2 frag_total invalido=%u\n", frag.frag_total);
    resetFullV2Rx();
    return;
  }
  if (frag.frag_idx >= frag.frag_total) {
    Serial.printf("[SCAN] full-v2 frag_idx invalido idx=%u total=%u\n",
                  frag.frag_idx, frag.frag_total);
    resetFullV2Rx();
    return;
  }

  const uint32_t now = millis();
  if (!g_fullV2Rx.active || frag.frag_idx == 0 ||
      g_fullV2Rx.expectedTotal != frag.frag_total ||
      (g_fullV2Rx.lastMs != 0 && (now - g_fullV2Rx.lastMs) > 5000)) {
    resetFullV2Rx();
    g_fullV2Rx.active = true;
    g_fullV2Rx.expectedTotal = frag.frag_total;
  }
  g_fullV2Rx.lastMs = now;

  const size_t offset = (size_t)frag.frag_idx * sizeof(frag.chunk);
  if (offset >= sizeof(g_fullV2Rx.data)) {
    Serial.printf("[SCAN] full-v2 offset invalido=%u\n", (unsigned)offset);
    resetFullV2Rx();
    return;
  }

  const size_t remain = sizeof(g_fullV2Rx.data) - offset;
  const size_t copyLen = (remain < sizeof(frag.chunk)) ? remain : sizeof(frag.chunk);
  memcpy(&g_fullV2Rx.data[offset], frag.chunk, copyLen);
  g_fullV2Rx.receivedMask |= (uint8_t)(1U << frag.frag_idx);

  const uint8_t expectedMask = (uint8_t)((1U << g_fullV2Rx.expectedTotal) - 1U);
  Serial.printf("[SCAN] ciclo=%d adv=%s rssi=%d type=0x02 ctr=%llu full-v2 frag=%u/%u\n",
                scanCycle, advMac.c_str(), rssi, (unsigned long long)ctr,
                (unsigned)(frag.frag_idx + 1), (unsigned)frag.frag_total);

  if (g_fullV2Rx.receivedMask != expectedMask) return;

  FullPayloadV2 full{};
  memcpy(&full, g_fullV2Rx.data, sizeof(full));

  Serial.print("  full.ts=");
  Serial.print(full.ts);
  Serial.print(" a[g]=");
  Serial.print(full.ax, 3);
  Serial.print(",");
  Serial.print(full.ay, 3);
  Serial.print(",");
  Serial.print(full.az, 3);
  Serial.print(" g[dps]=");
  Serial.print(full.gx, 2);
  Serial.print(",");
  Serial.print(full.gy, 2);
  Serial.print(",");
  Serial.print(full.gz, 2);
  Serial.print(" steps=");
  Serial.print(full.steps);
  Serial.print(" ff=");
  Serial.print(full.ff);
  Serial.print(" inact=");
  Serial.print(full.inact);
  Serial.print(" spo2=");
  Serial.print(full.spo2);
  Serial.print(" hr=");
  Serial.println(full.hr);

  resetFullV2Rx();
}

class MyAdvertisedDeviceCallbacks : public NimBLEAdvertisedDeviceCallbacks {
  void onResult(NimBLEAdvertisedDevice *device) override {
    const std::string advMac = device->getAddress().toString();

    if (kFilterByAdvMac && g_hasTrustedAdvMac && !macStringEqualsBytes(advMac, g_trustedAdvMac)) {
      return;
    }

    if (!device->haveManufacturerData()) return;
    const std::string raw = device->getManufacturerData();
    const size_t totalLen = raw.length();
    if (totalLen < 3) return;

    const uint8_t *mfg = reinterpret_cast<const uint8_t *>(raw.data());
    const uint16_t company = (uint16_t)mfg[0] | ((uint16_t)mfg[1] << 8);
    if (company != kCompanyId) return;

    g_cycleTargetPackets++;

    if (kLogTargetManufacturer) {
      Serial.print("[RX][RAW] adv=");
      Serial.print(advMac.c_str());
      Serial.print(" rssi=");
      Serial.print(device->getRSSI());
      Serial.print(" company=0x");
      if (company < 0x1000) Serial.print('0');
      if (company < 0x100) Serial.print('0');
      if (company < 0x10) Serial.print('0');
      Serial.print(company, HEX);
      Serial.print(" mfgLen=");
      Serial.print(totalLen);
      Serial.print(" data=");
      printHexBytes(mfg, totalLen);
      Serial.println();
    }

    const uint8_t *frame = mfg + 2;
    const size_t frameLen = totalLen - 2;
    if (frameLen < 1 + 8) return;

    const uint8_t frameType = frame[0];
    const uint64_t ctr = readLeU64(&frame[1]);
    if (kVerboseRxLogs) {
      Serial.print("[RX][TARGET] type=0x");
      if (frameType < 0x10) Serial.print('0');
      Serial.print(frameType, HEX);
      Serial.print(" ctr=0x");
      Serial.printf("%08lX%08lX",
                    (unsigned long)(ctr >> 32),
                    (unsigned long)(ctr & 0xFFFFFFFFULL));
      Serial.print(" frameLen=");
      Serial.print(frameLen);
      Serial.print(" frame=");
      printHexBytes(frame, frameLen);
      Serial.println();
    }

    size_t cipherLen = 0;
    if (frameType == 0x01) {
      cipherLen = kShortCipherLen;
      g_cycleTargetShort++;
    } else if (frameType == 0x02) {
      if (frameLen == kFullFrameLenV2) {
        cipherLen = kFullCipherLenV2;
      } else if (frameLen == kFullFrameLenV1) {
        cipherLen = kFullCipherLenV1;
      } else {
        Serial.printf("[SCAN] len inesperado type=0x%02X got=%u exp=%u|%u\n",
                      frameType, (unsigned)frameLen,
                      (unsigned)kFullFrameLenV2, (unsigned)kFullFrameLenV1);
        return;
      }
      g_cycleTargetFull++;
    } else {
      Serial.printf("[SCAN] frameType desconhecido: 0x%02X (len=%u)\n",
                    frameType, (unsigned)frameLen);
      return;
    }

    if (frameType == 0x01 && frameLen != kShortFrameLen) {
      Serial.printf("[SCAN] len inesperado type=0x%02X got=%u exp=%u\n",
                    frameType, (unsigned)frameLen, (unsigned)kShortFrameLen);
      return;
    }

    const uint8_t *cipher = frame + 9;

    uint8_t ivCandidates[2][6] = {0};
    uint8_t candidateCount = 0;

    if (g_hasTxMacForIv) {
      memcpy(ivCandidates[candidateCount++], g_txMacForIv, 6);
    }

    uint8_t advMacBytes[6] = {0};
    if (parseMacString(advMac.c_str(), advMacBytes)) {
      bool duplicate = false;
      for (uint8_t i = 0; i < candidateCount; i++) {
        if (memcmp(ivCandidates[i], advMacBytes, 6) == 0) {
          duplicate = true;
          break;
        }
      }
      if (!duplicate && candidateCount < 2) {
        memcpy(ivCandidates[candidateCount++], advMacBytes, 6);
      }
    }

    if (candidateCount == 0) {
      Serial.println("[SCAN] sem MAC candidata para IV");
      return;
    }

    uint8_t plain[32] = {0};
    bool decoded = false;
    uint8_t usedMac[6] = {0};

    for (uint8_t i = 0; i < candidateCount; i++) {
      uint8_t iv[16];
      buildIv(iv, ivCandidates[i], frameType, ctr);
      if (!decryptAESCTR(cipher, cipherLen, iv, plain)) continue;

      if (frameType == 0x01) {
        ShortPlain sp{};
        memcpy(&sp, plain, sizeof(sp));

        // Sanity check para evitar "lixo" quando IV/key nao bate.
        if (memcmp(sp.mac, ivCandidates[i], 6) == 0 && plausibleEpoch(sp.ts)) {
          decoded = true;
          memcpy(usedMac, ivCandidates[i], 6);
          break;
        }
      } else {
        // Para frame full não temos MAC no plaintext; aceita a primeira decriptação.
        decoded = true;
        memcpy(usedMac, ivCandidates[i], 6);
        break;
      }
    }

    Serial.printf("[SCAN] ciclo=%d adv=%s rssi=%d type=0x%02X ctr=%llu ",
                  scanCycle, advMac.c_str(), device->getRSSI(), frameType,
                  (unsigned long long)ctr);

    if (!decoded) {
      Serial.println("-> decrypt FAIL");
      return;
    }

    if (kVerboseRxLogs) {
      Serial.print("[RX][DECRYPT] type=0x");
      if (frameType < 0x10) Serial.print('0');
      Serial.print(frameType, HEX);
      Serial.print(" plain=");
      printHexBytes(plain, cipherLen);
      Serial.println();
    }

    Serial.print("ivMac=");
    printMacBytes(usedMac);
    Serial.println();

    if (frameType == 0x01) {
      ShortPlain sp{};
      memcpy(&sp, plain, sizeof(sp));
      Serial.print("  short.mac=");
      printMacBytes(sp.mac);
      Serial.print(" ts=");
      Serial.println(sp.ts);
    } else {
      if (cipherLen == kFullCipherLenV1) {
        FullPlain fp{};
        memcpy(&fp, plain, sizeof(fp));
        Serial.print("  full-v1.seq=");
        Serial.print(fp.rec_seq);
        Serial.print(" ts=");
        Serial.print(fp.rec_ts);
        Serial.print(" spo2=");
        Serial.print(fp.spo2);
        Serial.print(" hr=");
        Serial.print(fp.hr);
        Serial.print(" steps=");
        Serial.print(fp.steps16);
        Serial.print(" flags=0x");
        Serial.println(fp.flags, HEX);
      } else if (cipherLen == kFullCipherLenV2) {
        FullFragPlainV2 frag{};
        memcpy(&frag, plain, sizeof(frag));

        if (kVerboseRxLogs) {
          Serial.print("  full-v2.frag idx=");
          Serial.print(frag.frag_idx);
          Serial.print(" total=");
          Serial.println(frag.frag_total);
        }

        handleFullV2Fragment(frag, advMac, device->getRSSI(), ctr);
      } else {
        Serial.print("[SCAN] full cipherLen inesperado=");
        Serial.println(cipherLen);
      }
    }
  }
};

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== BLE Scanner compativel com broadcast atual (ESP32/NimBLE) ===");

  g_hasTxMacForIv = parseMacString(txMacForIvStr, g_txMacForIv);
  g_hasTrustedAdvMac = parseMacString(trustedAdvMacStr, g_trustedAdvMac);
  resetFullV2Rx();

  Serial.print("Company ID esperado: 0x");
  Serial.println(kCompanyId, HEX);
  Serial.print("Filtro por MAC advertising: ");
  Serial.println(kFilterByAdvMac ? "ON" : "OFF");
  Serial.print("MAC para IV fixa: ");
  Serial.println(g_hasTxMacForIv ? txMacForIvStr : "(nao definida)");

  NimBLEDevice::init("");
  pBLEScan = NimBLEDevice::getScan();
  // true => receber duplicados (necessario para ver mudancas de payload
  // do mesmo advertiser dentro do mesmo ciclo de scan).
  pBLEScan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks(), true);
  pBLEScan->setDuplicateFilter(0);
  pBLEScan->setActiveScan(true);
  pBLEScan->setInterval(100);
  pBLEScan->setWindow(99);

  Serial.println("BLE inicializado. A comecar scan...\n");
}

void loop() {
  scanCycle++;
  g_cycleTargetPackets = 0;
  g_cycleTargetShort = 0;
  g_cycleTargetFull = 0;
  Serial.printf("--- Ciclo %d: scan %d segundos (filtro company=0x%04X) ---\n",
                scanCycle, SCAN_TIME, kCompanyId);

  pBLEScan->start(SCAN_TIME, false);
  Serial.printf("--- Ciclo %d: target packets=%lu short=%lu full=%lu ---\n\n",
                scanCycle,
                (unsigned long)g_cycleTargetPackets,
                (unsigned long)g_cycleTargetShort,
                (unsigned long)g_cycleTargetFull);

  pBLEScan->clearResults();
  delay(50);
}
