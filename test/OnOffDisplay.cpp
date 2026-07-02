#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <nrf_power.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1351.h>
#include <SPI.h>

// Pinos
#define BTN_PIN        0       // Botão para ligar/desligar
#define OLED_RST_PIN   8       // <-- ALTERA AQUI SE USARES OUTRO PINO

// Tempos
#define LONG_PRESS_TIME 5000   // 5 segundos
#define DEBOUNCE_TIME    50    // 50ms

bool isRunning = false;

// ----------------------------------------------
// Função: Debounce do botão
// ----------------------------------------------
bool buttonPressedStable() {
  if (digitalRead(BTN_PIN) == LOW) {
    delay(DEBOUNCE_TIME);
    return digitalRead(BTN_PIN) == LOW;
  }
  return false;
} 

// ----------------------------------------------
// Função: Espera pressão contínua de 5 segundos
// ----------------------------------------------
bool waitForLongPress() {
  if (!buttonPressedStable()) return false;

  unsigned long start = millis();
  while (millis() - start < LONG_PRESS_TIME) {
    if (digitalRead(BTN_PIN) == HIGH) return false;
  }
  return true;
}

// ----------------------------------------------
// Espera libertação do botão
// ----------------------------------------------
void waitRelease() {
  while (digitalRead(BTN_PIN) == LOW) delay(5);
  delay(30);
}

// ----------------------------------------------
// MANTER DISPLAY DESLIGADO (RESET LOW)
// ----------------------------------------------
void display_hold_reset() {
  pinMode(OLED_RST_PIN, OUTPUT);
  digitalWrite(OLED_RST_PIN, LOW);    // Display apagado
}

// ----------------------------------------------
// LIGAR DISPLAY (pulso de reset + init)
// ----------------------------------------------
void display_startup() {
  pinMode(OLED_RST_PIN, OUTPUT);

  digitalWrite(OLED_RST_PIN, LOW);
  delay(20);
  digitalWrite(OLED_RST_PIN, HIGH);
  delay(20);

  // Exemplo de inicialização:
  // display.begin();  // Adafruit_SSD1351, etc.
  // display.fillScreen(BLACK);
}

// ----------------------------------------------
// FUNÇÃO PARA DESLIGAR O SISTEMA
// ----------------------------------------------
void goToSleep() {
  Serial.println("A desligar...");

  // Apaga display
  display_hold_reset();

  // Espera o botão ser libertado
  waitRelease();

  delay(50);

  // Configurar wake-up por LOW
  nrf_gpio_cfg_input(BTN_PIN, NRF_GPIO_PIN_PULLUP);
  nrf_gpio_cfg_sense_input(BTN_PIN,
                           NRF_GPIO_PIN_PULLUP,
                           NRF_GPIO_PIN_SENSE_LOW);

  Serial.flush();
  delay(5);

  // Entrar em SYSTEM OFF (1µA)
  NRF_POWER->SYSTEMOFF = 1;

  // Nunca volta daqui
}

// ----------------------------------------------
// SETUP
// ----------------------------------------------
void setup() {
  pinMode(BTN_PIN, INPUT_PULLUP);
  pinMode(OLED_RST_PIN, OUTPUT);

  Serial.begin(115200);
  delay(150);

  Serial.println("MCU acordou!");

  // Proteção anti-glitch:
  if (digitalRead(BTN_PIN) == HIGH) {
    Serial.println("Wake glitch → voltar a dormir");
    goToSleep();
  }

  // Se acordou com botão pressionado:
  if (buttonPressedStable()) {
    Serial.println("Botão pressionado ao acordar...");

    if (waitForLongPress()) {
      Serial.println("Ligado com long press!");
      isRunning = true;

      display_startup();
      return;
    }

    Serial.println("Pressão curta → desligar");
    goToSleep();
  }

  // Se chegou aqui → arranque normal
  isRunning = true;

  display_startup();
}

// ----------------------------------------------
// LOOP PRINCIPAL
// ----------------------------------------------
void loop() {
  if (isRunning) {

    // Detetar long press para desligar
    if (buttonPressedStable()) {
      Serial.println("Pressão detetada → verificar...");
      if (waitForLongPress()) {
        goToSleep();
      }
    }

    Serial.println("Sistema a correr...");
    delay(1000);
  }
}