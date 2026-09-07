// ============================================================
// Ble.cpp - Implementacao do modulo BLE (ver Ble.h para a visao geral)
// ============================================================
// Este ficheiro:
//   1) Declara os servicos/characteristics GATT (UUIDs) usados para
//      falar com a app do telemovel.
//   2) Implementa a logica de troca da chave AES e de sincronizacao de
//      hora/data (Current Time) durante o "provisioning" inicial.
//   3) Implementa os callbacks BLE (escrita/ligacao/desligacao) e a API
//      publica do modulo (Ble::begin(), Ble::startBroadcast(), etc.).
// O "modo de dados" propriamente dito (cifra AES-CTR, nonces
// persistentes, e a tarefa FreeRTOS de streaming) foi extraido para
// BleGattDump.cpp (2026-09-07, modularizacao) — ver BleInternal.h para
// o estado/objetos GATT partilhados entre os dois ficheiros.
#include "Ble/Ble.h"
#include "BleInternal.h"

#include "Display/Ui.h"
#include "Storage/Storage.h"
#include "QspiRingBuffer/QspiRingBuffer.h"
#include "Clock/Clock.h"
#include "Ppg/Ppg.h"

#include <bluefruit.h>
#include <rtos.h>

// UUIDs dos servicos e characteristics GATT expostos pelo wearable.
// - wearableService: servico "guarda-chuva" custom do dispositivo, usado
//   tanto no provisioning (chave AES) como no modo de dados (dump).
// - aesKeyChar: characteristic de escrita onde a app envia a chave AES
//   partilhada, usada para cifrar/decifrar dados sensiveis.
// - currentTimeService/currentTimeChar: servico e characteristic PADRAO
//   do Bluetooth SIG ("Current Time", UUID16 0x2A2B) usados para a app
//   enviar a data/hora atual ao dispositivo.
// - dumpCtrlChar: characteristic de escrita para a app pedir
//   inicio/paragem da transmissao de dados dos sensores.
// - dumpDataChar: characteristic de notificacao pela qual os pacotes de
//   dados dos sensores (fragmentados) sao enviados ao telemovel.
// - dumpStatusChar: characteristic de notificacao/leitura com o estado
//   atual da transmissao (streaming/idle, contagens, motivo do estado).
// - liveSnapshotChar (2026-08-06): notificacao periodica (~1/seg) do
//   registo mais recente do ring buffer, independente do atraso do dump
//   historico em dumpDataChar — ver comentario junto da sua declaracao.
// Nao sao mais "static": ficam com ligacao externa (ver BleInternal.h)
// porque BleGattDump.cpp (gattDumpTask, sendLiveSnapshot,
// publishDumpStatus) tambem lhes acede diretamente.
BLEService        wearableService("12345678-1234-5678-1234-56789abcdef0");
BLECharacteristic aesKeyChar     ("abcd1234-5678-1234-5678-abcdef123456");
BLEService        currentTimeService(UUID16_SVC_CURRENT_TIME);
BLECharacteristic currentTimeChar(UUID16_CHR_CURRENT_TIME);
BLECharacteristic dumpCtrlChar   ("abcd1234-5678-1234-5678-abcdef200001");
BLECharacteristic dumpDataChar   ("abcd1234-5678-1234-5678-abcdef200002");
BLECharacteristic dumpStatusChar ("abcd1234-5678-1234-5678-abcdef200003");
// emergencyAlertChar: notificacao dedicada a alertas de emergencia (SOS
// manual ou queda+inatividade), separada de dumpStatusChar para nao
// misturar semanticas (estado do streaming vs. um evento critico raro) e
// para nao ter de alterar o formato ja fixo do DumpStatusPacket existente.
BLECharacteristic emergencyAlertChar("abcd1234-5678-1234-5678-abcdef200004");
// emergencyProfileWriteChar/emergencyProfileChar: par de characteristics
// para o perfil de emergencia (JSON) do paciente — o bridge escreve o
// payload (dados que ja tem via ORM) em emergencyProfileWriteChar, o
// dispositivo guarda-o em flash (ver Storage::saveEmergencyProfile) e
// espelha-o em emergencyProfileChar, que fica disponivel por leitura
// (ex.: por uma app no telemovel, sem depender do bridge) mesmo antes de
// qualquer escrita nova, porque begin() pre-carrega o que ja estiver
// persistido. Par de characteristics em vez de uma so' (leitura+escrita
// na mesma) porque write() e' invocado pelo dispositivo para espelhar o
// valor (ver emergencyProfileWriteCallback), e nao faz sentido a app
// poder escrever diretamente no valor "servido" sem passar por Storage.
BLECharacteristic emergencyProfileWriteChar("abcd1234-5678-1234-5678-abcdef200005");
BLECharacteristic emergencyProfileChar     ("abcd1234-5678-1234-5678-abcdef200006");
// liveSnapshotChar (2026-08-06): notificacao dedicada a um "instantaneo
// ao vivo" do registo mais recente do ring buffer (QspiRingBuffer::
// peekLatest()), independente do atraso do dump historico em
// dumpDataChar. Resolve o caso em que o dispositivo passou tempo a
// gravar sem BLE ligado: dumpDataChar continua a entregar tudo em ordem
// cronologica a partir do mais antigo (nunca perde dados), mas isso
// significa que pode passar minutos so' a mostrar dados antigos ate'
// apanhar o atraso — liveSnapshotChar existe so' para dar ao
// bridge/dashboard o "agora" imediatamente, em paralelo, sem interferir
// com o esvaziamento sequencial do historico. Mesmo formato de pacote
// (DumpDataPacket, fragmentado/cifrado da mesma forma) para reaproveitar
// o codigo de descodificacao ja existente no bridge (decode_full_plain).
BLECharacteristic liveSnapshotChar         ("abcd1234-5678-1234-5678-abcdef200007");
// batteryService: servico PADRAO do Bluetooth SIG "Battery Service"
// (UUID16 0x180F, characteristic "Battery Level" 0x2A19), atraves da
// classe BLEBas ja fornecida pela biblioteca Bluefruit (ver
// services/BLEBas.h) — mesmo raciocinio ja usado para currentTimeService/
// currentTimeChar acima (UUID padrao reconhecido por qualquer app/
// ferramenta BLE genérica), mas aqui a propria biblioteca ja oferece uma
// classe pronta para o servico, por isso nao se reconstroi a
// characteristic a mao (ver Ble::updateBatteryLevel() no fim deste
// ficheiro, e Battery.h para a origem do valor de percentagem publicado).
BLEBas batteryService;

// Ligado/desligado do "modo de dados" (ver BleInternal.h) - lido tambem
// por gattDumpTask em BleGattDump.cpp.
volatile bool s_dataModeEnabled = false;

namespace {

// Flags "volatile" porque sao escritas dentro de callbacks BLE (que
// correm no contexto/tarefa da stack Bluefruit) e lidas no loop
// principal — evita que o compilador otimize leituras assumindo que o
// valor nao muda "sozinho". So' usadas neste ficheiro (provisioning),
// por isso continuam com ligacao interna.
volatile bool s_aesArrived = false;
volatile bool s_timestampArrived = false;
volatile uint32_t s_timestamp = 0;

// Contador de sequencia dos alertas de emergencia — ver EmergencyAlertPacket.
uint16_t s_emergencyAlertSeq = 0;

// Helpers de calendario usados apenas para validar/converter a data
// recebida via BLE (nao ha biblioteca de data/hora disponivel aqui).
bool isLeapYear(uint16_t y) {
  return ((y % 4U) == 0U) && (((y % 100U) != 0U) || ((y % 400U) == 0U));
}

uint8_t daysInMonth(uint16_t y, uint8_t m) {
  static const uint8_t days[12] = {31,28,31,30,31,30,31,31,30,31,30,31};
  if (m < 1 || m > 12) return 0;
  if (m == 2 && isLeapYear(y)) return 29;
  return days[m - 1];
}

// Converte o payload bruto da characteristic padrao "Current Time"
// (Bluetooth SIG, UUID 0x2A2B) para um timestamp UTC em segundos desde
// 1970-01-01 (epoch), fazendo tambem validacao dos campos recebidos.
// Formato dos 10 bytes: ano (2 bytes little-endian), mes, dia, hora,
// minuto, segundo, dia-da-semana, sub-segundo, motivo-de-ajuste.
// O calculo do numero de dias usa o algoritmo classico de Howard
// Hinnant (baseado em "eras" de 400 anos) para converter uma data do
// calendario gregoriano em dias desde a epoch, sem depender de
// bibliotecas de data/hora do sistema.
bool ctsToEpochUtc(const uint8_t *data, uint16_t len, uint32_t &outEpoch) {
  if (len != 10 || data == nullptr) return false;

  const uint16_t year = (uint16_t)data[0] | ((uint16_t)data[1] << 8);
  const uint8_t month = data[2];
  const uint8_t day = data[3];
  const uint8_t hour = data[4];
  const uint8_t minute = data[5];
  const uint8_t second = data[6];

  if (year < 1970U || year > 2099U) return false;
  if (month < 1U || month > 12U) return false;
  if (day < 1U || day > daysInMonth(year, month)) return false;
  if (hour > 23U || minute > 59U || second > 59U) return false;

  int y = (int)year;
  const unsigned m = (unsigned)month;
  const unsigned d = (unsigned)day;
  y -= (m <= 2U);
  const int era = (y >= 0) ? (y / 400) : ((y - 399) / 400);
  const unsigned yoe = (unsigned)(y - era * 400); // [0, 399]
  const int mp = (int)m + ((m > 2U) ? -3 : 9);
  const unsigned doy = (153U * (unsigned)mp + 2U) / 5U + d - 1U;
  const unsigned doe = yoe * 365U + yoe / 4U - yoe / 100U + doy;
  const int64_t days = (int64_t)era * 146097LL + (int64_t)doe - 719468LL;
  if (days < 0) return false;

  const uint64_t sec =
      (uint64_t)days * 86400ULL + (uint64_t)hour * 3600ULL +
      (uint64_t)minute * 60ULL + (uint64_t)second;
  if (sec == 0ULL || sec > 0xFFFFFFFFULL) return false;

  outEpoch = (uint32_t)sec;
  return true;
}

} // namespace

// ============================================================
// Callbacks BLE (correm no contexto/tarefa da stack Bluefruit sempre
// que o telemovel escreve numa characteristic ou liga/desliga)
// ============================================================

// Chamado quando a app escreve na characteristic aesKeyChar, isto é,
// quando envia a chave AES partilhada durante o "provisioning". So
// aceita a escrita uma unica vez por dispositivo: se ja existir uma
// chave guardada em flash, ignora silenciosamente novas escritas (para
// nao permitir que qualquer ligacao troque a chave depois de definida).
static void aesKeyCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                           uint8_t *data, uint16_t len) {
  (void)conn_hdl;
  (void)chr;

  if (Storage::hasAesKey()) {
    Serial.println("[BLE] AES already in flash, ignoring write");
    return;
  }

  // Restrito aos 3 comprimentos reais de chave AES (128/192/256 bits) —
  // antes aceitava-se qualquer valor entre AES_KEY_MIN_LEN e
  // AES_KEY_MAX_LEN (ex.: 20 bytes), o que passava na validacao mas nao
  // correspondia a nenhuma variante suportada por encryptRecord() (ver
  // "Cifra AES-CTR do modo de dados" em BleGattDump.cpp) — bug
  // encontrado ao implementar a cifra: uma chave "valida" por este
  // criterio antigo bloquearia todo o streaming de dados (encryptRecord()
  // devolveria sempre false).
  if (len != 16 && len != 24 && len != 32) {
    Serial.println("[BLE] AES key invalid length (precisa 16, 24 ou 32 bytes)");
    return;
  }

  if (!Storage::saveAesKey(data, len)) {
    Serial.println("[BLE] failed to save AES key");
    return;
  }

  cacheAesKey(data, len);
  s_aesArrived = true;
  Serial.println("[BLE] AES key received and stored");
}

// Chamado quando a app escreve na characteristic padrao "Current Time"
// (0x2A2B), tipicamente logo apos a ligacao, para sincronizar a hora do
// dispositivo com a do telemovel. Valida e converte o payload para
// epoch UTC e publica o resultado no modulo Clock.
//
// BLOQUEIO DE SEGURANCA (2026-07-08, rotina de seguranca, FW-001): esta
// characteristic usa SECMODE_OPEN (sem pairing/bonding, ver Ble.h) e
// continua acessivel via GATT mesmo depois de currentTimeService deixar
// de ser anunciado no "modo de dados" (startBroadcast() so' controla o
// advertising, nao remove characteristics ja registadas) — qualquer
// central BLE que descubra o handle por descoberta de servicos pode
// escrever aqui em qualquer altura. Sem este bloqueio, um atacante ligado
// durante o streaming (ate 2 centrais simultaneos, ver Bluefruit.begin(2,0)
// em main.cpp) conseguia reescrever o relogio do dispositivo a qualquer
// momento, falsificando os timestamps gravados nos registos de sensores e
// em alertas de emergencia (Clock::setUtc() e' a unica fonte de
// 'timestamp'/'timestamp_utc' usada em todo o firmware) — quebra da
// integridade forense/clinica dos dados sem alterar o formato do pacote.
// Mitigacao contida: bloquear a hora (mesmo padrao ja usado para a chave
// AES em aesKeyCallback) assim que o dispositivo entra em modo de dados
// (s_dataModeEnabled, ver startBroadcast()) — ensureTimeSync() so' corre
// ANTES disso, por isso o fluxo normal de provisioning fica intacto. O
// bridge (ble_bridge.py, _maybe_send_time()) ja trata a falha desta
// escrita como nao-fatal ("normal se ja sincronizada"), logo nao precisa
// de alteracao. Nao fecha a janela de reescrita ANTES do primeiro sync
// (fase de provisioning continua sem autenticacao — ver SECURITY_STATUS.md,
// FW-002, ligado a auditoria mais ampla de pairing/bonding prevista na
// rotina S04).
//
// RESOLVIDO (Fase A de seguranca BLE, 2026-07-20, ver SECURITY_STATUS.md
// BLE-001/BLE-004/BLE-006 e Ble::begin()): currentTimeChar passou de
// SECMODE_OPEN para SECMODE_ENC_NO_MITM na escrita — a partir de agora e'
// preciso bonding+encriptacao de link (Legacy Pairing/Just Works, sem
// MITM) antes do SoftDevice sequer entregar esta escrita ao callback
// abaixo, o que fecha tambem a janela de reescrita ANTES do primeiro sync
// descrita acima (nao dependia de s_dataModeEnabled, era o unico caso sem
// mitigacao nenhuma). Protecao contra MITM (Numeric Comparison via ecra
// OLED) continua pendente da Fase B — ver BLE-003.
static void timestampCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                              uint8_t *data, uint16_t len) {
  (void)conn_hdl;
  (void)chr;

  if (s_dataModeEnabled) {
    Serial.println("[BLE] current-time write ignorada (ja em modo de dados, hora bloqueada)");
    return;
  }

  if (len != 10) {
    Serial.print("[BLE] invalid current-time len: ");
    Serial.println(len);
    return;
  }

  uint32_t ts = 0;
  if (!ctsToEpochUtc(data, len, ts)) {
    Serial.println("[BLE] invalid current-time payload");
    return;
  }

  if (ts == 0) {
    Serial.println("[BLE] invalid current-time value: 0");
    return;
  }

  s_timestamp = ts;
  s_timestampArrived = true;
  Clock::setUtc(s_timestamp);
  Serial.print("[BLE] timestamp received: ");
  Serial.println(s_timestamp);
}

// Chamado quando a app escreve na characteristic dumpCtrlChar para
// pedir explicitamente o inicio (kDumpCtrlStart) ou a paragem
// (kDumpCtrlStop) do streaming de dados dos sensores. So tem efeito
// quando o dispositivo ja esta em modo de dados (s_dataModeEnabled),
// isto é, depois de startBroadcast() ter sido chamado.
static void dumpCtrlCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                             uint8_t *data, uint16_t len) {
  (void)chr;
  if (len < 1 || data == nullptr) return;

  // Instrumentacao (2026-07-31, investigacao de "force_reading" nao
  // fiavel a partir do dashboard): antes desta linha, um write recebido
  // fora da janela em que s_dataModeEnabled esta ativo era descartado
  // aqui em baixo SEM nenhum log — do lado do dashboard/bridge parecia
  // um comando "perdido" sem qualquer pista no serial do firmware sobre
  // se sequer chegou. Log sempre (aceite ou nao) para permitir, com
  // hardware, distinguir "o BLE nunca entregou o write" de "o write
  // chegou mas foi descartado por s_dataModeEnabled=false".
  Serial.print("[BLEG][DUMP] write recebido cmd=0x");
  Serial.print(data[0], HEX);
  Serial.print(" len=");
  Serial.print(len);
  Serial.print(" s_dataModeEnabled=");
  Serial.println(s_dataModeEnabled ? "1" : "0");

  if (!s_dataModeEnabled) {
    Serial.println("[BLEG][DUMP] write descartado: modo de dados inativo");
    return;
  }

  const uint8_t cmd = data[0];
  if (cmd == kDumpCtrlStart) {
    (void)conn_hdl;
    s_dumpStartRequested = true;
    s_dumpStopRequested = false;
    s_dumpPendingValid = false;
    s_dumpWindowImmediate = false;
    s_dumpState = DUMP_IDLE;
    s_dumpSentRecords = 0;
    s_dumpAckedRecords = 0;
    Serial.println("[BLEG][DUMP] START");
    publishDumpStatus(DUMP_STREAMING, 1, 0);
    return;
  }

  if (cmd == kDumpCtrlStop) {
    s_dumpStopRequested = true;
    Serial.println("[BLEG][DUMP] STOP");
    return;
  }

  if (cmd == kDumpCtrlForceHr) {
    // Um unico comando/botao pede as duas leituras "agora": a FC fica
    // em streaming forcado durante `seconds` (pode demorar alguns
    // segundos a estabilizar um valor fiavel) e o SpO2 e' medido de
    // imediato na proxima iteracao da task (medicao unica, ~seg a mais).
    uint16_t seconds = kForceHrDefaultSeconds;
    if (len >= 3) {
      seconds = static_cast<uint16_t>(data[1]) | (static_cast<uint16_t>(data[2]) << 8);
    }
    Ppg::requestManualHr(static_cast<uint32_t>(seconds) * 1000UL);
    Ppg::requestManualSpo2();
    Serial.print("[BLEG][DUMP] FORCE_HR+SPO2 segundos=");
    Serial.println(seconds);
    return;
  }

  if (cmd == kDumpCtrlResetReadings) {
    // *** DESTRUTIVO E IRREVERSIVEL *** — ver aviso junto de
    // kDumpCtrlResetReadings. Apaga apenas os registos de leituras
    // (ring buffer); calibracao do IMU e chave AES ficam intactas.
    //
    // CONCORRENCIA (atualizado 2026-07-08): gattDumpTask (le/remove) e
    // storageTask em main.cpp (escreve) tambem acedem ao ring buffer.
    // QspiRingBuffer::format() (chamado abaixo) agora toma um mutex
    // interno ao modulo (o mesmo usado por push()/peek()/pop()/
    // advanceTail(), ver QspiRingBuffer.cpp) — a chamada bloqueia ate
    // qualquer push()/peek()/pop()/advanceTail() em curso nessa outra
    // task terminar, por isso a corrida contra quem escreve, referida
    // aqui antes como nao eliminada, esta de facto fechada agora, nao
    // so reduzida. Pedir a paragem do streaming e esperar um pouco
    // continua a fazer sentido (evita gattDumpTask ficar bloqueada no
    // mutex a tentar ler enquanto o reset decorre, e da tempo ao
    // streaming em curso para acabar de forma limpa), mas ja nao e o
    // que garante a correcao — o mutex e que garante isso.
    s_dumpStopRequested = true;
    vTaskDelay(pdMS_TO_TICKS(100));
    const bool ok = QspiRingBuffer::format();
    Serial.print("[BLEG][DUMP] RESET_READINGS ok=");
    Serial.println(ok ? "1" : "0");
    return;
  }
}

// Chamado quando o bridge escreve na characteristic emergencyProfileWriteChar
// (JSON do perfil de emergencia do paciente, montado pelo bridge a partir do
// ORM — ver build_emergency_profile_payload() em storage_advanced.py). Sem
// nenhuma camada de cifra AES-CTR propria (igual a dumpCtrlCallback/
// aesKeyCallback): protegido so pela encriptacao de link BLE (bonding),
// nao pelo encryptRecord() usado no caminho device->bridge de dumpDataChar.
// Guarda em flash e espelha de imediato em emergencyProfileChar, para que
// uma leitura subsequente (app no telemovel, ou o proprio bridge numa
// ligacao futura) veja logo o valor mais recente sem depender de reiniciar
// o dispositivo.
static void emergencyProfileWriteCallback(uint16_t conn_hdl, BLECharacteristic *chr,
                                           uint8_t *data, uint16_t len) {
  (void)conn_hdl;
  (void)chr;

  if (!Storage::saveEmergencyProfile(data, len)) {
    Serial.println("[BLE] failed to save emergency profile");
    return;
  }

  emergencyProfileChar.write(data, len);
  Serial.print("[BLE] emergency profile received and stored, len=");
  Serial.println(len);
}

// Chamado pela stack Bluefruit sempre que um central (telemovel) se
// liga ao dispositivo. Se estivermos em modo de dados, o streaming
// arranca automaticamente ao ligar (nao é preciso a app enviar o
// comando de start explicitamente); durante o provisioning nao ha nada
// a fazer aqui alem de registar a ligacao.
static void periphConnectCallback(uint16_t conn_hdl) {
  Serial.print("[BLE] connected conn_hdl=");
  Serial.println(conn_hdl);

  if (!s_dataModeEnabled) {
    Serial.println("[BLE] provisioning link");
    return;
  }

  s_dumpStartRequested = true; // automatico
  s_dumpStopRequested = false;
  s_dumpPendingValid = false;
  s_dumpWindowImmediate = false;
  s_dumpState = DUMP_IDLE;
  Serial.println("[BLEG][DUMP] auto START");
  publishDumpStatus(DUMP_STREAMING, 1, 0);
}

// Chamado pela stack Bluefruit quando a ligacao ao central é perdida
// (app fechou, saiu de alcance, etc.). Repoe imediatamente o estado do
// streaming para inativo, para nao continuar "a pensar" que esta a
// enviar dados sem ninguem do outro lado.
static void periphDisconnectCallback(uint16_t conn_hdl, uint8_t reason) {
  (void)conn_hdl;
  Serial.print("[BLE] disconnected reason=0x");
  Serial.println(reason, HEX);
  s_dumpStartRequested = false;
  s_dumpStopRequested = false;
  s_dumpPendingValid = false;
  s_dumpWindowImmediate = false;
  s_dumpState = DUMP_IDLE;
}

namespace Ble {

// Ver documentacao completa em Ble.h. Aqui a implementacao segue,
// passo a passo, a ordem: registar callbacks de ligacao -> criar o
// servico/characteristics do wearable -> criar o servico padrao de
// hora -> configurar e arrancar o advertising de provisioning -> criar
// a tarefa de streaming (ainda inativa nesta fase).
bool begin() {
  Clock::begin();
  Serial.print("[BLE] build tag: ");
  Serial.println(kBleBuildTag);

  // Estes callbacks disparam sempre que um telemovel se liga/desliga,
  // independentemente do modo (provisioning ou dados).
  Bluefruit.Periph.setConnectCallback(periphConnectCallback);
  Bluefruit.Periph.setDisconnectCallback(periphDisconnectCallback);

  // Fase A de seguranca BLE (2026-07-20, ver SECURITY_STATUS.md
  // BLE-001/BLE-002/BLE-004/BLE-006): bond=1/mitm=0 ja sao os defaults da
  // propria SDK (BLESecurity.cpp, _sec_param_default) e bastariam
  // sozinhos para o que a Fase A pede, mas declara-se aqui de forma
  // explicita para nao depender silenciosamente de defaults de uma
  // biblioteca de terceiros. IO_CAPS=NONE + MITM=false -> pairing "Just
  // Works" (Legacy Pairing, NAO LESC: NRF_CRYPTOCELL nao esta definido
  // para esta placa/build, ver platformio.ini e variants/
  // Seeed_XIAO_nRF52840_Sense_Plus). Protecao contra MITM (Numeric
  // Comparison, exige o ecra OLED ainda nao montado) fica para a Fase B
  // — ver BLE-003 em SECURITY_STATUS.md.
  Bluefruit.Security.setIOCaps(false, false, false);
  Bluefruit.Security.setMITM(false);

  wearableService.begin();

  // Characteristic de escrita para a app enviar a chave AES. Ate
  // 2026-07-20 usava SECMODE_OPEN (sem exigir pairing/bonding BLE) e
  // dependia so' da logica applicacional (so aceita a primeira escrita,
  // ver aesKeyCallback) para se proteger — ver FW-002/BLE-001 em
  // SECURITY_STATUS.md. RESOLVIDO (Fase A de seguranca BLE, 2026-07-20):
  // agora exige SECMODE_ENC_NO_MITM (bonding + encriptacao de link antes
  // do SoftDevice aceitar a escrita); a logica de "so aceita a 1a
  // escrita" mantem-se como segunda camada, nao substituida por isto.
  aesKeyChar.setProperties(CHR_PROPS_WRITE);
  aesKeyChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  aesKeyChar.setMaxLen(AES_KEY_MAX_LEN);
  aesKeyChar.setWriteCallback(aesKeyCallback);
  aesKeyChar.begin();

  // Characteristic de controlo (start/stop) do streaming; aceita
  // escrita com e sem resposta (WRITE_WO_RESP) para reduzir latencia
  // do lado da app ao pedir o inicio da transmissao.
  // Fase A (2026-07-20): permissao de escrita passou a SECMODE_ENC_NO_MITM.
  dumpCtrlChar.setProperties(CHR_PROPS_WRITE | CHR_PROPS_WRITE_WO_RESP);
  dumpCtrlChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  dumpCtrlChar.setMaxLen(8);
  dumpCtrlChar.setWriteCallback(dumpCtrlCallback);
  dumpCtrlChar.begin();

  // Characteristic apenas de notificacao/indicacao: o dispositivo
  // "empurra" os pacotes de dados para a app, que nunca escreve aqui
  // (por isso SECMODE_NO_ACCESS na escrita). Tamanho fixo porque todos
  // os pacotes DumpDataPacket tem o mesmo tamanho.
  // Fase A (2026-07-20): permissao de leitura/notify passou a
  // SECMODE_ENC_NO_MITM.
  dumpDataChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_INDICATE);
  dumpDataChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  dumpDataChar.setFixedLen(sizeof(DumpDataPacket));
  dumpDataChar.begin();

  // Characteristic de estado: pode ser lida sob pedido ou recebida via
  // notify sempre que o estado do streaming muda.
  // Fase A (2026-07-20): permissao de leitura/notify passou a
  // SECMODE_ENC_NO_MITM.
  dumpStatusChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  dumpStatusChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  dumpStatusChar.setFixedLen(sizeof(DumpStatusPacket));
  dumpStatusChar.begin();

  // Characteristic do instantaneo ao vivo (2026-08-06) — ver comentario
  // completo junto da declaracao de liveSnapshotChar acima e de
  // sendLiveSnapshot() em BleGattDump.cpp. Mesmo formato/protecao que
  // dumpDataChar (so notificacao, nunca escrita da app), porque
  // transporta o mesmo tipo de pacote (DumpDataPacket).
  liveSnapshotChar.setProperties(CHR_PROPS_NOTIFY);
  liveSnapshotChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  liveSnapshotChar.setFixedLen(sizeof(DumpDataPacket));
  liveSnapshotChar.begin();

  // Characteristic dedicada a alertas de emergencia (SOS manual ou
  // queda+inatividade prolongada) — ver Emergency.h. So o dispositivo
  // escreve aqui (app nunca escreve, daí SECMODE_NO_ACCESS), e o valor
  // fica disponivel por leitura mesmo que nao haja ligacao ativa no
  // momento exato do alerta (a app pode ler ao reconectar-se).
  // Fase A (2026-07-20): permissao de leitura/notify passou a
  // SECMODE_ENC_NO_MITM.
  emergencyAlertChar.setProperties(CHR_PROPS_NOTIFY | CHR_PROPS_READ);
  emergencyAlertChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  emergencyAlertChar.setFixedLen(sizeof(EmergencyAlertPacket));
  emergencyAlertChar.begin();

  // Characteristic de escrita para o bridge enviar o perfil de emergencia
  // (JSON, tamanho variavel por paciente). Mesmo nivel de protecao que
  // dumpCtrlChar: so encriptacao de link BLE, sem cifra AES-CTR propria
  // (ver emergencyProfileWriteCallback).
  emergencyProfileWriteChar.setProperties(CHR_PROPS_WRITE | CHR_PROPS_WRITE_WO_RESP);
  emergencyProfileWriteChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  emergencyProfileWriteChar.setMaxLen(EMERGENCY_PROFILE_MAX_LEN);
  emergencyProfileWriteChar.setWriteCallback(emergencyProfileWriteCallback);
  emergencyProfileWriteChar.begin();

  // Characteristic de leitura do perfil de emergencia. Sem setFixedLen():
  // o comportamento por omissao de qualquer BLECharacteristic ja e
  // variavel (vlen=1), o mesmo padrao usado por aesKeyChar/dumpCtrlChar —
  // setFixedLen(N) e o que optaria por tamanho fixo, o que nao serve aqui
  // porque o JSON varia de paciente para paciente.
  emergencyProfileChar.setProperties(CHR_PROPS_READ);
  emergencyProfileChar.setPermission(SECMODE_ENC_NO_MITM, SECMODE_NO_ACCESS);
  emergencyProfileChar.setMaxLen(EMERGENCY_PROFILE_MAX_LEN);
  emergencyProfileChar.begin();

  // Pre-carrega o valor local com o que ja estiver persistido em flash (ou
  // "{}" vazio se ainda nao houver nenhum), para ficar disponivel por
  // leitura logo no arranque, sem depender de uma escrita nova do bridge
  // nesta ligacao — mesmo padrao que dumpStatusChar/emergencyAlertChar
  // usam para popular o valor local antes do primeiro evento.
  {
    uint8_t profBuf[EMERGENCY_PROFILE_MAX_LEN];
    size_t profLen = 0;
    if (Storage::hasEmergencyProfile() && Storage::loadEmergencyProfile(profBuf, sizeof(profBuf), profLen)) {
      emergencyProfileChar.write(profBuf, profLen);
    } else {
      emergencyProfileChar.write(reinterpret_cast<const uint8_t *>("{}"), 2);
    }
  }

  // Servico padrao "Battery Service" (0x180F/0x2A19) — ver comentario
  // junto da declaracao de batteryService, acima. begin() aqui cria o
  // servico/characteristic com as propriedades ja definidas pela propria
  // classe BLEBas (READ+NOTIFY, SECMODE_OPEN/NO_ACCESS, 1 byte fixo);
  // o valor so fica com sentido depois da primeira chamada a
  // Ble::updateBatteryLevel() (ver main.cpp, sample periodico via
  // Battery::sample()) — ate la fica a 0, que e' um valor seguro por
  // omissao (nao e' interpretado como "bateria cheia").
  if (batteryService.begin() != ERROR_NONE) {
    Serial.println("[BLE] falha ao iniciar Battery Service (0x180F) — nivel de bateria nao sera publicado por BLE");
  }

  // Servico/characteristic padrao do Bluetooth SIG para sincronizacao
  // de hora (0x2A2B). Usar o UUID standard permite, em teoria, que
  // qualquer app compativel com BLE "Current Time" consiga escrever
  // aqui, embora neste projeto seja a app dedicada que o faz.
  currentTimeService.begin();
  currentTimeChar.setProperties(CHR_PROPS_WRITE);
  currentTimeChar.setPermission(SECMODE_OPEN, SECMODE_ENC_NO_MITM);
  currentTimeChar.setMaxLen(10);
  currentTimeChar.setWriteCallback(timestampCallback);
  currentTimeChar.begin();

  // Advertising inicial ("provisioning"): anuncia os dois servicos
  // (wearable + current time) para que a app consiga encontrar e
  // ligar-se ao dispositivo antes de haver chave AES/hora definidas.
  // restartOnDisconnect(true) garante que volta a anunciar-se
  // automaticamente se a ligacao cair nesta fase.
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addService(wearableService);
  Bluefruit.Advertising.addService(currentTimeService);
  // O pacote de advertising principal (31 bytes) já vai cheio com os dois
  // UUIDs de serviço acima, por isso o nome ("Wearable", definido em
  // Bluefruit.setName() no main.cpp) vai no "scan response" — um segundo
  // pacote que apps como o nRF Connect também leem automaticamente ao
  // fazer scan. Sem isto, o dispositivo aparecia sem nome nas apps de
  // scan BLE, tornando-o impossível de identificar no meio de outros
  // dispositivos próximos.
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(160, 244);
  const bool provStartOk = Bluefruit.Advertising.start(0);
  Serial.print("[BLE] provisioning adv start=");
  Serial.println(provStartOk ? "OK" : "FAIL");
  Serial.print("[BLE] provisioning adv running=");
  Serial.println(Bluefruit.Advertising.isRunning() ? "1" : "0");

  // Cria a tarefa de streaming uma unica vez; ela fica em espera
  // passiva (DUMP_IDLE, sem pedido de start) ate startBroadcast() ser
  // chamado mais tarde e uma ligacao ser estabelecida em modo de dados.
  if (s_dumpTaskHandle == nullptr) {
    BaseType_t ok = xTaskCreate(
        gattDumpTask,
        "ble_gatt_dump_task",
        kGattDumpTaskStackWords,
        nullptr,
        TASK_PRIO_LOW,
        &s_dumpTaskHandle);
    if (ok != pdPASS) {
      s_dumpTaskHandle = nullptr;
      Serial.println("[BLEG][DUMP] failed to create task");
    }
  }

  Serial.println("[BLE] provisioning service active");
  return true;
}

// Espera bloqueante por uma escrita da app durante o provisioning
// (chave AES ou hora atual), com um mecanismo de "kick" por timeout:
// se ficar um central ligado sem completar a escrita esperada dentro
// de kProvisionCentralTimeoutMs, esse central e desconectado a forca
// para libertar a ligacao a um central diferente (o dashboard real).
// Ver comentario junto de kProvisionCentralTimeoutMs para o bug real
// que isto resolve.
static bool waitForProvisionWrite(volatile bool &arrivedFlag, const char *waitLabel) {
  uint32_t lastLog = 0;
  uint32_t connectedSinceMs = 0;
  bool wasConnected = false;

  while (!arrivedFlag) {
    const uint32_t now = millis();
    const bool isConnected = Bluefruit.connected() > 0;

    if (isConnected && !wasConnected) {
      // Um central novo acabou de se ligar — comeca a contar o prazo
      // a partir de agora, nao do inicio desta funcao.
      connectedSinceMs = now;
    }
    wasConnected = isConnected;

    if (isConnected && (now - connectedSinceMs) >= kProvisionCentralTimeoutMs) {
      Serial.print("[BLE] central ligado sem completar '");
      Serial.print(waitLabel);
      Serial.println("' dentro do tempo limite -> a desconectar (pode nao ser o dashboard real)");
      uint16_t handles[2] = {0};
      const uint8_t connCount = Bluefruit.getConnectedHandles(handles, 2);
      for (uint8_t i = 0; i < connCount; i++) {
        Bluefruit.disconnect(handles[i]);
      }
      // Reinicia a contagem para nao disparar disconnect() em loop
      // apertado enquanto o evento de desconexao ainda nao foi
      // processado pela stack.
      connectedSinceMs = now;
      wasConnected = false;
    }

    if ((now - lastLog) >= kBleProvisionWaitLogMs) {
      lastLog = now;
      Serial.print("[BLE] wait ");
      Serial.print(waitLabel);
      Serial.print("... adv=");
      Serial.print(Bluefruit.Advertising.isRunning() ? "1" : "0");
      Serial.print(" connected=");
      Serial.println(Bluefruit.connected());
    }
    delay(100);
  }
  return true;
}

bool ensureAesKey() {
  // Caminho rapido: ja existe uma chave persistida em flash de um
  // provisioning anterior — nao é preciso esperar por BLE outra vez.
  if (Storage::hasAesKey()) {
    uint8_t buf[AES_KEY_MAX_LEN] = {0};
    size_t n = 0;
    if (Storage::loadAesKey(buf, sizeof(buf), n)) {
      cacheAesKey(buf, n);
      Serial.println("[BLE] AES key loaded from flash");
      uiMessage("AES key", "recebida");
      delay(1200);
      return true;
    }
  }

  // Sem chave em flash: bloqueia aqui (busy-wait com delay) ate a app
  // se ligar e escrever a chave na characteristic aesKeyChar — o
  // aesKeyCallback (registado em begin()) e quem marca s_aesArrived.
  Serial.println("[BLE] waiting for AES key via BLE...");
  uiMessage("Receber", "AES key");
  waitForProvisionWrite(s_aesArrived, "AES");

  uiMessage("AES key", "recebida");
  delay(1200);
  return true;
}

bool ensureTimeSync() {
  // Forca sync novo nesta fase de arranque: mesmo que ja tenha havido
  // um timestamp anterior (de um arranque passado), este é descartado
  // para garantir que ficamos com a hora atual e nao uma desatualizada.
  s_timestampArrived = false;
  s_timestamp = 0;
  Clock::invalidate();

  // Bloqueia ate a app se ligar e escrever a hora atual na
  // characteristic "Current Time" — timestampCallback marca
  // s_timestampArrived quando isso acontece.
  Serial.println("[BLE] waiting for Current Time (0x2A2B) via BLE...");
  uiMessage("Pedir", "Hora e Data");
  waitForProvisionWrite(s_timestampArrived, "TIME");

  // A partir daqui comeca a transicao do modo "provisioning" para o
  // "modo de dados": ja temos chave AES e hora sincronizada, por isso
  // fechamos as ligacoes atuais e paramos este advertising, para que
  // startBroadcast() (chamado depois pelo main.cpp) possa arrancar um
  // advertising limpo e dedicado ao modo de dados.
  //
  // Congela auto-restart do advertising de provisioning antes da transicao.
  Bluefruit.Advertising.restartOnDisconnect(false);

  // Fecha ligacoes BLE do provisioning para libertar stack/roles
  // antes de entrar no modo de dados por GATT.
  // Tamanho 2: Bluefruit.begin(2, 0) em main.cpp reserva no maximo 2
  // ligacoes perifericas simultaneas — nunca pode haver mais handles do
  // que isso a devolver.
  uint16_t handles[2] = {0};
  const uint8_t connCount = Bluefruit.getConnectedHandles(handles, 2);
  if (connCount > 0) {
    for (uint8_t i = 0; i < connCount; i++) {
      Bluefruit.disconnect(handles[i]);
    }
    Serial.print("[BLE] disconnected centrals after time sync: ");
    Serial.println(connCount);
    // Espera evento real de disconnect no stack.
    const uint32_t t0 = millis();
    while (Bluefruit.connected() > 0 && (millis() - t0) < 3000) {
      delay(20);
    }
    Serial.print("[BLE] connected after wait: ");
    Serial.println(Bluefruit.connected());
  }

  // Para explicitamente o advertising de provisioning.
  const bool stopOk = Bluefruit.Advertising.stop();
  Serial.print("[BLE] provisioning adv stop=");
  Serial.println(stopOk ? "OK" : "FAIL");
  delay(100);
  Serial.print("[BLE] provisioning adv running=");
  Serial.println(Bluefruit.Advertising.isRunning() ? "1" : "0");

  uiMessage("Hora e Data", "recebida");
  delay(1000);
  return true;
}

uint32_t timestamp() {
  return s_timestamp;
}

bool hasTimestamp() {
  return s_timestampArrived;
}

bool startBroadcast() {
  // Modo apenas GATT: sem broadcast de manufacturer data. Reconstroi o
  // advertising do zero (stop + clearData) para garantir que nao
  // sobram dados/servicos configurados na fase de provisioning.
  (void)Bluefruit.Advertising.stop();
  delay(30);
  Bluefruit.Advertising.clearData();
  Bluefruit.ScanResponse.clearData();
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addService(wearableService);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(160, 244);
  if (!Bluefruit.Advertising.start(0)) {
    s_dataModeEnabled = false;
    Serial.println("[BLE] failed to start GATT advertising");
    return false;
  }

  s_dataModeEnabled = true;
  Serial.print("[BLE] GATT-only mode active (auto dump ON CONNECT, window=");
  Serial.print(kGattDumpWindowMs / 1000);
  Serial.print("s, target=");
  Serial.print(kWindowTargetRecords);
  Serial.println(" rec/window)");
  Serial.print("[BLE] advRunning=");
  Serial.print(Bluefruit.Advertising.isRunning() ? "1" : "0");
  Serial.print(" connected=");
  Serial.print(Bluefruit.connected());
  Serial.print(" dumpTask=");
  Serial.println((s_dumpTaskHandle != nullptr) ? "1" : "0");
  return true;
}

void stopBroadcast() {
  // Sinaliza a gattDumpTask para parar (via s_dumpStopRequested) e para
  // o advertising do modo de dados. Note que s_dumpState so é reposto
  // aqui de forma otimista; a tarefa tambem o repoe ao processar o
  // pedido de stop, para lidar com corridas entre esta chamada e o loop.
  s_dataModeEnabled = false;
  s_dumpStartRequested = false;
  s_dumpStopRequested = true;
  s_dumpPendingValid = false;
  s_dumpState = DUMP_IDLE;
  (void)Bluefruit.Advertising.stop();
  Serial.println("[BLE] GATT adv stopped");
}

bool isBroadcastActive() {
  return s_dataModeEnabled && Bluefruit.Advertising.isRunning();
}

// *** DIAGNOSTICO TEMPORARIO (otimizacao de RAM) *** — ver Ble.h.
uint32_t dumpTaskStackHighWaterMarkWords() {
  if (s_dumpTaskHandle == nullptr) return 0;
  return static_cast<uint32_t>(uxTaskGetStackHighWaterMark(s_dumpTaskHandle));
}

void notifyEmergencyAlert(uint8_t alertType, uint32_t timestampUtc) {
  EmergencyAlertPacket pkt{};
  pkt.type = alertType;
  pkt.reserved = 0;
  pkt.seq = ++s_emergencyAlertSeq;
  pkt.timestamp_utc = timestampUtc;

  emergencyAlertChar.write(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  if (Bluefruit.connected() > 0) {
    (void)emergencyAlertChar.notify(reinterpret_cast<const uint8_t *>(&pkt), sizeof(pkt));
  }

  Serial.print("[BLE] alerta de emergencia enviado, tipo=");
  Serial.print(alertType);
  Serial.print(" seq=");
  Serial.println(pkt.seq);
}

void updateBatteryLevel(uint8_t percent) {
  if (percent > 100) percent = 100; // defensivo: BLEBas.write()/notify() esperam 0-100.

  // write() atualiza sempre o valor local da characteristic (disponivel
  // por leitura mesmo sem ligacao ativa no momento, tal como
  // dumpStatusChar/emergencyAlertChar acima); notify() so tem efeito com
  // uma ligacao ativa, por isso e' condicional (mesmo padrao usado em
  // publishDumpStatus()/notifyEmergencyAlert()).
  batteryService.write(percent);
  if (Bluefruit.connected() > 0) {
    (void)batteryService.notify(percent);
  }
}

} // namespace Ble
