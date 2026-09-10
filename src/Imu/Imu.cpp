// Imu.cpp - driver/wrapper LSM6DS3 (accel+giro 6 eixos): init I2C, calibracao (offset de fabrico), leitura raw/calibrada, task FreeRTOS com pedometro/queda/inatividade/pacing sobre a magnitude de aceleracao.
#include "Imu/Imu.h"
#include "Display/Ui.h"
#include "Storage/Storage.h"
#include "Clock/Clock.h"
#include <LSM6DS3.h>
#include <Wire.h>
#include <rtos.h>
#include <math.h>
#include <stdio.h>

// XIAO Sense/Sense Plus: IMU onboard esta no Wire1
#if defined(TARGET_SEEED_XIAO_NRF52840_SENSE) || defined(TARGET_SEEED_XIAO_NRF52840_SENSE_PLUS) || defined(ARDUINO_XIAO_MG24)
  #define IMU_I2C_BUS Wire1
  static const char *kImuBusName = "Wire1";
#else
  #define IMU_I2C_BUS Wire
  static const char *kImuBusName = "Wire";
#endif

#define IMU_I2C_SCAN_ENABLE 0 // pode bloquear no boot se o bus estiver num estado invalido; so diagnostico manual

static LSM6DS3 imu(I2C_MODE, 0x6A);
static ImuCalibration g_cal = {0, 0, 0, 0, 0, 0};
static bool g_started = false;
static TaskHandle_t g_taskHandle = nullptr;
static volatile bool g_taskRunning = false; // task criada (g_taskHandle) vs a correr de facto
static Imu::Sample g_latestSample = {}; // partilhada com getLatestSample(), acesso so em secao critica
static volatile uint32_t g_stepCount = 0;
// dia UTC (epoch/86400) da contagem atual; 0 = sentinela "sem relogio valido ainda". Reinicia a meia-noite UTC (LOINC 41950-7), ver resetStepsIfNewDay()
static uint32_t g_stepCountDayEpoch = 0;

// so reinicia com relogio valido (Clock::isValid()); sem isso, mantem contador continuo em vez de arriscar falso positivo de mudanca de dia com nowUtc()==0
static void resetStepsIfNewDay() {
  if (!Clock::isValid()) return;
  const uint32_t today = Clock::nowUtc() / 86400UL;
  if (g_stepCountDayEpoch != 0 && today != g_stepCountDayEpoch) {
    g_stepCount = 0;
  }
  g_stepCountDayEpoch = today;
}

static const int CAL_NUM_SAMPLES = 500;
static const int CAL_SAMPLE_DELAY_MS = 5;
static const uint32_t IMU_TASK_RATE_HZ = 52;
static const TickType_t IMU_TASK_PERIOD_TICKS = pdMS_TO_TICKS(19); // ~52.6 Hz, alinhado com accelSampleRate/gyroSampleRate=52
static const uint16_t IMU_TASK_STACK_WORDS = 768; // reduzido de 1024, ainda por confirmar com uxTaskGetStackHighWaterMark() em hardware real

namespace Imu {

static void stampDateTime(char *out, size_t outLen) {
  if (!Clock::formatDateTime(out, outLen)) {
    snprintf(out, outLen, "00/00/0000 00:00:00");
  }
}

// so usada com IMU_I2C_SCAN_ENABLE=1 (diagnostico manual)
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

// estado acumulado (entre iteracoes da task) dos detetores de movimento
struct MotionState {
  float magLowPass = 1.0f;     // media movel (passa-baixo) da magnitude de aceleracao, aproxima gravidade/postura
  bool stepArmed = true;
  uint8_t stepHighCount = 0;
  uint32_t lastStepMs = 0;
  uint8_t freefallCount = 0;
  bool freefall = false;
  uint16_t inactivityCount = 0;
  bool inactivity = false;
  bool turnArmed = true;
  uint8_t turnHighCount = 0;
  uint16_t turnEventsInWindow = 0;
  uint32_t pacingWindowStartMs = 0;
  uint8_t pacingIndex = 0;
};

static MotionState g_motion;

// pedometro: filtro passa-alto sobre |a| (hp = accMag - media movel lenta), pico = passo; exige N amostras altas seguidas + tempo refratario + rearm abaixo de threshold
static bool detectStep(float accMag, uint32_t nowMs) {
  constexpr float kAlpha = 0.96f;
  constexpr float kStepRiseThreshold = 0.20f;
  constexpr float kStepRearmThreshold = 0.06f;
  constexpr uint8_t kStepMinHighSamples = 2; // ~38 ms @ 52 Hz
  constexpr uint32_t kStepRefractoryMs = 320;

  g_motion.magLowPass = (kAlpha * g_motion.magLowPass) + ((1.0f - kAlpha) * accMag);
  const float highPass = accMag - g_motion.magLowPass;

  if (highPass > kStepRiseThreshold) {
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

  if (highPass < kStepRearmThreshold) {
    g_motion.stepArmed = true;
    g_motion.stepHighCount = 0;
  }
  return false;
}

// queda livre: |a| cai perto de 0g (sem reacao de apoio); exige N amostras seguidas abaixo do threshold, estado persiste (nao e one-shot)
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

// inatividade: giro abaixo de kGyroStillDps E accel perto de 1g constante, sustentado por kInactivitySamples (~2s); "contador com fuga" (kNoiseDecay) evita que 1 amostra ruidosa reinicie tudo
static bool detectInactivity(float accMag, float gx, float gy, float gz) {
  constexpr float kGyroStillDps = 6.0f;
  constexpr float kAccelStillDeltaG = 0.12f; // alargado de 0.08g: 3s sem nenhuma amostra acima disso era dificil de sustentar com micro-tremor normal do pulso
  constexpr uint16_t kInactivitySamples = 104; // 2 s @ 52 Hz
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

// "pacing"/curvas apertadas via norma do giroscopio: proxy precoce de deambulacao (wandering), complementar ao geofencing — sinal nao validado clinicamente, heuristicas por afinar com dados reais. Conta rajadas de rotacao acima de threshold (mesmo padrao rise/rearm de detectStep), converte para indice 0-100 por janela de 1 min
static uint8_t detectPacing(float gyroNorm, uint32_t nowMs) {
  constexpr float kTurnGyroThresholdDps = 45.0f;
  constexpr float kTurnRearmThresholdDps = 15.0f;
  constexpr uint8_t kTurnMinHighSamples = 5;        // ~96 ms @ 52 Hz
  constexpr uint32_t kPacingWindowMs = 60000;
  constexpr uint16_t kPacingTurnsForMaxScore = 12;  // 12+ curvas/min -> indice 100

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

// task de aquisicao (~52Hz via vTaskDelayUntil, evita drift); publica Sample com valores RAW, detetores usam valores calibrados internamente
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
      const float cax = ax - g_cal.accel_x;
      const float cay = ay - g_cal.accel_y;
      const float caz = az - g_cal.accel_z;
      const float cgx = gx - g_cal.gyro_x;
      const float cgy = gy - g_cal.gyro_y;
      const float cgz = gz - g_cal.gyro_z;
      const float accMag = sqrtf((cax * cax) + (cay * cay) + (caz * caz));
      const uint32_t nowMs = millis();

      resetStepsIfNewDay();
      if (detectStep(accMag, nowMs)) {
        g_stepCount++;
        Serial.print("[IMU] passo: ");
        Serial.println(g_stepCount);
      }

      const bool freefall = detectFreefall(accMag);
      const bool inactivity = detectInactivity(accMag, cgx, cgy, cgz);
      const float gyroNorm = sqrtf((cgx * cgx) + (cgy * cgy) + (cgz * cgz));
      const uint8_t pacingIndex = detectPacing(gyroNorm, nowMs);

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

      taskENTER_CRITICAL();
      g_latestSample = sample;
      taskEXIT_CRITICAL();

      printDivider++;
      if (printDivider >= 52) { // ~1x/seg
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

    vTaskDelayUntil(&lastWake, IMU_TASK_PERIOD_TICKS);
  }
}

bool begin() {
  Serial.print("[IMU] ");
  Serial.print(kImuBusName);
  Serial.println(".begin()");
  Serial.flush();
  IMU_I2C_BUS.begin();
  delay(10);

#if IMU_I2C_SCAN_ENABLE
  i2cScan(IMU_I2C_BUS, kImuBusName);
#endif

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

// pede parado via display, espera 3s, le CAL_NUM_SAMPLES amostras e usa a media como offset; Z do accel subtrai tambem 1g (gravidade que se quer preservar nas leituras calibradas)
static bool runCalibration(ImuCalibration &out) {
  Serial.println("[IMU] a calibrar - manter parado");
  Serial.flush();

  Serial.println("[IMU] -> uiMessage");
  Serial.flush();
  uiMessage("Iniciar", "Calibracao");
  Serial.println("[IMU] <- uiMessage OK");
  Serial.flush();

  delay(3000); // tempo para o utilizador pousar o dispositivo

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

// reaproveita calibracao ja gravada em flash; so recalibra se nao existir
bool ensureCalibrated() {
  if (!g_started) {
    Serial.println("[IMU] ensureCalibrated: nao inicializado");
    return false;
  }

  if (Storage::hasCalibration() && Storage::loadCalibration(g_cal)) {
    Serial.println("[IMU] calibracao carregada do FS");
    uiMessage("IMU", "Calibrado");
    delay(2500);
    return true;
  }

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

// idempotente: se a task ja existe, no-op; reinicia g_motion/g_stepCount antes de criar
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
  g_stepCountDayEpoch = 0;

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

uint32_t taskStackHighWaterMarkWords() {
  if (g_taskHandle == nullptr) return 0;
  return static_cast<uint32_t>(uxTaskGetStackHighWaterMark(g_taskHandle));
}

} // namespace Imu
