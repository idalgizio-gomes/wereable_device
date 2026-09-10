// test_lora_isolated.cpp - teste isolado do radio LoRa (Wio-SX1262): SPI em bruto sem RadioLib, para separar "pino errado" de "lib interpreta mal". So compila no env PlatformIO test_lora_isolated (ver platformio.ini), nao faz parte do firmware principal.
// uso: pio run -e test_lora_isolated -t upload, depois monitor serie 115200

#include <Arduino.h>
#include <SPI.h>

// pinout: RF_SW/DIO1/BUSY confianca alta (ver Lora.h); NSS e hipotese, ja falhou no RadioLib
constexpr uint8_t kPinNssCandidate = A3;
constexpr uint8_t kPinBusy = D8;
constexpr uint8_t kPinDio1 = D7;
constexpr uint8_t kPinRfSwitch = A2;

void printPinState(const char *name, uint8_t pin) {
  Serial.print("[TEST] ");
  Serial.print(name);
  Serial.print(" (pino ");
  Serial.print(pin);
  Serial.print(") le: ");
  Serial.println(digitalRead(pin) ? "HIGH" : "LOW");
}

void setup() {
  Serial.begin(115200);
  const uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 5000) {
    delay(10);
  }
  Serial.println();
  Serial.println("=== Teste isolado LoRa - parametro a parametro ===");
  Serial.print("NSS candidato = pino "); Serial.println(kPinNssCandidate);

  // RF switch nao e tocado aqui de proposito: sem confirmar o LoRa, mexer nele corta a antena BLE (ver Lora.cpp)
  pinMode(kPinBusy, INPUT);
  pinMode(kPinDio1, INPUT);
  pinMode(kPinNssCandidate, OUTPUT);
  digitalWrite(kPinNssCandidate, HIGH);  // NSS inativo (idle) em repouso

  Serial.println();
  Serial.println("[TESTE 1] Estado em repouso dos pinos (antes de qualquer SPI):");
  printPinState("BUSY", kPinBusy);
  printPinState("DIO1", kPinDio1);
  Serial.println("  Interpretacao: BUSY normalmente comeca LOW (chip pronto) ou "
                  "HIGH por breves instantes apos ligar (chip a arrancar). Se "
                  "ficar sempre HIGH mesmo apos alguns segundos, e um mau sinal "
                  "(ver TESTE 3).");

  Serial.println();
  Serial.println("[TESTE 2] Leitura SPI em bruto (comando GetStatus, opcode 0xC0):");
  SPI.begin();
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  digitalWrite(kPinNssCandidate, LOW);
  delayMicroseconds(5);
  const uint8_t statusByte1 = SPI.transfer(0xC0);  // opcode GetStatus
  const uint8_t statusByte2 = SPI.transfer(0x00);  // NOP para receber a resposta
  digitalWrite(kPinNssCandidate, HIGH);
  SPI.endTransaction();

  Serial.print("  Byte 1 (eco/lixo tipico durante o envio do opcode): 0x");
  Serial.println(statusByte1, HEX);
  Serial.print("  Byte 2 (deveria ser o registo de status real do SX1262): 0x");
  Serial.println(statusByte2, HEX);
  Serial.println("  Interpretacao: se o byte 2 vier sempre 0x00 OU sempre 0xFF "
                  "em varias execucoes (reiniciar a placa e correr de novo para "
                  "confirmar), muito provavelmente NAO HA nenhum chip a "
                  "responder neste pino NSS - e um sinal forte de que o pino "
                  "esta errado (barramento MISO em floating, sem pull-up/down, "
                  "leitura aleatoria a cada reset). Um valor com padrao "
                  "reconhecivel e ESTAVEL entre execucoes (ex.: sempre o mesmo "
                  "valor especifico tipo 0x2X, ver tabela 13-76 do datasheet do "
                  "SX1262 para o significado dos bits) sugere fortemente que HA "
                  "um chip real a responder neste NSS.");

  Serial.println();
  Serial.println("[TESTE 3] A aguardar BUSY descer (timeout 2s)...");
  const uint32_t waitStart = millis();
  while (digitalRead(kPinBusy) == HIGH && (millis() - waitStart) < 2000) {
    delay(5);
  }
  const bool busyStillHigh = (digitalRead(kPinBusy) == HIGH);
  Serial.print("  BUSY final: ");
  Serial.println(busyStillHigh ? "HIGH (nunca desceu - suspeito, ver interpretacao)"
                                : "LOW (comportamento esperado de um chip pronto)");
  if (busyStillHigh) {
    Serial.println("  Interpretacao: BUSY preso a HIGH pode significar (a) o "
                    "pino D8 nao esta mesmo ligado ao BUSY do modulo (esta a "
                    "ler um pull-up interno do MCU sem nada do outro lado), ou "
                    "(b) o modulo esta mesmo ocupado/preso num estado anomalo.");
  }

  Serial.println();
  Serial.println("=== Fim dos testes isolados ===");
  Serial.println("Proximo passo se os resultados acima nao forem conclusivos: "
                  "testar outro pino candidato a NSS (mudar kPinNssCandidate "
                  "acima) ou testar a versao 4.6.0 do RadioLib (ver "
                  "PROJECT_STATUS.md, pesquisa 2026-07-03) - so depois disso "
                  "voltar ao codigo completo (main.cpp / Lora.cpp).");
}

void loop() {
  delay(1000);
}
