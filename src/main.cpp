// ============================================================
// main.cpp — Ponto de entrada do firmware do wearable (nRF52840)
// ============================================================
// Este ficheiro é o "maestro" do dispositivo: liga tudo o que os outros
// módulos (Imu, Ppg, Ble, Storage, QspiRingBuffer, Clock, Display) sabem
// fazer sozinhos. A ideia geral do dispositivo é:
//
//   1) O utilizador carrega no botão (BTN_PIN) durante alguns segundos
//      (long-press) para ligar o dispositivo.
//   2) O firmware inicializa o ecrã, o armazenamento, o Bluetooth (BLE),
//      o sensor de movimento (IMU) e o sensor cardíaco/SpO2 (PPG).
//   3) Uma tarefa em segundo plano (storageTask, ver mais abaixo) lê as
//      últimas amostras do IMU/PPG e grava-as num "ring buffer" persistido
//      em memória flash externa (QspiRingBuffer), para depois poderem ser
//      lidas/descarregadas (ver os scripts em test/*.py).
//   4) O loop() principal mantém um "heartbeat" (LED a piscar), atualiza
//      o ecrã com a hora/data, e vigia o botão: se for feito um novo
//      long-press, o dispositivo entra em modo de baixo consumo
//      (SYSTEM_OFF) através de goToSleep().
//
// Este projeto corresponde ao dispositivo wearable descrito no artigo de
// monitorização comportamental para cuidados de demência: os dados aqui
// recolhidos (IMU + frequência cardíaca + SpO2) são depois usados, num
// computador, para classificar atividades e detetar anomalias de rotina.
// ============================================================

#include <Arduino.h>
#include <Adafruit_TinyUSB.h>   // Pilha USB (porta série sobre USB) usada para debug/CLI
#include <bluefruit.h>          // Pilha Bluetooth Low Energy (BLE) da Adafruit/Nordic
#include <nrf_power.h>          // Acesso direto a registos de energia do chip nRF52840
#include <SPI.h>
#include <string.h>             // strcmp() usado no bypass de debug DEBUG_SERIAL_WAKE
#include <rtos.h>               // FreeRTOS (sistema operativo em tempo real usado para as tasks)
#include <math.h>
#include <Adafruit_GFX.h>       // Biblioteca genérica de desenho (texto, formas) para ecrãs
#include <Adafruit_SSD1351.h>   // Driver específico do ecrã OLED SSD1351
#include "Display/app_icons.h"  // Imagens/logótipos (bitmaps) mostrados no arranque
#include "Display/Ui.h"
#include "Storage/Storage.h"           // Guarda calibração do IMU e a chave AES na flash interna
#include "Imu/Imu.h"                   // Sensor de movimento (acelerómetro + giroscópio)
#include "Ppg/Ppg.h"                   // Sensor ótico de frequência cardíaca / SpO2
#include "Ble/Ble.h"                   // Serviço Bluetooth (emparelhamento, troca de chave, sincronização de hora)
#include "QspiRingBuffer/QspiRingBuffer.h" // "Diário" circular de registos guardado na flash externa (QSPI)
#include "Clock/Clock.h"               // Relógio interno (hora/data), sincronizado via BLE
#include "Lora/Lora.h"                 // Rádio LoRa Wio-SX1262 (experimental — ver Lora.h)

// ============================================================
// FLAGS DE CONFIGURAÇÃO (interruptores para ligar/desligar comportamentos)
// Mudar estes valores e voltar a compilar altera o que o firmware faz,
// sem ser preciso mexer no resto do código.
// ============================================================

// FLAG DE WIPE — apaga calib+aes residuais do selfTest antigo.
// Coloca a 1 numa única flash, depois volta a 0.
// (Serve só para "limpar o disco" uma vez, depois de testes antigos que
// deixaram dados de calibração/chave AES inválidos gravados na flash.)
#define WIPE_STALE_STORAGE 0

// Se a 1, corre um teste automático ao ring buffer da flash externa no
// arranque (escreve, lê e apaga dados de teste). Só serve para debug.
#define QSPI_RING_BUFFER_SELF_TEST 0

// Se a 1 (normal), a tarefa que grava amostras IMU/PPG no ring buffer
// é criada no arranque. Desligar isto (0) impede a gravação de dados.
#define STORAGE_TASK_ENABLE 1

// ------------------------------------------------------------
// *** DEBUG TEMPORÁRIO — REMOVER QUANDO O BOTÃO FÍSICO FOR REPARADO ***
// Enquanto o botão ligado a BTN_PIN estiver partido/desligado, não há
// forma de satisfazer o long-press exigido em waitForLongPress(). Com
// esta flag a 1, o firmware aceita também um comando de texto enviado
// pela porta série (Serial Monitor) como substituto do long-press:
//   - escrever "WAKE"  e Enter -> equivale a premir e manter o botão
//     os 5 segundos exigidos para ligar o dispositivo.
//   - escrever "SLEEP" e Enter -> equivale a um long-press para desligar
//     (entra em SYSTEM_OFF), já que sem botão também não há como pedir
//     isso fisicamente.
// Isto NÃO simula o hardware do botão em si; é só um atalho de teste.
// Voltar a pôr a 0 (ou apagar este bloco) assim que o botão for
// resoldado/substituído.
#define DEBUG_SERIAL_WAKE 1

// ------------------------------------------------------------
// *** DEBUG TEMPORÁRIO — REMOVER NO FIM DESTA FASE DE TESTES ***
// A cada teste em hardware, o dispositivo entrava em SYSTEM_OFF (baixo
// consumo) por inatividade/long-press e exigia todo o ciclo de "acordar
// fisicamente com reset + enviar WAKE pela série" outra vez — muito lento
// para uma sessão de testes com várias iterações seguidas. Com esta flag
// a 1, goToSleep() fica praticamente desativado: regista a intenção no
// Serial mas NÃO desliga o dispositivo, mantendo-o sempre ligado e
// acessível por BLE/série. Poupança de energia fica sacrificada de
// propósito durante o desenvolvimento. Voltar a 0 antes de qualquer uso
// real com bateria (senão a bateria nunca dura, o dispositivo nunca
// entra realmente em baixo consumo).
#define DEBUG_DISABLE_SLEEP 1

// ------------------------------------------------------------
// *** DIAGNOSTICO TEMPORARIO — otimizacao de RAM ***
// Com esta flag a 1, o loop() imprime periodicamente quanta stack cada
// task do FreeRTOS ainda tem de folga (o minimo historico, "high water
// mark"), para decidirmos com dados reais — em vez de adivinhar — se os
// tamanhos de stack reservados (*_TASK_STACK_WORDS) podem ser reduzidos
// com seguranca. Depois de recolher uns minutos de dados em uso normal,
// pode voltar a 0 e ser removida.
#define DEBUG_STACK_WATERMARKS 1

// ============================================================
// PINOS — mapeamento entre nomes com significado e os pinos físicos da placa
// ============================================================
#define BTN_PIN 0                  // raw, igual ao código de referência — pino do botão físico
#define LONG_PRESS_TIME 5000       // tempo (ms) que é preciso manter o botão premido para ligar/desligar
#define DEBOUNCE_TIME 50           // tempo (ms) de espera para ignorar "ruído" mecânico do botão

#define OLED_CS_PIN    D9          // Chip Select do ecrã OLED (seleciona o dispositivo no barramento SPI)
#define OLED_DC_PIN    D10         // Data/Command do ecrã (diz ao ecrã se o byte enviado é dado ou comando)
#define OLED_RST_PIN   D11         // Reset físico do ecrã

// ============================================================
// DISPLAY — configuração do ecrã OLED (128x128 pixels, a cores)
// ============================================================
#define SCREEN_W 128
#define SCREEN_H 128
#define COLOR_BLACK 0x0000
#define COLOR_WHITE 0xFFFF

// SPIM3 dedicado — não conflita com TWIM1 (Wire1, IMU interno)
// (O nRF52840 tem vários periféricos SPI/I2C independentes; ao dar ao ecrã
// o seu próprio barramento SPI (SPIM3), evita-se que o tráfego do ecrã
// interfira com a comunicação I2C do IMU, que usa outro periférico.)
SPIClass dispSPI(NRF_SPIM3, PIN_SPI1_MISO, PIN_SPI1_SCK, PIN_SPI1_MOSI);
Adafruit_SSD1351 display(SCREEN_W, SCREEN_H, &dispSPI,
                         OLED_CS_PIN, OLED_DC_PIN, OLED_RST_PIN);

// Indica se o dispositivo está "acordado" e a operar normalmente
// (true entre o long-press de ligar e o long-press de desligar).
bool isRunning = false;

// ============================================================
// STORAGE TASK -> QSPI RING BUFFER
// 1 registo por amostra IMU (52 Hz), com SPO2/HR opcionais.
// ------------------------------------------------------------
// Esta secção define uma "tarefa" do FreeRTOS (uma função que corre em
// paralelo/concorrência com o resto do programa) cujo único trabalho é:
//   1. Perguntar ao módulo Imu qual foi a última amostra de movimento lida.
//   2. Perguntar ao módulo Ppg se há uma leitura nova de SpO2 e/ou de
//      frequência cardíaca (estas chegam com muito menos frequência que
//      o IMU, por isso podem não estar disponíveis em todas as amostras).
//   3. Juntar tudo num único "payload" (pacote de dados) e empurrá-lo
//      para o ring buffer persistido em flash (QspiRingBuffer::push),
//      que funciona como um livro de registo circular: quando fica
//      cheio, os registos mais antigos vão sendo substituídos.
// ============================================================
namespace {

// Identificador do "tipo" de registo gravado no ring buffer — permite, no
// futuro, distinguir vários formatos de payload sem ambiguidade ao ler.
constexpr uint16_t STORAGE_REC_TYPE_IMU_PPG_V1 = 0x1001;
// Tamanho da pilha (stack) reservada para esta tarefa do FreeRTOS, em
// "palavras" de 32 bits. Precisa de ser suficiente para as variáveis
// locais e chamadas de função desta task, sem desperdiçar RAM.
// *** OTIMIZAÇÃO DE RAM (redução conservadora, ver DEBUG_STACK_WATERMARKS
// mais abaixo) ***: reduzido de 2048 para 1536 words (-2048 bytes). O
// corpo copia apenas structs pequenos (ImuPpgPayloadV1, ~44 bytes) e
// chama QspiRingBuffer::push, que por sua vez fala com o driver SPI da
// flash externa — mantém-se ~25% de margem sobre essa profundidade
// estimada. Ainda NÃO foi confirmado com uxTaskGetStackHighWaterMark()
// em hardware real — confirmar margem confortável antes de reduzir mais.
constexpr uint16_t STORAGE_TASK_STACK_WORDS = 1536;
// Quando não há amostra nova do IMU, a task espera este tempo (ms) antes
// de voltar a verificar, para não gastar CPU/energia num ciclo apertado.
constexpr uint32_t STORAGE_TASK_IDLE_MS = 5;

// Estrutura "achatada" (packed = sem espaços de alinhamento entre campos)
// que representa uma amostra combinada de IMU + PPG, tal como é gravada
// no ring buffer. Tem de caber no espaço fixo de cada slot da flash
// (ver kPayloadSize), por isso os campos são deliberadamente compactos
// (ex.: SpO2/HR como inteiros pequenos em vez de float).
struct __attribute__((packed)) ImuPpgPayloadV1 {
  float ax;          // aceleração no eixo X (g)
  float ay;          // aceleração no eixo Y (g)
  float az;          // aceleração no eixo Z (g)
  float gx;          // velocidade angular no eixo X (graus/s)
  float gy;          // velocidade angular no eixo Y (graus/s)
  float gz;          // velocidade angular no eixo Z (graus/s)
  uint32_t steps;    // contagem acumulada de passos, calculada pelo módulo Imu
  uint8_t ff;        // 1 se foi detetado um evento de queda livre (free-fall) nesta amostra
  uint8_t inact;     // 1 se o dispositivo está atualmente considerado "inativo" (parado)
  int16_t spo2;       // última leitura de saturação de oxigénio (%), 0 se não houver leitura nova
  int16_t hr_x10;     // última frequência cardíaca em bpm (nota: apesar do nome "_x10", é gravado o valor arredondado em bpm, não x10)
};

// Verificação em tempo de compilação: garante que a estrutura acima cabe
// no espaço reservado para cada registo do ring buffer. Se alguém
// adicionar um campo a mais e isto ultrapassar o limite, o build falha
// aqui em vez de corromper dados silenciosamente em runtime.
static_assert(sizeof(ImuPpgPayloadV1) <= QspiRingBuffer::kPayloadSize,
              "ImuPpgPayloadV1 must fit ring payload");

TaskHandle_t g_storageTaskHandle = nullptr;   // referência à task do FreeRTOS, para a poder gerir depois
volatile bool g_storageTaskRunning = false;   // flag simples de estado (não é lida noutro sítio atualmente)

// Converte um valor "long" (mais largo) para int16_t, cortando (saturando)
// nos limites em vez de dar overflow silencioso. Usado para guardar
// leituras de SpO2/HR nos campos int16_t do payload em segurança.
int16_t clampToI16(long v) {
  if (v > 32767L) return 32767;
  if (v < -32768L) return -32768;
  return static_cast<int16_t>(v);
}

// Corpo da tarefa em segundo plano que liga IMU + PPG ao armazenamento.
// Corre em loop infinito (como é normal numa task do FreeRTOS) até o
// dispositivo ser desligado.
void storageTask(void *arg) {
  (void)arg;
  g_storageTaskRunning = true;

  uint32_t lastImuTs = 0;
  uint32_t consumedSpo2Ts = 0;
  uint32_t consumedHrTs = 0;
  uint32_t pushed = 0;
  uint32_t pushFail = 0;
  uint32_t lastPrintMs = 0;

  Serial.println("[STOR] storage_task iniciada");

  while (true) {
    // Passo 1: tentar obter a amostra de IMU mais recente. Se ainda não
    // houver nenhuma (ex.: IMU acabou de arrancar), esperar um pouco e
    // tentar de novo — isto evita um loop "ocupado" a consumir CPU/energia.
    Imu::Sample imu = {};
    if (!Imu::getLatestSample(imu)) {
      vTaskDelay(pdMS_TO_TICKS(STORAGE_TASK_IDLE_MS));
      continue;
    }

    // Se o timestamp for igual ao da última vez, é a MESMA amostra que já
    // foi gravada (a task do IMU ainda não produziu uma nova) — ignorar
    // para não duplicar registos no ring buffer.
    if (imu.timestamp_ms == 0 || imu.timestamp_ms == lastImuTs) {
      vTaskDelay(pdMS_TO_TICKS(1));
      continue;
    }
    lastImuTs = imu.timestamp_ms;

    // Por omissão assume-se "sem leitura nova" de SpO2/HR (0). O PPG só
    // atualiza estes valores esporadicamente (ex.: 1x/minuto), por isso a
    // maioria das amostras de IMU não terá dados de PPG associados.
    int16_t spo2Out = 0;
    int16_t hrOutX10 = 0;

    Ppg::Metrics ppg = {};
    if (Ppg::getLatest(ppg)) {
      // "Consumir" a leitura de SpO2 apenas se for diferente da última já
      // gravada (identificada pelo seu próprio timestamp), para não
      // repetir o mesmo valor em várias amostras de IMU seguidas.
      if (ppg.spo2_valid && ppg.spo2_timestamp_ms != 0 &&
          ppg.spo2_timestamp_ms != consumedSpo2Ts) {
        spo2Out = clampToI16(ppg.spo2_value);
        consumedSpo2Ts = ppg.spo2_timestamp_ms;
      }

      // Mesma lógica para a frequência cardíaca (HR), arredondada ao bpm
      // mais próximo antes de gravar.
      if (ppg.hr_valid && ppg.hr_timestamp_ms != 0 &&
          ppg.hr_timestamp_ms != consumedHrTs) {
        const long hr10 = lroundf(ppg.hr_bpm);
        hrOutX10 = clampToI16(hr10);
        consumedHrTs = ppg.hr_timestamp_ms;
      }
    }

    // Passo 2: montar o registo combinado (IMU + PPG) que vai ser gravado.
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
    payload.hr_x10 = hrOutX10;

    // Preferir o relógio real (UTC, sincronizado por BLE) como timestamp
    // do registo, se já estiver disponível; caso contrário usar o
    // millis() interno do IMU (tempo desde o arranque) como recurso.
    uint32_t recTs = imu.timestamp_ms;
    const uint32_t nowUtc = Clock::nowUtc();
    if (nowUtc != 0) recTs = nowUtc;

    // Passo 3: gravar o registo no ring buffer da flash externa.
    if (QspiRingBuffer::push(STORAGE_REC_TYPE_IMU_PPG_V1,
                             reinterpret_cast<const uint8_t *>(&payload),
                             sizeof(payload),
                             recTs)) {
      pushed++;
      if (payload.spo2 != 0 || payload.hr_x10 != 0) {
        Serial.print("[STOR] PPG reg spo2=");
        Serial.print(payload.spo2);
        Serial.print(" hr=" );
        Serial.println(payload.hr_x10);
      }
    } else {
      pushFail++;
    }

    const uint32_t now = millis();
    if ((now - lastPrintMs) >= 1000) {
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

} // namespace

// ============================================================
// BOTÃO — leitura e deteção de long-press (premir demorado)
// ------------------------------------------------------------
// O botão é ativo-LOW: o pino lê LOW (0V) quando está premido, e HIGH
// (por causa da resistência de pull-up interna) quando está solto.
// ============================================================

// Bloqueia até o botão ser largado (voltar a HIGH), ou até passar
// timeoutMs (0 = esperar indefinidamente). Útil depois de confirmar um
// long-press, para não reagir várias vezes ao mesmo toque prolongado.
bool waitRelease(uint32_t timeoutMs = 0) {
  const uint32_t t0 = millis();
  while (digitalRead(BTN_PIN) == LOW) {
    if (timeoutMs != 0 && (millis() - t0) >= timeoutMs) {
      return false;
    }
    delay(5);
  }
  delay(30);  // pequena pausa extra para "assentar" o sinal (debounce de largada)
  return true;
}

// Verifica se o botão está premido "de forma estável": lê uma vez, espera
// DEBOUNCE_TIME (para ignorar ruído elétrico de contacto mecânico) e
// confirma que continua premido. Evita falsos positivos de toques curtos.
bool buttonPressedStable() {
  if (digitalRead(BTN_PIN) == LOW) {
    delay(DEBOUNCE_TIME);
    return digitalRead(BTN_PIN) == LOW;
  }
  return false;
}

#if DEBUG_SERIAL_WAKE
// *** DEBUG TEMPORÁRIO *** — ver explicação junto de DEBUG_SERIAL_WAKE.
// Lê linhas de texto disponíveis na porta série (sem bloquear) e devolve
// true se a última linha completa recebida for exatamente "cmd"
// (ignorando espaços/CR à volta). Usado como substituto do botão físico.
bool serialCommandReceived(const char *cmd) {
  static char buf[16];
  static uint8_t len = 0;
  bool matched = false;

  while (Serial.available() > 0) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (len > 0) {
        buf[len] = '\0';
        if (strcmp(buf, cmd) == 0) matched = true;
        len = 0;
      }
    } else if (len < sizeof(buf) - 1) {
      buf[len++] = c;
    }
  }
  return matched;
}
#endif

// Espera para ver se o botão é mantido premido durante LONG_PRESS_TIME
// (5 segundos) seguidos. Se for largado antes disso, devolve false
// (não foi um long-press, foi só um toque curto). Se aguentar os 5s,
// espera ainda que o utilizador largue o botão antes de confirmar,
// para não disparar a ação outra vez enquanto o dedo ainda lá está.
//
// *** DEBUG TEMPORÁRIO ***: com DEBUG_SERIAL_WAKE=1, escrever "WAKE" na
// porta série durante a espera também conta como long-press confirmado
// (substituto do botão partido). Ver DEBUG_SERIAL_WAKE acima.
bool waitForLongPress() {
#if DEBUG_SERIAL_WAKE
  if (serialCommandReceived("WAKE")) {
    Serial.println("[DEBUG] comando WAKE recebido -> a simular long-press");
    return true;
  }
#endif
  if (!buttonPressedStable()) return false;
  unsigned long start = millis();
  while (millis() - start < LONG_PRESS_TIME) {
    if (digitalRead(BTN_PIN) == HIGH) return false;
#if DEBUG_SERIAL_WAKE
    if (serialCommandReceived("WAKE")) {
      Serial.println("[DEBUG] comando WAKE recebido a meio -> a simular long-press");
      return true;
    }
#endif
  }
  // Confirma long-press apenas apos libertar o botao.
  waitRelease();
  return true;
}

// ============================================================
// SYSTEM OFF — via SoftDevice, com USB e LATCH tratados
// ------------------------------------------------------------
// "SYSTEM OFF" é o modo de consumo mais baixo do nRF52840: o chip
// desliga quase tudo e só volta a arrancar (como se fosse um reset)
// quando acontece o evento de "wake" configurado — neste caso, o botão
// a ser premido (nível LOW). Antes de entrar neste modo é preciso
// desligar/guardar em segurança tudo o que estava ativo (sensores,
// Bluetooth, dados pendentes em flash), senão pode haver perda de dados
// ou comportamento estranho no próximo arranque.
// ============================================================
void goToSleep() {
#if DEBUG_DISABLE_SLEEP
  Serial.println("[DEBUG] goToSleep() pedido, mas DEBUG_DISABLE_SLEEP=1 -> a ignorar (dispositivo continua ligado)");
  return;
#endif
  Serial.println("A desligar...");
  Serial.flush();
  isRunning = false;

  // Garante MAX30101 sem emissao antes do SYSTEM_OFF.
  // (Se o sensor cardíaco ficasse com o LED aceso, continuaria a gastar
  // corrente mesmo com o resto do chip "desligado".)
  Ppg::prepareForSystemOff();
  Ble::stopBroadcast();
  // Força a escrita imediata de quaisquer dados do ring buffer que ainda
  // só estivessem em memória, para não se perderem ao desligar.
  (void)QspiRingBuffer::sync();

  // Apaga LED e display
  digitalWrite(LED_BUILTIN, HIGH);   // OFF (ativo LOW)
  pinMode(OLED_RST_PIN, OUTPUT);
  digitalWrite(OLED_RST_PIN, LOW);

  // 1 — Desligar USB (impede wake imediato por VBUS)
  // TinyUSB detach removido para evitar bloqueio durante power-off.

  // 2 — Desabilitar wakes por eventos USB-power no SoftDevice
  // Eventos USB do SoftDevice nao sao alterados neste caminho minimo.

  // 3 — Limpar LATCH residual dos GPIOs
  // (Os pinos do nRF52 guardam um "latch" quando mudam de estado durante
  // certas transições de energia; se não for limpo, pode impedir o chip
  // de detetar corretamente o próximo evento de wake-up.)
  NRF_GPIO->LATCH = NRF_GPIO->LATCH;
  NRF_P1->LATCH   = NRF_P1->LATCH;

  // 4 — Garantir libertação do botão antes de armar SENSE

  // 5 — Configurar wake-up por LOW
  // "SENSE_LOW" diz ao hardware: "quando este pino ficar em nível baixo
  // (botão premido), gera um evento que acorda o chip do SYSTEM_OFF".
  nrf_gpio_cfg_input(BTN_PIN, NRF_GPIO_PIN_PULLUP);
  nrf_gpio_cfg_sense_input(BTN_PIN,
                           NRF_GPIO_PIN_PULLUP,
                           NRF_GPIO_PIN_SENSE_LOW);
  // Sem delay aqui para reduzir janela com tasks ainda ativas.

  // 6 — Entrar em SYSTEM_OFF via SoftDevice
  // (Tem de ser pedido através do SoftDevice — a pilha BLE da Nordic —
  // e não diretamente ao hardware, porque o SoftDevice também gere
  // energia/rádio internamente.)
  uint32_t rc = sd_power_system_off();
  (void)rc;

  // Fallback caso o SD não esteja ativo
  NRF_POWER->SYSTEMOFF = 1;

  // Se ainda estivermos aqui, SYSTEMOFF falhou/emulado.
  // Reinicia para evitar ficar preso e exigir reset fisico.
  NVIC_SystemReset();
}

// ============================================================
// DISPLAY — só inicializa depois do wake confirmado
// ------------------------------------------------------------
// Funções auxiliares para desenhar no ecrã OLED: mostrar logótipos no
// arranque e mensagens de texto simples (erros, hora/data).
// ============================================================

// Desenha um bitmap monocromático (ex.: um logótipo) centrado no ecrã,
// mantém-no visível durante "ms" milissegundos.
void showLogo(const uint8_t *bits, int16_t w, int16_t h, uint16_t ms) {
  display.fillScreen(COLOR_BLACK);
  int16_t x = (SCREEN_W - w) / 2;
  int16_t y = (SCREEN_H - h) / 2;
  display.drawXBitmap(x, y, bits, w, h, COLOR_WHITE);
  delay(ms);
}

// Sequência de arranque visual: liga o barramento SPI do ecrã, inicializa
// o driver do ecrã e mostra os três logótipos (IPCA, 2Ai, Intellicare)
// em sequência, 1.5s cada, antes de limpar o ecrã para uso normal.
void showReady() {
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

// ============================================================
// UI — mensagem simples no display (até duas linhas centradas)
// ============================================================
// Mostra até duas linhas de texto, centradas horizontalmente no ecrã.
// Usado tanto para mensagens de erro ("IMU ERRO") como para a hora/data.
// Se line2 for nullptr, mostra só uma linha, centrada verticalmente.
void uiMessage(const char *line1, const char *line2) {
  display.fillScreen(COLOR_BLACK);
  display.setTextColor(COLOR_WHITE);
  display.setTextSize(2);

  auto drawCentered = [&](const char *txt, int16_t y) {
    int16_t x1, y1; uint16_t w, h;
    display.getTextBounds(txt, 0, y, &x1, &y1, &w, &h);
    int16_t x = (SCREEN_W - (int16_t)w) / 2;
    if (x < 0) x = 0;
    display.setCursor(x, y);
    display.print(txt);
  };

  if (line2 == nullptr) {
    drawCentered(line1, 56);
  } else {
    drawCentered(line1, 44);
    drawCentered(line2, 70);
  }
}

// Mostra a hora e a data atuais no ecrã. Se o relógio ainda não estiver
// sincronizado (Clock::isValid() == false), mostra os textos genéricos
// "HORA"/"DATA" em vez de valores errados.
void showHourDateScreen() {
  char line1[16] = "HORA";
  char line2[16] = "DATA";
  if (Clock::isValid()) {
    (void)Clock::formatTime(line1, sizeof(line1));
    (void)Clock::formatDate(line2, sizeof(line2));
  }

  uiMessage(line1, line2);
}

// ============================================================
// FUNÇÕES DE INICIALIZAÇÃO — cada uma liga um subsistema/módulo
// ------------------------------------------------------------
// São todas chamadas, em sequência, dentro de setup() (ver mais abaixo).
// Cada uma delas mostra no ecrã um aviso de erro (2 segundos) se o
// respetivo módulo falhar a inicializar, mas o arranque continua na
// mesma para os módulos seguintes — para o dispositivo tentar funcionar
// parcialmente mesmo que um sensor específico falhe.
// ============================================================

// STORAGE — corre depois do long-press (USB CDC já enumerou)
// Inicializa o sistema de ficheiros interno (para a calibração do IMU e
// a chave AES) e regista no log se já existem esses dados guardados de
// uma sessão anterior.
void initStorage() {
  if (!Storage::begin()) return;

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

// IMU — inicializa o sensor de movimento, garante que está calibrado
// (calibra automaticamente se for a primeira vez) e arranca a task em
// segundo plano que vai continuamente lendo amostras.
void initImu() {
  if (!Imu::begin()) {
    uiMessage("IMU", "ERRO");
    delay(2000);
    return;
  }
  if (!Imu::ensureCalibrated()) {
    uiMessage("IMU", "ERRO");
    delay(2000);
    return;
  }

  if (!Imu::startTask()) {
    Serial.println("[IMU] nao foi possivel iniciar imu_task");
    uiMessage("IMU TASK", "ERRO");
    delay(2000);
    return;
  }
  Serial.println("[IMU] imu_task ativa");
}

// PPG — task única para SPO2 (1/min) + HR quando inatividade IMU
// Inicializa o sensor ótico e arranca a sua task, que decide sozinha
// quando medir SpO2 (periodicamente) e quando medir frequência cardíaca
// (aproveitando períodos em que o IMU deteta o utilizador parado, o que
// dá leituras de HR mais limpas por haver menos ruído de movimento).
void initPpg() {
  Serial.println("[PPG] initPpg(): inicio");
  if (!Ppg::begin()) {
    Serial.println("[PPG] init falhou");
    return;
  }

  if (!Ppg::startTask()) {
    Serial.println("[PPG] nao foi possivel iniciar ppg_task");
    return;
  }

  Serial.println("[PPG] ppg_task ativa");
}

// BLE — serviço, advertising e receção da AES key
// Liga a pilha Bluetooth, garante que existe uma chave AES (para cifrar
// dados sensíveis trocados com a app/telemóvel) e tenta sincronizar o
// relógio interno através da ligação BLE.
void initBle() {
  if (!Ble::begin()) {
    uiMessage("BLE", "ERRO");
    delay(2000);
    return;
  }
  Ble::ensureAesKey();
  Ble::ensureTimeSync();
}

// BLE DATA LINK (GATT-only)
// Liga o "canal de dados" BLE (advertising + serviço GATT) que permite a
// uma app externa ligar-se ao dispositivo e trocar dados/comandos.
void initBleDataLink() {
  if (!Ble::startBroadcast()) {
    Serial.println("[BLE] GATT-only start failed");
    return;
  }
  Serial.println("[BLE] GATT-only active");
}

// LORA — inicializacao EXPERIMENTAL do radio Wio-SX1262 (ver Lora.h para
// o aviso completo sobre pinos com confianca baixa). Deliberadamente
// tolerante a falhas: se o radio nao responder (pino errado), regista o
// erro e o resto do arranque continua normalmente — nada no resto do
// firmware depende de Lora::begin() ter sucesso.
void initLora() {
  if (!Lora::begin()) {
    Serial.println("[LORA] init falhou — a continuar sem radio LoRa (ver Lora.h, pinout ainda por confirmar)");
    return;
  }
  Serial.println("[LORA] radio ativo");
  // Envio de teste unico no arranque, so para validar deteccao +
  // transmissao numa mesma sessao — nao faz parte de nenhuma logica de
  // emergencia ainda.
  Lora::sendTest("CareWear LoRa test");
}

// QSPI RING BUFFER — inicializa o "livro de registo" na flash externa
// onde ficam guardadas as amostras de IMU/PPG (ver storageTask acima).
void initQspiRingBuffer() {
  if (!QspiRingBuffer::begin(true)) {
    Serial.println("[QSPIRB] init falhou");
    return;
  }

  Serial.print("[QSPIRB] capacidade slots: ");
  Serial.println(QspiRingBuffer::capacity());
  Serial.print("[QSPIRB] count atual:      ");
  Serial.println(QspiRingBuffer::count());

#if QSPI_RING_BUFFER_SELF_TEST
  if (!QspiRingBuffer::selfTest()) {
    Serial.println("[QSPIRB] self-test falhou");
  }
#endif
}

// Cria a task storageTask (definida no início do ficheiro) que faz a
// ponte entre o IMU/PPG e o ring buffer. TASK_PRIO_LOW porque gravar
// dados é menos urgente do que ler os sensores em tempo real.
void initStorageTask() {
#if STORAGE_TASK_ENABLE
  if (g_storageTaskHandle != nullptr) return;

  BaseType_t ok = xTaskCreate(
      storageTask,
      "storage_task",
      STORAGE_TASK_STACK_WORDS,
      nullptr,
      TASK_PRIO_LOW,
      &g_storageTaskHandle);

  if (ok != pdPASS) {
    g_storageTaskHandle = nullptr;
    Serial.println("[STOR] falha ao criar storage_task");
    return;
  }

  Serial.println("[STOR] storage_task ativa");
#endif
}

// ============================================================
// SETUP — chamado automaticamente UMA VEZ pelo Arduino ao arrancar
// ------------------------------------------------------------
// Nota importante sobre o ciclo de vida deste dispositivo: como ele usa
// SYSTEM_OFF (ver goToSleep) em vez de um "sleep" normal, cada vez que o
// utilizador prime o botão para ligar, o chip faz na verdade um arranque
// completo do zero — é por isso que quase toda a lógica de inicialização
// (sensores, BLE, storage) está aqui dentro do setup(), e não seria
// preciso um "wake handler" separado.
// ============================================================
void setup() {
  pinMode(BTN_PIN, INPUT_PULLUP);

  // LED onboard como heartbeat (XIAO nRF52840 é ativo LOW)
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);   // apagado por defeito

  Serial.begin(115200);
  delay(100);
  Serial.println("Acordou do System OFF");

  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);

  // Inicializa SoftDevice S140 — necessário para sd_power_*
  // Reserva 2 ligacoes perifericas para permitir provisioning e data link.
  // (O SoftDevice é a pilha de rádio/BLE da Nordic; tem de ser iniciado
  // logo no arranque porque outras partes do firmware, como o modo
  // SYSTEM_OFF, dependem de funções que só existem depois disto.)
  Bluefruit.begin(2, 0);
  Bluefruit.setName("Wearable");
  Serial.println("SoftDevice inicializado");

  // *** DEBUG TEMPORÁRIO *** (ver DEBUG_SERIAL_WAKE): se chegar o comando
  // "WAKE" pela série, avança logo para o arranque normal, sem esperar
  // pelo botão físico (que está partido). Deixa isRunning=true e cai
  // diretamente na sequência de boot mais abaixo.
  bool debugForcedWake = false;
#if DEBUG_SERIAL_WAKE
  Serial.println("[DEBUG] botao fisico indisponivel: escreve WAKE + Enter para ligar");
#endif
#if DEBUG_DISABLE_SLEEP
  // Com o "dormir" desativado (ver DEBUG_DISABLE_SLEEP), nao faz sentido
  // esperar pelo botao/WAKE aqui — arranca logo a fundo, exatamente como
  // se um WAKE tivesse chegado, para evitar qualquer caminho que
  // terminasse a chamar goToSleep() (que agora e' um no-op) e deixasse o
  // setup() sair sem nunca ter ligado nada.
  Serial.println("[DEBUG] DEBUG_DISABLE_SLEEP=1 -> a arrancar sempre, sem esperar por botao/WAKE");
  debugForcedWake = true;
#endif

#if !DEBUG_DISABLE_SLEEP
  // Anti-glitch: se o botão não estiver a ser premido (LOW) pouco depois
  // de o chip arrancar, assume-se que o "acordar" foi espúrio (ruído
  // elétrico, ligação USB, etc.) e volta-se a dormir passados 8s sem
  // confirmação, para poupar bateria. Todo este bloco fica desativado
  // quando DEBUG_DISABLE_SLEEP=1 (debugForcedWake já vem true de cima),
  // para nenhum caminho conseguir sair do setup() sem arrancar.
  const uint32_t waitPressStart = millis();
  while (digitalRead(BTN_PIN) == HIGH) {
#if DEBUG_SERIAL_WAKE
    if (serialCommandReceived("WAKE")) {
      Serial.println("[DEBUG] comando WAKE recebido -> a ligar sem botao fisico");
      debugForcedWake = true;
      break;
    }
#endif
    if ((millis() - waitPressStart) > 8000) {
      Serial.println("Aguardar long press para ligar -> dormir");
      goToSleep();
      return;
    }
    delay(5);
  }
#endif // !DEBUG_DISABLE_SLEEP

  // Botão está premido ao arrancar -> só liga mesmo se for um long-press
  // (5s), para evitar ligar por engano com um toque acidental curto.
  Serial.println("Botao pressionado ao acordar...");
  if (!debugForcedWake && !waitForLongPress()) {
    Serial.println("Botão pressionado ao acordar...");

    // Ramo morto (if (false)): código de arranque "reduzido" mantido
    // aqui como referência histórica, mas nunca executado — não apagar
    // sem confirmar que não é preciso para debug futuro.
    if (false) {
      Serial.println("Ligado após long press!");
      waitRelease();
      isRunning = true;
      showReady();
      initStorage();
      initImu();
      initPpg();
      initBle();
      initQspiRingBuffer();
      showHourDateScreen();
      return;
    }

    Serial.println("Pressão curta -> voltar a dormir");
    goToSleep();
    return;
  }

  // Arranque normal apos long-press validado: liga todos os subsistemas,
  // pela ordem abaixo. A ordem importa um pouco — por exemplo, o BLE e o
  // ring buffer são inicializados antes do IMU/storageTask, para que a
  // task de gravação já encontre tudo pronto quando começar a correr.
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
  Serial.println("[BOOT] step: initLora");
  initLora();
  Serial.println("[BOOT] step: showHourDateScreen");
  showHourDateScreen();
  Serial.println("[BOOT] step: setup done");
}

// ============================================================
// LOOP — chamado repetidamente pelo Arduino depois do setup()
// ------------------------------------------------------------
// Enquanto o dispositivo está ligado (isRunning == true), este ciclo:
//   1. Vigia o botão: se for premido, pausa o PPG (para não interferir
//      com a leitura durante os ~5s de verificação) e espera para ver
//      se é um long-press de desligar. Se não for, retoma o PPG.
//   2. Faz "piscar" o LED brevemente a cada ~1s, como sinal visual de
//      que o firmware está vivo e a correr (heartbeat).
//   3. Atualiza o ecrã com a hora/data uma vez por segundo.
// Note-se que os módulos IMU, PPG, BLE e a gravação em flash correm nas
// suas próprias tasks do FreeRTOS (ver storageTask, Imu::startTask,
// Ppg::startTask) — este loop() não faz a leitura dos sensores
// diretamente, serve apenas de "vigia" e interface com o utilizador.
// ============================================================
void loop() {
  static uint32_t lastUiMs = 0;

  if (isRunning) {
#if DEBUG_SERIAL_WAKE
    // *** DEBUG TEMPORÁRIO ***: comando "SLEEP" pela série substitui o
    // long-press físico para desligar, enquanto o botão não existir.
    if (serialCommandReceived("SLEEP")) {
      Serial.println("[DEBUG] comando SLEEP recebido -> a desligar sem botao fisico");
      goToSleep();
      return;
    }
#endif
    if (buttonPressedStable()) {
      Serial.println("Press�o detectada -> verificar 5 segundos...");
      // Suspende o PPG durante a verificação do long-press porque o
      // sensor cardíaco é sensível a movimento/vibração — não faz
      // sentido continuar a medir enquanto se aguarda a decisão do
      // utilizador de desligar ou não.
      Ppg::suspendForPowerCheck();
      if (waitForLongPress()) {
        goToSleep();
      } else {
        Ppg::resumeAfterPowerCheck();
      }
    }

    // Heartbeat: pulso curto a cada segundo
    digitalWrite(LED_BUILTIN, LOW);    // ON
    delay(50);
    digitalWrite(LED_BUILTIN, HIGH);   // OFF
    delay(950);

    const uint32_t nowMs = millis();
    if ((nowMs - lastUiMs) >= 1000) {
      lastUiMs = nowMs;
      showHourDateScreen();
    }

#if DEBUG_STACK_WATERMARKS
    // *** DIAGNOSTICO TEMPORARIO *** (ver DEBUG_STACK_WATERMARKS acima):
    // a cada 15s, imprime quanta stack cada task ainda tem por gastar no
    // pior caso observado ate agora. Valores altos e estaveis ao longo do
    // tempo indicam que o *_TASK_STACK_WORDS respetivo esta generoso e
    // pode ser reduzido; valores a aproximarem-se de 0 indicam perigo de
    // stack overflow e NAO devem ser reduzidos.
    static uint32_t lastStackLogMs = 0;
    if ((nowMs - lastStackLogMs) >= 15000) {
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





