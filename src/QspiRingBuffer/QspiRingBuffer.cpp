// QspiRingBuffer.cpp - ver QspiRingBuffer.h para a visao geral e o layout na flash (setor de metadados + area de dados em slots).
// MetaWire: metadados globais gravados como journal no setor 0 (cada flush acrescenta uma copia nova, so apaga o setor quando enche) — NOR flash so apaga por setor, com ciclos limitados.
// SlotWire: 1 registo de dados por slot de 64 bytes, com magic+CRC32 proprios para validar cada um independentemente.
#include "QspiRingBuffer/QspiRingBuffer.h"

#include <Adafruit_SPIFlash.h>
#include <rtos.h>
#include <cstring>
#include <cstddef>
#include <cstdint>

namespace {

constexpr uint32_t kMetaMagic = 0x51524246; // "QRBF"
constexpr uint32_t kMetaVersion = 2;
constexpr uint32_t kSlotMagic = 0x52454344; // "RECD"

constexpr uint32_t kSectorSize = 4096;
constexpr uint32_t kMetaSector = 0;
constexpr uint32_t kDataStartSector = 1;
constexpr uint16_t kSlotSize = 64;

// throttling da escrita de metadados: flush so ao atingir 1 destes limites, para nao desgastar a flash a cada push/pop
constexpr uint32_t kMetaFlushMinOps = 2048;
constexpr uint32_t kMetaFlushIntervalMs = 15000;

struct __attribute__((packed)) SlotWire {
  uint32_t magic;
  uint32_t seq;
  uint32_t timestamp;
  uint16_t type;
  uint16_t len;
  uint8_t payload[QspiRingBuffer::kPayloadSize];
  uint32_t crc32;
};

static_assert(sizeof(SlotWire) == kSlotSize, "SlotWire size must be 64 bytes");

struct __attribute__((packed)) MetaWire {
  uint32_t magic;
  uint32_t version;
  uint32_t sector_size;
  uint32_t slot_size;
  uint32_t data_start_sector;
  uint32_t capacity_slots;
  uint32_t head;              // proximo slot livre onde push() escreve
  uint32_t tail;              // slot mais antigo ainda por consumir
  uint32_t count;
  uint32_t next_seq;
  uint32_t dropped;           // registos perdidos por sobrescrita antes de serem lidos
  uint32_t commit_seq;        // sequencia do proprio registo no journal, para achar a copia mais recente
  uint32_t crc32;
};

#if defined(EXTERNAL_FLASH_USE_QSPI)
Adafruit_FlashTransport_QSPI s_flashTransport;
#elif defined(EXTERNAL_FLASH_USE_SPI)
Adafruit_FlashTransport_SPI s_flashTransport(EXTERNAL_FLASH_USE_CS, EXTERNAL_FLASH_USE_SPI);
#else
#error "No external (Q)SPI flash transport defined for this board"
#endif

Adafruit_SPIFlash s_flash(&s_flashTransport);
bool s_started = false;
MetaWire s_meta = {}; // copia em RAM, espelha a flash exceto operacoes pendentes de flush
uint32_t s_totalSectors = 0;
uint32_t s_slotsPerSector = 0;
uint32_t s_metaSlotsPerSector = 0;
uint32_t s_metaNextSlot = 0;
uint32_t s_metaLastFlushMs = 0;
uint32_t s_metaOpsSinceFlush = 0;
bool s_metaDirty = false;

// mutex FreeRTOS (nao desativa interrupcoes, so bloqueia a task concorrente): s_meta e lido/escrito por storageTask (push, ~52Hz) e gattDumpTask (count/peek/advanceTail/pop) concorrentemente; taskENTER_CRITICAL nao serve aqui porque as funcoes fazem I/O de flash (SPI, pode demorar)
SemaphoreHandle_t s_mutex = nullptr;

void ensureMutex() {
  if (s_mutex == nullptr) {
    s_mutex = xSemaphoreCreateMutex();
  }
}

class LockGuard {
 public:
  LockGuard() {
    ensureMutex();
    xSemaphoreTake(s_mutex, portMAX_DELAY);
  }
  ~LockGuard() { xSemaphoreGive(s_mutex); }
  LockGuard(const LockGuard &) = delete;
  LockGuard &operator=(const LockGuard &) = delete;
};

// versao sem lock, para isEmpty() nao tentar readquirir o mutex (nao-reentrante) dentro de uma secao ja protegida
uint32_t countUnlocked() {
  if (!s_started) return 0;
  return s_meta.count;
}

// algumas variantes Seeed referem P25Q16H mas nem sempre existe em flash_devices.h
#ifndef P25Q16H
#define P25Q16H                                                               \
  {                                                                           \
    .total_size = (1UL << 21), /* 2 MiB */                                    \
        .start_up_time_us = 5000, .manufacturer_id = 0x85,                    \
    .memory_type = 0x60, .capacity = 0x15, .max_clock_speed_mhz = 55,         \
    .quad_enable_bit_mask = 0x02, .has_sector_protection = false,             \
    .supports_fast_read = true, .supports_qspi = true,                        \
    .supports_qspi_writes = true, .write_status_register_split = false,       \
    .single_status_byte = false, .is_fram = false,                            \
  }
#endif

static const SPIFlash_Device_t kKnownFlashDevices[] = {
    P25Q16H,                // flash usada nas variantes Seeed XIAO
    GD25Q16C,               // fallback comum de 2 MiB
    W25Q16JV_IQ             // fallback comum de 2 MiB
};

static constexpr size_t kKnownFlashDeviceCount =
    sizeof(kKnownFlashDevices) / sizeof(kKnownFlashDevices[0]);

// FNV-1a: checksum simples/rapido (sem tabelas), usado como "CRC" para detetar corrupcao/escrita incompleta
uint32_t fnv1a(const uint8_t *data, size_t len) {
  uint32_t hash = 2166136261u;
  for (size_t i = 0; i < len; i++) {
    hash ^= data[i];
    hash *= 16777619u;
  }
  return hash;
}

uint32_t metaCrc(const MetaWire &m) {
  return fnv1a(reinterpret_cast<const uint8_t *>(&m), offsetof(MetaWire, crc32));
}

uint32_t slotCrc(const SlotWire &s) {
  return fnv1a(reinterpret_cast<const uint8_t *>(&s), offsetof(SlotWire, crc32));
}

bool metaIsValid(const MetaWire &m) {
  if (m.magic != kMetaMagic || m.version != kMetaVersion) return false;
  if (m.sector_size != kSectorSize || m.slot_size != kSlotSize) return false;
  if (m.data_start_sector != kDataStartSector) return false;
  if (m.capacity_slots == 0) return false;
  if (m.head >= m.capacity_slots || m.tail >= m.capacity_slots) return false;
  if (m.count > m.capacity_slots) return false;
  if (metaCrc(m) != m.crc32) return false;
  return true;
}

uint32_t metaSlotAddress(uint32_t metaSlot) {
  return (kMetaSector * kSectorSize) + (metaSlot * sizeof(MetaWire));
}

// flash NOR apagada = todos os bits a 1 (0xFF); distingue "posicao livre" de "dados invalidos"
bool metaIsErased(const MetaWire &m) {
  const uint8_t *p = reinterpret_cast<const uint8_t *>(&m);
  for (size_t i = 0; i < sizeof(MetaWire); i++) {
    if (p[i] != 0xFF) return false;
  }
  return true;
}

bool readMetaSlot(uint32_t metaSlot, MetaWire &out) {
  if (metaSlot >= s_metaSlotsPerSector) return false;
  const uint32_t addr = metaSlotAddress(metaSlot);
  return s_flash.readBuffer(addr, reinterpret_cast<uint8_t *>(&out), sizeof(out)) == sizeof(out);
}

bool writeMetaSlot(uint32_t metaSlot, const MetaWire &in) {
  if (metaSlot >= s_metaSlotsPerSector) return false;
  const uint32_t addr = metaSlotAddress(metaSlot);
  return s_flash.writeBuffer(addr, reinterpret_cast<const uint8_t *>(&in), sizeof(in)) == sizeof(in);
}

// acha a copia mais recente e valida no journal (setor 0) + a proxima posicao livre; necessario porque um desligar inesperado pode deixar varias copias
bool scanMetaJournal(MetaWire &latest, uint32_t &nextMetaSlot) {
  bool found = false;
  uint32_t newestCommit = 0;
  uint32_t firstFree = s_metaSlotsPerSector;

  for (uint32_t i = 0; i < s_metaSlotsPerSector; i++) {
    MetaWire entry = {};
    if (!readMetaSlot(i, entry)) return false;

    if (metaIsErased(entry)) {
      if (firstFree == s_metaSlotsPerSector) firstFree = i;
      continue;
    }

    if (!metaIsValid(entry)) {
      continue;
    }

    if (!found || (int32_t)(entry.commit_seq - newestCommit) > 0) {
      newestCommit = entry.commit_seq;
      latest = entry;
      found = true;
    }
  }

  if (!found) return false;

  if (firstFree == s_metaSlotsPerSector) {
    nextMetaSlot = s_metaSlotsPerSector;
  } else {
    nextMetaSlot = firstFree;
  }
  return true;
}

// unica funcao que escreve metadados de facto na flash; as restantes so decidem QUANDO chama-la (maybeFlushMeta)
bool persistMetaNow() {
  if (!s_started) return false;

  if (s_metaSlotsPerSector == 0) {
    Serial.println("[QSPIRB] metadata journal invalido");
    return false;
  }

  if (s_metaNextSlot >= s_metaSlotsPerSector) {
    if (!s_flash.eraseSector(kMetaSector)) {
      Serial.println("[QSPIRB] erro a apagar setor de metadados");
      return false;
    }
    s_metaNextSlot = 0;
  }

  MetaWire out = s_meta;
  out.crc32 = 0;
  out.commit_seq = s_meta.commit_seq + 1;
  out.crc32 = metaCrc(out);

  if (!writeMetaSlot(s_metaNextSlot, out)) {
    Serial.println("[QSPIRB] erro a escrever journal de metadados");
    return false;
  }

  s_meta = out;
  s_metaNextSlot++;
  s_metaDirty = false;
  s_metaOpsSinceFlush = 0;
  s_metaLastFlushMs = millis();
  return true;
}

// adia a escrita ate kMetaFlushMinOps acumuladas OU kMetaFlushIntervalMs decorridos; sync() existe para forcar antes de um desligar controlado
bool maybeFlushMeta(bool force) {
  if (!s_metaDirty) return true;

  if (!force) {
    const uint32_t now = millis();
    const bool byOps = (s_metaOpsSinceFlush >= kMetaFlushMinOps);
    const bool byTime = (s_metaLastFlushMs == 0) || ((now - s_metaLastFlushMs) >= kMetaFlushIntervalMs);
    if (!byOps && !byTime) return true;
  }

  return persistMetaNow();
}

void markMetaDirty() {
  s_metaDirty = true;
  s_metaOpsSinceFlush++;
  (void)maybeFlushMeta(false);
}

uint32_t incIndex(uint32_t idx) {
  idx++;
  if (idx >= s_meta.capacity_slots) idx = 0;
  return idx;
}

uint32_t decIndex(uint32_t idx) {
  if (idx == 0) return (s_meta.capacity_slots > 0) ? (s_meta.capacity_slots - 1) : 0;
  return idx - 1;
}

uint32_t slotToDataSector(uint32_t slotIndex) {
  return slotIndex / s_slotsPerSector;
}

uint32_t slotAddress(uint32_t slotIndex) {
  const uint32_t dataSector = slotToDataSector(slotIndex);
  const uint32_t slotInSector = slotIndex % s_slotsPerSector;
  const uint32_t physicalSector = kDataStartSector + dataSector;
  return (physicalSector * kSectorSize) + (slotInSector * kSlotSize);
}

bool readSlot(uint32_t slotIndex, SlotWire &out) {
  const uint32_t addr = slotAddress(slotIndex);
  return s_flash.readBuffer(addr, reinterpret_cast<uint8_t *>(&out), sizeof(out)) == sizeof(out);
}

bool writeSlot(uint32_t slotIndex, const SlotWire &in) {
  const uint32_t addr = slotAddress(slotIndex);
  return s_flash.writeBuffer(addr, reinterpret_cast<const uint8_t *>(&in), sizeof(in)) == sizeof(in);
}

// apaga o setor de dados antes de comecar a escrever nele; se tail cair no mesmo setor, esses registos sao removidos logicamente primeiro (dropped++) para o estado ficar consistente com o erase
bool prepareHeadSectorForWrite() {
  if ((s_meta.head % s_slotsPerSector) != 0) return true;

  const uint32_t targetDataSector = slotToDataSector(s_meta.head);
  const uint32_t droppedBefore = s_meta.dropped;

  while (s_meta.count > 0 && slotToDataSector(s_meta.tail) == targetDataSector) {
    s_meta.tail = incIndex(s_meta.tail);
    s_meta.count--;
    s_meta.dropped++;
  }

  static bool s_dataLossWarned = false; // aviso unico, o continuo vai por BLE (DumpStatusPacket::data_loss_flag)
  if (!s_dataLossWarned && s_meta.dropped > droppedBefore) {
    s_dataLossWarned = true;
    Serial.println("[QSPIRB] AVISO: ring buffer cheio — a sobrescrever registos antigos ainda nao consumidos");
  }

  const uint32_t physicalSector = kDataStartSector + targetDataSector;
  if (!s_flash.eraseSector(physicalSector)) {
    Serial.print("[QSPIRB] erro a apagar setor de dados ");
    Serial.println(physicalSector);
    return false;
  }
  return true;
}

bool decodeSlot(const SlotWire &in, QspiRingBuffer::Record &out) {
  if (in.magic != kSlotMagic) return false;
  if (in.len > QspiRingBuffer::kPayloadSize) return false;
  if (slotCrc(in) != in.crc32) return false;

  out.seq = in.seq;
  out.timestamp = in.timestamp;
  out.type = in.type;
  out.len = in.len;
  memcpy(out.payload, in.payload, QspiRingBuffer::kPayloadSize);
  return true;
}

// implementacao real de format(), sem lock proprio para begin() poder chamar dentro do seu proprio LockGuard (mutex nao-reentrante)
bool formatUnlocked() {
  if (!s_flash.begin()) {
    Serial.println("[QSPIRB] format: flash.begin() falhou");
    return false;
  }

  s_totalSectors = s_flash.size() / kSectorSize;
  s_slotsPerSector = kSectorSize / kSlotSize;
  s_metaSlotsPerSector = kSectorSize / sizeof(MetaWire);

  if (s_totalSectors <= kDataStartSector || s_slotsPerSector == 0 || s_metaSlotsPerSector == 0) {
    Serial.println("[QSPIRB] format: geometria invalida");
    return false;
  }

  MetaWire fresh = {};
  fresh.magic = kMetaMagic;
  fresh.version = kMetaVersion;
  fresh.sector_size = kSectorSize;
  fresh.slot_size = kSlotSize;
  fresh.data_start_sector = kDataStartSector;
  fresh.capacity_slots = (s_totalSectors - kDataStartSector) * s_slotsPerSector;
  fresh.head = 0;
  fresh.tail = 0;
  fresh.count = 0;
  fresh.next_seq = 1; // 0 fica reservado para "nunca atribuido"
  fresh.dropped = 0;
  fresh.commit_seq = 0;
  fresh.crc32 = 0;

  if (!s_flash.eraseSector(kMetaSector)) {
    Serial.println("[QSPIRB] format: falha erase metadata");
    return false;
  }

  s_meta = fresh;
  s_metaNextSlot = 0;
  s_metaDirty = true;
  s_metaOpsSinceFlush = kMetaFlushMinOps; // forca persistMetaNow() a gravar ja, sem esperar pelo throttling normal
  s_metaLastFlushMs = 0;
  s_started = true;
  if (!persistMetaNow()) {
    s_started = false;
    return false;
  }
  Serial.print("[QSPIRB] format OK. capacidade=");
  Serial.println(s_meta.capacity_slots);
  Serial.print("[QSPIRB] meta flush policy ops=");
  Serial.print(kMetaFlushMinOps);
  Serial.print(" interval_ms=");
  Serial.println(kMetaFlushIntervalMs);
  return true;
}

} // namespace

namespace QspiRingBuffer {

bool begin(bool formatIfNeeded) {
  LockGuard lock;
  if (s_started) return true;

  uint8_t jedec[4] = {0};
  s_flashTransport.begin();
  const bool jedecOk = s_flashTransport.readCommand(SFLASH_CMD_READ_JEDEC_ID, jedec, 4);
  s_flashTransport.end();
  if (jedecOk) {
    Serial.print("[QSPIRB] JEDEC raw: 0x");
    Serial.print(jedec[0], HEX);
    Serial.print(" 0x");
    Serial.print(jedec[1], HEX);
    Serial.print(" 0x");
    Serial.println(jedec[2], HEX);
  } else {
    Serial.println("[QSPIRB] nao conseguiu ler JEDEC");
  }

  if (!s_flash.begin(kKnownFlashDevices, kKnownFlashDeviceCount)) {
    Serial.println("[QSPIRB] flash.begin() falhou (device nao reconhecido)");
    return false;
  }

  s_totalSectors = s_flash.size() / kSectorSize;
  s_slotsPerSector = kSectorSize / kSlotSize;
  s_metaSlotsPerSector = kSectorSize / sizeof(MetaWire);

  if (s_totalSectors <= kDataStartSector || s_slotsPerSector == 0 || s_metaSlotsPerSector == 0) {
    Serial.println("[QSPIRB] geometria invalida da flash");
    return false;
  }

  MetaWire loaded = {};
  uint32_t nextMetaSlot = 0;
  if (scanMetaJournal(loaded, nextMetaSlot)) {
    s_meta = loaded;
    s_metaNextSlot = nextMetaSlot;
    s_metaDirty = false;
    s_metaOpsSinceFlush = 0;
    s_metaLastFlushMs = millis();
    s_started = true;
    Serial.print("[QSPIRB] pronto. slots=");
    Serial.print(s_meta.capacity_slots);
    Serial.print(" count=");
    Serial.println(s_meta.count);
    Serial.print("[QSPIRB] meta flush policy ops=");
    Serial.print(kMetaFlushMinOps);
    Serial.print(" interval_ms=");
    Serial.println(kMetaFlushIntervalMs);
    return true;
  }

  if (!formatIfNeeded) {
    Serial.println("[QSPIRB] metadados invalidos e format desativado");
    return false;
  }

  return formatUnlocked();
}

bool format() {
  LockGuard lock;
  return formatUnlocked();
}

bool push(uint16_t type, const uint8_t *payload, uint16_t len, uint32_t timestamp) {
  LockGuard lock;
  if (!s_started) return false;
  if (len > kPayloadSize) return false;
  if (len > 0 && payload == nullptr) return false;

  // avisa 1x aos 90% (kRingBufferNearFullThreshold), ANTES de prepareHeadSectorForWrite() comecar a substituir dados
  static bool s_nearFullWarned = false;
  if (!s_nearFullWarned && s_meta.capacity_slots > 0 &&
      (static_cast<float>(s_meta.count) / s_meta.capacity_slots) >= kRingBufferNearFullThreshold) {
    s_nearFullWarned = true;
    Serial.println("[QSPIRB] AVISO: ring buffer a 90% da capacidade — exportar em breve antes de começar a substituir dados antigos");
  }

  if (!prepareHeadSectorForWrite()) return false;

  SlotWire slot = {};
  slot.magic = kSlotMagic;
  slot.seq = s_meta.next_seq++;
  if (s_meta.next_seq == 0) s_meta.next_seq = 1; // evita seq=0 apos overflow do uint32_t
  slot.timestamp = timestamp;
  slot.type = type;
  slot.len = len;
  if (len > 0) memcpy(slot.payload, payload, len);
  slot.crc32 = slotCrc(slot);

  if (!writeSlot(s_meta.head, slot)) {
    Serial.println("[QSPIRB] erro a escrever slot");
    return false;
  }

  if (s_meta.count == s_meta.capacity_slots) {
    s_meta.tail = incIndex(s_meta.tail); // cheio: novo registo substitui logicamente o mais antigo
  } else {
    s_meta.count++;
  }

  s_meta.head = incIndex(s_meta.head);
  markMetaDirty();
  return true;
}

// sem lock: so seguro chamado do mesmo contexto/task que chama push()
uint32_t nextSeq() {
  if (!s_started) return 0;
  return s_meta.next_seq;
}

bool peek(Record &out) {
  LockGuard lock;
  if (!s_started || s_meta.count == 0) return false;

  SlotWire slot = {};
  if (!readSlot(s_meta.tail, slot)) return false;
  return decodeSlot(slot, out);
}

bool peekLatest(Record &out) {
  LockGuard lock;
  if (!s_started || s_meta.count == 0) return false;

  // head aponta para o proximo slot livre -> o mais recente ja gravado esta em head-1; seguro mesmo com push() concorrente porque ambos usam o mesmo mutex
  const uint32_t latestIdx = decIndex(s_meta.head);
  SlotWire slot = {};
  if (!readSlot(latestIdx, slot)) return false;
  return decodeSlot(slot, out);
}

bool pop(Record &out) {
  LockGuard lock;
  if (!s_started || s_meta.count == 0) return false;

  // loop cobre o caso raro de corrupcao (ex. reset a meio de escrita): avanca e tenta o slot seguinte em vez de bloquear tudo
  while (s_meta.count > 0) {
    SlotWire slot = {};
    if (readSlot(s_meta.tail, slot) && decodeSlot(slot, out)) {
      s_meta.tail = incIndex(s_meta.tail);
      s_meta.count--;
      markMetaDirty();
      return true;
    }

    s_meta.tail = incIndex(s_meta.tail);
    s_meta.count--;
    s_meta.dropped++;
    markMetaDirty();
  }

  return false;
}

// evita repetir readSlot()+decodeSlot() de pop() quando o chamador ja fez peek() sobre o mesmo slot (caminho quente do streaming BLE, ate ~52 vezes/seg)
bool advanceTail() {
  LockGuard lock;
  if (!s_started || s_meta.count == 0) return false;
  s_meta.tail = incIndex(s_meta.tail);
  s_meta.count--;
  markMetaDirty();
  return true;
}

bool isEmpty() {
  LockGuard lock;
  return countUnlocked() == 0;
}

uint32_t count() {
  LockGuard lock;
  return countUnlocked();
}

uint32_t capacity() {
  LockGuard lock;
  if (!s_started) return 0;
  return s_meta.capacity_slots;
}

uint32_t droppedByErase() {
  LockGuard lock;
  if (!s_started) return 0;
  return s_meta.dropped;
}

bool sync() {
  LockGuard lock;
  if (!s_started) return false;
  return maybeFlushMeta(true); // ignora throttling, grava ja se houver alteracoes pendentes
}

bool selfTest() {
  Serial.println("[QSPIRB] self-test: inicio");
  if (!begin(true)) return false;
  if (!format()) return false;

  uint8_t p1[] = {1, 2, 3, 4};
  uint8_t p2[] = {5, 6, 7};
  uint8_t p3[] = {8, 9};

  if (!push(10, p1, sizeof(p1), 111)) return false;
  if (!push(11, p2, sizeof(p2), 222)) return false;
  if (!push(12, p3, sizeof(p3), 333)) return false;

  if (count() != 3) {
    Serial.println("[QSPIRB] self-test: count != 3");
    return false;
  }

  Record r = {};
  if (!peek(r) || r.seq != 1 || r.type != 10 || r.len != sizeof(p1)) {
    Serial.println("[QSPIRB] self-test: peek falhou");
    return false;
  }

  if (!pop(r) || r.seq != 1 || r.timestamp != 111) return false;
  if (!pop(r) || r.seq != 2 || r.timestamp != 222) return false;
  if (!pop(r) || r.seq != 3 || r.timestamp != 333) return false;
  if (!isEmpty()) return false;

  if (!format()) return false; // limpa no fim para deixar o modulo pronto para uso real

  Serial.println("[QSPIRB] self-test: OK");
  return true;
}

} // namespace QspiRingBuffer
