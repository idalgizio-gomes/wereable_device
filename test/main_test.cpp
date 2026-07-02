// ============================================================
// main_test.cpp — Variante com SoftDevice S140 ativo
//
// Diferenças face ao main.cpp:
//   1. Inclui <bluefruit.h>
//   2. Inicializa o SoftDevice via Bluefruit.begin() no setup
//   3. Em goToSleep, chama sd_power_system_off() (API do SD)
//      mantém NRF_POWER->SYSTEMOFF = 1 como fallback
//
// IMPORTANTE: Para compilar este ficheiro em vez do main.cpp,
// renomeia main.cpp para main.cpp.bak antes do build, OU
// adiciona ao platformio.ini:
//     build_src_filter = +<main_test.cpp> -<main.cpp>
// ============================================================

#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <nrf_power.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1351.h>

// ============================================================
// PINOS
// ============================================================
#define BTN_PIN 0
#define LONG_PRESS_TIME 5000
#define DEBOUNCE_TIME 50

#define OLED_CS_PIN    D9
#define OLED_DC_PIN    D10
#define OLED_RST_PIN   D11

#define DISP_SCK_PIN   D17
#define DISP_MOSI_PIN  D19
#define DISP_MISO_PIN  D18

// ============================================================
// DISPLAY
// ============================================================
#define SCREEN_W 128
#define SCREEN_H 128
#define COLOR_BLACK 0x0000
#define COLOR_WHITE 0xFFFF

SPIClass dispSPI(NRF_SPIM2, DISP_MISO_PIN, DISP_SCK_PIN, DISP_MOSI_PIN);
Adafruit_SSD1351 display(SCREEN_W, SCREEN_H, &dispSPI,
                         OLED_CS_PIN, OLED_DC_PIN, OLED_RST_PIN);

bool isRunning = false;

// ============================================================
// BOTÃO
// ============================================================
void waitRelease() {
  while (digitalRead(BTN_PIN) == LOW) {
    delay(5);
  }
  delay(30);
}

bool buttonPressedStable() {
  if (digitalRead(BTN_PIN) == LOW) {
    delay(DEBOUNCE_TIME);
    return digitalRead(BTN_PIN) == LOW;
  }
  return false;
}

bool waitForLongPress() {
  if (!buttonPressedStable()) return false;
  unsigned long start = millis();
  while (millis() - start < LONG_PRESS_TIME) {
    if (digitalRead(BTN_PIN) == HIGH) return false;
  }
  return true;
}

// ============================================================
// SYSTEM OFF — agora via SoftDevice
// ============================================================
void goToSleep() {
  Serial.println("A desligar...");
  Serial.flush();

  // Apaga LED e display
  digitalWrite(LED_BUILTIN, HIGH);   // OFF (ativo LOW)
  pinMode(OLED_RST_PIN, OUTPUT);
  digitalWrite(OLED_RST_PIN, LOW);

  // 1 — Desligar USB para que o VBUS deixe de gerar wake imediato
  TinyUSBDevice.detach();      // remove pull-up de D+, host vê desconexão
  delay(20);
  NRF_USBD->ENABLE = 0;        // desliga periférico USBD

  // 2 — Desabilitar wakes por eventos de USB-power no SoftDevice
  sd_power_usbpwrrdy_enable(false);
  sd_power_usbdetected_enable(false);
  sd_power_usbremoved_enable(false);

  // 3 — Limpar LATCH residual nos GPIOs (evita wake fantasma)
  NRF_GPIO->LATCH = NRF_GPIO->LATCH;
  NRF_P1->LATCH   = NRF_P1->LATCH;

  // 4 — Garantir botão libertado antes de armar SENSE
  waitRelease();
  delay(50);

  // 5 — Configurar wake-up por LOW
  nrf_gpio_cfg_input(BTN_PIN, NRF_GPIO_PIN_PULLUP);
  nrf_gpio_cfg_sense_input(BTN_PIN,
                           NRF_GPIO_PIN_PULLUP,
                           NRF_GPIO_PIN_SENSE_LOW);
  delay(5);

  // 6 — Pedir ao SoftDevice para entrar em SYSTEM_OFF
  sd_power_system_off();

  // Fallback caso o SD não esteja ativo
  NRF_POWER->SYSTEMOFF = 1;

  while (1) { /* não retorna */ }
}

// ============================================================
// DISPLAY
// ============================================================
void showReady() {
  dispSPI.begin();
  display.begin();
  display.fillScreen(COLOR_BLACK);
  display.setTextColor(COLOR_WHITE);
  display.setTextSize(2);
  display.setCursor(8, 50);
  display.print("Dispositivo");
  display.setCursor(8, 72);
  display.print("ligado");
}

// ============================================================
// SETUP
// ============================================================
void setup() {
  pinMode(BTN_PIN, INPUT_PULLUP);

  // LED onboard como heartbeat (no XIAO nRF52840 é ativo LOW)
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);   // apagado por defeito

  Serial.begin(115200);
  delay(100);

  Serial.println("Acordou do System OFF (variante SD)");

  // Inicializa o SoftDevice S140.
  // Mesmo sem usar BLE ativamente, isto põe o SD num estado conhecido
  // e habilita as APIs sd_*.
  Bluefruit.begin();
  Bluefruit.setName("WearableTest");
  Serial.println("SoftDevice inicializado");

  // Anti-glitch
  if (digitalRead(BTN_PIN) == HIGH) {
    Serial.println("Wake glitch -> voltar a dormir");
    goToSleep();
  }

  // Long-press para confirmar wake
  if (buttonPressedStable()) {
    Serial.println("Botão pressionado ao acordar...");

    if (waitForLongPress()) {
      Serial.println("Ligado após long press!");
      isRunning = true;
      showReady();
      return;
    }

    Serial.println("Pressão curta -> voltar a dormir");
    goToSleep();
  }

  // Caminho fallback
  isRunning = true;
  showReady();
}

// ============================================================
// LOOP — pisca LED como heartbeat para confirmar que está ativo
// ============================================================
void loop() {
  if (isRunning) {
    if (buttonPressedStable()) {
      Serial.println("Pressão detectada -> verificar 5 segundos...");
      if (waitForLongPress()) {
        goToSleep();
      }
    }

    // Heartbeat: pulso curto de LED a cada segundo
    digitalWrite(LED_BUILTIN, LOW);   // ON  (ativo LOW)
    delay(50);
    digitalWrite(LED_BUILTIN, HIGH);  // OFF
    delay(950);

    Serial.println("Sistema a correr...");
  }
}
