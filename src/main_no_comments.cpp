

#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <nrf_power.h>
#include <SPI.h>
#include <string.h>
#include <rtos.h>
#include <math.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1351.h>
#include "Display/app_icons.h"
#include "Display/Ui.h"
#include "Storage/Storage.h"
#include "Imu/Imu.h"
#include "Ppg/Ppg.h"
#include "Ble/Ble.h"
#include "QspiRingBuffer/QspiRingBuffer.h"
#include "ImuPpgPayload.h"
#include "Clock/Clock.h"
#include "Lora/Lora.h"
#include "Emergency/Emergency.h"
#include "Nfc/Nfc.h"
#include "Battery/Battery.h"

#define WIPE_STALE_STORAGE 0
#define WIPE_RING_BUFFER 0
#define QSPI_RING_BUFFER_SELF_TEST 0
#define STORAGE_TASK_ENABLE 1
#define DEBUG_SERIAL_WAKE 1
#define DEBUG_DISABLE_SLEEP 1
#define DEBUG_STACK_WATERMARKS 1
#define DEBUG_FORCE_IMU_RECALIBRATION 0
#define BTN_PIN 0
#define LONG_PRESS_TIME 5000
#define DEBOUNCE_TIME 50
#define OLED_CS_PIN D9
#define OLED_DC_PIN D10
#define OLED_RST_PIN D11
#define SCREEN_W 128
#define SCREEN_H 128
#define COLOR_BLACK 0x0000
#define COLOR_WHITE 0xFFFF

SPIClass dispSPI(NRF_SPIM3, PIN_SPI1_MISO, PIN_SPI1_SCK, PIN_SPI1_MOSI);
Adafruit_SSD1351 display(SCREEN_W, SCREEN_H, &dispSPI,
                         OLED_CS_PIN, OLED_DC_PIN, OLED_RST_PIN);

bool isRunning = false;

namespace
{

  constexpr uint16_t STORAGE_TASK_STACK_WORDS = 768;

  constexpr uint32_t STORAGE_TASK_IDLE_MS = 5;

  static_assert(sizeof(ImuPpgPayloadV1) <= QspiRingBuffer::kPayloadSize,
                "ImuPpgPayloadV1 must fit ring payload");

  TaskHandle_t g_storageTaskHandle = nullptr;

  int16_t clampToI16(long v){
    if (v > 32767L)
      return 32767;
    if (v < -32768L)
      return -32768;
    return static_cast<int16_t>(v);
  }

  void storageTask(void *arg){
    (void)arg;

    uint32_t lastImuTs = 0;
    uint32_t consumedSpo2Ts = 0;
    uint32_t consumedHrTs = 0;
    uint32_t pushed = 0;
    uint32_t pushFail = 0;
    uint32_t lastPrintMs = 0;

    Serial.println("[STOR] storage_task iniciada");

    while (true)
    {

      Imu::Sample imu = {};
      if (!Imu::getLatestSample(imu))
      {
        vTaskDelay(pdMS_TO_TICKS(STORAGE_TASK_IDLE_MS));
        continue;
      }

      if (imu.timestamp_ms == 0 || imu.timestamp_ms == lastImuTs)
      {
        vTaskDelay(pdMS_TO_TICKS(1));
        continue;
      }
      
      lastImuTs = imu.timestamp_ms;

      int16_t spo2Out = 0;
      int16_t hrOutX10 = 0;

      Ppg::Metrics ppg = {};
      if (Ppg::getLatest(ppg))
      {

        if (ppg.spo2_valid && ppg.spo2_timestamp_ms != 0 &&
            ppg.spo2_timestamp_ms != consumedSpo2Ts)
        {
          spo2Out = clampToI16(ppg.spo2_value);
          consumedSpo2Ts = ppg.spo2_timestamp_ms;
        }

        if (ppg.hr_valid && ppg.hr_timestamp_ms != 0 &&
            ppg.hr_timestamp_ms != consumedHrTs)
        {
          const long hr10 = lroundf(ppg.hr_bpm);
          hrOutX10 = clampToI16(hr10);
          consumedHrTs = ppg.hr_timestamp_ms;
        }
      }

      ImuPpgPayloadV1 payload = {};
      payload.ax = imu.ax;
      payload.ay = imu.ay;
      payload.az = imu.az;
      payload.gx = imu.gx;
      payload.gy = imu.gy;
      payload.gz = imu.gz;
      payload.steps = imu.step_count;
      payload.ff = imu.freefall ? 1 : 0;
      payload.inact = imu.inactivity ? 1 : 0;
      payload.spo2 = spo2Out;
      payload.hr = hrOutX10;
      payload.pacing_index = imu.pacing_index;

      uint32_t recTs = imu.timestamp_ms;
      const uint32_t nowUtc = Clock::nowUtc();
      if (nowUtc != 0)
        recTs = nowUtc;

      if (QspiRingBuffer::push(kImuPpgRecordTypeV1,
                               reinterpret_cast<const uint8_t *>(&payload),
                               sizeof(payload),
                               recTs))
      {
        pushed++;
        if (payload.spo2 != 0 || payload.hr != 0)
        {
          Serial.print("[STOR] PPG reg spo2=");
          Serial.print(payload.spo2);
          Serial.print(" hr=");
          Serial.println(payload.hr);
        }
      }
      else
      {
        pushFail++;
      }

      const uint32_t now = millis();
      if ((now - lastPrintMs) >= 1000)
      {
        lastPrintMs = now;
        Serial.print("[STOR] push/s=");
        Serial.print(pushed);
        Serial.print(" fail=");
        Serial.print(pushFail);
        Serial.print(" ring_count=");
        Serial.println(QspiRingBuffer::count());
        pushed = 0;
        pushFail = 0;
      }
    }
  }

}

void delayPollingEmergency(uint32_t ms)
{
  const uint32_t start = millis();
  while ((millis() - start) < ms)
  {
    Emergency::update();
    delay(5);
  }
}

bool waitRelease(uint32_t timeoutMs = 0, bool pollEmergency = false)
{
  const uint32_t t0 = millis();
  while (digitalRead(BTN_PIN) == LOW)
  {
    if (timeoutMs != 0 && (millis() - t0) >= timeoutMs)
    {
      return false;
    }
    if (pollEmergency)
      Emergency::update();
    delay(5);
  }
  delay(30);
  return true;
}

bool buttonPressedStable(bool pollEmergency = false)
{
  if (digitalRead(BTN_PIN) == LOW)
  {
    if (pollEmergency)
    {
      delayPollingEmergency(DEBOUNCE_TIME);
    }
    else
    {
      delay(DEBOUNCE_TIME);
    }
    return digitalRead(BTN_PIN) == LOW;
  }
  return false;
}

#if DEBUG_SERIAL_WAKE

const char *pollSerialLine()
{
  static char buf[16];
  static uint8_t len = 0;

  while (Serial.available() > 0)
  {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r')
    {
      if (len > 0)
      {
        buf[len] = '\0';
        len = 0;
        return buf;
      }
    }
    else if (len < sizeof(buf) - 1)
    {
      buf[len++] = c;
    }
  }
  return nullptr;
}

bool serialCommandReceived(const char *cmd)
{
  const char *line = pollSerialLine();
  return line != nullptr && strcmp(line, cmd) == 0;
}
#endif

bool waitForLongPress(bool pollEmergency = false)
{
#if DEBUG_SERIAL_WAKE
  if (serialCommandReceived("WAKE"))
  {
    Serial.println("[DEBUG] comando WAKE recebido -> a simular long-press");
    return true;
  }
#endif
  if (!buttonPressedStable(pollEmergency))
    return false;
  unsigned long start = millis();
  while (millis() - start < LONG_PRESS_TIME)
  {
    if (digitalRead(BTN_PIN) == HIGH)
      return false;
#if DEBUG_SERIAL_WAKE
    if (serialCommandReceived("WAKE"))
    {
      Serial.println("[DEBUG] comando WAKE recebido a meio -> a simular long-press");
      return true;
    }
#endif
    if (pollEmergency)
    {
      Emergency::update();
      delay(5);
    }
  }

  waitRelease(0, pollEmergency);
  return true;
}

void goToSleep()
{
#if DEBUG_DISABLE_SLEEP
  Serial.println("[DEBUG] goToSleep() pedido, mas DEBUG_DISABLE_SLEEP=1 -> a ignorar (dispositivo continua ligado)");
  return;
#endif
  Serial.println("A desligar...");
  Serial.flush();
  isRunning = false;

  Ppg::prepareForSystemOff();
  Ble::stopBroadcast();

  (void)QspiRingBuffer::sync();

  digitalWrite(LED_BUILTIN, HIGH);
  pinMode(OLED_RST_PIN, OUTPUT);
  digitalWrite(OLED_RST_PIN, LOW);

  NRF_GPIO->LATCH = NRF_GPIO->LATCH;
  NRF_P1->LATCH = NRF_P1->LATCH;

  nrf_gpio_cfg_input(BTN_PIN, NRF_GPIO_PIN_PULLUP);
  nrf_gpio_cfg_sense_input(BTN_PIN,
                           NRF_GPIO_PIN_PULLUP,
                           NRF_GPIO_PIN_SENSE_LOW);

  uint32_t rc = sd_power_system_off();
  (void)rc;

  NRF_POWER->SYSTEMOFF = 1;

  NVIC_SystemReset();
}

void showLogo(const uint8_t *bits, int16_t w, int16_t h, uint16_t ms)
{
  display.fillScreen(COLOR_BLACK);
  int16_t x = (SCREEN_W - w) / 2;
  int16_t y = (SCREEN_H - h) / 2;
  display.drawXBitmap(x, y, bits, w, h, COLOR_WHITE);
  delay(ms);
}

void showReady()
{
  Serial.println("showReady: dispSPI.begin()");
  dispSPI.begin();

  Serial.println("showReady: display.begin()");
  display.begin();

  Serial.println("showReady: IPCA");
  showLogo(IPCA_Logo_bits, IPCA_Logo_width, IPCA_Logo_height, 1500);

  Serial.println("showReady: 2AI");
  showLogo(twoAI_Logo_bits, twoAI_Logo_width, twoAI_Logo_height, 1500);

  Serial.println("showReady: Intellicare");
  showLogo(Intellicare_Logo_bits, Intellicare_Logo_width, Intellicare_Logo_height, 1500);

  display.fillScreen(COLOR_BLACK);
  Serial.println("showReady: done");
}

void uiMessage(const char *line1, const char *line2)
{
  display.fillScreen(COLOR_BLACK);
  display.setTextColor(COLOR_WHITE);
  display.setTextSize(2);

  auto drawCentered = [&](const char *txt, int16_t y)
  {
    int16_t x1, y1;
    uint16_t w, h;
    display.getTextBounds(txt, 0, y, &x1, &y1, &w, &h);
    int16_t x = (SCREEN_W - (int16_t)w) / 2;
    if (x < 0)
      x = 0;
    display.setCursor(x, y);
    display.print(txt);
  };

  if (line2 == nullptr)
  {
    drawCentered(line1, 56);
  }
  else
  {
    drawCentered(line1, 44);
    drawCentered(line2, 70);
  }
}

void showHourDateScreen()
{
  char line1[16] = "HORA";
  char line2[16] = "DATA";
  if (Clock::isValid())
  {
    (void)Clock::formatTime(line1, sizeof(line1));
    (void)Clock::formatDate(line2, sizeof(line2));
  }

  uiMessage(line1, line2);
}

void initStorage()
{
  if (!Storage::begin())
    return;

#if WIPE_STALE_STORAGE
  Serial.println("[Storage] WIPE: a apagar calib + aes residuais");
  Storage::clearAll();
#endif

  Serial.print("[Storage] hasCalibration: ");
  Serial.println(Storage::hasCalibration() ? "SIM" : "NAO");
  Serial.print("[Storage] hasAesKey:      ");
  Serial.println(Storage::hasAesKey() ? "SIM" : "NAO");

  Storage::validate();
}

void initImu()
{
#if DEBUG_FORCE_IMU_RECALIBRATION

  Serial.println("[DEBUG] DEBUG_FORCE_IMU_RECALIBRATION=1 -> a apagar calibracao antiga");
  Storage::clearCalibration();
#endif
  if (!Imu::begin())
  {
    uiMessage("IMU", "ERRO");
    delay(2000);
    return;
  }
  if (!Imu::ensureCalibrated())
  {
    uiMessage("IMU", "ERRO");
    delay(2000);
    return;
  }

  if (!Imu::startTask())
  {
    Serial.println("[IMU] nao foi possivel iniciar imu_task");
    uiMessage("IMU TASK", "ERRO");
    delay(2000);
    return;
  }
  Serial.println("[IMU] imu_task ativa");
}

void initPpg()
{
  Serial.println("[PPG] initPpg(): inicio");
  if (!Ppg::begin())
  {
    Serial.println("[PPG] init falhou");
    return;
  }

  if (!Ppg::startTask())
  {
    Serial.println("[PPG] nao foi possivel iniciar ppg_task");
    return;
  }

  Serial.println("[PPG] ppg_task ativa");
}

void initBle()
{
  if (!Ble::begin())
  {
    uiMessage("BLE", "ERRO");
    delay(2000);
    return;
  }
  Ble::ensureAesKey();
  Ble::ensureTimeSync();
}

void initBleDataLink()
{
  if (!Ble::startBroadcast())
  {
    Serial.println("[BLE] GATT-only start failed");
    return;
  }
  Serial.println("[BLE] GATT-only active");
}

void initLora()
{
  if (!Lora::begin())
  {
    Serial.println("[LORA] init falhou — a continuar sem radio LoRa (ver Lora.h, pinout ainda por confirmar)");
    return;
  }
  Serial.println("[LORA] radio ativo");

  Lora::sendTest("CareWear LoRa test");
}

void initNfc()
{
  if (!Nfc::begin())
  {
    Serial.println("[NFC] init nao avancou — a continuar sem NFC (ver Nfc.h, antena por confirmar)");
    return;
  }
  Serial.println("[NFC] ativo");
}

void initBattery()
{
  Battery::begin();
}

void initQspiRingBuffer()
{
  if (!QspiRingBuffer::begin(true))
  {
    Serial.println("[QSPIRB] init falhou");
    return;
  }

#if WIPE_RING_BUFFER
  Serial.println("[QSPIRB] WIPE: a reformatar (reprovisionamento pedido pelo utilizador)");
  if (!QspiRingBuffer::format())
  {
    Serial.println("[QSPIRB] WIPE: format() falhou");
  }
#endif

  Serial.print("[QSPIRB] capacidade slots: ");
  Serial.println(QspiRingBuffer::capacity());
  Serial.print("[QSPIRB] count atual:      ");
  Serial.println(QspiRingBuffer::count());

#if QSPI_RING_BUFFER_SELF_TEST
  if (!QspiRingBuffer::selfTest())
  {
    Serial.println("[QSPIRB] self-test falhou");
  }
#endif
}

void initStorageTask()
{
#if STORAGE_TASK_ENABLE
  if (g_storageTaskHandle != nullptr)
    return;

  BaseType_t ok = xTaskCreate(
      storageTask,
      "storage_task",
      STORAGE_TASK_STACK_WORDS,
      nullptr,
      TASK_PRIO_LOW,
      &g_storageTaskHandle);

  if (ok != pdPASS)
  {
    g_storageTaskHandle = nullptr;
    Serial.println("[STOR] falha ao criar storage_task");
    return;
  }

  Serial.println("[STOR] storage_task ativa");
#endif
}

void setup()
{
  pinMode(BTN_PIN, INPUT_PULLUP);

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);

  Serial.begin(115200);
#if DEBUG_DISABLE_SLEEP
  Serial.println("[BOOT] AVISO: DEBUG_DISABLE_SLEEP=1 -- sem poupanca de energia; so desligar depois do botao fisico (BTN_PIN) estar reparado (ver DEBUG_SERIAL_WAKE/DEBUG_DISABLE_SLEEP em main.cpp)");
#endif
#if DEBUG_SERIAL_WAKE
  Serial.println("[BOOT] AVISO: DEBUG_SERIAL_WAKE=1 -- comando WAKE/SLEEP pela serie substitui o botao fisico (BTN_PIN); so desligar depois do botao estar reparado (ver DEBUG_SERIAL_WAKE/DEBUG_DISABLE_SLEEP em main.cpp)");
#endif
  delay(100);
  Serial.println("Acordou do System OFF");

  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);

  Bluefruit.begin(2, 0);
  Bluefruit.setName("Wearable");
  Serial.println("SoftDevice inicializado");

  bool debugForcedWake = false;
#if DEBUG_SERIAL_WAKE
  Serial.println("[DEBUG] botao fisico indisponivel: escreve WAKE + Enter para ligar");
#endif
#if DEBUG_DISABLE_SLEEP

  Serial.println("[DEBUG] DEBUG_DISABLE_SLEEP=1 -> a arrancar sempre, sem esperar por botao/WAKE");
  debugForcedWake = true;
#endif

#if !DEBUG_DISABLE_SLEEP

  const uint32_t waitPressStart = millis();
  while (digitalRead(BTN_PIN) == HIGH)
  {
#if DEBUG_SERIAL_WAKE
    if (serialCommandReceived("WAKE"))
    {
      Serial.println("[DEBUG] comando WAKE recebido -> a ligar sem botao fisico");
      debugForcedWake = true;
      break;
    }
#endif
    if ((millis() - waitPressStart) > 8000)
    {
      Serial.println("Aguardar long press para ligar -> dormir");
      goToSleep();
      return;
    }
    delay(5);
  }
#endif

  Serial.println("Botao pressionado ao acordar...");
  if (!debugForcedWake && !waitForLongPress())
  {
    Serial.println("Botão pressionado ao acordar...");

    Serial.println("Pressão curta -> voltar a dormir");
    goToSleep();
    return;
  }

  isRunning = true;
  Serial.println("[BOOT] step: showReady");
  showReady();
  Serial.println("[BOOT] step: initStorage");
  initStorage();
  Serial.println("[BOOT] step: initBle");
  initBle();
  Serial.println("[BOOT] step: initQspiRingBuffer");
  initQspiRingBuffer();
  Serial.println("[BOOT] step: initImu");
  initImu();
  Serial.println("[BOOT] step: initStorageTask");
  initStorageTask();
  Serial.println("[BOOT] step: initPpg");
  initPpg();
  Serial.println("[BOOT] step: initBleDataLink");
  initBleDataLink();
  Serial.println("[BOOT] step: initBattery");
  initBattery();
  Serial.println("[BOOT] step: initLora");
  initLora();
  Serial.println("[BOOT] step: initNfc");
  initNfc();
  Serial.println("[BOOT] step: initEmergency");
  Emergency::begin(BTN_PIN);
  Serial.println("[BOOT] step: showHourDateScreen");
  showHourDateScreen();
  Serial.println("[BOOT] step: setup done");
}

void loop()
{
  static uint32_t lastUiMs = 0;

  if (isRunning)
  {
#if DEBUG_SERIAL_WAKE

    const char *serialLine = pollSerialLine();
    if (serialLine != nullptr)
    {
      if (strcmp(serialLine, "SLEEP") == 0)
      {
        Serial.println("[DEBUG] comando SLEEP recebido -> a desligar sem botao fisico");
        goToSleep();
        return;
      }
      if (strcmp(serialLine, "SOS") == 0)
      {
        Serial.println("[DEBUG] comando SOS recebido -> a disparar alerta de teste");
        Emergency::triggerTestAlert();
      }

      if (strcmp(serialLine, "CLEARKEY") == 0)
      {
        bool ok = Storage::removeAesKey();
        Serial.println(ok
                           ? "[DEBUG] comando CLEARKEY recebido -> chave AES apagada; reprovisiona via aesKeyChar"
                           : "[DEBUG] comando CLEARKEY recebido -> falha ao apagar a chave AES");
      }
    }
#endif
    Emergency::update();
    Nfc::update();

    if (buttonPressedStable(/*pollEmergency=*/true))
    {

      Serial.println("Pressao detectada -> verificar 5 segundos...");

      Ppg::suspendForPowerCheck();
      if (waitForLongPress(/*pollEmergency=*/true))
      {
        goToSleep();
      }
      else
      {
        Ppg::resumeAfterPowerCheck();
      }
    }

    digitalWrite(LED_BUILTIN, LOW);
    delayPollingEmergency(50);
    digitalWrite(LED_BUILTIN, HIGH);
    delayPollingEmergency(950);

    const uint32_t nowMs = millis();
    if ((nowMs - lastUiMs) >= 1000)
    {
      lastUiMs = nowMs;
      showHourDateScreen();
    }

    static uint32_t lastBatteryMs = 0;
    constexpr uint32_t kBatterySampleIntervalMs = 60000;
    if ((nowMs - lastBatteryMs) >= kBatterySampleIntervalMs)
    {
      lastBatteryMs = nowMs;
      Battery::Reading batt{};
      if (Battery::sample(batt))
      {
        Ble::updateBatteryLevel(batt.percent);
        Serial.print("[BATTERY] mv=");
        Serial.print(batt.voltage_mv);
        Serial.print(" raw_adc=");
        Serial.print(batt.raw_adc);
        Serial.print(" percent=");
        Serial.println(batt.percent);
      }
    }

#if DEBUG_STACK_WATERMARKS

    static uint32_t lastStackLogMs = 0;
    if ((nowMs - lastStackLogMs) >= 15000)
    {
      lastStackLogMs = nowMs;
      Serial.print("[STACK] storage_task free_words=");
      Serial.print(g_storageTaskHandle != nullptr
                       ? uxTaskGetStackHighWaterMark(g_storageTaskHandle)
                       : 0);
      Serial.print(" (of ");
      Serial.print(STORAGE_TASK_STACK_WORDS);
      Serial.println(")");

      Serial.print("[STACK] imu_task     free_words=");
      Serial.println(Imu::taskStackHighWaterMarkWords());

      Serial.print("[STACK] ppg_task     free_words=");
      Serial.println(Ppg::taskStackHighWaterMarkWords());

      Serial.print("[STACK] ble_dump_task free_words=");
      Serial.println(Ble::dumpTaskStackHighWaterMarkWords());
    }
#endif

    Serial.println("Sistema a correr...");
  }
}
