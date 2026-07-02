/*
  MAX30105 Breakout: Output all the raw Red/IR/Green readings
  By: Nathan Seidle @ SparkFun Electronics
  Date: October 2nd, 2016
  https://github.com/sparkfun/MAX30105_Breakout

  Outputs all Red/IR/Green values.

  Hardware Connections (Breakoutboard to Arduino):
  -5V = 5V (3.3V is allowed)
  -GND = GND
  -SDA = A4 (or SDA)
  -SCL = A5 (or SCL)
  -INT = Not connected

  The MAX30105 Breakout can handle 5V or 3.3V I2C logic. We recommend powering the board with 5V
  but it will also run at 3.3V.

  This code is released under the [MIT License](http://opensource.org/licenses/MIT).
*/

#include <Wire.h>
#include "MAX30105.h"

MAX30105 particleSensor;

// === LOW PASS FILTER 1º Order (Fc ~ 5 Hz, Fs = 100 Hz) ===
float lowPassFilter(float x) {
  static float Fs = 100.0;
  static float Ts = 1.0 / Fs;
  static float fc = 5;
  static float Rc = 1.0 /(2.0 * PI * fc);
  const float alpha = Ts / (Rc + Ts);      // ~5 Hz de corte
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

float derivative(float x) {
  static float prev = 0;
  float y = x - prev;
  prev = x;
  return y;
}

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

// === MOVING AVERAGE FOR BPM ===
float smoothBPM(float bpm) {
  const int N = 5;   // 3, 5 ou 7 são bons valores
  static float buf[N];
  static int idx = 0;
  static bool filled = false;
  static float sum = 0;

  sum -= buf[idx];     // tira o antigo
  buf[idx] = bpm;      // coloca o novo
  sum += bpm;

  idx++;
  if (idx >= N) {
    idx = 0;
    filled = true;
  }

  if (!filled)
    return sum / idx;

  return sum / N;
}



void setup()
{
  Serial.begin(115200);
  Serial.println("MAX30105 Basic Readings Example");

  // Initialize sensor
  if (particleSensor.begin() == false)
  {
    Serial.println("MAX30105 was not found. Please check wiring/power. ");
    while (1);
  }

  // Configuração do MAX30105
  //byte ledBrightness = 0x4F,0x5F best,0x6F
  //5 furo da bracelete e esquina em cima do osso do pulso
  byte ledBrightness = 0x5F;
  byte sampleAverage = 8; // 1, 2, 4, 8, 16 ou 32 amostras para média (reduz ruído)
  byte ledMode = 3; 
  int sampleRate = 100; // 50, 100, 200 ou 400 amostras por segundo
  int pulseWidth = 411; // 69, 118, 215 ou 411 us
  int adcRange = 4096; // 2048, 4096, 8192 ou 16384

  particleSensor.setup(
    ledBrightness,
    sampleAverage,
    ledMode,
    sampleRate,
    pulseWidth,
    adcRange
  );

  particleSensor.setPulseAmplitudeRed(0);
  particleSensor.setPulseAmplitudeIR(0);

}

void loop()
{
  long raw = particleSensor.getGreen();
  float low = lowPassFilter(raw);       // sinal filtrado
  float high = highPassFilter(low);        // passa-alto sobre o passa-baixo
  float diff = derivative(high);
  bool beat = detectHeartbeat(diff);

  //Serial.print(raw);
  //Serial.print("\t");
  //Serial.print(low);
  //Serial.print("\t");
  //Serial.print(high);
  //Serial.print("\t");
  //Serial.print(diff);
  //Serial.println("\t");
  //Serial.println(beat ? 1 : 0);
  
  if (beat) {
    float bpm = computeBPM();
    float bpmSmooth = smoothBPM(bpm);
    Serial.print("BPM: ");
    Serial.print(bpm);
    Serial.print("  |  Smoothed: ");
    Serial.println(bpmSmooth);
  }

  
  

}