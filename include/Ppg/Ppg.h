// ============================================================================
// Modulo Ppg (PhotoPletysmoGrafia)
// ----------------------------------------------------------------------------
// Responsavel por controlar o sensor optico MAX3010x (MAX30105/MAX30101),
// que usa LEDs (vermelho/infravermelho/verde) e um fotodetector para medir,
// atraves da pele (normalmente no dedo), variacoes de absorcao de luz
// causadas pelo sangue a circular. A partir desses sinais o modulo calcula:
//   - SpO2 (saturacao de oxigenio no sangue, em %), usando o algoritmo
//     oficial da Maxim (maxim_heart_rate_and_oxygen_saturation).
//   - Frequencia cardiaca (HR, em batimentos por minuto - BPM), usando um
//     pipeline de filtros digitais proprio (passa-baixo + passa-alto +
//     derivada + deteccao de cruzamento por zero) sobre o canal verde.
//
// Todo o trabalho pesado (comunicacao I2C com o sensor, leitura de amostras,
// calculo de SpO2/HR) corre numa unica task do FreeRTOS (ver ppgTask no
// .cpp), para nao bloquear o loop principal da aplicacao. Este ficheiro
// (.h) expõe apenas a API publica que o resto do firmware usa para:
//   1) inicializar o sensor (begin),
//   2) arrancar a task de leitura (startTask / isTaskRunning),
//   3) ler o ultimo resultado calculado (getLatest),
//   4) suspender/retomar a leitura durante uma verificacao de long-press do
//      botao de ligar/desligar (suspendForPowerCheck / resumeAfterPowerCheck),
//   5) desligar tudo em preparacao para o dispositivo entrar em System Off
//      (prepareForSystemOff), o modo de consumo minimo do nRF52840.
// ============================================================================

#ifndef PPG_H_
#define PPG_H_

#include <Arduino.h>

namespace Ppg {

// Snapshot (fotografia) do ultimo resultado calculado pelo sensor PPG.
// E' preenchido pela task interna e lido por quem chama getLatest().
struct Metrics {
  uint32_t spo2_timestamp_ms; // Instante (millis()) da ultima medicao de SpO2.
  int32_t spo2_value;         // Ultimo valor de SpO2 calculado, em percentagem (%).
  bool spo2_valid;            // true se spo2_value e' fiavel (dedo presente e algoritmo confiante).

  uint32_t hr_timestamp_ms;   // Instante (millis()) do ultimo batimento cardiaco detetado.
  float hr_bpm;                // Ultima frequencia cardiaca calculada, em batimentos por minuto.
  bool hr_valid;                // true se hr_bpm e' fiavel (batimento valido detetado recentemente).

  bool finger_present;        // true se o sensor detetou um dedo colocado sobre os LEDs.
};

// Inicializa o sensor MAX30105: procura-o no barramento I2C, configura-o e
// coloca-o em modo de espera (LEDs desligados / shutdown) ate a task
// comecar a fazer leituras. Deve ser chamada uma vez no arranque do
// firmware (ou implicitamente por startTask(), que a chama se necessario).
// Retorna true se o sensor foi encontrado e inicializado com sucesso.
bool begin();

// Cria (se ainda nao existir) a task do FreeRTOS responsavel por:
//   - medir SpO2 uma vez por minuto (ver SPO2_INTERVAL_MS no .cpp);
//   - medir a frequencia cardiaca (HR) em continuo enquanto o IMU reportar
//     "inactivity" (utilizador parado), poupando energia quando ha
//     movimento, altura em que a leitura de HR seria pouco fiavel.
// Chamar apos begin() ter sido executado com sucesso (ou deixa que
// startTask() invoque begin() automaticamente). Retorna true se a task
// ja estava a correr ou foi criada com sucesso; false em caso de falha
// de inicializacao do sensor ou de criacao da task.
bool startTask();

// Indica se a task de leitura PPG esta ativa e a correr neste momento.
// Util para diagnosticos/logs ou para decidir se vale a pena chamar
// getLatest().
bool isTaskRunning();

// Copia, de forma atomica (protegida por secao critica), o snapshot mais
// recente de metricas (SpO2 e HR) para 'out'. Pode ser chamada a qualquer
// momento por outras partes do firmware (ex.: para enviar dados por BLE ou
// mostrar no ecra). Retorna false se o sensor ainda nao foi inicializado
// (begin() nao teve sucesso).
bool getLatest(Metrics &out);

// Desliga os LEDs do sensor imediatamente. E' chamada quando se deteta o
// inicio de um long-press no botao de ligar/desligar, para poupar energia
// e evitar leituras enquanto se aguarda para confirmar se o utilizador
// realmente quer desligar o dispositivo (a task fica "pausada" em modo
// idle ate resumeAfterPowerCheck() ou prepareForSystemOff() serem chamadas).
void suspendForPowerCheck();

// Cancela a suspensao provocada por suspendForPowerCheck(), permitindo que
// a task volte a fazer leituras normais de SpO2/HR. Deve ser chamada
// quando o long-press e' interrompido/cancelado antes de se confirmar o
// desligar do dispositivo. Nao tem efeito se, entretanto, ja tiver sido
// pedido um desligar definitivo via prepareForSystemOff().
void resumeAfterPowerCheck();

// Prepara o sensor para o dispositivo entrar em System Off (modo de
// consumo de energia minimo do nRF52840, do qual so se sai por reset).
// Desliga os LEDs e coloca o MAX30101 em shutdown, e marca um pedido de
// desligar interno para que a task deixe de tentar medir. Deve ser
// chamada mesmo antes do dispositivo entrar efetivamente em System Off.
void prepareForSystemOff();

// *** DIAGNOSTICO TEMPORARIO (otimizacao de RAM) ***
// Devolve a menor quantidade de stack livre (em palavras de 32 bits) que
// a ppg_task alguma vez teve desde que arrancou. Serve para decidir, com
// dados reais, se PPG_TASK_STACK_WORDS pode ser reduzido com seguranca.
// Devolve 0 se a task ainda nao estiver a correr.
uint32_t taskStackHighWaterMarkWords();

} // namespace Ppg

#endif
