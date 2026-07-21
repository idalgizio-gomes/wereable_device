// =============================================================================
// QspiRingBuffer - implementacao
// -----------------------------------------------------------------------------
// Ver QspiRingBuffer.h para a visao geral do modulo e o diagrama de layout
// na flash (setor de metadados + area de dados dividida em slots).
//
// Este ficheiro define dois "formatos de fio" (structs "packed", ou seja,
// sem padding entre campos, para que o layout em bytes seja previsivel
// quando gravado/lido diretamente da flash):
//
//   - MetaWire: o registo de metadados globais do ring (head, tail, count,
//     proximo numero de sequencia, etc.). E gravado no setor 0 como um
//     "journal": cada operacao de escrita adiciona uma NOVA copia dentro
//     do mesmo setor (em vez de reescrever a mesma posicao), e ao ler
//     usa-se sempre a copia mais recente (maior commit_seq). Isto existe
//     porque a flash NOR so pode ser apagada por setor inteiro e tem um
//     numero de ciclos de escrita/apagamento limitado; escrever um novo
//     registo no journal e muito mais barato do que apagar o setor a
//     cada atualizacao. So quando o setor de metadados fica cheio de
//     copias e que ele e finalmente apagado (ver persistMetaNow).
//
//   - SlotWire: um registo individual de dados (amostra de sensor) tal
//     como e gravado num "slot" de 64 bytes na area de dados. Cada slot
//     tem o seu proprio magic number e CRC32, para que seja possivel
//     validar cada registo independentemente ao ler (deteta corrupcao
//     causada por escrita incompleta, desgaste da flash, etc.).
// =============================================================================

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

// Politica de "throttling" (atraso deliberado) da escrita dos metadados na
// flash: os metadados so sao persistidos de facto quando se atingir um
// destes limites, o que vier primeiro. Isto existe para reduzir o desgaste
// da flash: sem este limite, cada push()/pop() teria de gravar os
// metadados imediatamente, gastando ciclos de escrita/apagamento a um
// ritmo muito mais rapido do que o necessario. Ver maybeFlushMeta().
constexpr uint32_t kMetaFlushMinOps = 2048;     // Numero de operacoes (push/pop) acumuladas antes de forcar um flush.
constexpr uint32_t kMetaFlushIntervalMs = 15000; // Tempo maximo (ms) que os metadados podem ficar "sujos" sem serem gravados.

// Formato "em fio" de um registo de dados dentro de um slot de 64 bytes na
// area de dados da flash. "packed" garante que nao ha padding do
// compilador entre campos, para o layout em bytes ser exato.
struct __attribute__((packed)) SlotWire {
  uint32_t magic;     // Assinatura kSlotMagic; usada para distinguir um slot valido de flash "em branco" (0xFF) ou lixo.
  uint32_t seq;        // Numero de sequencia unico e crescente, atribuido no push().
  uint32_t timestamp;  // Timestamp da amostra (fornecido pelo chamador de push()).
  uint16_t type;       // Tipo/categoria do registo, definido pelo chamador.
  uint16_t len;        // Numero de bytes validos em payload.
  uint8_t payload[QspiRingBuffer::kPayloadSize]; // Dados brutos da amostra.
  uint32_t crc32;       // Checksum (FNV-1a) de todos os campos anteriores, para deteccao de corrupcao.
};

static_assert(sizeof(SlotWire) == kSlotSize, "SlotWire size must be 64 bytes");

// Formato "em fio" do registo de metadados globais, gravado no setor 0.
struct __attribute__((packed)) MetaWire {
  uint32_t magic;             // Assinatura kMetaMagic.
  uint32_t version;           // Versao do formato de metadados (para deteccao de incompatibilidade apos updates de firmware).
  uint32_t sector_size;       // Tamanho de setor usado quando estes metadados foram gravados (validacao de geometria da flash).
  uint32_t slot_size;         // Tamanho de slot usado (idem).
  uint32_t data_start_sector; // Primeiro setor da area de dados (idem).
  uint32_t capacity_slots;    // Numero total de slots disponiveis no buffer.
  uint32_t head;              // Indice do proximo slot livre onde push() vai escrever.
  uint32_t tail;              // Indice do slot mais antigo ainda por consumir (proximo a ser lido por peek()/pop()).
  uint32_t count;             // Numero de slots atualmente ocupados (validos, ainda nao consumidos).
  uint32_t next_seq;          // Proximo numero de sequencia a atribuir a um novo registo.
  uint32_t dropped;           // Contador cumulativo de registos perdidos por terem sido sobrescritos antes de serem lidos.
  uint32_t commit_seq;        // Numero de sequencia do proprio registo de metadados no journal (usado para achar a copia mais recente).
  uint32_t crc32;             // Checksum (FNV-1a) de todos os campos anteriores.
};

#if defined(EXTERNAL_FLASH_USE_QSPI)
Adafruit_FlashTransport_QSPI s_flashTransport;
#elif defined(EXTERNAL_FLASH_USE_SPI)
Adafruit_FlashTransport_SPI s_flashTransport(EXTERNAL_FLASH_USE_CS, EXTERNAL_FLASH_USE_SPI);
#else
#error "No external (Q)SPI flash transport defined for this board"
#endif

Adafruit_SPIFlash s_flash(&s_flashTransport);
bool s_started = false;      // true depois de begin()/format() terem inicializado a flash e os metadados com sucesso.
MetaWire s_meta = {};        // Copia em RAM dos metadados atuais (espelha o que esta gravado na flash, exceto operacoes ainda pendentes de flush).
uint32_t s_totalSectors = 0;      // Numero total de setores fisicos na flash detetada.
uint32_t s_slotsPerSector = 0;    // Quantos slots de dados cabem num setor (kSectorSize / kSlotSize).
uint32_t s_metaSlotsPerSector = 0; // Quantas copias de MetaWire cabem no setor de metadados (usado como journal).
uint32_t s_metaNextSlot = 0;      // Proxima posicao livre no journal de metadados onde a proxima copia sera escrita.
uint32_t s_metaLastFlushMs = 0;   // Timestamp (millis()) do ultimo flush de metadados persistido, para a regra de "byTime".
uint32_t s_metaOpsSinceFlush = 0; // Numero de operacoes (push/pop) acumuladas desde o ultimo flush, para a regra de "byOps".
bool s_metaDirty = false;         // true quando s_meta em RAM tem alteracoes ainda nao persistidas na flash.

// *** CORRECAO DE CONCORRENCIA (2026-07-08, rotina diaria) ***: s_meta e
// todo o resto do estado acima e lido/escrito por DUAS tasks FreeRTOS
// independentes — storageTask (main.cpp, chama push() a ~52Hz) e
// gattDumpTask (Ble.cpp, chama count()/peek()/advanceTail()/pop() ao
// transmitir por BLE) — sem qualquer secao critica antes desta correcao
// (o unico aviso escrito sobre isto, em Ble.cpp junto de
// kDumpCtrlResetReadings, ja dizia explicitamente que uma correcao
// completa "exigiria sincronizacao (mutex/secao critica) dentro do
// proprio QspiRingBuffer" mas ficava "fora do ambito" daquele comando
// pontual). Um context switch a meio de uma sequencia read-modify-write
// sobre s_meta.head/tail/count (ex.: storageTask a meio de push() quando
// gattDumpTask preempta com advanceTail()) pode perder um incremento/
// decremento e dessincronizar o estado logico do buffer do que
// realmente esta gravado na flash. Ao contrario do taskENTER_CRITICAL/
// taskEXIT_CRITICAL ja usado em Imu.cpp/Ppg.cpp (secoes muito curtas, so
// uma copia de struct), as funcoes deste ficheiro fazem I/O de flash
// (SPI, pode demorar) misturado com as mutacoes de s_meta — desativar
// interrupcoes durante esse tempo bloquearia a pilha BLE/temporizadores.
// Por isso usa-se aqui um mutex FreeRTOS (bloqueia a task concorrente,
// mas nao desativa interrupcoes), tomado/largado com um pequeno RAII
// (LockGuard, abaixo) para cobrir todos os pontos de retorno existentes
// sem reestruturar cada funcao para um unico ponto de saida.
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

// Versao interna (sem lock) de count(), para uso por isEmpty() sem
// tentar readquirir o mutex (nao-reentrante) dentro de uma secao ja
// protegida por LockGuard.
uint32_t countUnlocked() {
  if (!s_started) return 0;
  return s_meta.count;
}

// Algumas variantes Seeed referem P25Q16H mas este device nao existe
// em algumas versoes de flash_devices.h. Definimos localmente para garantir
// deteccao por JEDEC e inicializacao robusta.
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

// Hash FNV-1a: algoritmo de checksum simples e rapido, adequado para
// microcontroladores (sem tabelas de lookup, so operacoes basicas).
// Usado aqui como "CRC" para detetar corrupcao/escrita incompleta nos
// registos gravados na flash (nao e um CRC32 "verdadeiro" no sentido
// polinomial, mas cumpre o mesmo papel de deteccao de erros).
uint32_t fnv1a(const uint8_t *data, size_t len) {
  uint32_t hash = 2166136261u;
  for (size_t i = 0; i < len; i++) {
    hash ^= data[i];
    hash *= 16777619u;
  }
  return hash;
}

// Calcula o checksum de um MetaWire cobrindo todos os campos ANTES do
// proprio campo crc32 (offsetof garante isto), para que o checksum nao
// dependa de si mesmo.
uint32_t metaCrc(const MetaWire &m) {
  return fnv1a(reinterpret_cast<const uint8_t *>(&m), offsetof(MetaWire, crc32));
}

// Idem, mas para um SlotWire (registo de dados individual).
uint32_t slotCrc(const SlotWire &s) {
  return fnv1a(reinterpret_cast<const uint8_t *>(&s), offsetof(SlotWire, crc32));
}

// Valida um registo de metadados lido da flash: confirma a assinatura,
// versao e geometria esperadas, garante que os indices/contagens fazem
// sentido dentro da capacidade atual, e por fim confirma o checksum.
// Usado ao carregar o journal de metadados (scanMetaJournal) para
// distinguir uma copia valida de lixo/corrupcao/dados de uma versao
// antiga incompativel.
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

// Calcula o endereco fisico na flash da N-esima copia de metadados
// dentro do setor de metadados (journal). As copias sao gravadas em
// sequencia dentro do mesmo setor ate este ficar cheio (ver
// persistMetaNow).
uint32_t metaSlotAddress(uint32_t metaSlot) {
  return (kMetaSector * kSectorSize) + (metaSlot * sizeof(MetaWire));
}

// Uma flash NOR apagada tem todos os bits a 1 (0xFF por byte). Esta
// funcao deteta se uma posicao do journal de metadados ainda nao foi
// escrita desde o ultimo apagamento do setor, distinguindo "posicao
// livre" de "posicao com dados invalidos/corrompidos".
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

// Percorre todas as posicoes do journal de metadados (setor 0) a procura
// da copia mais recente e valida, e tambem descobre qual e a proxima
// posicao livre para escrita (a primeira posicao ainda "apagada").
// Isto e necessario porque, apos um desligar inesperado, pode haver
// varias copias de metadados no setor (algumas antigas, talvez uma
// parcialmente escrita/corrompida); a copia "correta" a usar e sempre a
// mais recente com commit_seq mais alto (usando aritmetica com sinal
// para lidar corretamente com o wrap-around do contador de 32 bits).
bool scanMetaJournal(MetaWire &latest, uint32_t &nextMetaSlot) {
  bool found = false;
  uint32_t newestCommit = 0;
  uint32_t firstFree = s_metaSlotsPerSector;

  for (uint32_t i = 0; i < s_metaSlotsPerSector; i++) {
    MetaWire entry = {};
    if (!readMetaSlot(i, entry)) return false;

    if (metaIsErased(entry)) {
      // Posicao ainda em branco: marca-a como candidata a proxima escrita
      // (so a primeira encontrada interessa) e continua a varrer o resto
      // do setor a procura de copias validas mais recentes que possam
      // existir por engano (nao deveria acontecer em condicoes normais).
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

// Grava de facto os metadados atuais (s_meta) na flash, adicionando uma
// nova copia ao journal. Esta e a UNICA funcao que efetivamente escreve
// metadados na flash; as restantes funcoes so decidem QUANDO chama-la
// (ver maybeFlushMeta) para poupar ciclos de escrita/apagamento.
bool persistMetaNow() {
  if (!s_started) return false;

  if (s_metaSlotsPerSector == 0) {
    Serial.println("[QSPIRB] metadata journal invalido");
    return false;
  }

  // O journal enche-se com o tempo (cada flush acrescenta uma copia).
  // Quando ja nao ha espaco livre no setor de metadados, e preciso
  // apagar o setor inteiro (NOR flash so apaga por setor) e recomecar o
  // journal do inicio antes de poder escrever a nova copia.
  if (s_metaNextSlot >= s_metaSlotsPerSector) {
    if (!s_flash.eraseSector(kMetaSector)) {
      Serial.println("[QSPIRB] erro a apagar setor de metadados");
      return false;
    }
    s_metaNextSlot = 0;
  }

  // commit_seq incrementa a cada escrita para permitir a scanMetaJournal
  // identificar sempre qual e a copia mais recente do journal.
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

// Decide se e altura de persistir os metadados na flash agora, ou se
// pode continuar a adiar (mantendo apenas a versao em RAM atualizada).
// Sem este adiamento, cada push()/pop() geraria uma escrita imediata no
// journal de metadados, o que desgastaria a flash muito mais depressa
// (e cada push()/pop() ja e frequente, ao contrario da escrita de
// dados, que so precisa de um novo slot). As duas condicoes que forcam
// o flush (kMetaFlushMinOps operacoes acumuladas OU
// kMetaFlushIntervalMs decorridos desde o ultimo flush) sao um
// compromisso entre durabilidade da flash e risco de perder o estado
// mais recente em caso de desligar inesperado — por isso sync() existe,
// para ser chamado explicitamente antes de um desligar controlado.
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

// Marca os metadados em RAM como alterados e tenta, de forma oportunista,
// fazer o flush segundo a politica de throttling (ver maybeFlushMeta).
// Chamada no fim de qualquer operacao que altere s_meta (push/pop).
void markMetaDirty() {
  s_metaDirty = true;
  s_metaOpsSinceFlush++;
  (void)maybeFlushMeta(false);
}

// Avanca um indice de slot logico (head ou tail), fazendo "wrap-around"
// para 0 ao atingir a capacidade total — e este wrap que torna o buffer
// "circular".
uint32_t incIndex(uint32_t idx) {
  idx++;
  if (idx >= s_meta.capacity_slots) idx = 0;
  return idx;
}

// Converte um indice de slot logico (0..capacity_slots-1) no indice do
// setor de DADOS a que pertence (0 = primeiro setor a seguir ao setor
// de metadados, ver kDataStartSector).
uint32_t slotToDataSector(uint32_t slotIndex) {
  return slotIndex / s_slotsPerSector; // relativo ao inicio da area de dados
}

// Traduz um indice de slot logico para o endereco fisico (em bytes) na
// flash onde esse slot esta gravado, combinando o setor fisico de dados
// com o deslocamento do slot dentro desse setor.
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

// A NOR flash apaga por setor. Ao iniciar escrita num setor novo, precisamos
// apagar o setor inteiro; se ele ainda tiver dados validos antigos, esses
// registos sao descartados (drop por setor).
//
// Esta funcao e chamada antes de cada push() e so faz algo quando head
// aponta exatamente para o PRIMEIRO slot de um setor de dados (ou seja,
// estamos prestes a comecar a escrever num setor ainda nao preparado
// neste "lap" do buffer circular). Nesse caso:
//   1. Se o tail (o registo mais antigo ainda por consumir) tambem cai
//      dentro desse mesmo setor, esses registos vao ser destruidos pelo
//      apagamento — por isso sao removidos logicamente primeiro
//      (avancando tail e incrementando o contador de "dropped"), para
//      que o estado do buffer (count/tail) va manter-se consistente com
//      o que realmente existe fisicamente na flash depois do erase.
//   2. So depois disso o setor e apagado fisicamente, deixando-o pronto
//      para receber novos slots (a escrita em NOR flash so pode
//      transformar bits de 1 para 0, por isso e preciso apagar — repor
//      tudo a 1 — antes de poder escrever dados novos nesse setor).
bool prepareHeadSectorForWrite() {
  if ((s_meta.head % s_slotsPerSector) != 0) return true;

  const uint32_t targetDataSector = slotToDataSector(s_meta.head);
  const uint32_t droppedBefore = s_meta.dropped;

  while (s_meta.count > 0 && slotToDataSector(s_meta.tail) == targetDataSector) {
    s_meta.tail = incIndex(s_meta.tail);
    s_meta.count--;
    s_meta.dropped++;
  }

  // Aviso único (não repetido a cada perda, para não inundar o log): a
  // primeira vez que o buffer começa a sobrescrever registos ainda não
  // consumidos, avisa uma vez. O sinal contínuo (para a app/dashboard)
  // vai por BLE em DumpStatusPacket::data_loss_flag (ver Ble.cpp) — este
  // print é só para diagnóstico durante desenvolvimento/série.
  static bool s_dataLossWarned = false;
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

// Converte um SlotWire "em fio" (tal como esta gravado na flash) para um
// Record "em memoria", validando primeiro que o slot e genuino (magic
// correto), que o comprimento declarado e plausivel, e que o checksum
// bate certo (ou seja, os bytes nao foram corrompidos nem a escrita
// ficou incompleta, ex.: por perda de energia a meio de um writeBuffer).
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

// (Re)cria os metadados do buffer do zero — implementacao real de
// format() (ver QspiRingBuffer.h), extraida para uma funcao interna
// SEM lock proprio para que begin() a possa chamar diretamente quando
// precisa de formatar (ja dentro do seu proprio LockGuard) sem tentar
// readquirir um mutex nao-reentrante. O format() publico (mais abaixo)
// e so um wrapper fino que toma o lock e chama esta funcao.
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

  // Metadados "em branco": head/tail/count a 0 (buffer vazio), proximo
  // numero de sequencia comeca em 1 (0 fica reservado para poder
  // distinguir "nunca atribuido" de um seq real, se necessario).
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
  fresh.next_seq = 1;
  fresh.dropped = 0;
  fresh.commit_seq = 0;
  fresh.crc32 = 0;

  // format() apaga sempre o setor de metadados (ao contrario da operacao
  // normal, onde as escritas se acumulam no journal) porque estamos a
  // reiniciar o buffer do zero: nao faz sentido manter copias antigas.
  if (!s_flash.eraseSector(kMetaSector)) {
    Serial.println("[QSPIRB] format: falha erase metadata");
    return false;
  }

  s_meta = fresh;
  s_metaNextSlot = 0;
  s_metaDirty = true;
  // Forca s_metaOpsSinceFlush a atingir logo o limiar de kMetaFlushMinOps
  // para que persistMetaNow() abaixo grave imediatamente os metadados
  // recem-formatados na flash, em vez de ficar apenas em RAM a espera do
  // throttling normal (ver maybeFlushMeta).
  s_metaOpsSinceFlush = kMetaFlushMinOps;
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

// Ver documentacao completa em QspiRingBuffer.h.
bool begin(bool formatIfNeeded) {
  LockGuard lock; // Protege s_meta/s_started contra push()/peek()/pop() concorrentes de outra task — ver aviso de concorrencia acima.
  if (s_started) return true; // Chamar begin() varias vezes e seguro (no-op apos a primeira).

  // Diagnostico JEDEC antes de iniciar o driver alto nivel: le o ID
  // JEDEC diretamente do chip de flash (comando de baixo nivel) so para
  // efeitos de log, ajudando a confirmar no terminal serie que tipo de
  // chip esta realmente instalado na placa antes de tentar usa-lo.
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

  // Tenta recuperar o estado existente do buffer lendo o journal de
  // metadados gravado na flash (isto e o que permite ao buffer
  // "sobreviver" a um reinicio ou desligar do dispositivo).
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

// Ver documentacao completa em QspiRingBuffer.h.
bool format() {
  LockGuard lock; // Ver formatUnlocked() acima e o aviso de concorrencia no topo do ficheiro.
  return formatUnlocked();
}

// Ver documentacao completa em QspiRingBuffer.h.
bool push(uint16_t type, const uint8_t *payload, uint16_t len, uint32_t timestamp) {
  LockGuard lock; // Ver aviso de concorrencia no topo do ficheiro — serializa com peek()/pop()/advanceTail() chamados por gattDumpTask.
  if (!s_started) return false;
  if (len > kPayloadSize) return false;
  if (len > 0 && payload == nullptr) return false;

  // Aviso antecipado, único (2026-07-03): a kRingBufferNearFullThreshold
  // (ver QspiRingBuffer.h — constante partilhada com Ble.cpp) da
  // capacidade, avisa UMA vez, ANTES de o buffer começar mesmo a
  // substituir dados antigos (isso só acontece em
  // prepareHeadSectorForWrite(), mais abaixo). Dá tempo a quem estiver a
  // monitorizar (ver DumpStatusPacket::data_loss_flag em Ble.cpp) de
  // exportar os dados antes de haver qualquer perda real.
  static bool s_nearFullWarned = false;
  if (!s_nearFullWarned && s_meta.capacity_slots > 0 &&
      (static_cast<float>(s_meta.count) / s_meta.capacity_slots) >= kRingBufferNearFullThreshold) {
    s_nearFullWarned = true;
    Serial.println("[QSPIRB] AVISO: ring buffer a 90% da capacidade — exportar em breve antes de começar a substituir dados antigos");
  }

  // Garante que o setor onde vamos escrever (o setor que contem o slot
  // "head") ja foi apagado e esta pronto a receber dados; se head cair
  // dentro de um buffer cheio, esta chamada tambem descarta os registos
  // antigos desse setor que ainda nao tinham sido consumidos.
  if (!prepareHeadSectorForWrite()) return false;

  SlotWire slot = {};
  slot.magic = kSlotMagic;
  slot.seq = s_meta.next_seq++;
  if (s_meta.next_seq == 0) s_meta.next_seq = 1; // Evita usar 0 como numero de sequencia apos dar a volta ao uint32_t.
  slot.timestamp = timestamp;
  slot.type = type;
  slot.len = len;
  if (len > 0) memcpy(slot.payload, payload, len);
  slot.crc32 = slotCrc(slot);

  if (!writeSlot(s_meta.head, slot)) {
    Serial.println("[QSPIRB] erro a escrever slot");
    return false;
  }

  // Se o buffer ja estava cheio, o novo registo substitui logicamente o
  // mais antigo: avanca-se tail (o registo que "desaparece") em vez de
  // aumentar count, porque a capacidade maxima ja foi atingida — este e
  // o comportamento essencial de um ring buffer ("os dados novos
  // empurram os antigos para fora").
  if (s_meta.count == s_meta.capacity_slots) {
    s_meta.tail = incIndex(s_meta.tail);
  } else {
    s_meta.count++;
  }

  s_meta.head = incIndex(s_meta.head);
  markMetaDirty();
  return true;
}

// Ver documentacao completa em QspiRingBuffer.h.
bool peek(Record &out) {
  LockGuard lock; // Ver aviso de concorrencia no topo do ficheiro — serializa com push() chamado por storageTask.
  if (!s_started || s_meta.count == 0) return false;

  SlotWire slot = {};
  if (!readSlot(s_meta.tail, slot)) return false;
  return decodeSlot(slot, out);
}

// Ver documentacao completa em QspiRingBuffer.h.
bool pop(Record &out) {
  LockGuard lock; // Ver aviso de concorrencia no topo do ficheiro — serializa com push() chamado por storageTask.
  if (!s_started || s_meta.count == 0) return false;

  // Normalmente o slot em tail e valido e o loop sai logo na primeira
  // iteracao. O loop existe para o caso raro de corrupcao (ex.: reset a
  // meio de uma escrita anterior): em vez de falhar logo, avanca-se
  // tail e tenta-se o slot seguinte, ate encontrar um registo valido ou
  // esgotar o buffer — assim um unico slot corrompido nao bloqueia
  // permanentemente a leitura de todos os registos a seguir a ele.
  while (s_meta.count > 0) {
    SlotWire slot = {};
    if (readSlot(s_meta.tail, slot) && decodeSlot(slot, out)) {
      s_meta.tail = incIndex(s_meta.tail);
      s_meta.count--;
      markMetaDirty();
      return true;
    }

    // Se houver corrupcao, descarta slot e tenta o seguinte.
    s_meta.tail = incIndex(s_meta.tail);
    s_meta.count--;
    s_meta.dropped++;
    markMetaDirty();
  }

  return false;
}

// Ver documentacao completa em QspiRingBuffer.h.
// *** OTIMIZACAO DE CPU/FLASH (2026-07-07, rotina diaria) ***: esta funcao
// existe para o chamador poder confirmar o consumo de um registo ja lido
// por peek() sem pagar o custo de o reler da flash. Antes desta funcao
// existir, o unico caminho disponivel para "avancar tail" apos um peek()
// bem sucedido era pop(), que faz sempre um novo readSlot() (transacao
// QSPI) + decodeSlot() (checksum FNV-1a sobre ~60 bytes + memcpy de 44
// bytes) — repetindo exatamente o trabalho que peek() já tinha acabado de
// fazer sobre o MESMO slot, so para deitar fora o resultado. No caminho
// quente do streaming BLE (gattDumpTask/peekImuPpgRecord em Ble.cpp), isto
// corria a ate ~52 registos/seg (taxa do IMU), ou seja, ate ~52 leituras
// QSPI + descodificacoes por segundo eram puro desperdicio, chegando a
// duplicar o numero de transacoes de flash nesse caminho. Ver Ble.cpp para
// os dois pontos onde pop() foi substituido por advanceTail().
bool advanceTail() {
  LockGuard lock; // Ver aviso de concorrencia no topo do ficheiro — serializa com push() chamado por storageTask.
  if (!s_started || s_meta.count == 0) return false;
  s_meta.tail = incIndex(s_meta.tail);
  s_meta.count--;
  markMetaDirty();
  return true;
}

// Ver documentacao completa em QspiRingBuffer.h.
bool isEmpty() {
  LockGuard lock;
  return countUnlocked() == 0; // Usa a versao sem lock: count() readquiriria o mutex (nao-reentrante) dentro desta secao.
}

// Ver documentacao completa em QspiRingBuffer.h.
uint32_t count() {
  LockGuard lock;
  return countUnlocked();
}

// Ver documentacao completa em QspiRingBuffer.h.
uint32_t capacity() {
  LockGuard lock;
  if (!s_started) return 0;
  return s_meta.capacity_slots;
}

// Ver documentacao completa em QspiRingBuffer.h.
uint32_t droppedByErase() {
  LockGuard lock;
  if (!s_started) return 0;
  return s_meta.dropped;
}

// Ver documentacao completa em QspiRingBuffer.h.
bool sync() {
  LockGuard lock; // Protege maybeFlushMeta()/persistMetaNow() da mesma forma que push()/pop() acima.
  if (!s_started) return false;
  return maybeFlushMeta(true); // force=true ignora o throttling normal e grava imediatamente se houver alteracoes pendentes.
}

// Ver documentacao completa em QspiRingBuffer.h.
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

  // Limpa no fim para deixar modulo pronto para uso real.
  if (!format()) return false;

  Serial.println("[QSPIRB] self-test: OK");
  return true;
}

} // namespace QspiRingBuffer
