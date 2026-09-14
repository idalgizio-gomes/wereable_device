// Ppg.cpp - sensor MAX3010x: config SpO2 vs HR, pipeline de filtros (canal verde -> BPM), task FreeRTOS que alterna SpO2 (1/min) e HR (enquanto inativo, via IMU).
#include "Ppg/Ppg.h"

#include "Imu/Imu.h"
#include "Clock/Clock.h"
#include <MAX30105.h>
#include <spo2_algorithm.h>
#include <Wire.h>
#include <rtos.h>
#include <math.h>
#include <stdio.h>

#define PPG_USE_EXTERNAL_WIRE_ONLY 1 // PPG externo em D4/D5

// captura de sinal raw (raw/low/high/diff+deteccao) para diagnostico pontual do detetor de HR; sem custo quando desligado (#if remove do binario)
#define DEBUG_HR_RAW_CAPTURE 0
#define DEBUG_HR_RAW_CAPTURE_SAMPLES 800  // ~8s a 100Hz


namespace {

MAX30105 g_sensor;
bool g_started = false;
TaskHandle_t g_taskHandle = nullptr;
volatile bool g_taskRunning = false;
Ppg::Metrics g_latest = {}; // protegido por secoes criticas
TwoWire *g_ppgBus = nullptr;
const char *g_ppgBusName = "N/A";

constexpr uint32_t SPO2_INTERVAL_MS = 30000;
constexpr uint32_t HR_SAMPLE_INTERVAL_MS = 10;
constexpr uint32_t TASK_LOOP_DELAY_IDLE_MS = 200;
constexpr uint32_t TASK_LOOP_DELAY_HR_MS = 2;
constexpr uint32_t HR_STREAM_STOP_HOLDOFF_MS = 3000; // tolerancia apos fim de inactivity antes de desligar HR
constexpr uint32_t kManualHrMaxDurationMs = 30000;
constexpr uint16_t PPG_TASK_STACK_WORDS = 640; // reduzido de 1152 apos medir uxTaskGetStackHighWaterMark() real (~160/1152 usadas)
constexpr uint32_t FINGER_THRESHOLD = 50000;
constexpr int32_t SPO2_BUFFER_LEN = 100; // exigido pelo algoritmo Maxim

//Reduzido de 2000 para 500 — com 2s de intervalo, tirar o sensor do pulso deixava ruido passar como batimento valido ate ao proximo check
constexpr uint32_t HR_FINGER_CHECK_INTERVAL_MS = 500;
constexpr byte HR_FINGER_CHECK_IR_AMPLITUDE = 60; // mesmo brilho de setupForSpo2()

uint32_t g_irBuffer[SPO2_BUFFER_LEN];
uint32_t g_redBuffer[SPO2_BUFFER_LEN];
bool g_hrStreaming = false;
uint32_t g_lastHrSampleMs = 0;

// streaming de HR so usa LED verde (sem gate de IR); g_hrFingerPresent guarda a ultima verificacao real (checkFingerPresentBrief) exigida antes de aceitar um batimento, senao ruido/luz ambiente era aceite como batimento com a placa fora do pulso
bool g_hrFingerPresent = false;
uint32_t g_lastHrFingerCheckMs = 0;
uint32_t g_inactOffSinceMs = 0;
volatile bool g_shutdownRequested = false;
volatile bool g_suspendForPowerCheck = false;
volatile uint32_t g_manualHrDeadlineMs = 0; // 0 = nenhum pedido pendente
volatile bool g_manualSpo2Requested = false;
#if DEBUG_HR_RAW_CAPTURE
uint32_t g_hrRawCaptureCount = 0;
#endif

void stampDateTime(char *out, size_t outLen) {
  if (!Clock::formatDateTime(out, outLen)) {
    snprintf(out, outLen, "00/00/0000 00:00:00");
  }
}

// recuperacao do I2C externo: se o firmware reiniciar a meio de uma transacao, o escravo pode prender SDA em LOW
#if defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
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

// gera ate 18 pulsos de clock + condicao STOP manual para libertar o barramento preso
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

// estado do pipeline de deteccao de batimento, unificado para poder ser reiniciado em bloco (resetHrFilterState); antes vivia disperso em statics locais e nao era reposto entre streamings, contaminando o BPM seguinte com o valor de uma sessao anterior
struct HrFilterState {
  float lpPrevY = 0;
  float hpPrevX = 0;
  float hpPrevY = 0;
  float derivPrev = 0;
  float beatPrevDiff = 0;
  unsigned long beatLastMs = 0;
  float beatPeakAbsHigh = 0;         // gate de amplitude minima, ver kMinBeatPeakAmplitude
  unsigned long bpmLastBeatTime = 0;
  float bpmValue = 0;
  float smoothBuf[5] = {0, 0, 0, 0, 0};
  int smoothIdx = 0;
  bool smoothFilled = false;
  float smoothSum = 0;
};

HrFilterState g_hrFilter;

void resetHrFilterState() {
  g_hrFilter = HrFilterState{};
#if DEBUG_HR_RAW_CAPTURE
  g_hrRawCaptureCount = 0;
#endif
}

void ledsOff() {
  g_sensor.setPulseAmplitudeRed(0);
  g_sensor.setPulseAmplitudeIR(0);
  g_sensor.setPulseAmplitudeGreen(0);
}

void sensorIdle() {
  ledsOff();
  g_sensor.shutDown();
}

// wakeUp primeiro para garantir que "apagar LED" e mesmo aplicado; usada fora do fluxo normal da task (suspendForPowerCheck/prepareForSystemOff)
void forceLedsOffNow() {
  if (!g_started) return;
  g_sensor.wakeUp();
  g_sensor.setPulseAmplitudeRed(0);
  g_sensor.setPulseAmplitudeIR(0);
  g_sensor.setPulseAmplitudeGreen(0);
  g_sensor.setPulseAmplitudeProximity(0);
  g_sensor.shutDown();
}

// pipeline: raw -> passa-baixo -> passa-alto -> derivada -> deteccao de cruzamento por zero (pico do batimento)
// LOW PASS 1a ordem, Fc~5Hz, Fs=100Hz
float lowPassFilter(float x) {
  static float Fs = 100.0;
  static float Ts = 1.0 / Fs;
  static float fc = 5;
  static float Rc = 1.0 / (2.0 * PI * fc);
  const float alpha = Ts / (Rc + Ts);
  const float y = g_hrFilter.lpPrevY + alpha * (x - g_hrFilter.lpPrevY);
  g_hrFilter.lpPrevY = y;
  return y;
}

// HIGH PASS 1a ordem, Fc~0.5Hz, Fs=100Hz
float highPassFilter(float x) {
  static float Fs = 100.0;
  static float Ts = 1.0 / Fs;
  static float fc = 0.5;
  static float Rc = 1.0 / (2.0 * PI * fc);
  const float alpha = Rc / (Rc + Ts);
  const float y = alpha * (g_hrFilter.hpPrevY + x - g_hrFilter.hpPrevX);
  g_hrFilter.hpPrevX = x;
  g_hrFilter.hpPrevY = y;
  return y;
}

float derivative(float x) {
  const float y = x - g_hrFilter.derivPrev;
  g_hrFilter.derivPrev = x;
  return y;
}

// abaixo disto, ruido/artefacto de movimento era aceite como cruzamento por zero valido (~160-190 "bpm" implausivel em repouso); valor conservador, a afinar com mais capturas
constexpr float kMinBeatPeakAmplitude = 40.0f;

// pico POS->NEG + amplitude minima do ciclo + anti-rebote de 300ms (200 BPM max)
bool detectHeartbeat(float diff, float high) {
  bool beatDetected = false;

  const float absHigh = fabsf(high);
  if (absHigh > g_hrFilter.beatPeakAbsHigh) {
    g_hrFilter.beatPeakAbsHigh = absHigh;
  }

  if (g_hrFilter.beatPrevDiff > 0 && diff <= 0) {
    unsigned long now = millis();

    if (now - g_hrFilter.beatLastMs > 300 && g_hrFilter.beatPeakAbsHigh >= kMinBeatPeakAmplitude) {
      beatDetected = true;
      g_hrFilter.beatLastMs = now;
    }
    g_hrFilter.beatPeakAbsHigh = 0;
  }

  g_hrFilter.beatPrevDiff = diff;
  return beatDetected;
}

// dt fora de 300-2000ms (30-200 BPM) mantem o ultimo valor calculado
float computeBPM() {
  unsigned long now = millis();
  int deltaMs = now - g_hrFilter.bpmLastBeatTime;

  if (deltaMs > 300 && deltaMs < 2000) {
    g_hrFilter.bpmValue = 60000.0 / deltaMs;
  }

  g_hrFilter.bpmLastBeatTime = now;
  return g_hrFilter.bpmValue;
}

// media movel simples, N=5
float smoothBPM(float bpm) {
  const int N = 5;

  g_hrFilter.smoothSum -= g_hrFilter.smoothBuf[g_hrFilter.smoothIdx];
  g_hrFilter.smoothBuf[g_hrFilter.smoothIdx] = bpm;
  g_hrFilter.smoothSum += bpm;

  g_hrFilter.smoothIdx++;
  if (g_hrFilter.smoothIdx >= N) {
    g_hrFilter.smoothIdx = 0;
    g_hrFilter.smoothFilled = true;
  }

  if (!g_hrFilter.smoothFilled) {
    return g_hrFilter.smoothSum / g_hrFilter.smoothIdx;
  }

  return g_hrFilter.smoothSum / N;
}

// modo SpO2: Red+IR (ledMode=2), parametros recomendados pela lib/exemplos SparkFun/Maxim
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

// modo HR continuo: liga os 3 LEDs e desliga Red/IR, so verde fica ativo (melhor SNR para volume sanguineo, menos energia)
// sampleAverage=1 (nao 8): FIFO_CONFIG faz media de N amostras por entrada na FIFO, dividindo a taxa efetiva por N; com 8 a FIFO so recebia amostra nova a ~12.5Hz, nao 100Hz como o pipeline (Fs=100 fixo, HR_SAMPLE_INTERVAL_MS=10ms) assume
void setupForHr() {
  g_sensor.wakeUp();
  const byte ledBrightness = 0x5F;
  const byte sampleAverage = 1;
  const byte ledMode = 3;      // Red + IR + Green
  const int sampleRate = 100;
  const int pulseWidth = 411;
  const int adcRange = 4096;
  g_sensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
  g_sensor.setPulseAmplitudeRed(0);
  g_sensor.setPulseAmplitudeIR(0);
}

bool waitSampleAvailable(uint32_t timeoutMs);

// liga IR por um instante, descarta amostras residuais (gravadas so com verde antes do IR ligar, senao davam sempre falso-negativo), le, desliga IR outra vez
bool checkFingerPresentBrief() {
  g_sensor.setPulseAmplitudeIR(HR_FINGER_CHECK_IR_AMPLITUDE);

  for (int i = 0; i < 4; i++) {
    if (!waitSampleAvailable(50)) break;
    g_sensor.check();
    g_sensor.nextSample();
  }

  uint32_t ir = 0;
  if (waitSampleAvailable(50)) {
    g_sensor.check();
    ir = g_sensor.getIR();
  }
  g_sensor.setPulseAmplitudeIR(0);
  return ir >= FINGER_THRESHOLD;
}

void startHrStreaming() {
  if (g_hrStreaming) return;
  setupForHr();
  resetHrFilterState();
  g_hrStreaming = true;
  g_lastHrSampleMs = 0;
  g_lastHrFingerCheckMs = 0; // forca verificacao de dedo imediata, senao os 1os ~2s aceitavam sem verificar
  g_hrFingerPresent = false;
  Serial.println("[PPG] HR stream ON");
}

void stopHrStreaming() {
  if (!g_hrStreaming) return;
  sensorIdle();
  g_hrStreaming = false;
  g_lastHrSampleMs = 0;
  g_inactOffSinceMs = 0;
  Serial.println("[PPG] HR stream OFF");
}

bool waitSampleAvailable(uint32_t timeoutMs) {
  const uint32_t startMs = millis();
  while (!g_sensor.available()) {
    g_sensor.check();
    if ((millis() - startMs) >= timeoutMs) {
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  return true;
}

// medicao completa e bloqueante (~1s): modo SpO2, dedo, 100 pares Red/IR, algoritmo Maxim; sensor sempre desligado no fim (finish)
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
  maxim_heart_rate_and_oxygen_saturation(
      g_irBuffer, SPO2_BUFFER_LEN, g_redBuffer,
      &spo2, &vSpo2, &hr, &vHr);

  validSpo2 = (vSpo2 != 0);
  validHr = (vHr != 0);
  return finish(validSpo2);
}

// pipeline completo sobre 1 amostra verde; devolve true so quando um batimento valido (30-200 BPM) e detetado
bool processHrSample(float &bpmOut, bool &validOut, bool &fingerPresent) {
  validOut = false;
  fingerPresent = true; // pipeline HR sem gate de IR proprio; o gate real e feito fora, via g_hrFingerPresent
  long raw = g_sensor.getGreen();
  float low = lowPassFilter(raw);
  float high = highPassFilter(low);
  float diff = derivative(high);
  bool beat = detectHeartbeat(diff, high);

#if DEBUG_HR_RAW_CAPTURE
  if (g_hrRawCaptureCount < DEBUG_HR_RAW_CAPTURE_SAMPLES) {
    Serial.print(F("[HRRAW] t="));
    Serial.print(millis());
    Serial.print(F(" raw="));
    Serial.print(raw);
    Serial.print(F(" low="));
    Serial.print(low, 3);
    Serial.print(F(" high="));
    Serial.print(high, 3);
    Serial.print(F(" diff="));
    Serial.print(diff, 3);
    Serial.print(F(" beat="));
    Serial.println(beat ? 1 : 0);
    g_hrRawCaptureCount++;
  }
#endif

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

// loop infinito: suspensao/shutdown -> SpO2 periodico -> HR continuo se inativo -> ritmo adaptativo
void ppgTask(void *arg) {
  (void)arg;
  g_taskRunning = true;
  uint32_t lastSpo2Ms = millis() - SPO2_INTERVAL_MS; // forca 1a tentativa de SpO2 no arranque
  uint32_t lastStatusMs = 0;

  Serial.println("[PPG] task iniciada");

  while (true) {
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

    // pedido manual (requestManualHr) equivale a inactivity enquanto o prazo nao expira
    const uint32_t manualDeadline = g_manualHrDeadlineMs;
    const bool manualHrActive = manualDeadline != 0 && (int32_t)(manualDeadline - nowMs) > 0;
    if (manualDeadline != 0 && !manualHrActive) {
      g_manualHrDeadlineMs = 0;
    }
    const bool wantHr = inactivity || manualHrActive;

    // SpO2: sensor nao faz os 2 modos ao mesmo tempo, interrompe HR se ativo
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

    if (wantHr) {
      g_inactOffSinceMs = 0;

      if (!g_hrStreaming) {
        startHrStreaming();
      }

      if ((nowMs - g_lastHrFingerCheckMs) >= HR_FINGER_CHECK_INTERVAL_MS) {
        g_lastHrFingerCheckMs = nowMs;
        g_hrFingerPresent = checkFingerPresentBrief();
        taskENTER_CRITICAL();
        g_latest.finger_present = g_hrFingerPresent;
        taskEXIT_CRITICAL();
        if (!g_hrFingerPresent) {
          resetHrFilterState(); // sem dedo: descarta estado acumulado que pudesse gerar batimento falso ao voltar
        }
      }

      if ((nowMs - g_lastHrSampleMs) >= HR_SAMPLE_INTERVAL_MS) {
        g_lastHrSampleMs = nowMs;

        float hrBpm = 0.0f;
        bool validHr = false;
        bool finger = false;
        const bool gotBeat = processHrSample(hrBpm, validHr, finger);

        if (gotBeat && validHr && g_hrFingerPresent) {
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
      // so desliga HR apos HR_STREAM_STOP_HOLDOFF_MS de tolerancia, para nao cortar por oscilacoes curtas do IMU
      if (g_inactOffSinceMs == 0) {
        g_inactOffSinceMs = nowMs;
      } else if ((nowMs - g_inactOffSinceMs) >= HR_STREAM_STOP_HOLDOFF_MS) {
        stopHrStreaming();
        g_inactOffSinceMs = 0;
      }
    }

    if ((nowMs - lastStatusMs) >= 5000) {
      lastStatusMs = nowMs;
      Ppg::Metrics snap = {};
      taskENTER_CRITICAL();
      snap = g_latest;
      taskEXIT_CRITICAL();
    }

    const uint32_t delayMs = g_hrStreaming ? TASK_LOOP_DELAY_HR_MS : TASK_LOOP_DELAY_IDLE_MS;
    vTaskDelay(pdMS_TO_TICKS(delayMs));
  }
}

} // namespace

namespace Ppg {

// procura o sensor nos buses candidatos (por defeito so Wire externo), recupera o bus se preso, sonda 0x57, so depois chama begin() da lib
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
    bus.setWireTimeout(25000, true); // evita bloqueio indefinido se o sensor nao responder
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

bool getLatest(Metrics &out) {
  if (!g_started) return false;
  taskENTER_CRITICAL();
  out = g_latest;
  taskEXIT_CRITICAL();
  return true;
}

void suspendForPowerCheck() {
  g_suspendForPowerCheck = true;
  forceLedsOffNow();
}

// so cancela se nao tiver havido shutdown definitivo entretanto
void resumeAfterPowerCheck() {
  if (!g_shutdownRequested) {
    g_suspendForPowerCheck = false;
  }
}

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

void requestManualHr(uint32_t durationMs) {
  if (g_shutdownRequested) return;
  if (durationMs > kManualHrMaxDurationMs) durationMs = kManualHrMaxDurationMs;
  g_manualHrDeadlineMs = millis() + durationMs;
}

void requestManualSpo2() {
  if (g_shutdownRequested) return;
  g_manualSpo2Requested = true;
}

uint32_t taskStackHighWaterMarkWords() {
  if (g_taskHandle == nullptr) return 0;
  return static_cast<uint32_t>(uxTaskGetStackHighWaterMark(g_taskHandle));
}

} // namespace Ppg
