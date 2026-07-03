// ============================================================================
// Ppg.cpp
// ----------------------------------------------------------------------------
// Implementacao do modulo PPG (ver Ppg.h para a visao geral e a API
// publica). Aqui vivem:
//   - A configuracao de baixo nivel do sensor MAX3010x para os dois modos
//     de operacao que usamos (modo SpO2 vs modo HR).
//   - O pipeline de filtros digitais que transforma o sinal bruto do canal
//     verde em batimentos cardiacos (BPM).
//   - A task do FreeRTOS (ppgTask) que decide, a cada iteracao, se deve
//     medir SpO2 (uma vez por minuto) e/ou HR (enquanto o utilizador
//     estiver parado, segundo o IMU), e que respeita pedidos de
//     suspensao/desligar vindos do resto do firmware.
// ============================================================================

#include "Ppg/Ppg.h"

#include "Imu/Imu.h"
#include "Clock/Clock.h"
#include <MAX30105.h>
#include <spo2_algorithm.h>
#include <Wire.h>
#include <rtos.h>
#include <math.h>
#include <stdio.h>

// PPG externo ligado em D4/D5 => usar apenas o barramento Wire externo.
#define PPG_USE_EXTERNAL_WIRE_ONLY 1


namespace {

MAX30105 g_sensor;             // Driver/objeto do sensor MAX30105 (biblioteca SparkFun).
bool g_started = false;        // true depois de begin() inicializar o sensor com sucesso.
TaskHandle_t g_taskHandle = nullptr;   // Handle da task FreeRTOS de leitura (ppgTask).
volatile bool g_taskRunning = false;   // true enquanto ppgTask() estiver a correr (usado por isTaskRunning()).
Ppg::Metrics g_latest = {};    // Ultimo snapshot de metricas calculado; protegido por secoes criticas.
TwoWire *g_ppgBus = nullptr;   // Barramento I2C onde o sensor foi encontrado (Wire ou Wire1).
const char *g_ppgBusName = "N/A"; // Nome do barramento, apenas para logs.

// --- Parametros de temporizacao/configuracao da task ---
constexpr uint32_t SPO2_INTERVAL_MS = 30000;        // Intervalo entre medicoes de SpO2 (30 s; comentario no .h diz "1/min" mas o valor real e' 30s).
constexpr uint32_t HR_SAMPLE_INTERVAL_MS = 10;       // Intervalo minimo entre amostras sucessivas do pipeline de HR.
constexpr uint32_t TASK_LOOP_DELAY_IDLE_MS = 200;    // Pausa da task quando nao ha streaming de HR ativo (poupa CPU/energia).
constexpr uint32_t TASK_LOOP_DELAY_HR_MS = 2;        // Pausa da task quando o streaming de HR esta ativo (precisa de amostrar rapido, ~100 Hz).
constexpr uint32_t HR_STREAM_STOP_HOLDOFF_MS = 3000; // Tempo de tolerancia apos deixar de haver "inactivity" antes de desligar o streaming de HR (evita ligar/desligar aos saltos).
constexpr uint32_t kManualHrMaxDurationMs = 30000;   // Limite superior para requestManualHr(), para nao gastar bateria indefinidamente por um pedido esquecido.
// *** OTIMIZAÇÃO DE RAM (2ª ronda, com dados reais de hardware) ***:
// reduzido de 1152 para 640 words (-2048 bytes / -3584 bytes face ao
// valor original de 1536). Justificação: captura real de
// uxTaskGetStackHighWaterMark() em 2026-07-03 (ver DEBUG_STACK_WATERMARKS
// em main.cpp e PROJECT_STATUS.md) mostrou apenas ~160 words realmente
// usadas de 1152 reservadas (free=992/1152, ~86% livre) durante ~30s de
// uso normal (streaming BLE ativo, sem forçar HR/SpO2). 640 words mantém
// ainda ~3x de margem sobre esse uso observado (640-160=480 words livres
// esperadas). Este é o corte mais apertado dos três (storage_task e
// ble_gatt_dump_task ficam com mais margem) porque a task chama o driver
// MAX30105 e o algoritmo de SpO2 da Maxim (maxim_heart_rate_and_
// oxygen_saturation), cuja profundidade de chamadas internas é mais
// difícil de estimar sem medir — os arrays grandes (g_irBuffer/
// g_redBuffer, 100 amostras cada) já estão fora da stack (globais/
// estáticos), não contam aqui. Ainda por confirmar em hardware real com
// este novo valor — reativar DEBUG_STACK_WATERMARKS e validar que
// free_words continua confortável acima de 0, incluindo durante uma
// medição de SpO2 completa (ramo mais pesado desta task).
constexpr uint16_t PPG_TASK_STACK_WORDS = 640;       // Tamanho da stack (em palavras) atribuida a ppgTask.
constexpr uint32_t FINGER_THRESHOLD = 50000;         // Valor minimo de luz IR refletida para se considerar que ha um dedo sobre o sensor.
constexpr int32_t SPO2_BUFFER_LEN = 100;             // Numero de amostras (IR+Red) recolhidas para cada calculo de SpO2 (exigido pelo algoritmo da Maxim).

uint32_t g_irBuffer[SPO2_BUFFER_LEN];   // Buffer de amostras do canal infravermelho, usado so' no calculo de SpO2.
uint32_t g_redBuffer[SPO2_BUFFER_LEN];  // Buffer de amostras do canal vermelho, usado so' no calculo de SpO2.
bool g_hrStreaming = false;             // true quando o sensor esta configurado no modo continuo de HR (LEDs Red+IR+Green).
uint32_t g_lastHrSampleMs = 0;          // Timestamp da ultima amostra de HR processada (para respeitar HR_SAMPLE_INTERVAL_MS).
uint32_t g_inactOffSinceMs = 0;         // Timestamp de quando a "inactivity" deixou de ser verdadeira (usado no holdoff antes de parar o streaming de HR).
volatile bool g_shutdownRequested = false;     // true depois de prepareForSystemOff(): a task deixa de medir definitivamente.
volatile bool g_suspendForPowerCheck = false;  // true durante um long-press do botao de power em validacao: a task pausa temporariamente.
volatile uint32_t g_manualHrDeadlineMs = 0;    // millis() ate quando um pedido requestManualHr() ainda esta ativo (0 = nenhum pedido pendente).
volatile bool g_manualSpo2Requested = false;   // true depois de requestManualSpo2(): forca uma medicao de SpO2 na proxima iteracao da task, sem esperar por SPO2_INTERVAL_MS.

// Formata a data/hora atual (vinda do modulo Clock) numa string, para usar
// em mensagens de log. Se o relogio ainda nao estiver disponivel, escreve
// uma data "zero" em vez de deixar a string vazia/lixo.
void stampDateTime(char *out, size_t outLen) {
  if (!Clock::formatDateTime(out, outLen)) {
    snprintf(out, outLen, "00/00/0000 00:00:00");
  }
}

// --- Recuperacao do barramento I2C externo (SDA/SCL) ---
// Por vezes, se o firmware reiniciar a meio de uma transacao I2C, um
// escravo (o sensor) pode ficar "preso" a segurar a linha SDA em LOW,
// bloqueando todo o barramento. As duas funcoes seguintes verificam esse
// estado e tentam desbloquear manualmente o barramento (gerando pulsos de
// clock e uma condicao STOP) antes de o inicializar como I2C normal.
#if defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
// Verifica se as linhas SDA e SCL estao ambas em HIGH (repouso), como
// seria de esperar num barramento I2C livre/saudavel.
bool externalBusLinesHigh() {
  pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
  pinMode(PIN_WIRE_SCL, INPUT_PULLUP);
  delay(2);
  const int sda = digitalRead(PIN_WIRE_SDA);
  const int scl = digitalRead(PIN_WIRE_SCL);
  Serial.print("[PPG] Wire linhas SDA/SCL=");
  Serial.print(sda);
  Serial.print("/");
  Serial.println(scl);
  return (sda == HIGH) && (scl == HIGH);
}

// Tenta desbloquear manualmente o barramento I2C externo, caso alguma
// linha esteja presa em LOW. Gera ate 18 pulsos de clock em SCL (o
// suficiente para um escravo terminar qualquer byte que estivesse a meio
// de transmitir) e depois forca uma condicao STOP manual, devolvendo o
// controlo das linhas ao periferico I2C normal.
bool recoverExternalI2cBus() {
  if (externalBusLinesHigh()) return true;

  Serial.println("[PPG] tentativa de recovery do Wire externo");

  pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
  pinMode(PIN_WIRE_SCL, OUTPUT);
  digitalWrite(PIN_WIRE_SCL, HIGH);
  delayMicroseconds(10);

  for (int i = 0; i < 18 && digitalRead(PIN_WIRE_SDA) == LOW; i++) {
    digitalWrite(PIN_WIRE_SCL, LOW);
    delayMicroseconds(10);
    digitalWrite(PIN_WIRE_SCL, HIGH);
    delayMicroseconds(10);
  }

  // Forca uma condicao STOP.
  pinMode(PIN_WIRE_SDA, OUTPUT);
  digitalWrite(PIN_WIRE_SDA, LOW);
  delayMicroseconds(10);
  digitalWrite(PIN_WIRE_SCL, HIGH);
  delayMicroseconds(10);
  pinMode(PIN_WIRE_SDA, INPUT_PULLUP);
  delayMicroseconds(10);

  return externalBusLinesHigh();
}
#endif

struct HrFilterState {
  // Mantido apenas para compatibilidade de compilacao.
};

// Reduz a corrente de todos os LEDs do sensor (vermelho/IR/verde) para
// zero, sem o colocar em shutdown. Usado antes de desligar o sensor por
// completo ou ao alternar entre os modos SpO2/HR.
void ledsOff() {
  g_sensor.setPulseAmplitudeRed(0);
  g_sensor.setPulseAmplitudeIR(0);
  g_sensor.setPulseAmplitudeGreen(0);
}

// Coloca o sensor em repouso: apaga os LEDs e entra em shutdown (modo de
// baixo consumo do proprio MAX3010x, mas ainda contactavel por I2C).
// E' o estado "normal" entre medicoes, para poupar energia.
void sensorIdle() {
  ledsOff();
  g_sensor.shutDown();
}

// Garante, de forma imediata e sem depender do estado interno da task,
// que os LEDs ficam desligados e o sensor em shutdown. Ao contrario de
// sensorIdle(), acorda o sensor primeiro (wakeUp) para assegurar que os
// comandos de "apagar LED" sao mesmo aplicados, e tambem desliga o LED de
// proximidade. Usada em suspendForPowerCheck()/prepareForSystemOff(), que
// podem ser chamadas a qualquer momento, fora do fluxo normal da task.
void forceLedsOffNow() {
  if (!g_started) return;
  g_sensor.wakeUp();
  g_sensor.setPulseAmplitudeRed(0);
  g_sensor.setPulseAmplitudeIR(0);
  g_sensor.setPulseAmplitudeGreen(0);
  g_sensor.setPulseAmplitudeProximity(0);
  g_sensor.shutDown();
}

// ----------------------------------------------------------------------------
// Pipeline de deteccao de batimento cardiaco (canal verde)
// ----------------------------------------------------------------------------
// O sinal bruto do LED verde (raw) contem: uma componente continua/lenta
// (variacoes de perfusao, movimento, luz ambiente) e uma componente
// periodica rapida causada pelos batimentos cardiacos (a "onda de pulso").
// As funcoes abaixo aplicam, em sequencia, um pipeline classico de deteccao
// de batimentos:
//   raw -> passa-baixo (remove ruido de alta frequencia)
//        -> passa-alto  (remove a deriva/offset lento, so' sobra a pulsacao)
//        -> derivada    (realca as subidas/descidas rapidas do pulso)
//        -> deteccao de cruzamento por zero (identifica o pico do batimento)
// Cada filtro mantem o seu proprio estado em variaveis "static", por isso
// so' deve haver uma "instancia logica" deste pipeline a correr de cada
// vez (o que e' o caso: so' a ppgTask os chama).
// === LOW PASS FILTER 1º Order (Fc ~ 5 Hz, Fs = 100 Hz) ===
float lowPassFilter(float x) {
  static float Fs = 100.0;
  static float Ts = 1.0 / Fs;
  static float fc = 5;
  static float Rc = 1.0 / (2.0 * PI * fc);
  const float alpha = Ts / (Rc + Ts);
  static float prevY = 0;
  static float y = 0;
  y = prevY + alpha * (x - prevY);
  prevY = y;
  return y;
}

// === HIGH PASS FILTER 1º Order (Fc ~ 0.5 Hz, Fs = 100 Hz) ===
float highPassFilter(float x) {
  static float Fs = 100.0;
  static float Ts = 1.0 / Fs;
  static float fc = 0.5;
  static float Rc = 1.0 / (2.0 * PI * fc);
  const float alpha = Rc / (Rc + Ts);
  static float prevX = 0;
  static float prevY = 0;
  float y = alpha * (prevY + x - prevX);
  prevX = x;
  prevY = y;
  return y;
}

// Derivada discreta simples: diferenca entre a amostra atual e a anterior.
// Transforma o sinal filtrado numa curva que cruza o zero exatamente no
// pico de cada batimento, o que facilita a deteccao a seguir.
float derivative(float x) {
  static float prev = 0;
  float y = x - prev;
  prev = x;
  return y;
}

// Deteta um batimento cardiaco quando a derivada do sinal passa de
// positiva para negativa/zero (um pico foi ultrapassado). Inclui um
// "anti-rebote" temporal: ignora deteccoes a menos de 300 ms da anterior,
// o que corresponde a um limite fisiologico de 200 BPM (batimentos mais
// rapidos do que isso sao tratados como ruido/artefacto, nao um batimento real).
bool detectHeartbeat(float diff) {
  static float prev = 0;
  static unsigned long lastBeat = 0;

  bool beatDetected = false;

  // Zero crossing POS -> NEG
  if (prev > 0 && diff <= 0) {
    unsigned long now = millis();

    // Anti-rebote: ignora falsos picos < 300ms (200 BPM máx)
    if (now - lastBeat > 300) {
      beatDetected = true;
      lastBeat = now;
    }
  }

  prev = diff;
  return beatDetected;
}

// Converte o intervalo de tempo entre dois batimentos consecutivos (dt, em
// ms) numa frequencia cardiaca instantanea em BPM (60000 ms / dt). So'
// aceita valores de dt fisiologicamente plausiveis (entre 300 e 2000 ms,
// ou seja 30-200 BPM); fora desse intervalo mantem o ultimo valor calculado.
float computeBPM() {
  static unsigned long lastBeatTime = 0;
  static float bpm = 0;

  unsigned long now = millis();
  int dt = now - lastBeatTime;

  if (dt > 300 && dt < 2000) { // 30–200 BPM
    bpm = 60000.0 / dt;
  }

  lastBeatTime = now;
  return bpm;
}

// Suaviza o valor de BPM com uma media movel simples das ultimas N=5
// leituras, para reduzir a oscilacao batimento-a-batimento e apresentar um
// valor mais estavel ao utilizador.
// === MOVING AVERAGE FOR BPM ===
float smoothBPM(float bpm) {
  const int N = 5;
  static float buf[N];
  static int idx = 0;
  static bool filled = false;
  static float sum = 0;

  sum -= buf[idx];
  buf[idx] = bpm;
  sum += bpm;

  idx++;
  if (idx >= N) {
    idx = 0;
    filled = true;
  }

  if (!filled) {
    return sum / idx;
  }

  return sum / N;
}

// Configura o MAX3010x para o modo usado na medicao de SpO2: acorda o
// sensor e liga os LEDs vermelho+IR (ledMode=2) com brilho e taxa de
// amostragem adequados ao algoritmo maxim_heart_rate_and_oxygen_saturation
// (que precisa de amostras sincronizadas de Red e IR). Estes parametros
// (brilho, media de amostras, largura de pulso, gama do ADC) seguem os
// valores recomendados pela biblioteca/exemplos da SparkFun/Maxim.
void setupForSpo2() {
  g_sensor.wakeUp();
  const byte ledBrightness = 60;
  const byte sampleAverage = 4;
  const byte ledMode = 2;      // Red + IR
  const byte sampleRate = 100;
  const int pulseWidth = 411;
  const int adcRange = 4096;
  g_sensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
}

// Configura o MAX3010x para o modo usado na medicao continua de HR:
// acorda o sensor e ativa os 3 LEDs (Red+IR+Green, ledMode=3), mas de
// seguida desliga explicitamente Red e IR, deixando apenas o LED verde
// ativo. O canal verde e' o preferido para deteccao de batimento porque
// tem melhor relacao sinal/ruido para variacoes de volume sanguineo na
// pele e consome menos energia do que manter os tres LEDs ligados.
void setupForHr() {
  g_sensor.wakeUp();
  const byte ledBrightness = 0x5F;
  const byte sampleAverage = 8;
  const byte ledMode = 3;      // Red + IR + Green
  const int sampleRate = 100;
  const int pulseWidth = 411;
  const int adcRange = 4096;
  g_sensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
  g_sensor.setPulseAmplitudeRed(0);
  g_sensor.setPulseAmplitudeIR(0);
}

// Liga o "streaming" continuo de HR (se ainda nao estiver ligado):
// configura o sensor no modo HR (LED verde) e reinicia o temporizador de
// amostragem. Chamada pela task quando o IMU reporta inatividade.
void startHrStreaming() {
  if (g_hrStreaming) return;
  setupForHr();
  g_hrStreaming = true;
  g_lastHrSampleMs = 0;
  Serial.println("[PPG] HR stream ON");
}

// Desliga o streaming de HR: coloca o sensor em repouso (sensorIdle) e
// reinicia os contadores associados. Chamada quando vai comecar uma
// medicao de SpO2, quando o utilizador deixa de estar inativo (apos o
// holdoff) ou quando a task e' suspensa/desligada.
void stopHrStreaming() {
  if (!g_hrStreaming) return;
  sensorIdle();
  g_hrStreaming = false;
  g_lastHrSampleMs = 0;
  g_inactOffSinceMs = 0;
  Serial.println("[PPG] HR stream OFF");
}

// Espera (fazendo polling nao bloqueante via vTaskDelay, para nao
// monopolizar o CPU/scheduler) ate o sensor ter uma nova amostra
// disponivel na FIFO, ou ate se esgotar o timeout indicado. Retorna false
// em caso de timeout (por exemplo, sensor desligado ou sem resposta).
bool waitSampleAvailable(uint32_t timeoutMs) {
  const uint32_t t0 = millis();
  while (!g_sensor.available()) {
    g_sensor.check();
    if ((millis() - t0) >= timeoutMs) {
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  return true;
}

// Executa uma medicao completa de SpO2 (e, como subproduto, de HR vindo
// do mesmo algoritmo). Fluxo:
//   1) configura o sensor no modo SpO2 e verifica se ha um dedo presente
//      (comparando a leitura de IR com FINGER_THRESHOLD);
//   2) se houver dedo, recolhe SPO2_BUFFER_LEN (100) pares de amostras
//      Red/IR, uma de cada vez, respeitando timeouts e voltando a
//      verificar a presenca do dedo a cada amostra (se o dedo for
//      retirado a meio, a medicao e' abortada);
//   3) corre o algoritmo oficial da Maxim (maxim_heart_rate_and_oxygen_
//      saturation) sobre os buffers recolhidos, que devolve SpO2 e HR
//      juntamente com flags de validade;
//   4) desliga sempre o sensor no fim (via o lambda 'finish', que garante
//      sensorIdle() em todos os caminhos de saida, sucesso ou falha).
// Retorna true apenas se o SpO2 calculado for valido.
bool measureSpo2(int32_t &spo2, bool &validSpo2, int32_t &hr, bool &validHr, bool &fingerPresent) {
  auto finish = [&](bool ret) {
    sensorIdle();
    return ret;
  };

  setupForSpo2();
  g_sensor.check();
  const uint32_t irCheck = g_sensor.getIR();

  fingerPresent = irCheck >= FINGER_THRESHOLD;
  if (!fingerPresent) {
    validSpo2 = false;
    validHr = false;
    return finish(false);
  }

  for (int i = 0; i < SPO2_BUFFER_LEN; i++) {
    if (!waitSampleAvailable(250)) {
      validSpo2 = false;
      validHr = false;
      return finish(false);
    }

    g_redBuffer[i] = g_sensor.getRed();
    g_irBuffer[i]  = g_sensor.getIR();
    g_sensor.nextSample();

    if (g_irBuffer[i] < FINGER_THRESHOLD) {
      validSpo2 = false;
      validHr = false;
      fingerPresent = false;
      return finish(false);
    }
  }

  int8_t vSpo2 = 0;
  int8_t vHr = 0;
  // Algoritmo de referencia da Maxim: analisa as 100 amostras Red/IR e
  // devolve SpO2 (%), HR (BPM) e um indicador de validade (vSpo2/vHr) para
  // cada um, com base na qualidade/periodicidade do sinal.
  maxim_heart_rate_and_oxygen_saturation(
      g_irBuffer, SPO2_BUFFER_LEN, g_redBuffer,
      &spo2, &vSpo2, &hr, &vHr);

  validSpo2 = (vSpo2 != 0);
  validHr = (vHr != 0);
  return finish(validSpo2);
}

// Processa uma unica amostra do canal verde atraves do pipeline de
// deteccao de batimento (lowPass -> highPass -> derivative ->
// detectHeartbeat). Se um batimento for detetado, calcula o BPM
// instantaneo e a sua versao suavizada, aceitando-o apenas se estiver
// dentro da gama fisiologica plausivel (30-200 BPM). Retorna true apenas
// quando um batimento valido foi detetado nesta chamada (chamada
// repetidamente pela task, uma vez por amostra).
bool processHrSample(float &bpmOut, bool &validOut, bool &fingerPresent) {
  validOut = false;
  // Alinhado com test/HR.cpp: pipeline HR sem gate de IR/finger.
  // O getGreen() internamente ja tenta obter nova amostra via FIFO.
  fingerPresent = true;
  long raw = g_sensor.getGreen();
  float low = lowPassFilter(raw);
  float high = highPassFilter(low);
  float diff = derivative(high);
  bool beat = detectHeartbeat(diff);

  if (!beat) {
    return false;
  }

  float bpm = computeBPM();
  float bpmSmooth = smoothBPM(bpm);
  if (bpm > 30.0f && bpm < 200.0f) {
    bpmOut = bpmSmooth;
    validOut = true;
    return true;
  }

  return false;
}

// ----------------------------------------------------------------------------
// ppgTask: task FreeRTOS que corre em loop infinito e concentra toda a
// logica de escalonamento das leituras do sensor PPG. E' criada por
// startTask() e nunca termina (so' "pausa" quando suspensa/desligada).
//
// A cada iteracao do loop decide, por esta ordem:
//   1) Se ha um pedido de suspensao (long-press do botao) ou de desligar
//      definitivo (System Off): se sim, garante o sensor em repouso e
//      "dorme" um pouco antes de voltar a verificar (nao faz mais nada).
//   2) Se ja passou SPO2_INTERVAL_MS desde a ultima medicao de SpO2: faz
//      uma medicao completa (bloqueante, ~1s) e guarda o resultado.
//   3) Consulta o IMU: se o utilizador esta "inativo" (parado), mantem/
//      inicia o streaming continuo de HR e processa uma amostra por
//      iteracao (respeitando HR_SAMPLE_INTERVAL_MS). Se deixou de estar
//      inativo, so' desliga o streaming de HR apos um periodo de
//      tolerancia (HR_STREAM_STOP_HOLDOFF_MS), para nao cortar a leitura
//      por pequenas oscilacoes de movimento.
//   4) Ajusta o proprio ritmo do loop: dorme pouco (2 ms) quando esta a
//      fazer streaming de HR, para amostrar a alta frequencia, e dorme
//      mais (200 ms) quando esta parado, para poupar energia/CPU.
// Todas as escritas a g_latest (o snapshot partilhado lido por
// getLatest()) sao protegidas por taskENTER_CRITICAL()/taskEXIT_CRITICAL()
// para evitar leituras inconsistentes a partir de outras tasks.
// ----------------------------------------------------------------------------
void ppgTask(void *arg) {
  (void)arg;
  g_taskRunning = true;
  // Forca primeira tentativa de SpO2 logo no arranque da task.
  uint32_t lastSpo2Ms = millis() - SPO2_INTERVAL_MS;
  uint32_t lastStatusMs = 0;

  Serial.println("[PPG] task iniciada");

  while (true) {
    // --- Passo 1: respeitar pedidos de suspensao/desligar ---
    // Enquanto o dispositivo estiver a validar um long-press de power-off
    // (g_suspendForPowerCheck) ou ja tiver sido pedido o desligar
    // definitivo (g_shutdownRequested), nao fazemos nenhuma medicao: so'
    // garantimos que o sensor fica em repouso e voltamos a dormir.
    if (g_shutdownRequested || g_suspendForPowerCheck) {
      if (g_hrStreaming) {
        stopHrStreaming();
      } else {
        sensorIdle();
      }
      vTaskDelay(pdMS_TO_TICKS(TASK_LOOP_DELAY_IDLE_MS));
      continue;
    }

    const uint32_t nowMs = millis();
    Imu::Sample imuSample = {};
    const bool hasImu = Imu::getLatestSample(imuSample);
    const bool inactivity = hasImu && imuSample.inactivity;

    // Pedido manual de HR (ver requestManualHr()/dumpCtrlChar em Ble.cpp):
    // trata-se como equivalente a "inactivity" para efeitos de streaming,
    // enquanto o prazo nao expirar. Isto permite medir mesmo em movimento
    // quando pedido explicitamente, sabendo que a leitura pode ser menos
    // fiavel (ver aviso em Ppg.h).
    const uint32_t manualDeadline = g_manualHrDeadlineMs;
    const bool manualHrActive = manualDeadline != 0 && (int32_t)(manualDeadline - nowMs) > 0;
    if (manualDeadline != 0 && !manualHrActive) {
      g_manualHrDeadlineMs = 0; // prazo expirado - limpa o pedido
    }
    const bool wantHr = inactivity || manualHrActive;

    // --- Passo 2: medicao periodica de SpO2 (ou forcada por pedido manual) ---
    // Uma vez a cada SPO2_INTERVAL_MS, interrompe temporariamente o
    // streaming de HR (o sensor nao consegue fazer os dois modos ao
    // mesmo tempo) e faz uma medicao completa e bloqueante de SpO2.
    // g_manualSpo2Requested (ver requestManualSpo2()) antecipa esta
    // medicao sem esperar pelo intervalo normal.
    if ((nowMs - lastSpo2Ms) >= SPO2_INTERVAL_MS || g_manualSpo2Requested) {
      g_manualSpo2Requested = false;
      if (g_hrStreaming) {
        stopHrStreaming();
      }

      int32_t spo2 = 0;
      int32_t hrFromSpo2 = 0;
      bool validSpo2 = false;
      bool validHrFromSpo2 = false;
      bool finger = false;

      measureSpo2(spo2, validSpo2, hrFromSpo2, validHrFromSpo2, finger);

      taskENTER_CRITICAL();
      g_latest.spo2_timestamp_ms = nowMs;
      g_latest.spo2_value = spo2;
      g_latest.spo2_valid = validSpo2;
      g_latest.finger_present = finger;
      taskEXIT_CRITICAL();

      char tsSpo2[24];
      stampDateTime(tsSpo2, sizeof(tsSpo2));
      Serial.print("[PPG] SPO2 minuto -> ");
      if (validSpo2) {
        Serial.print(spo2);
        Serial.print("%");
      } else {
        Serial.print("invalido/sem dedo");
      }
      Serial.print(" time=");
      Serial.println(tsSpo2);

      lastSpo2Ms = nowMs;
    }

    // --- Passo 3: streaming continuo de HR, quando inativo OU pedido manual ---
    // So' faz sentido medir frequencia cardiaca com fiabilidade quando o
    // utilizador esta parado (o IMU reporta inactivity); movimento
    // introduz artefactos que o pipeline de filtros nao consegue separar
    // de um batimento real. wantHr tambem fica true durante uma janela
    // pedida explicitamente via requestManualHr(), mesmo em movimento.
    if (wantHr) {
      g_inactOffSinceMs = 0;

      if (!g_hrStreaming) {
        startHrStreaming();
      }

      if ((nowMs - g_lastHrSampleMs) >= HR_SAMPLE_INTERVAL_MS) {
        g_lastHrSampleMs = nowMs;

        float hrBpm = 0.0f;
        bool validHr = false;
        bool finger = false;
        const bool gotBeat = processHrSample(hrBpm, validHr, finger);

        taskENTER_CRITICAL();
        g_latest.finger_present = finger;
        taskEXIT_CRITICAL();

        if (gotBeat && validHr) {
          const float hrRounded = roundf(hrBpm);
          taskENTER_CRITICAL();
          g_latest.hr_timestamp_ms = nowMs;
          g_latest.hr_bpm = hrRounded;
          g_latest.hr_valid = true;
          taskEXIT_CRITICAL();

          char tsHr[24];
          stampDateTime(tsHr, sizeof(tsHr));
          Serial.print("[PPG] HR beat -> ");
          Serial.print((int)hrRounded);
          Serial.print(" bpm time=");
          Serial.println(tsHr);
        }
      }
    } else if (g_hrStreaming) {
      // O utilizador deixou de estar inativo, mas so' desligamos o
      // streaming de HR apos HR_STREAM_STOP_HOLDOFF_MS de tolerancia,
      // para nao interromper a leitura por breves oscilacoes do IMU.
      if (g_inactOffSinceMs == 0) {
        g_inactOffSinceMs = nowMs;
      } else if ((nowMs - g_inactOffSinceMs) >= HR_STREAM_STOP_HOLDOFF_MS) {
        stopHrStreaming();
        g_inactOffSinceMs = 0;
      }
    }

    // Snapshot periodico apenas para eventual inspecao/debug local; o
    // valor lido nao e' usado, mas a copia mantem o padrao de acesso
    // protegido a g_latest.
    if ((nowMs - lastStatusMs) >= 5000) {
      lastStatusMs = nowMs;
      Ppg::Metrics snap = {};
      taskENTER_CRITICAL();
      snap = g_latest;
      taskEXIT_CRITICAL();
    }

    // --- Passo 4: ritmo adaptativo do loop ---
    // Amostra rapido (2 ms, ~100 Hz) enquanto esta a captar HR em
    // continuo; caso contrario dorme mais (200 ms) para poupar energia.
    const uint32_t delayMs = g_hrStreaming ? TASK_LOOP_DELAY_HR_MS : TASK_LOOP_DELAY_IDLE_MS;
    vTaskDelay(pdMS_TO_TICKS(delayMs));
  }
}

} // namespace

namespace Ppg {

// Ver documentacao completa em Ppg.h.
// Procura o sensor MAX3010x nos barramentos I2C candidatos (por defeito
// so' o Wire externo, ver PPG_USE_EXTERNAL_WIRE_ONLY), tentando recuperar
// o barramento se este estiver preso, sondando o endereco I2C 0x57 e so'
// depois chamando o begin() da biblioteca do sensor. Ao encontrar o
// sensor, deixa-o em repouso (sensorIdle) e marca o modulo como iniciado.
bool begin() {
  if (g_started) return true;
  struct CandidateBus {
    TwoWire *bus;
    const char *name;
  };
  CandidateBus candidates[] = {
#if PPG_USE_EXTERNAL_WIRE_ONLY
      {&Wire, "Wire"},
#else
      {&Wire1, "Wire1"},
      {&Wire, "Wire"},
#endif
  };

  bool ok = false;
  for (size_t i = 0; i < (sizeof(candidates) / sizeof(candidates[0])); i++) {
    TwoWire &bus = *candidates[i].bus;
    Serial.print("[PPG] begin: ");
    Serial.print(candidates[i].name);
    Serial.println(".begin()");

#if defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
    if (candidates[i].bus == &Wire) {
      if (!recoverExternalI2cBus()) {
        Serial.println("[PPG] Wire externo preso (SDA/SCL LOW) apos recovery");
        Serial.println("[PPG] verificar: SDA/SCL trocados, GND comum, pull-ups e alimentacao do modulo");
        continue;
      }
    }
#endif

    bus.begin();
    bus.setClock(100000);
#if defined(WIRE_HAS_TIMEOUT)
    // Evita bloqueio indefinido em transacoes I2C quando o sensor nao responde.
    bus.setWireTimeout(25000, true);
#endif

    Serial.print("[PPG] begin: probe 0x57 em ");
    Serial.println(candidates[i].name);
    bus.beginTransmission(0x57);
    const uint8_t probeErr = bus.endTransmission();
    if (probeErr != 0) {
      Serial.print("[PPG] sem resposta em ");
      Serial.print(candidates[i].name);
      Serial.print(" (err=");
      Serial.print(probeErr);
      Serial.println(")");
      continue;
    }

    Serial.print("[PPG] begin: MAX30105.begin() em ");
    Serial.println(candidates[i].name);
    if (!g_sensor.begin(bus, I2C_SPEED_FAST)) {
      Serial.print("[PPG] begin() falhou em ");
      Serial.println(candidates[i].name);
      continue;
    }

    g_ppgBus = &bus;
    g_ppgBusName = candidates[i].name;
    ok = true;
    break;
  }

  if (!ok) {
#if PPG_USE_EXTERNAL_WIRE_ONLY
    Serial.println("[PPG] MAX3010x nao encontrado em Wire externo");
#else
    Serial.println("[PPG] MAX3010x nao encontrado em Wire1 nem Wire");
#endif
    return false;
  }

  sensorIdle();
  g_shutdownRequested = false;
  g_suspendForPowerCheck = false;
  g_started = true;
  Serial.print("[PPG] MAX30105 inicializado no bus ");
  Serial.println(g_ppgBusName);
  return true;
}

// Garante que o sensor esta inicializado (chamando begin() se necessario)
// e, se a task ainda nao existir, cria-a com xTaskCreate. Se a task ja
// estiver a correr, e' uma chamada sem efeito (idempotente).
bool startTask() {
  if (!g_started && !begin()) return false;
  if (g_taskHandle != nullptr) return true;

  BaseType_t ok = xTaskCreate(
      ppgTask,
      "ppg_task",
      PPG_TASK_STACK_WORDS,
      nullptr,
      TASK_PRIO_NORMAL,
      &g_taskHandle);

  if (ok != pdPASS) {
    g_taskHandle = nullptr;
    Serial.println("[PPG] falha ao criar task");
    return false;
  }

  return true;
}

bool isTaskRunning() {
  return g_taskRunning && (g_taskHandle != nullptr);
}

// Copia atomica do ultimo snapshot de metricas (protegida por secao
// critica porque g_latest e' escrito pela task ppgTask e lido por
// quem chama esta funcao, potencialmente em tasks/contexto diferentes).
bool getLatest(Metrics &out) {
  if (!g_started) return false;
  taskENTER_CRITICAL();
  out = g_latest;
  taskEXIT_CRITICAL();
  return true;
}

// Chamada assim que se deteta o inicio de um long-press no botao de
// power: apaga os LEDs imediatamente (forceLedsOffNow, sem esperar pela
// proxima iteracao da task) e sinaliza a flag que faz a task entrar em
// modo de espera (ver Passo 1 de ppgTask). Isto evita continuar a gastar
// energia com o sensor enquanto se aguarda a confirmacao do long-press.
void suspendForPowerCheck() {
  g_suspendForPowerCheck = true;
  forceLedsOffNow();
}

// Cancela a suspensao pedida por suspendForPowerCheck(), mas apenas se
// entretanto nao tiver sido pedido um desligar definitivo
// (g_shutdownRequested); isso garante que, uma vez confirmado o System
// Off, nada consegue "reanimar" a task por engano.
void resumeAfterPowerCheck() {
  if (!g_shutdownRequested) {
    g_suspendForPowerCheck = false;
  }
}

// Chamada quando o dispositivo vai mesmo entrar em System Off. Marca os
// dois pedidos (shutdown definitivo + suspensao) para que a task nunca
// mais tente medir, reinicia o estado de streaming de HR (para o caso de
// o dispositivo acordar mais tarde por reset e reiniciar tudo do zero) e
// forca os LEDs a desligar imediatamente e o sensor a entrar em shutdown,
// para minimizar o consumo residual antes do corte de energia.
void prepareForSystemOff() {
  g_shutdownRequested = true;
  g_suspendForPowerCheck = true;
  g_hrStreaming = false;
  g_lastHrSampleMs = 0;
  g_inactOffSinceMs = 0;
  g_manualHrDeadlineMs = 0;
  g_manualSpo2Requested = false;
  forceLedsOffNow();
}

// Ver Ppg.h. Limita durationMs a kManualHrMaxDurationMs e ignora o
// pedido se o dispositivo ja estiver a desligar — nao faz sentido ligar
// o sensor mesmo antes do System Off.
void requestManualHr(uint32_t durationMs) {
  if (g_shutdownRequested) return;
  if (durationMs > kManualHrMaxDurationMs) durationMs = kManualHrMaxDurationMs;
  g_manualHrDeadlineMs = millis() + durationMs;
}

// Ver Ppg.h. Marca um pedido que a task consome (e limpa) na proxima
// iteracao do seu loop — nao ha necessidade de prazo, a medicao de SpO2
// e' unica e bloqueante, nao um streaming continuo como o HR.
void requestManualSpo2() {
  if (g_shutdownRequested) return;
  g_manualSpo2Requested = true;
}

// *** DIAGNOSTICO TEMPORARIO (otimizacao de RAM) *** — ver Ppg.h.
uint32_t taskStackHighWaterMarkWords() {
  if (g_taskHandle == nullptr) return 0;
  return static_cast<uint32_t>(uxTaskGetStackHighWaterMark(g_taskHandle));
}

} // namespace Ppg
