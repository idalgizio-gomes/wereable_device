// =============================================================================
// QspiRingBuffer
// -----------------------------------------------------------------------------
// Modulo responsavel por gravar (e ler de volta) registos de sensores
// (amostras de IMU + PPG, por exemplo) numa memoria flash externa QSPI/SPI,
// usando uma estrutura de "ring buffer" (buffer circular) persistente.
//
// Porque existe:
//   O dispositivo (wearable de monitorizacao para pessoas com demencia)
//   precisa de guardar continuamente amostras de sensores mesmo quando nao
//   ha ligacao Bluetooth/telemovel para descarregar os dados em tempo real.
//   Como a RAM do microcontrolador e pequena e volatil, os registos sao
//   escritos numa flash externa (que sobrevive a reinicios/desligar) e
//   organizados como um buffer circular: quando o espaco acaba, os dados
//   mais antigos vao sendo substituidos pelos mais recentes (ver
//   "prepareHeadSectorForWrite" no .cpp).
//
// Layout na flash (visao geral):
//
//   Setor 0                 Setores 1..N (area de dados)
//   +----------------+      +--------+--------+--------+     +--------+
//   | Metadados      |      | Setor 1| Setor 2| Setor 3| ... | Setor N|
//   | (journal, ver  |      | (slots)| (slots)| (slots)|     | (slots)|
//   |  abaixo)       |      +--------+--------+--------+     +--------+
//   +----------------+
//
//   - "Setor de metadados" (setor 0): guarda um pequeno registo (MetaWire,
//     ver .cpp) com informacao global do ring: indices de head/tail,
//     contagem de elementos, numero de sequencia, etc. E gravado como um
//     "journal" (varias copias sucessivas dentro do mesmo setor) para
//     poupar ciclos de apagamento da flash (NOR flash so pode ser apagada
//     por setor inteiro, e tem um numero limitado de ciclos de escrita).
//
//   - "Area de dados" (setores seguintes): dividida em "slots" de tamanho
//     fixo (64 bytes cada). Cada slot guarda um registo (SlotWire, ver
//     .cpp) com o seu proprio magic number, numero de sequencia,
//     timestamp, tipo, comprimento, payload e CRC32 para deteccao de
//     corrupcao.
//
// Uso tipico:
//   1. Chamar begin() uma vez no arranque do firmware.
//   2. Chamar push() sempre que houver uma nova amostra para guardar.
//   3. Chamar peek()/pop() para ler/consumir os registos mais antigos
//      (por exemplo, ao sincronizar com uma app externa).
//   4. Chamar sync() antes de desligar o dispositivo, para garantir que os
//      metadados pendentes ficam gravados na flash.
// =============================================================================

#ifndef QSPI_RING_BUFFER_H_
#define QSPI_RING_BUFFER_H_

#include <Arduino.h>

namespace QspiRingBuffer {

// Numero maximo de bytes de dados uteis (payload) que cada registo pode
// transportar. Este valor, somado aos campos fixos do registo, define o
// tamanho total de cada "slot" gravado na flash (ver SlotWire no .cpp).
static constexpr uint16_t kPayloadSize = 44;

// Limiar de "quase cheio" do ring buffer (fracao da capacidade ocupada,
// 0.0-1.0). Constante partilhada entre QspiRingBuffer.cpp (aviso unico ao
// atingir o limiar, ver s_meta.count/s_meta.capacity_slots no .cpp) e
// Ble.cpp (publishDumpStatus(), que reporta o mesmo estado "quase cheio"
// via BLE) — mantida aqui para as duas partes nunca desalinharem.
static constexpr float kRingBufferNearFullThreshold = 0.90f;

// Representacao "em memoria" (ja descodificada) de um registo lido do
// ring buffer. E o que as funcoes peek()/pop() devolvem ao chamador.
struct Record {
  uint32_t seq;                  // Numero de sequencia unico e crescente do registo.
  uint32_t timestamp;            // Instante de captura da amostra (definido por quem chama push()).
  uint16_t type;                 // Tipo/categoria do registo (definido pelo chamador, ex.: IMU vs PPG).
  uint16_t len;                  // Numero de bytes validos em payload (<= kPayloadSize).
  uint8_t payload[kPayloadSize]; // Dados brutos do registo.
};

// Inicializa a flash QSPI externa e carrega os metadados do ring buffer
// a partir da flash (procurando a copia mais recente no journal de
// metadados). Deve ser chamada uma vez, tipicamente no setup() do
// firmware, antes de qualquer outra funcao deste modulo.
// Se os metadados nao existirem ou estiverem invalidos/corrompidos, o
// buffer e formatado automaticamente desde que formatIfNeeded=true; caso
// contrario a funcao falha (retorna false) sem alterar a flash.
// Retorna true se o modulo ficou pronto a usar.
bool begin(bool formatIfNeeded = true);

// (Re)cria os metadados do ring buffer do zero: apaga o setor de
// metadados e inicializa head/tail/count/sequencia a valores "vazios".
// E destrutiva para o estado logico do buffer (os registos antigos deixam
// de ser alcancaveis, mesmo que os bytes ainda existam fisicamente na
// area de dados ate serem sobrescritos). Usar apenas quando se quer
// mesmo reiniciar o buffer (ex.: primeiro arranque, ou recuperacao de
// erro). Retorna true se a formatacao foi bem sucedida.
bool format();

// Escreve um novo registo no topo (head) do ring buffer.
// - type: identificador definido pelo chamador para distinguir o tipo de
//   amostra (ex.: IMU, PPG, evento).
// - payload/len: dados brutos a gravar; len tem de ser <= kPayloadSize.
// - timestamp: opcional, pode ficar a 0 nesta fase caso o chamador ainda
//   nao tenha uma hora valida (ex.: relogio ainda nao sincronizado).
// Se o buffer estiver cheio, o registo mais antigo (tail) e descartado
// automaticamente para abrir espaco (comportamento tipico de ring
// buffer). Retorna true se o registo foi gravado com sucesso na flash.
bool push(uint16_t type, const uint8_t *payload, uint16_t len, uint32_t timestamp = 0);

// Le o registo mais antigo do buffer (tail) sem o remover. Util para
// inspecionar o proximo registo antes de decidir consumi-lo com pop().
// Retorna false se o buffer estiver vazio ou nao tiver sido inicializado.
bool peek(Record &out);

// Le e remove o registo mais antigo do buffer (tail), avancando o
// indice de tail. Se encontrar um slot corrompido (falha de CRC ou
// magic invalido) durante a leitura, descarta-o silenciosamente e tenta
// o slot seguinte, ate encontrar um registo valido ou esvaziar o buffer.
// Retorna false se o buffer estiver vazio ou nao houver nenhum registo
// valido para devolver.
bool pop(Record &out);

// Remove logicamente o registo em tail (avanca tail, decrementa count,
// marca os metadados como "sujos" para o proximo flush) SEM voltar a ler
// nem a descodificar o slot da flash. So e seguro chamar isto depois de
// um peek() bem sucedido sobre o MESMO registo, sem qualquer outra
// push()/pop()/advanceTail()/format() a acontecer entre os dois — ou
// seja, quando o chamador ja tem a certeza (por ja ter lido o registo
// com sucesso momentos antes) de que o slot em tail e valido, e so quer
// confirmar o consumo. Ao contrario de pop(), NAO tenta recuperar de
// slots corrompidos (nao ha nada para recuperar: o slot ja foi validado
// pelo peek() anterior). Pensado para o padrao "peek() -> usa o registo
// -> confirma remocao", ja usado pelo dump BLE (ver peekImuPpgRecord()
// e gattDumpTask() em Ble.cpp), onde reler o mesmo slot da flash com
// pop() so para o descartar seria uma leitura QSPI + CRC + memcpy
// redundantes por cada registo. Retorna false se o buffer ja estava
// vazio.
bool advanceTail();

// Indica se o buffer nao tem nenhum registo pendente para ler.
bool isEmpty();

// Numero de registos atualmente armazenados no buffer (ainda nao
// consumidos por pop()).
uint32_t count();

// Numero maximo de registos (slots) que o buffer consegue guardar,
// calculado a partir do tamanho total da flash e do tamanho do slot.
uint32_t capacity();

// Numero total de registos que foram perdidos por terem sido
// sobrescritos (apagados por setor) antes de serem consumidos, desde a
// ultima formatacao. Util para diagnostico/telemetria.
uint32_t droppedByErase();

// Forca a persistencia imediata dos metadados pendentes (head/tail/
// count/etc.) no journal de metadados da flash, ignorando as regras de
// "throttling" normalmente usadas para poupar ciclos de escrita (ver
// kMetaFlushMinOps/kMetaFlushIntervalMs no .cpp). Deve ser chamada antes
// de desligar o dispositivo (ou entrar em modos de baixo consumo em que
// a flash pode nao ser tocada de novo em breve), para minimizar a perda
// de estado caso o dispositivo desligue de forma inesperada.
// Retorna true se os metadados ja estavam sincronizados ou foram
// gravados com sucesso.
bool sync();

// Executa um self-test isolado deste modulo: formata o buffer, escreve
// alguns registos de teste, confirma que sao lidos corretamente (peek/
// pop) e no fim volta a formatar para deixar o modulo limpo e pronto
// para uso real. Pensado para ser chamado manualmente durante
// desenvolvimento/debug, nao faz parte do fluxo normal de operacao.
// Retorna true se todas as verificacoes passaram.
bool selfTest();

} // namespace QspiRingBuffer

#endif
