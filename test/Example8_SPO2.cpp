#include <Wire.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

MAX30105 particleSensor;

// ------------------- INTERRUPÇÃO -------------------
#define MAX30105_INT_PIN D15

volatile bool resetSensorRequested = false;

void sensorInterruptISR() {
  resetSensorRequested = true;
}

// ------------------- BUFFERS -------------------
#define MAX_BRIGHTNESS 255

#if defined(__AVR_ATmega328P__) || defined(__AVR_ATmega168__)
uint16_t irBuffer[100];
uint16_t redBuffer[100];
#else
uint32_t irBuffer[100];
uint32_t redBuffer[100];
#endif

int32_t bufferLength;
int32_t spo2;
int8_t validSPO2;
int32_t heartRate;
int8_t validHeartRate;

byte pulseLED = 11;
byte readLED = 13;

// ------------------- RESET SENSOR -------------------
void resetMAX30105()
{
  Serial.println(F("\n[INT] Reset ao MAX30105..."));

  particleSensor.shutDown();
  delay(100);

  particleSensor.wakeUp();
  delay(100);

  byte ledBrightness = 60;
  byte sampleAverage = 4;
  byte ledMode = 2;
  byte sampleRate = 100;
  int pulseWidth = 411;
  int adcRange = 4096;

  particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
  particleSensor.clearFIFO();

  Serial.println(F("[INT] Sensor reiniciado.\n"));
}

// ------------------- SETUP -------------------
void setup()
{
  Serial.begin(115200);
  while (!Serial) delay(10);

  pinMode(pulseLED, OUTPUT);
  pinMode(readLED, OUTPUT);

  // Configurar interrupção
  pinMode(MAX30105_INT_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(MAX30105_INT_PIN), sensorInterruptISR, FALLING);

  Serial.println(F("Inicializar MAX30105..."));

  if (!particleSensor.begin(Wire, I2C_SPEED_FAST))
  {
    Serial.println(F("MAX30105 nao encontrado. Verifica ligacoes."));
    while (1);
  }

  Serial.println(F("Sensor encontrado. A fazer reset inicial..."));
  resetMAX30105();

  Serial.println(F("Coloca o dedo no sensor e prime qualquer tecla..."));
  while (Serial.available() == 0);
  Serial.read();
}

// ------------------- LOOP -------------------
void loop()
{
  // Reset se interrupção disparar
  if (resetSensorRequested)
  {
    resetSensorRequested = false;
    resetMAX30105();
  }

  bufferLength = 100;

  // Primeira aquisição
  for (byte i = 0; i < bufferLength; i++)
  {
    while (!particleSensor.available())
      particleSensor.check();

    redBuffer[i] = particleSensor.getRed();
    irBuffer[i] = particleSensor.getIR();
    particleSensor.nextSample();

    Serial.print(F("red="));
    Serial.print(redBuffer[i]);
    Serial.print(F(", ir="));
    Serial.println(irBuffer[i]);
  }

  maxim_heart_rate_and_oxygen_saturation(
    irBuffer, bufferLength, redBuffer,
    &spo2, &validSPO2,
    &heartRate, &validHeartRate
  );

  // Loop contínuo
  while (1)
  {
    // Permitir reset mesmo dentro do loop infinito
    if (resetSensorRequested)
    {
      resetSensorRequested = false;
      resetMAX30105();
      break; // sai do while(1)
    }

    // Shift buffers
    for (byte i = 25; i < 100; i++)
    {
      redBuffer[i - 25] = redBuffer[i];
      irBuffer[i - 25] = irBuffer[i];
    }

    // Novas leituras
    for (byte i = 75; i < 100; i++)
    {
      while (!particleSensor.available())
        particleSensor.check();

      digitalWrite(readLED, !digitalRead(readLED));

      redBuffer[i] = particleSensor.getRed();
      irBuffer[i] = particleSensor.getIR();
      particleSensor.nextSample();

      Serial.print(F("red="));
      Serial.print(redBuffer[i]);
      Serial.print(F(", ir="));
      Serial.print(irBuffer[i]);

      Serial.print(F(", HR="));
      Serial.print(heartRate);

      Serial.print(F(", HRvalid="));
      Serial.print(validHeartRate);

      Serial.print(F(", SPO2="));
      Serial.print(spo2);

      Serial.print(F(", SPO2Valid="));
      Serial.println(validSPO2);
    }

    maxim_heart_rate_and_oxygen_saturation(
      irBuffer, bufferLength, redBuffer,
      &spo2, &validSPO2,
      &heartRate, &validHeartRate
    );
  }
}