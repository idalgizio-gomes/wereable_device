#include <Arduino.h>
#include <Adafruit_TinyUSB.h>

#include <bluefruit.h>  // BLE
#include <Adafruit_LittleFS.h>  //flash interna (memória)
#include <InternalFileSystem.h>

using namespace Adafruit_LittleFS_Namespace;

#include <Crypto.h>  // AES-CTR
#include <AES.h>
#include <CTR.h>

// Timers para controlar os envios
unsigned long lastShortMsgTime = 0;
unsigned long lastFullMsgTime = 0;

// Intervalos
const unsigned long SHORT_INTERVAL = 20000;   // 20 segundos
const unsigned long FULL_INTERVAL  = 300000;  // 5 minutos

// ===========================
// BLE SERVICE + CHARACTERISTICS
// ===========================

// Serviço principal do wearable
BLEService wearableService("12345678-1234-5678-1234-56789abcdef0");

// Characteristic que envia AES KEY (32 bytes)
BLECharacteristic aesKeyChar("abcd1234-5678-1234-5678-abcdef123456");
// Characteristic que envia timestamp (4 bytes)
BLECharacteristic timestampChar("abcd1234-5678-1234-5678-abcdef000001");


// =========================
// VARIÁVEIS PARA ARMAZENAR
// =========================
uint8_t storedAesKey[32] = {0};
uint32_t storedTimestamp  = 0;

bool aesKeyLoadedFromFlash = false;
bool aesKeyReceived = false;
bool timestampReceived = false;

// MAC do próprio wearable
uint8_t rawDeviceMac[6] = {0}; 
uint8_t deviceMac[6] = {0};


// ================================
// AES-CTR ENCRYPTION FUNCTION
// ================================
AES256 aes;
CTR<AES256> ctr;

// IV fixo . 
uint8_t iv[16] = {0};

void encryptAESCTR(uint8_t *input, size_t length, uint8_t *output){
    // Limpa estado interno
    ctr.clear();

    // Configura chave AES-256 (32 bytes)
    ctr.setKey(storedAesKey, 32);

    // Usar IV (neste caso, constante — pode ser modificado conforme necessário)
    ctr.setIV(iv, 16);

    // Encripta
    ctr.encrypt(output, input, length);
}


// ===============================
// FUNÇÕES FLASH LITTLEFS
// ===============================
bool loadAESKeyFromFlash() {
  if (!InternalFS.begin()) {
      Serial.println("Falha ao iniciar InternalFS.");
      return false;
  }
  if (!InternalFS.exists("/aes.key")) {
      Serial.println("Nenhuma AES key guardada na Flash.");
      return false;
  }

  File f = InternalFS.open("/aes.key", FILE_O_READ);
  if (!f) {
      Serial.println("Erro ao abrir ficheiro AES.");
      return false;
  }

  f.read(storedAesKey, 32);
  f.close();

  Serial.print("AES Key carregada da Flash: ");
  for (int i = 0; i < 32; i++) Serial.printf("%02X ", storedAesKey[i]);
  Serial.println();

  return true;
}

void saveAESKeyToFlash(uint8_t *key) {
  InternalFS.remove("/aes.key"); // remove antes de escrever

  File f = InternalFS.open("/aes.key", FILE_O_WRITE);
  if (!f) {
    Serial.println("Erro ao criar ficheiro AES.");
    return;
  }
  f.write(key, 32);
  f.close();
  Serial.println("AES KEY gravada permanentemente na Flash!");
}


// ===========================
// CALLBACK: RECEÇÃO DA AES KEY
// ===========================
void aesKeyCallback(uint16_t conn_hdl, BLECharacteristic* chr, uint8_t* data, uint16_t len){
  if (aesKeyLoadedFromFlash) {
        Serial.println("⚠ AES já existe na Flash — ignorando envio BLE.");
        return;
  }

  Serial.println("\n=== AES KEY RECEBIDA VIA BLE ===");

  if (len != 32) {
    Serial.printf("Erro: esperado 32 bytes, recebido %u bytes.\n", len);
    return;
  }

  // Extrair AES KEY (32 bytes)
  memcpy(storedAesKey, data , 32);

  Serial.print("AES Key armazenada: ");
  for (int i = 0; i < 32; i++) Serial.printf("%02X ", storedAesKey[i]);
  Serial.println();

  saveAESKeyToFlash(storedAesKey);

  aesKeyReceived = true;
}


// ===========================
// CALLBACK: RECEÇÃO DO TIMESTAMP
// ===========================
void timestampCallback(uint16_t conn_hdl, BLECharacteristic* chr, uint8_t* data, uint16_t len){
  Serial.println("\n=== TIMESTAMP RECEBIDO ===");

  if (len != 4) {
    Serial.printf("Erro: esperado 4 bytes, recebido %u bytes.\n", len);
    return;
  }

  // Little Endian → reconstrói UINT32
  storedTimestamp = 
        (uint32_t)data[0]
      | ((uint32_t)data[1] << 8)
      | ((uint32_t)data[2] << 16)
      | ((uint32_t)data[3] << 24);

  Serial.printf("Timestamp armazenado: %lu\n", storedTimestamp);

  timestampReceived = true;
}


// ===========================
// SETUP
// ===========================
void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  Serial.println("=== Wearable BLE – A Guardar AES Key e Timestamp ===");

  // Inicializa FLASH
  InternalFS.begin();

  // Tenta carregar AES KEY guardada anteriormente
  aesKeyLoadedFromFlash = loadAESKeyFromFlash();

  // Inicialização BLE
  Bluefruit.begin();
  Bluefruit.setName("Wearable");

  // -> BUSCAR MAC DO DISPOSITIVO
  Bluefruit.getAddr(rawDeviceMac);  // formato little-endian

  for (int i = 0; i < 6; i++) {
    deviceMac[i] = rawDeviceMac[5 - i]; // formato big-endian
  }

  Serial.print("MAC do wearable: ");
  for (int i = 0; i < 6; i++) {
    Serial.printf("%02X ", deviceMac[i]); // MAC address no formato certo
  }
  Serial.println();
  // -------------------------------

  // Criar serviço
  wearableService.begin();

  // -------- AES KEY CHARACTERISTIC --------
  aesKeyChar.setProperties(CHR_PROPS_WRITE);
  aesKeyChar.setPermission(SECMODE_OPEN, SECMODE_OPEN);
  aesKeyChar.setMaxLen(32);          // 32 (AES KEY)
  aesKeyChar.setWriteCallback(aesKeyCallback);
  aesKeyChar.begin();

  // -------- TIMESTAMP CHARACTERISTIC --------
  timestampChar.setProperties(CHR_PROPS_WRITE);
  timestampChar.setPermission(SECMODE_OPEN, SECMODE_OPEN);
  timestampChar.setMaxLen(4);        // UINT32
  timestampChar.setWriteCallback(timestampCallback);
  timestampChar.begin();

  // Advertising para permitir ligação do beacon
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addService(wearableService);
  Bluefruit.Advertising.start(0);

  
  if (aesKeyLoadedFromFlash){
    Serial.println("BLE ativo. AES KEY encontrada, à espera do Timestamp...");
  }else{
    Serial.println("BLE ativo. À espera de AES KEY e Timestamp...");
  }
}

void sendBroadcast(uint8_t *payload, size_t length){

  Serial.println("\n=== BROADCAST: Enviar payload cifrada ===");
    
  Serial.print("Payload cifrada: ");
  for (int i = 0; i < length; i++) {
      Serial.printf("%02X ", payload[i]);
  }
  Serial.println();

  // Parar Advertising atual
  Bluefruit.Advertising.stop();
  Bluefruit.Advertising.clearData();
  Bluefruit.ScanResponse.clearData();


  // Criar Manufacturer Data (2 bytes ID + payload)
  uint8_t manufacturerData[2 + length];

  // ID do fabricante — podes alterar depois
  manufacturerData[0] = 0x34;
  manufacturerData[1] = 0x12;

  // Copiar payload cifrada
  memcpy(&manufacturerData[2], payload, length);

  // Adicionar dados ao Advertising
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addManufacturerData(manufacturerData, sizeof(manufacturerData));

  // Intervalos de Advertising (100 ms)
  Bluefruit.Advertising.setInterval(160, 160);

  // Iniciar Advertising indefinidamente
  Bluefruit.Advertising.start(0);

  Serial.println(" Broadcast ativo! Payload cifrada está a ser transmitida...");
}


void sendShortMessage() {
  // Verifica se chave AES carregada
  if (!(aesKeyLoadedFromFlash || aesKeyReceived)) {
      Serial.println("⚠ ShortMessage: AES KEY NÃO ENCONTRADA — aguardar chave...");
      return;   
  }

  Serial.println("\n=== SHORT MESSAGE: Preparar envio ===");

  uint8_t payload[10];
  memcpy(payload, deviceMac, 6);
  memcpy(payload + 6, &storedTimestamp, 4);

  Serial.print("Payload: ");
  for (int i = 0; i < 10; i++) {
      Serial.printf("%02X ", payload[i]);  // Imprime em hexadecimal com 2 dígitos
  }
  Serial.println();

  uint8_t encryptedPayload[10];
  encryptAESCTR(payload, 10, encryptedPayload);

  sendBroadcast(encryptedPayload, 10);
  Serial.print("Payload Encriptado: ");
  for (int i = 0; i < 10; i++) {
      Serial.printf("%02X ", encryptedPayload[i]);  // Imprime cada byte em hexadecimal
  }
  Serial.println();
  Serial.println("Short message broadcast enviado.");
}


void sendFullMessage() {
  // Verifica se chave AES carregada
  if (!(aesKeyLoadedFromFlash || aesKeyReceived)) {
      Serial.println("⚠ ShortMessage: AES KEY NÃO ENCONTRADA — aguardar chave...");
      return;   
  }

  Serial.println("\n=== FULL MESSAGE: Preparar envio ===");

  uint8_t mask = 0xFF;
  uint8_t heartRate = 65;
  uint8_t oximetry = 98;
  uint8_t battery = 90;
  uint16_t steps = 1024;
  uint16_t distance = 5000;
  uint8_t payload[18];
  memcpy(payload, deviceMac, 6);
  memcpy(payload + 6, &storedTimestamp, 4);
  payload[10] = mask; //mascara de eventos (SOS, Queda, Remoção Pulseira)
  payload[11] = heartRate;
  payload[12] = oximetry;
  payload[13] = battery;
  memcpy(payload + 14, &steps, 2);
  memcpy(payload + 16, &distance, 2);

  uint8_t encryptedPayload[18];
    encryptAESCTR(payload, 18, encryptedPayload);

  sendBroadcast(encryptedPayload, 18);
    Serial.println("Full message broadcast enviado.");
}


// ===========================
// LOOP PRINCIPAL
// ===========================
void loop() {
  static bool ready = false;

  bool aesReady = aesKeyLoadedFromFlash || aesKeyReceived;

  if (aesReady && timestampReceived) {
    Serial.println("\n>>> SISTEMA PRONTO PARA BROADCAST CIFRADO <<<");
    Serial.println("Agora podes mudar para o modo beacon e transmitir dados encriptados.");

    ready = true;
    
    lastShortMsgTime = millis();
    lastFullMsgTime = millis();
    
    aesKeyReceived = false;
    timestampReceived = false;
  }

  // Se ainda não temos tudo, não faz nada
  if (!ready) return;

  unsigned long now = millis();

  // Short Message — a cada 20 segundos
  if (now - lastShortMsgTime >= SHORT_INTERVAL) {
    Serial.println("\n[SHORT MESSAGE] 20 segundos passaram → Enviar");
    sendShortMessage();
    lastShortMsgTime = now;
  }

  // Full Message — a cada 5 minutos
  if (now - lastFullMsgTime >= FULL_INTERVAL) {
    Serial.println("\n[FULL MESSAGE] 5 minutos passaram → Enviar");
    sendFullMessage();
    lastFullMsgTime = now;
  }

  delay(1000);
}