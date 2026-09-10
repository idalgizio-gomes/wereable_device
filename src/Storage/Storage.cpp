// Storage.cpp - calibracao IMU, chave AES, contador BLE e perfil de emergencia guardados como ficheiros binarios no LittleFS (flash interna)
#include "Storage/Storage.h"
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>

using namespace Adafruit_LittleFS_Namespace;

static const char *PATH_CALIB = "/calib.bin";
static const char *PATH_AES   = "/aes.bin";
static const char *PATH_COUNT = "/counter.bin";
static const char *PATH_EMERG = "/emerg.bin";

namespace Storage {

bool begin() {
  if (!InternalFS.begin()) {
    Serial.println("[Storage] InternalFS.begin() failed");
    return false;
  }
  Serial.println("[Storage] InternalFS ready");
  return true;
}

// ---------------- Calibration ----------------

bool saveCalibration(const ImuCalibration &cal) {
  InternalFS.remove(PATH_CALIB); // remove antes de escrever, senao sobrepoe um ficheiro maior e deixa bytes antigos no fim
  File f(InternalFS);
  if (!f.open(PATH_CALIB, FILE_O_WRITE)) {
    Serial.println("[Storage] failed to open calib for write");
    return false;
  }
  size_t n = f.write(reinterpret_cast<const uint8_t *>(&cal), sizeof(cal));
  f.close();
  return n == sizeof(cal);
}

bool loadCalibration(ImuCalibration &cal) {
  File f(InternalFS);
  if (!f.open(PATH_CALIB, FILE_O_READ)) return false;
  size_t n = f.read(reinterpret_cast<uint8_t *>(&cal), sizeof(cal));
  f.close();
  return n == sizeof(cal);
}

bool hasCalibration() {
  File f(InternalFS);
  if (!f.open(PATH_CALIB, FILE_O_READ)) return false;
  bool ok = f.size() == sizeof(ImuCalibration); // so tamanho, deteta corrupcao/truncamento sem copiar dados
  f.close();
  return ok;
}

bool clearCalibration() {
  return InternalFS.remove(PATH_CALIB) || !hasCalibration();
}

// ---------------- AES key ----------------

bool saveAesKey(const uint8_t *key, size_t len) {
  if (len < AES_KEY_MIN_LEN || len > AES_KEY_MAX_LEN) return false;
  InternalFS.remove(PATH_AES);
  File f(InternalFS);
  if (!f.open(PATH_AES, FILE_O_WRITE)) {
    Serial.println("[Storage] failed to open aes for write");
    return false;
  }
  size_t n = f.write(key, len);
  f.close();
  return n == len;
}

bool loadAesKey(uint8_t *buf, size_t bufLen, size_t &outLen) {
  outLen = 0;
  File f(InternalFS);
  if (!f.open(PATH_AES, FILE_O_READ)) return false;
  size_t sz = f.size();
  if (sz < AES_KEY_MIN_LEN || sz > AES_KEY_MAX_LEN) {
    f.close();
    return false;
  }
  if (sz > bufLen) {
    f.close();
    return false;
  }
  outLen = f.read(buf, sz);
  f.close();
  return outLen == sz;
}

bool removeAesKey() {
  InternalFS.remove(PATH_AES); // devolve true tanto se apagou como se ja nao existia; os dois sao sucesso aqui
  return !hasAesKey();
}

bool hasAesKey() {
  File f(InternalFS);
  if (!f.open(PATH_AES, FILE_O_READ)) return false;
  bool ok = f.size() >= AES_KEY_MIN_LEN && f.size() <= AES_KEY_MAX_LEN;
  f.close();
  return ok;
}

// ---------------- Persistent counter ----------------

namespace {
constexpr uint32_t kCounterMagic = 0x434E5452UL; // "CNTR"
struct CounterRecord {
  uint32_t magic;
  uint64_t counter;
  uint32_t checksum;
};

uint32_t counterChecksum(uint32_t magic, uint64_t counter) {
  uint32_t lo = static_cast<uint32_t>(counter & 0xFFFFFFFFUL);
  uint32_t hi = static_cast<uint32_t>(counter >> 32);
  return magic ^ lo ^ hi ^ 0xA5A5A5A5UL;
}
} // namespace

// escreve por cima do ficheiro existente (seek(0)+write+truncate, sem remove antes) para o ficheiro nunca deixar de existir; um remove()+write() como nas outras save*() deixaria uma janela em que /counter.bin nao existe, e uma perda de energia nessa janela faria counter_load() assumir "primeiro arranque" e reutilizar nonces ja usados com a mesma chave AES (quebra do CTR)
bool counter_save(uint64_t counter) {
  CounterRecord rec{kCounterMagic, counter, counterChecksum(kCounterMagic, counter)};
  File f(InternalFS);
  if (!f.open(PATH_COUNT, FILE_O_WRITE)) {
    Serial.println("[Storage] failed to open counter for write");
    return false;
  }
  if (!f.seek(0)) {
    Serial.println("[Storage] failed to seek counter for write");
    f.close();
    return false;
  }
  size_t n = f.write(reinterpret_cast<const uint8_t *>(&rec), sizeof(rec));
  const bool truncated = f.truncate(sizeof(rec)); // cobre o caso de sobrar um ficheiro maior de um formato anterior
  f.close();
  return n == sizeof(rec) && truncated;
}

bool counter_load(uint64_t &counter, bool *corrupted) {
  counter = 0;
  if (corrupted) *corrupted = false;
  File f(InternalFS);
  if (!f.open(PATH_COUNT, FILE_O_READ)) return false; // nunca criado - primeiro arranque genuino
  CounterRecord rec{};
  bool sizeOk = (f.size() == sizeof(rec));
  size_t n = sizeOk ? f.read(reinterpret_cast<uint8_t *>(&rec), sizeof(rec)) : 0;
  f.close();
  if (!sizeOk || n != sizeof(rec) || rec.magic != kCounterMagic ||
      rec.checksum != counterChecksum(rec.magic, rec.counter)) {
    // ficheiro existe mas nao bate certo (corrompido/escrita cortada/formato antigo) - distinto de "nunca guardado"; quem chama NAO deve assumir counter=0 aqui
    if (corrupted) *corrupted = true;
    return false;
  }
  counter = rec.counter;
  return true;
}

// ---------------- Emergency profile (JSON) ----------------

bool saveEmergencyProfile(const uint8_t *data, size_t len) {
  if (len > EMERGENCY_PROFILE_MAX_LEN) return false;
  InternalFS.remove(PATH_EMERG);
  File f(InternalFS);
  if (!f.open(PATH_EMERG, FILE_O_WRITE)) {
    Serial.println("[Storage] failed to open emerg for write");
    return false;
  }
  size_t n = f.write(data, len);
  f.close();
  return n == len;
}

bool loadEmergencyProfile(uint8_t *buf, size_t bufLen, size_t &outLen) {
  outLen = 0;
  File f(InternalFS);
  if (!f.open(PATH_EMERG, FILE_O_READ)) return false;
  size_t sz = f.size();
  if (sz > EMERGENCY_PROFILE_MAX_LEN) {
    f.close();
    return false;
  }
  if (sz > bufLen) {
    f.close();
    return false;
  }
  outLen = f.read(buf, sz);
  f.close();
  return outLen == sz;
}

bool hasEmergencyProfile() {
  File f(InternalFS);
  if (!f.open(PATH_EMERG, FILE_O_READ)) return false;
  f.close(); // sem tamanho fixo esperado (JSON varia por paciente), basta confirmar que abre
  return true;
}

// ---------------- Utility ----------------

bool clearAll() {
  bool a = InternalFS.remove(PATH_CALIB);
  bool b = InternalFS.remove(PATH_AES);
  bool c = InternalFS.remove(PATH_COUNT);
  bool d = InternalFS.remove(PATH_EMERG);
  return a || b || c || d; // "||" porque e normal nem todos existirem
}

// ---------------- Validation ----------------

bool validate() {
  bool ok = true;
  Serial.println("[Storage] validation: start");

  if (hasCalibration()) {
    ImuCalibration cal{};
    if (!loadCalibration(cal)) {
      Serial.println("[Storage] FAIL: calib exists but is unreadable");
      ok = false;
    } else {
      Serial.println("[Storage] calib: present and readable");
    }
  } else {
    Serial.println("[Storage] calib: absent");
  }

  if (hasAesKey()) {
    uint8_t key[AES_KEY_MAX_LEN] = {0};
    size_t keyLen = 0;
    if (!loadAesKey(key, sizeof(key), keyLen)) {
      Serial.println("[Storage] FAIL: aes exists but is unreadable");
      ok = false;
    } else {
      Serial.print("[Storage] aes: present and readable (len=");
      Serial.print(keyLen);
      Serial.println(")");
    }
  } else {
    Serial.println("[Storage] aes: absent");
  }

  uint64_t ctr = 0;
  if (counter_load(ctr)) {
    Serial.print("[Storage] counter: ");
    Serial.println((unsigned long)(ctr & 0xFFFFFFFFUL));
  } else {
    Serial.println("[Storage] counter: absent (will be created on first BLE transfer)");
  }

  Serial.println(ok ? "[Storage] validation: OK" : "[Storage] validation: WARNINGS");
  return ok;
}

} // namespace Storage
