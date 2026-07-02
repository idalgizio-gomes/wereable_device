#include <Wire.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

MAX30105 particleSensor;

// Configuração do sensor
const byte ledBrightness = 60;  // 0=Off, 255=50mA
const byte sampleAverage = 4;   // 1, 2, 4, 8, 16, 32
const byte ledMode = 2;         // 2 = Red + IR (necessário para SpO2)
const byte sampleRate = 100;    // 50, 100, 200, 400, 800, 1000, 1600, 3200
const int pulseWidth = 411;     // 69, 118, 215, 411
const int adcRange = 4096;      // 2048, 4096, 8192, 16384

// Buffers para amostras (100 amostras = 4 segundos a 25 sps com avg=4)
const int32_t BUFFER_LENGTH = 100;
uint32_t irBuffer[BUFFER_LENGTH];
uint32_t redBuffer[BUFFER_LENGTH];

// Resultados do algoritmo
int32_t spo2Value;
int8_t  validSPO2;
int32_t heartRateValue;
int8_t  validHeartRate;

// Limiar para deteção de dedo
const uint32_t FINGER_THRESHOLD = 50000;

void setup() {
  Serial.begin(115200);

  // Esperar serial conectar (importante para nRF52)
  delay(2000);

  Serial.println(F("\n=== Monitor SpO2 - MAX30105 ==="));

  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println(F("MAX30105 nao encontrado! Verifique ligacoes."));
    while (1);
  }

  particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);

  Serial.println(F("Sensor configurado. Coloque o dedo no sensor..."));
}

void loop() {
  // ---- Verificar se o dedo está presente ----
  // Ler uma amostra rápida para verificar presença do dedo
  particleSensor.check();
  uint32_t irCheck = particleSensor.getIR();

  if (irCheck < FINGER_THRESHOLD) {
    Serial.println(F("Aguardando dedo..."));
    delay(1000);
    return;
  }

  Serial.println(F("\nDedo detetado! A recolher 100 amostras..."));

  // ---- Recolher as primeiras 100 amostras usando FIFO corretamente ----
  for (int i = 0; i < BUFFER_LENGTH; i++) {
    // Esperar até haver dados novos no FIFO
    while (particleSensor.available() == false)
      particleSensor.check();

    redBuffer[i] = particleSensor.getRed();
    irBuffer[i]  = particleSensor.getIR();
    particleSensor.nextSample();  // Avançar para a próxima amostra no FIFO

    // Verificar se o dedo saiu durante a recolha
    if (irBuffer[i] < FINGER_THRESHOLD) {
      Serial.println(F("Dedo removido durante recolha. A reiniciar..."));
      return;
    }
  }

  // ---- Calcular SpO2 e Heart Rate com o algoritmo da biblioteca ----
  maxim_heart_rate_and_oxygen_saturation(
    irBuffer, BUFFER_LENGTH, redBuffer,
    &spo2Value, &validSPO2,
    &heartRateValue, &validHeartRate
  );

  // Mostrar resultado inicial
  Serial.println(F("================================"));
  if (validSPO2) {
    Serial.print(F("SpO2: ")); Serial.print(spo2Value); Serial.println(F("%"));
  } else {
    Serial.println(F("SpO2: --- (sinal insuficiente)"));
  }
  Serial.println(F("================================"));

  // ---- Modo contínuo: atualizar a cada 25 amostras ----
  Serial.println(F("\nModo continuo (mantenha o dedo no sensor):\n"));

  while (1) {
    // Descartar as 25 amostras mais antigas e mover as 75 restantes para o início
    for (int i = 25; i < BUFFER_LENGTH; i++) {
      redBuffer[i - 25] = redBuffer[i];
      irBuffer[i - 25]  = irBuffer[i];
    }

    // Recolher 25 novas amostras (posições 75-99)
    for (int i = 75; i < BUFFER_LENGTH; i++) {
      while (particleSensor.available() == false)
        particleSensor.check();

      redBuffer[i] = particleSensor.getRed();
      irBuffer[i]  = particleSensor.getIR();
      particleSensor.nextSample();

      // Se o dedo saiu, voltar ao loop principal
      if (irBuffer[i] < FINGER_THRESHOLD) {
        Serial.println(F("\nDedo removido. A aguardar novo dedo...\n"));
        return;
      }
    }

    // Recalcular SpO2 e Heart Rate
    maxim_heart_rate_and_oxygen_saturation(
      irBuffer, BUFFER_LENGTH, redBuffer,
      &spo2Value, &validSPO2,
      &heartRateValue, &validHeartRate
    );

    // Mostrar resultados
    Serial.print(F("SpO2: "));
    if (validSPO2) {
      Serial.print(spo2Value);
      Serial.println(F("%"));
    } else {
      Serial.println(F("---"));
    }
  }
}