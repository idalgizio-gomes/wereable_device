#ifndef CLOCK_H_
#define CLOCK_H_

#include <Arduino.h>

// =============================================================================
// Clock
// -----------------------------------------------------------------------------
// Este módulo implementa um "relógio" de tempo real (data/hora UTC) para o
// dispositivo, usando o periférico RTC2 do nRF52840 como base de tempo.
//
// O nRF52840 não tem uma bateria de relógio (RTC calendário) própria, por
// isso este módulo funciona assim:
//   1. Ao ligar, o RTC2 começa a contar a partir de 0 — não sabe que
//      horas/data são de verdade.
//   2. Quando o telemóvel/app envia a hora atual por BLE, setUtc() guarda
//      esse valor como "hora base" e passa a marcar o tempo como válido.
//   3. A partir daí, nowUtc() calcula a hora atual somando à hora base o
//      tempo que passou (medido em "ticks" do RTC2, que corre a
//      32.768 kHz), sem precisar de mais nenhuma comunicação.
//   4. Se o relógio nunca foi sincronizado (ou foi invalidado), isValid()
//      devolve false e nowUtc() devolve 0, para o resto do código saber
//      que não deve confiar na hora.
//
// É usado para dar timestamps a registos/eventos do dispositivo e para
// mostrar a hora/data ao utilizador (ex.: num ecrã).
// =============================================================================

namespace Clock {

// Inicializa o RTC2 como base de tempo (LFCLK 32.768 kHz).
// Deve ser chamada uma vez no arranque (setup()), antes de qualquer outra
// função deste módulo. É seguro chamar mais do que uma vez (não reinicia
// o relógio se já estiver inicializado). Devolve sempre true.
bool begin();

// Define epoch UTC (segundos Unix) recebido por BLE.
// Deve ser chamada sempre que chega um pacote de sincronização de hora
// (ex.: vindo da app no telemóvel). A partir desta chamada, isValid()
// passa a devolver true e nowUtc() passa a devolver a hora correta.
void setUtc(uint32_t epochUtc);

// Invalida o tempo atual (forca novo sync por BLE).
// Usar quando já não se pode confiar na hora atual (ex.: suspeita de
// deriva grande, ou reset de segurança) e é necessário voltar a
// sincronizar com o telemóvel antes de usar a hora outra vez.
void invalidate();

// True quando existe hora/data valida.
// Deve ser consultada antes de mostrar/usar a hora, para saber se já
// houve alguma sincronização por BLE (setUtc()) desde o arranque ou
// desde a última invalidate().
bool isValid();

// Epoch UTC atual (segundos). Devolve 0 se invalido.
// É o "agora" do dispositivo, calculado a partir da última sincronização
// mais o tempo decorrido no RTC2. Chamar sempre que for preciso saber a
// hora atual (ex.: para dar timestamp a um evento).
uint32_t nowUtc();

// Formata "HH:MM:SS" e "DD/MM/YYYY".
// Escreve a hora atual formatada em "out" (buffer fornecido pelo
// chamador, com capacidade "outLen"). Devolve false se "out" for nulo,
// o buffer for demasiado pequeno, ou o relógio ainda não estiver válido.
bool formatTime(char *out, size_t outLen);

// Escreve a data atual formatada ("DD/MM/YYYY") em "out". Mesmas regras
// de "out"/"outLen" e mesmas condições de falha que formatTime().
bool formatDate(char *out, size_t outLen);

// Escreve data e hora atuais juntas ("DD/MM/YYYY HH:MM:SS") em "out".
// Mesmas regras de "out"/"outLen" e mesmas condições de falha que
// formatTime()/formatDate(). Útil quando é preciso um único timestamp
// legível para logs/registos.
bool formatDateTime(char *out, size_t outLen);

} // namespace Clock

#endif
