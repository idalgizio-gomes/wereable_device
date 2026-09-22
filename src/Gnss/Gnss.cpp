// Gnss.cpp - driver CAM-M8Q (u-blox, I2C): deteta o barramento, faz polling de posicao (~1Hz)
// numa task que pode ser parada/reiniciada em runtime (ver initGnssPowerManagement() em
// main.cpp) - ao contrario do IMU/PPG, que correm sempre. Confirmado em hardware 2026-09-22.
#include "Gnss/Gnss.h"
#include "QspiRingBuffer/QspiRingBuffer.h"

#include <SparkFun_u-blox_GNSS_Arduino_Library.h>
#include <Wire.h>
#include <rtos.h>

namespace {

SFE_UBLOX_GNSS gnss;

TaskHandle_t g_taskHandle = nullptr;
volatile bool g_taskRunning = false; // task criada e a correr o loop principal
volatile bool g_stopRequested = false; // pedido de paragem; a task sai sozinha (nunca vTaskDelete de fora, ver stopTask())
bool g_started = false; // begin() teve sucesso
const char *g_busName = "N/A";

Gnss::Sample g_latestSample = {}; // partilhada com getLatestSample(), acesso so em secao critica

constexpr uint32_t kQueryIntervalMs = 1000; // o modulo so tem posicao nova a esta cadencia (ver test/GNSS.cpp)
constexpr uint16_t kGnssTaskStackWords = 768; // mesma ordem de grandeza do IMU_TASK_STACK_WORDS; por confirmar com uxTaskGetStackHighWaterMark() em hardware real

// tenta Wire (interno) e depois Wire1 (externo, D4/D5 - mesmo barramento onde o PPG esta
// confirmado); devolve true e regista o barramento assim que o modulo responder num dos dois.
bool beginGnssBus() {
  struct Candidate {
    TwoWire *bus;
    const char *name;
  };
  const Candidate candidates[] = {
      {&Wire, "Wire (interno)"},
      {&Wire1, "Wire1 (externo, D4/D5)"},
  };

  for (const auto &candidate : candidates) {
    Serial.print("[GNSS] a tentar barramento ");
    Serial.print(candidate.name);
    Serial.println("...");

    candidate.bus->begin();
    if (gnss.begin(*candidate.bus)) {
      g_busName = candidate.name;
      Serial.print("[GNSS] modulo encontrado em ");
      Serial.println(candidate.name);
      return true;
    }
    Serial.print("[GNSS] nao respondeu em ");
    Serial.println(candidate.name);
  }

  return false;
}

// task de polling (~1 Hz): sai do loop assim que g_stopRequested for true, faz o proprio
// vTaskDelete(nullptr) e limpa g_taskHandle/g_taskRunning - nunca e apagada de fora (deletar uma
// task a meio de uma transacao I2C podia deixar o barramento num estado invalido).
void gnssTask(void *arg) {
  (void)arg;

  g_taskRunning = true;
  Serial.println("[GNSS] task iniciada");

  while (!g_stopRequested) {
    const uint32_t startMs = millis();

    Gnss::Sample sample = {};
    sample.timestamp_ms = startMs;
    sample.fix = gnss.getGnssFixOk();
    sample.siv = gnss.getSIV();

    if (sample.fix) {
      sample.latitude = gnss.getLatitude();
      sample.longitude = gnss.getLongitude();
      sample.altitude_mm = gnss.getAltitude();
    }

    taskENTER_CRITICAL();
    g_latestSample = sample;
    taskEXIT_CRITICAL();

    // espera ate ao proximo poll, mas em fatias curtas para reagir a g_stopRequested sem
    // atraso de ate 1s na paragem
    while (!g_stopRequested && (millis() - startMs) < kQueryIntervalMs) {
      delay(50);
    }
  }

  Serial.println("[GNSS] task a parar (pedido de paragem)");
  g_taskRunning = false;
  g_taskHandle = nullptr;
  vTaskDelete(nullptr);
}

} // namespace

namespace Gnss {

bool begin() {
  if (g_started) return true;
  g_started = beginGnssBus();
  if (!g_started) {
    Serial.println("[GNSS] ERRO: modulo nao detetado em nenhum dos dois barramentos I2C.");
    return false;
  }
  // reduz trafego I2C: so UBX binario, sem o ruido das sentencas NMEA (mesma otimizacao do
  // exemplo oficial da biblioteca)
  gnss.setI2COutput(COM_TYPE_UBX);
  return true;
}

bool startTask() {
  if (!g_started) {
    Serial.println("[GNSS] startTask: GNSS nao inicializado");
    return false;
  }
  if (g_taskHandle != nullptr) {
    return true; // ja a correr, no-op (idempotente, mesmo padrao de Imu::startTask)
  }

  g_stopRequested = false;
  g_latestSample = Sample{};

  const BaseType_t ok = xTaskCreate(
      gnssTask,
      "gnss_task",
      kGnssTaskStackWords,
      nullptr,
      TASK_PRIO_NORMAL,
      &g_taskHandle);

  if (ok != pdPASS) {
    g_taskHandle = nullptr;
    Serial.println("[GNSS] startTask: falha ao criar task");
    return false;
  }
  return true;
}

void stopTask() {
  if (g_taskHandle == nullptr) return;
  g_stopRequested = true; // a propria task sai do loop, faz vTaskDelete(nullptr) e limpa o handle
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

const char *busName() {
  return g_busName;
}

} // namespace Gnss
