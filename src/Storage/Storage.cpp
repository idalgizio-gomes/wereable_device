// =============================================================================
// Storage.cpp
// -----------------------------------------------------------------------------
// Implementação do módulo Storage (ver Storage.h para a documentação da API
// pública). Aqui os dados (calibração do IMU, chave AES, contador BLE) são
// guardados como ficheiros binários simples dentro do LittleFS, um sistema
// de ficheiros que corre sobre a flash interna do nRF52840. Cada "tipo" de
// dado tem o seu próprio ficheiro, identificado por um caminho fixo.
// =============================================================================

#include "Storage/Storage.h"
#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>

using namespace Adafruit_LittleFS_Namespace;

// Caminhos fixos dos ficheiros usados para guardar cada tipo de dado.
static const char *PATH_CALIB = "/calib.bin";
static const char *PATH_AES   = "/aes.bin";
static const char *PATH_COUNT = "/counter.bin";

namespace Storage {

// Monta o sistema de ficheiros interno. Tem de ser chamada antes de
// qualquer leitura/escrita; se falhar (ex.: flash danificada ou não
// formatada), nenhuma outra função deste módulo deve ser usada.
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
  // Remove primeiro o ficheiro antigo (se existir) para garantir que a
  // escrita seguinte cria um ficheiro "limpo", em vez de sobrepor um
  // ficheiro maior e deixar bytes antigos no fim.
  InternalFS.remove(PATH_CALIB);
  File f(InternalFS);
  if (!f.open(PATH_CALIB, FILE_O_WRITE)) {
    Serial.println("[Storage] failed to open calib for write");
    return false;
  }
  // Escreve a struct inteira "tal como está" na memória (cópia binária
  // byte a byte), em vez de gravar campo a campo.
  size_t n = f.write(reinterpret_cast<const uint8_t *>(&cal), sizeof(cal));
  f.close();
  return n == sizeof(cal);
}

bool loadCalibration(ImuCalibration &cal) {
  File f(InternalFS);
  if (!f.open(PATH_CALIB, FILE_O_READ)) return false;
  // Lê os bytes do ficheiro diretamente para dentro da struct "cal".
  size_t n = f.read(reinterpret_cast<uint8_t *>(&cal), sizeof(cal));
  f.close();
  return n == sizeof(cal);
}

bool hasCalibration() {
  File f(InternalFS);
  if (!f.open(PATH_CALIB, FILE_O_READ)) return false;
  // Não é preciso ler o conteúdo: basta confirmar que o ficheiro existe
  // e tem exatamente o tamanho esperado da struct (deteta ficheiros
  // corrompidos/truncados sem gastar tempo a copiar dados).
  bool ok = f.size() == sizeof(ImuCalibration);
  f.close();
  return ok;
}

bool cal_save(const ImuCalibration &cal) {
  return saveCalibration(cal);
}

bool cal_load(ImuCalibration &cal) {
  return loadCalibration(cal);
}

// ---------------- AES key ----------------

bool saveAesKey(const uint8_t *key, size_t len) {
  // Recusa gravar chaves com tamanho fora do intervalo válido para
  // AES-128/256, evitando guardar dados que depois não seriam
  // utilizáveis pela camada de cifra.
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
  // Protege contra um ficheiro corrompido ou gravado incorretamente,
  // cujo tamanho já não corresponda a uma chave AES válida.
  if (sz < AES_KEY_MIN_LEN || sz > AES_KEY_MAX_LEN) {
    f.close();
    return false;
  }
  // Protege contra overflow: não escreve mais bytes do que o buffer do
  // chamador consegue receber.
  if (sz > bufLen) {
    f.close();
    return false;
  }
  outLen = f.read(buf, sz);
  f.close();
  return outLen == sz;
}

bool hasAesKey() {
  File f(InternalFS);
  if (!f.open(PATH_AES, FILE_O_READ)) return false;
  // Só considera que "existe chave" se o tamanho gravado estiver dentro
  // do intervalo aceite; caso contrário trata-se como se não existisse.
  bool ok = f.size() >= AES_KEY_MIN_LEN && f.size() <= AES_KEY_MAX_LEN;
  f.close();
  return ok;
}

bool aes_save(const uint8_t *key, size_t len) {
  return saveAesKey(key, len);
}

bool aes_load(uint8_t *buf, size_t bufLen, size_t &outLen) {
  return loadAesKey(buf, bufLen, outLen);
}

// ---------------- Persistent counter ----------------

// BUG CORRIGIDO (2026-07-07, rotina cloud, revisao dirigida a cifra
// AES-CTR): o formato anterior gravava so os 8 bytes crus do contador.
// counter_save() faz remove()+open()+write() (nao e uma transacao atomica
// do filesystem) e counter_load() tratava QUALQUER falha de leitura
// (ficheiro em falta OU tamanho errado, ex.: escrita cortada por perda de
// energia a meio de counter_save(), ~1x a cada ~21min de streaming
// continuo) da MESMA forma que "nunca guardado" - reserveNonceBatch()
// (Ble.cpp) interpretava isso como "comeca do zero", reutilizando nonces
// ja usados com a mesma chave AES (nunca rotacionada sem apagar a flash
// inteira) e quebrando a confidencialidade do CTR silenciosamente. Um
// magic number + checksum simples permite distinguir "ficheiro nunca
// criado" (primeiro arranque genuino, seguro comecar do zero) de
// "ficheiro existe mas esta corrompido" (NAO seguro assumir zero) via o
// parametro de saida opcional 'corrupted'.
namespace {
constexpr uint32_t kCounterMagic = 0x434E5452UL; // "CNTR", so para detetar corrupcao/versao antiga
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

bool counter_save(uint64_t counter) {
  CounterRecord rec{kCounterMagic, counter, counterChecksum(kCounterMagic, counter)};
  InternalFS.remove(PATH_COUNT);
  File f(InternalFS);
  if (!f.open(PATH_COUNT, FILE_O_WRITE)) {
    Serial.println("[Storage] failed to open counter for write");
    return false;
  }
  size_t n = f.write(reinterpret_cast<const uint8_t *>(&rec), sizeof(rec));
  f.close();
  return n == sizeof(rec);
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
    // Existe um ficheiro, mas nao bate certo (corrompido, escrita cortada,
    // ou formato antigo pre-2026-07-07) - distinto de "nunca guardado".
    // Quem chama NAO deve assumir counter=0 neste caso (ver
    // reserveNonceBatch() em Ble.cpp).
    if (corrupted) *corrupted = true;
    return false;
  }
  counter = rec.counter;
  return true;
}

bool counter_inc(uint64_t &counter) {
  uint64_t cur = 0;
  // Se ainda não existir contador guardado, counter_load() falha e
  // deixa "cur" a 0 — nesse caso começamos a contar a partir de 1,
  // por isso o resultado de counter_load() é ignorado aqui de propósito.
  (void)counter_load(cur);
  cur++;
  if (!counter_save(cur)) return false;
  counter = cur;
  return true;
}

// ---------------- Utility ----------------

bool clearAll() {
  // Tenta remover os três ficheiros; cada remove() devolve true só se o
  // ficheiro existia e foi apagado. Usa-se "||" (não "&&") porque é
  // normal que nem todos os ficheiros existam (ex.: sem calibração
  // ainda feita) — o objetivo é reportar se pelo menos algo foi limpo.
  bool a = InternalFS.remove(PATH_CALIB);
  bool b = InternalFS.remove(PATH_AES);
  bool c = InternalFS.remove(PATH_COUNT);
  return a || b || c;
}

// ---------------- Validation ----------------

bool validate() {
  // Percorre cada tipo de dado guardado e confirma que, quando existe,
  // consegue mesmo ser lido de volta. Não corrige nem apaga nada — só
  // reporta o estado por Serial, para ajudar a diagnosticar problemas
  // de flash/filesystem durante o desenvolvimento.
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
