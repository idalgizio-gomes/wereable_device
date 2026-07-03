// Imu.cpp
//
// Implementacao do driver/wrapper para o sensor IMU LSM6DS3 (acelerometro +
// giroscopio de 6 eixos), usado por este dispositivo de monitorizacao de
// pessoas com demencia para detetar padroes de movimento do utilizador.
//
// Responsabilidades principais deste ficheiro:
//  1. Inicializacao do sensor via I2C (begin()) com a configuracao de
//     frequencia de amostragem (~52 Hz) e range de escala.
//  2. Calibracao (runCalibration/ensureCalibrated): o sensor tem sempre um
//     pequeno erro sistematico de fabrico/montagem (offset). Para corrigir
//     isso, pede-se ao utilizador para pousar o dispositivo parado durante
//     alguns segundos, recolhem-se varias amostras e calcula-se a media,
//     que passa a ser subtraida a todas as leituras futuras.
//  3. Leitura de dados, raw (sem correcao) ou calibrada (com os offsets
//     aplicados).
//  4. Uma task FreeRTOS (imuTask) que corre em background, le o sensor
//     periodicamente e aplica tres algoritmos simples de deteccao de
//     movimento sobre a magnitude do vetor de aceleracao:
//       - Pedometro (detectStep): conta passos atraves de picos no sinal
//         de aceleracao filtrado.
//       - Deteccao de queda livre (detectFreefall): identifica quando a
//         aceleracao medida cai muito abaixo de 1g (o corpo deixa de estar
//         "apoiado", como acontece durante uma queda).
//       - Deteccao de inatividade (detectInactivity): identifica quando o
//         dispositivo esta parado (sem rotacao nem variacao de aceleracao)
//         durante varios segundos seguidos - relevante para alertar sobre
//         possivel imobilidade prolongada do utilizador.
//     O resultado de cada iteracao e guardado numa "ultima amostra"
//     (g_latestSample) protegida por uma secao critica, para poder ser lida
//     em seguranca por outras tasks atraves de getLatestSample().

#include "Imu/Imu.h"
#include "Display/Ui.h"
#include "Storage/Storage.h"
#include "Clock/Clock.h"
#include <LSM6DS3.h>
#include <Wire.h>
#include <rtos.h>
#include <math.h>
#include <stdio.h>

// Seleciona explicitamente o bus I2C do IMU por target.
// Nos XIAO Sense/Sense Plus o IMU onboard está no Wire1.
#if defined(TARGET_SEEED_XIAO_NRF52840_SENSE) || defined(TARGET_SEEED_XIAO_NRF52840_SENSE_PLUS) || defined(ARDUINO_XIAO_MG24)
  #define IMU_I2C_BUS Wire1
  static const char *kImuBusName = "Wire1";
#else
  #define IMU_I2C_BUS Wire
  static const char *kImuBusName = "Wire";
#endif

// Em alguns estados de bus o scan pode bloquear durante o boot.
// Usa 1 apenas para diagnóstico.
#define IMU_I2C_SCAN_ENABLE 0

// XIAO nRF52840 Sense (Plus) tem o LSM6DS3 no Wire1 (I2C interno).
// O construtor com 3 args só está disponível na versão Seeed da lib.
static LSM6DS3 imu(I2C_MODE, 0x6A);
// Offsets de calibracao atuais (media das leituras com o dispositivo
// parado). Sao subtraidos as leituras raw para obter valores calibrados.
static ImuCalibration g_cal = {0, 0, 0, 0, 0, 0};
// true depois de begin() ter inicializado o sensor com sucesso; usada
// como guarda nas restantes funcoes para evitar acessos ao sensor antes
// deste estar pronto.
static bool g_started = false;
// Handle da task FreeRTOS de aquisicao (imuTask); nullptr se a task ainda
// nao foi criada.
static TaskHandle_t g_taskHandle = nullptr;
// Sinaliza que a task de aquisicao ja arrancou e esta a correr o seu loop
// (distinto de g_taskHandle != nullptr, que so indica que foi criada).
static volatile bool g_taskRunning = false;
// Ultima amostra (leitura + deteccoes) produzida pela task de aquisicao.
// E partilhada entre a task do IMU (escreve) e quem chama getLatestSample()
// (le), por isso o acesso e sempre feito dentro de uma secao critica.
static Imu::Sample g_latestSample = {};
// Contador de passos, incrementado pela task de aquisicao sempre que o
// detetor de passos (detectStep) identifica um novo passo.
static volatile uint32_t g_stepCount = 0;

// Numero de amostras recolhidas durante a rotina de calibracao: quanto
// mais amostras, mais estavel/fiavel e a media calculada como offset.
static const int CAL_NUM_SAMPLES = 500;
// Pausa entre cada amostra durante a calibracao (ms), para espacar as
// leituras no tempo.
static const int CAL_SAMPLE_DELAY_MS = 5;
// Frequencia alvo de aquisicao da task do IMU (Hz) — usada apenas para logs.
static const uint32_t IMU_TASK_RATE_HZ = 52;
// Periodo entre iteracoes da task de aquisicao, em ticks do FreeRTOS.
// 19 ms corresponde a aproximadamente 52.6 Hz, alinhado com a taxa de
// amostragem configurada no sensor (accelSampleRate/gyroSampleRate = 52).
static const TickType_t IMU_TASK_PERIOD_TICKS = pdMS_TO_TICKS(19); // ~52.6 Hz
// Tamanho da stack (em words) reservada para a task de aquisicao do IMU.
// *** OTIMIZACAO DE RAM (reducao conservadora, ver DEBUG_STACK_WATERMARKS
// em main.cpp) ***: reduzido de 1024 para 768 words (-1024 bytes). O corpo
// da task so usa floats/structs pequenos e, uma vez por segundo, um bloco
// de diagnostico com snprintf + varios Serial.print (o ponto mais pesado
// em uso de stack desta task). 768 words mantem ~25% de margem sobre essa
// estimativa. Ainda NAO foi confirmado com uxTaskGetStackHighWaterMark()
// em hardware real (ver taskStackHighWaterMarkWords() em Imu.h) — quando
// o dispositivo estiver acessivel, confirmar que o valor reportado fica
// confortavelmente acima de 0 antes de considerar reduzir mais.
static const uint16_t IMU_TASK_STACK_WORDS = 768;

namespace Imu {

// Formata a data/hora atual (para usar nas linhas de log periodicas da
// task do IMU). Se o relogio ainda nao estiver disponivel/valido, cai
// para um valor "zero" em vez de deixar o buffer com lixo.
static void stampDateTime(char *out, size_t outLen) {
  if (!Clock::formatDateTime(out, outLen)) {
    snprintf(out, outLen, "00/00/0000 00:00:00");
  }
}

// Faz scan I2C nos dois buses (Wire e Wire1) e imprime os endereços.
// So e usada quando IMU_I2C_SCAN_ENABLE esta a 1 (diagnostico manual),
// pois pode bloquear se o bus estiver num estado invalido durante o boot.
static void i2cScan(TwoWire &bus, const char *name) {
  Serial.print("[IMU] scan "); Serial.print(name); Serial.println(":");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    bus.beginTransmission(addr);
    if (bus.endTransmission() == 0) {
      Serial.print("  -> 0x"); Serial.println(addr, HEX);
      found++;
    }
  }
  if (!found) Serial.println("  (nenhum dispositivo)");
}

// Estado interno (persiste entre chamadas) dos tres algoritmos de deteccao
// de movimento. E um "acumulador" que vai sendo atualizado a cada iteracao
// da task do IMU, mantendo memoria de amostras anteriores (necessario
// porque nenhuma destas deteccoes pode ser feita com uma unica leitura
// isolada).
struct MotionState {
  float magLowPass = 1.0f;     // Media movel (filtro passa-baixo) da magnitude de aceleracao; aproxima a componente "gravidade/postura".
  bool stepArmed = true;        // true = pronto para detetar o proximo passo (evita contar o mesmo passo varias vezes).
  uint8_t stepHighCount = 0;    // Nº de amostras consecutivas acima do limiar de subida do passo.
  uint32_t lastStepMs = 0;      // Instante (millis()) do ultimo passo contado, para aplicar o periodo refratario.
  uint8_t freefallCount = 0;    // Nº de amostras consecutivas com aceleracao muito baixa (possivel queda).
  bool freefall = false;        // Resultado atual da deteccao de queda livre.
  uint16_t inactivityCount = 0; // Nº de amostras consecutivas "paradas" (sem rotacao nem variacao de aceleracao).
  bool inactivity = false;      // Resultado atual da deteccao de inatividade.
  bool turnArmed = true;        // true = pronto para contar a proxima curva apertada (evita contar a mesma varias vezes).
  uint8_t turnHighCount = 0;    // Nº de amostras consecutivas com rotacao acima do limiar de curva apertada.
  uint16_t turnEventsInWindow = 0; // Nº de curvas apertadas contadas na janela de pacing atual.
  uint32_t pacingWindowStartMs = 0; // Instante (millis()) em que a janela de pacing atual comecou.
  uint8_t pacingIndex = 0;      // Ultimo indice de pacing (0-100) calculado no fim de uma janela.
};

static MotionState g_motion;

// Pedometro simples baseado num filtro passa-alto sobre a magnitude do
// vetor de aceleracao (|a| = sqrt(ax²+ay²+az²)).
//
// Ideia: cada passo produz um pico caracteristico na aceleracao. Para
// isolar esse pico do valor "de base" (gravidade/postura, ~1g quando
// parado), subtrai-se a magnitude atual a uma media movel lenta
// (magLowPass, atualizada com kAlpha=0.96 -> reage devagar). O resultado
// (hp = "high-pass") sobe quando ha um movimento brusco tipico de um passo.
//
// Para contar um passo exige-se:
//  - que hp ultrapasse kStepRiseThreshold durante pelo menos
//    kStepMinHighSamples amostras seguidas (filtra ruido/vibrações curtas);
//  - que tenha passado o tempo refratario kStepRefractoryMs desde o ultimo
//    passo (impede contar o mesmo passo duas vezes devido a oscilacoes);
//  - o detetor so volta a ficar "armado" (stepArmed) quando hp desce abaixo
//    de kStepRearmThreshold, ou seja, precisa de "assentar" entre passos.
// Devolve true exatamente na amostra em que um novo passo e confirmado.
static bool detectStep(float accMag, uint32_t nowMs) {
  constexpr float kAlpha = 0.96f;
  constexpr float kStepRiseThreshold = 0.20f;
  constexpr float kStepRearmThreshold = 0.06f;
  constexpr uint8_t kStepMinHighSamples = 2; // ~38 ms @ 52 Hz
  constexpr uint32_t kStepRefractoryMs = 320;

  g_motion.magLowPass = (kAlpha * g_motion.magLowPass) + ((1.0f - kAlpha) * accMag);
  const float hp = accMag - g_motion.magLowPass;

  if (hp > kStepRiseThreshold) {
    if (g_motion.stepHighCount < 255) g_motion.stepHighCount++;
  } else {
    g_motion.stepHighCount = 0;
  }

  if (g_motion.stepArmed && g_motion.stepHighCount >= kStepMinHighSamples) {
    if ((nowMs - g_motion.lastStepMs) > kStepRefractoryMs) {
      g_motion.lastStepMs = nowMs;
      g_motion.stepArmed = false;
      g_motion.stepHighCount = 0;
      return true;
    }
  }

  if (hp < kStepRearmThreshold) {
    g_motion.stepArmed = true;
    g_motion.stepHighCount = 0;
  }
  return false;
}

// Deteccao de queda livre. Durante uma queda livre real, o acelerometro
// deixa de sentir a reacao do apoio/chao e a magnitude de aceleracao medida
// cai para perto de 0g (em vez do ~1g habitual quando parado ou em
// movimento normal). Exige kFreefallSamples amostras consecutivas abaixo
// de kFreefallThresholdG antes de confirmar o estado de queda, para
// filtrar quedas de aceleracao momentaneas/ruido que nao correspondem a
// uma queda real. O estado fica persistido em g_motion.freefall (nao e
// "one-shot": mantem-se true enquanto a condicao se mantiver).
static bool detectFreefall(float accMag) {
  constexpr float kFreefallThresholdG = 0.30f;
  constexpr uint8_t kFreefallSamples = 6; // ~115 ms @ 52 Hz

  if (accMag < kFreefallThresholdG) {
    if (g_motion.freefallCount < 255) g_motion.freefallCount++;
  } else {
    g_motion.freefallCount = 0;
  }

  g_motion.freefall = (g_motion.freefallCount >= kFreefallSamples);
  return g_motion.freefall;
}

// Deteccao de inatividade prolongada: considera-se que o dispositivo esta
// "parado" quando, simultaneamente, o giroscopio nao regista rotacao
// significativa (norma abaixo de kGyroStillDps) e a aceleracao esta perto
// de 1g constante (accDelta = |accMag - 1g| abaixo de kAccelStillDeltaG,
// ou seja, sem impulsos de movimento). So depois de kInactivitySamples
// amostras seguidas "paradas" (~3 segundos a 52 Hz) e que o estado de
// inatividade e sinalizado — isto evita falsos positivos durante pausas
// curtas (ex.: parar num semaforo) e so acusa imobilidade realmente
// prolongada, relevante para deteccao de situacoes de risco no contexto
// de monitorizacao de pessoas com demencia.
static bool detectInactivity(float accMag, float gx, float gy, float gz) {
  constexpr float kGyroStillDps = 6.0f;
  // *** CORRECAO DE ROBUSTEZ ***: 0.05g estava demasiado apertado para uso
  // real (segurar a placa na mao introduz vibracao/tremor de poucas
  // dezenas de mg, que facilmente ultrapassa este limiar). Alargado para
  // 0.08g, ainda bem abaixo do que um movimento real produz.
  constexpr float kAccelStillDeltaG = 0.08f;
  constexpr uint16_t kInactivitySamples = 156; // 3 s @ 52 Hz
  // *** CORRECAO DE ROBUSTEZ ***: a logica original reiniciava o contador
  // para 0 numa UNICA amostra fora do limiar — bastava uma amostra
  // ruidosa (das 156 seguidas exigidas) para nunca se atingir 3s de
  // imobilidade, mesmo com o utilizador genuinamente parado. Passa a
  // "contador com fuga": uma amostra isolada de ruido so recua um pouco
  // (kNoiseDecay), nao apaga todo o progresso; so um periodo sustentado
  // de movimento real consegue baixar o contador mais depressa do que ele
  // sobe. Um kNoiseDecay maior do que o incremento (1) garante que
  // movimento genuino continua a interromper a deteccao rapidamente.
  constexpr uint16_t kNoiseDecay = 12;

  const float gyroNorm = sqrtf((gx * gx) + (gy * gy) + (gz * gz));
  const float accDelta = fabsf(accMag - 1.0f);

  const bool still = (gyroNorm < kGyroStillDps) && (accDelta < kAccelStillDeltaG);
  if (still) {
    if (g_motion.inactivityCount < 0xFFFF) g_motion.inactivityCount++;
  } else {
    g_motion.inactivityCount = (g_motion.inactivityCount > kNoiseDecay)
                                    ? (g_motion.inactivityCount - kNoiseDecay)
                                    : 0;
  }

  g_motion.inactivity = (g_motion.inactivityCount >= kInactivitySamples);
  return g_motion.inactivity;
}

// Deteccao de "pacing"/curvas apertadas via giroscopio: item 2 do backlog
// de investigacao (ver PROJECT_STATUS.md) — proxy precoce de deambulacao
// (wandering), complementar ao geofencing por GPS. A literatura associa
// padroes de deambulacao a mudancas de direcao frequentes e apertadas num
// espaco curto, em vez de percursos lineares.
//
// Como este dispositivo e usado no pulso (sem orientacao fixa em relacao
// ao corpo), usa-se a NORMA do giroscopio (rotacao total, independente do
// eixo) como aproximacao de "quao apertada" e uma rotacao — mais robusto a
// como o dispositivo esta orientado no pulso do que isolar um unico eixo
// (ex.: gz), mas nao distingue rotacao do proprio pulso/braco de uma curva
// real do corpo a andar: e um SINAL COMPLEMENTAR, nao uma deteccao de
// wandering validada clinicamente (a evidencia de eficacia clinica desta
// familia de sinais e ainda mista segundo a pesquisa registada no
// PROJECT_STATUS.md).
//
// Algoritmo: conta "eventos de curva apertada" — rajadas de rotacao acima
// de kTurnGyroThresholdDps, com o mesmo padrao rise/rearm ja usado em
// detectStep() — dentro de uma janela deslizante de kPacingWindowMs. No
// fim de cada janela, converte o numero de eventos num indice 0-100 (mais
// curvas apertadas por minuto = indice mais alto) e reinicia a contagem
// para a janela seguinte. Os limiares (graus/seg, curvas/min para indice
// maximo) sao heuristicas desta primeira iteracao, ainda por afinar com
// dados reais de uso (nao ha ainda historico real de wandering confirmado
// para calibrar contra ele).
static uint8_t detectPacing(float gyroNorm, uint32_t nowMs) {
  constexpr float kTurnGyroThresholdDps = 45.0f;    // limiar de rotacao para contar uma "curva apertada"
  constexpr float kTurnRearmThresholdDps = 15.0f;   // tem de descer abaixo disto antes da proxima curva contar
  constexpr uint8_t kTurnMinHighSamples = 5;        // ~96 ms @ 52 Hz, filtra picos de ruido curtos
  constexpr uint32_t kPacingWindowMs = 60000;       // janela de 1 minuto
  constexpr uint16_t kPacingTurnsForMaxScore = 12;  // 12+ curvas/min -> indice 100 (heuristico)

  if (gyroNorm > kTurnGyroThresholdDps) {
    if (g_motion.turnHighCount < 255) g_motion.turnHighCount++;
  } else {
    g_motion.turnHighCount = 0;
  }

  if (g_motion.turnArmed && g_motion.turnHighCount >= kTurnMinHighSamples) {
    if (g_motion.turnEventsInWindow < 0xFFFF) g_motion.turnEventsInWindow++;
    g_motion.turnArmed = false;
    g_motion.turnHighCount = 0;
  }

  if (gyroNorm < kTurnRearmThresholdDps) {
    g_motion.turnArmed = true;
  }

  if (g_motion.pacingWindowStartMs == 0) {
    g_motion.pacingWindowStartMs = nowMs;
  } else if ((nowMs - g_motion.pacingWindowStartMs) >= kPacingWindowMs) {
    const uint32_t score = (static_cast<uint32_t>(g_motion.turnEventsInWindow) * 100)
                            / kPacingTurnsForMaxScore;
    g_motion.pacingIndex = static_cast<uint8_t>(score > 100 ? 100 : score);
    g_motion.turnEventsInWindow = 0;
    g_motion.pacingWindowStartMs = nowMs;
  }

  return g_motion.pacingIndex;
}

// Corpo da task FreeRTOS de aquisicao do IMU. Corre indefinidamente
// (nunca retorna), acordando a um ritmo fixo definido por
// IMU_TASK_PERIOD_TICKS (~52 Hz) atraves de vTaskDelayUntil — usa-se
// vTaskDelayUntil em vez de vTaskDelay para manter um periodo estavel
// mesmo que o corpo do loop demore tempo variavel a executar (evita drift
// acumulado no ritmo de amostragem).
//
// Em cada iteracao:
//  1. Le os valores raw do sensor.
//  2. Aplica os offsets de calibracao manualmente (em vez de chamar
//     readCalibrated) para tambem obter a magnitude de aceleracao
//     calibrada (accMag), usada pelos tres detetores de movimento.
//  3. Corre os detetores de passo/queda/inatividade sobre essa magnitude.
//  4. Monta uma nova Sample com os valores raw (nao calibrados) mais os
//     resultados de deteccao, e publica-a em g_latestSample.
//  5. A cada 52 iteracoes (~1 s), imprime uma linha de diagnostico no
//     Serial com os valores atuais.
static void imuTask(void *arg) {
  (void)arg;

  g_taskRunning = true;
  TickType_t lastWake = xTaskGetTickCount();
  uint8_t printDivider = 0;

  Serial.print("[IMU] task iniciada a ");
  Serial.print(IMU_TASK_RATE_HZ);
  Serial.println(" Hz");

  while (true) {
    float ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
    if (readRaw(ax, ay, az, gx, gy, gz)) {
      // Aplica os offsets de calibracao "na mao" (em vez de usar
      // readCalibrated) para tambem ficarmos com os valores calibrados
      // individuais (cax..cgz), necessarios para calcular accMag e para
      // a deteccao de inatividade baseada no giroscopio calibrado.
      const float cax = ax - g_cal.accel_x;
      const float cay = ay - g_cal.accel_y;
      const float caz = az - g_cal.accel_z;
      const float cgx = gx - g_cal.gyro_x;
      const float cgy = gy - g_cal.gyro_y;
      const float cgz = gz - g_cal.gyro_z;
      // Magnitude (norma) do vetor de aceleracao calibrado: em repouso
      // deve rondar 1g; e o sinal de entrada dos tres detetores abaixo.
      const float accMag = sqrtf((cax * cax) + (cay * cay) + (caz * caz));
      const uint32_t nowMs = millis();

      if (detectStep(accMag, nowMs)) {
        g_stepCount++;
        Serial.print("[IMU] passo: ");
        Serial.println(g_stepCount);
      }

      const bool freefall = detectFreefall(accMag);
      const bool inactivity = detectInactivity(accMag, cgx, cgy, cgz);
      const float gyroNorm = sqrtf((cgx * cgx) + (cgy * cgy) + (cgz * cgz));
      const uint8_t pacingIndex = detectPacing(gyroNorm, nowMs);

      // Nota: a amostra publicada guarda os valores RAW (ax..gz), nao os
      // calibrados (cax..cgz); os valores calibrados sao usados apenas
      // internamente para alimentar os detetores de movimento.
      Sample sample = {};
      sample.timestamp_ms = nowMs;
      sample.ax = ax;
      sample.ay = ay;
      sample.az = az;
      sample.gx = gx;
      sample.gy = gy;
      sample.gz = gz;
      sample.step_count = g_stepCount;
      sample.freefall = freefall;
      sample.inactivity = inactivity;
      sample.pacing_index = pacingIndex;

      // Secao critica curta: protege a escrita de g_latestSample contra
      // uma leitura concorrente feita por getLatestSample() a partir de
      // outra task, evitando que esta leia uma amostra "a meio" de ser
      // atualizada (dados inconsistentes/rasgados).
      taskENTER_CRITICAL();
      g_latestSample = sample;
      taskEXIT_CRITICAL();

      // Log de diagnostico limitado a ~1x por segundo (a cada 52 amostras
      // a 52 Hz), para nao inundar o Serial com uma linha por amostra.
      printDivider++;
      if (printDivider >= 52) {
        printDivider = 0;
        char ts[24];
        stampDateTime(ts, sizeof(ts));
        Serial.print("[IMU] raw time=");
        Serial.print(ts);
        Serial.print(" a[g]=");
        Serial.print(ax, 3); Serial.print(",");
        Serial.print(ay, 3); Serial.print(",");
        Serial.print(az, 3);
        Serial.print(" g[dps]=");
        Serial.print(gx, 2); Serial.print(",");
        Serial.print(gy, 2); Serial.print(",");
        Serial.print(gz, 2);
        Serial.print(" steps=");
        Serial.print(sample.step_count);
        Serial.print(" ff=");
        Serial.print(sample.freefall ? "1" : "0");
        Serial.print(" inact=");
        Serial.print(sample.inactivity ? "1" : "0");
        Serial.print(" pacing=");
        Serial.println(sample.pacing_index);
      }
    }

    // Adormece ate ao proximo instante alvo (lastWake + periodo), mantendo
    // o ritmo de aquisicao regular independentemente do tempo gasto acima.
    vTaskDelayUntil(&lastWake, IMU_TASK_PERIOD_TICKS);
  }
}

// Ver documentacao completa em Imu.h. Aqui apenas os detalhes de
// implementacao: comeca por garantir que o bus I2C correto (Wire ou
// Wire1, consoante a placa) esta ativo, aplica a configuracao de
// frequencia/range do sensor e so depois chama imu.begin() da lib
// LSM6DS3, que efetivamente fala com o hardware e confirma que o sensor
// respondeu corretamente.
bool begin() {
  Serial.print("[IMU] ");
  Serial.print(kImuBusName);
  Serial.println(".begin()");
  Serial.flush();
  IMU_I2C_BUS.begin();
  delay(10);

  // Diagnóstico opcional: scan no bus do IMU.
#if IMU_I2C_SCAN_ENABLE
  i2cScan(IMU_I2C_BUS, kImuBusName);
#endif

  // Configuracao para aquisicao a 52 Hz.
  imu.settings.accelSampleRate = 52;
  imu.settings.gyroSampleRate = 52;
  imu.settings.accelRange = 4;
  imu.settings.gyroRange = 500;

  Serial.println("[IMU] imu.begin()");
  Serial.flush();
  if (imu.begin() != 0) {
    Serial.println("[IMU] erro a iniciar LSM6DS3");
    return false;
  }
  g_started = true;
  Serial.println("[IMU] LSM6DS3 inicializado");
  return true;
}

// Rotina que efetivamente calcula os offsets de calibracao. Pede ao
// utilizador (via display) para pousar o dispositivo parado, espera 2 s
// para dar tempo a que isso aconteca, e depois le CAL_NUM_SAMPLES amostras
// raw do sensor (uma a cada CAL_SAMPLE_DELAY_MS), acumulando a soma de
// cada eixo. No fim, a media de cada eixo passa a ser o offset desse eixo:
// - Para o giroscopio, o offset e simplesmente a media (parado, a
//   velocidade angular real deveria ser 0 dps em todos os eixos).
// - Para o acelerometro em X/Y, o mesmo raciocinio aplica-se (parado na
//   horizontal, a aceleracao nesses eixos deveria ser 0g).
// - Para o eixo Z do acelerometro subtrai-se adicionalmente 1.0f, porque
//   com o dispositivo pousado na horizontal o sensor mede ~1g devido a
//   gravidade — e essa componente de gravidade que se quer preservar (nao
//   remover) nas leituras calibradas, por isso so o "excesso" acima de 1g
//   e tratado como offset.
// Devolve sempre true (a rotina em si nao tem uma condicao de erro
// prevista); ver ensureCalibrated() para o tratamento de falhas gerais.
static bool runCalibration(ImuCalibration &out) {
  Serial.println("[IMU] a calibrar - manter parado");
  Serial.flush();

  Serial.println("[IMU] -> uiMessage");
  Serial.flush();
  uiMessage("Iniciar", "Calibracao");
  Serial.println("[IMU] <- uiMessage OK");
  Serial.flush();

  // Pequena espera para o utilizador pousar o dispositivo
  delay(2000);

  Serial.println("[IMU] start sample loop");
  Serial.flush();

  double sum_gx = 0, sum_gy = 0, sum_gz = 0;
  double sum_ax = 0, sum_ay = 0, sum_az = 0;

  for (int i = 0; i < CAL_NUM_SAMPLES; i++) {
    sum_gx += imu.readFloatGyroX();
    sum_gy += imu.readFloatGyroY();
    sum_gz += imu.readFloatGyroZ();
    sum_ax += imu.readFloatAccelX();
    sum_ay += imu.readFloatAccelY();
    sum_az += imu.readFloatAccelZ();
    delay(CAL_SAMPLE_DELAY_MS);

    if ((i % 100) == 0) {
      Serial.print("[IMU] sample "); Serial.println(i);
      Serial.flush();
    }
  }

  Serial.println("[IMU] sample loop done");
  Serial.flush();

  out.gyro_x  = sum_gx / CAL_NUM_SAMPLES;
  out.gyro_y  = sum_gy / CAL_NUM_SAMPLES;
  out.gyro_z  = sum_gz / CAL_NUM_SAMPLES;
  out.accel_x = sum_ax / CAL_NUM_SAMPLES;
  out.accel_y = sum_ay / CAL_NUM_SAMPLES;
  out.accel_z = (sum_az / CAL_NUM_SAMPLES) - 1.0f;  // remove 1g de gravidade em Z

  Serial.println("[IMU] calibracao concluida");
  Serial.print("  gyro  off (dps): ");
  Serial.print(out.gyro_x, 4); Serial.print(", ");
  Serial.print(out.gyro_y, 4); Serial.print(", ");
  Serial.println(out.gyro_z, 4);
  Serial.print("  accel off (g):   ");
  Serial.print(out.accel_x, 4); Serial.print(", ");
  Serial.print(out.accel_y, 4); Serial.print(", ");
  Serial.println(out.accel_z, 4);

  return true;
}

// Ver documentacao completa em Imu.h. Estrategia "cache em disco": evita
// obrigar o utilizador a recalibrar a cada arranque, reaproveitando a
// calibracao gravada anteriormente sempre que exista.
bool ensureCalibrated() {
  if (!g_started) {
    Serial.println("[IMU] ensureCalibrated: nao inicializado");
    return false;
  }

  // 1) Já existe calibração no FS? -> aplica directamente
  if (Storage::hasCalibration() && Storage::loadCalibration(g_cal)) {
    Serial.println("[IMU] calibracao carregada do FS");
    uiMessage("IMU", "Calibrado");
    delay(2500);
    return true;
  }

  // 2) Caso contrário, corre nova calibração e grava
  ImuCalibration fresh{};
  if (!runCalibration(fresh)) {
    uiMessage("Erro", "Calibracao");
    return false;
  }

  if (!Storage::saveCalibration(fresh)) {
    Serial.println("[IMU] falhou gravacao da calibracao");
    uiMessage("Erro a gravar", "Calibracao");
    return false;
  }

  g_cal = fresh;
  uiMessage("IMU", "Calibrado");
  delay(2500);
  return true;
}

// Ver documentacao completa em Imu.h. Traduz diretamente as chamadas da
// biblioteca LSM6DS3 (readFloatGyroX/Y/Z, readFloatAccelX/Y/Z) para os
// parametros de saida, sem qualquer processamento adicional.
bool readRaw(float &ax, float &ay, float &az,
             float &gx, float &gy, float &gz) {
  if (!g_started) return false;
  gx = imu.readFloatGyroX();
  gy = imu.readFloatGyroY();
  gz = imu.readFloatGyroZ();
  ax = imu.readFloatAccelX();
  ay = imu.readFloatAccelY();
  az = imu.readFloatAccelZ();
  return true;
}

// Ver documentacao completa em Imu.h. Reaproveita readRaw() e depois
// subtrai os offsets guardados em g_cal (calculados por runCalibration).
bool readCalibrated(float &ax, float &ay, float &az,
                    float &gx, float &gy, float &gz) {
  if (!readRaw(ax, ay, az, gx, gy, gz)) return false;
  gx -= g_cal.gyro_x;
  gy -= g_cal.gyro_y;
  gz -= g_cal.gyro_z;
  ax -= g_cal.accel_x;
  ay -= g_cal.accel_y;
  az -= g_cal.accel_z;
  return true;
}

const ImuCalibration &offsets() {
  return g_cal;
}

// Ver documentacao completa em Imu.h. Antes de criar a task, reinicia o
// estado de deteccao de movimento (g_motion) e a contagem de passos
// (g_stepCount), garantindo que um eventual restart da task comeca "do
// zero" e nao arrasta estado de uma execucao anterior. E idempotente:
// se a task ja existe (g_taskHandle != nullptr), nao faz nada e devolve
// true, para tornar seguro chamar esta funcao mais do que uma vez.
bool startTask() {
  if (!g_started) {
    Serial.println("[IMU] startTask: IMU nao inicializada");
    return false;
  }

  if (g_taskHandle != nullptr) {
    return true;
  }

  g_motion = MotionState{};
  g_stepCount = 0;

  BaseType_t ok = xTaskCreate(
      imuTask,
      "imu_task",
      IMU_TASK_STACK_WORDS,
      nullptr,
      TASK_PRIO_NORMAL,
      &g_taskHandle);

  if (ok != pdPASS) {
    g_taskHandle = nullptr;
    Serial.println("[IMU] startTask: falha ao criar task");
    return false;
  }

  return true;
}

bool isTaskRunning() {
  return g_taskRunning && (g_taskHandle != nullptr);
}

// Ver documentacao completa em Imu.h. A secao critica (taskENTER_CRITICAL/
// taskEXIT_CRITICAL) garante que a copia de g_latestSample para 'out' e
// atomica em relacao a escrita feita por imuTask(), evitando ler uma
// amostra parcialmente atualizada.
bool getLatestSample(Sample &out) {
  if (!isTaskRunning()) return false;
  taskENTER_CRITICAL();
  out = g_latestSample;
  taskEXIT_CRITICAL();
  return true;
}

uint32_t stepCount() {
  return g_stepCount;
}

// *** DIAGNOSTICO TEMPORARIO (otimizacao de RAM) *** — ver Imu.h.
uint32_t taskStackHighWaterMarkWords() {
  if (g_taskHandle == nullptr) return 0;
  return static_cast<uint32_t>(uxTaskGetStackHighWaterMark(g_taskHandle));
}

} // namespace Imu
