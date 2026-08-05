// Logger.h
//
// Modulo de logging por macros, com niveis (ERROR/WARN/INFO/DEBUG) e tags
// por modulo (ex.: "PPG", "BLE", "IMU"), inspirado no estilo usado por
// frameworks profissionais de firmware (ESP-IDF: ESP_LOGE/LOGW/LOGI/LOGD;
// Zephyr: LOG_ERR/LOG_WRN/LOG_INF/LOG_DBG). Disponibiliza:
//
//   LOG_ERROR(tag, fmt, ...)
//   LOG_WARN (tag, fmt, ...)
//   LOG_INFO (tag, fmt, ...)
//   LOG_DEBUG(tag, fmt, ...)
//
// ------------------------------------------------------------------------
// PORQUE MACROS E NAO FUNCOES
// ------------------------------------------------------------------------
// Uma funcao normal (ex. "void logInfo(const char *tag, const char *fmt,
// ...)") teria sempre um custo em runtime mesmo quando o nivel de log
// estivesse desligado: os argumentos (incluindo a string de formato, que
// ocupa espaco em flash) teriam sempre de ser avaliados e empilhados para a
// chamada, so' para a funcao decidir la' dentro "afinal nao imprimo nada".
// Com macros de pre-processador, quando um nivel esta desligado (ver
// LOG_LEVEL abaixo) a macro correspondente expande para um literal no-op
// que NAO referencia os argumentos (nem sequer a string de formato) — ou
// seja, essa chamada desaparece completamente do codigo fonte antes do
// compilador sequer ver o resto da expressao, e a string de formato nunca
// chega a existir na secao .rodata do binario final. Isto importa a serio
// aqui: o nRF52840 desta placa tem 1MB de flash partilhado com o resto do
// firmware (IMU, PPG, BLE, LoRa, NFC, ring buffer QSPI, etc.), e um projeto
// com dezenas de LOG_DEBUG espalhados pelo codigo poderia facilmente somar
// varios KB de strings de debug que nunca aparecem num build de producao.
//
// ------------------------------------------------------------------------
// ##__VA_ARGS__ (extensao GCC) EM VEZ DE ALTERNATIVAS
// ------------------------------------------------------------------------
// As macros aceitam argumentos variadicos (estilo printf) para alem do
// "tag" e do "fmt": LOG_INFO("BLE", "ligado, mtu=%d", mtu). O problema
// classico do C/C++ e' quando a chamada NAO tem argumentos extra, so'
// tag+fmt (ex.: LOG_INFO("BLE", "ligado")) — a macro variadica ainda assim
// tenta expandir para algo como "printf(fmt, )", com uma virgula a mais
// antes de um argumento vazio, o que e' erro de compilacao em C++ standard
// (antes do C++20 remover essa restricao para "..."). A extensao "##" do
// GCC antes de __VA_ARGS__ resolve isto: quando __VA_ARGS__ esta vazio, o
// "##" engole a virgula que o precede, e a chamada fica "printf(fmt)" sem
// virgula a mais. O toolchain deste projeto (arm-none-eabi-gcc, via
// PlatformIO/framework-arduinoadafruitnrf52) suporta esta extensao, por
// isso foi preferida a alternativas mais verbosas (ex. macros separadas
// LOG_INFO_0/LOG_INFO_N consoante o numero de argumentos, ou exigir sempre
// pelo menos um argumento variadico "vazio" nas chamadas sem dados extra).
//
// ------------------------------------------------------------------------
// LIMITACOES / DECISOES NAO-OBVIAS A CONHECER ANTES DE USAR
// ------------------------------------------------------------------------
//   1) Chamadas a estas macros ANTES de Serial.begin() (em setup()) nao
//      produzem nenhuma saida visivel no monitor serie — exatamente a
//      mesma limitacao que ja existe hoje com Serial.println/Serial.printf
//      usados diretamente no resto do firmware (ver main.cpp). Este ficheiro
//      nao inicializa nem verifica o estado do Serial; assume-se que quem
//      chama ja o fez, tal como acontece hoje.
//   2) Quando um nivel esta desligado por LOG_LEVEL (ver abaixo), a macro
//      correspondente NAO avalia nenhum dos argumentos passados. Se algum
//      argumento variadico tiver efeitos secundarios (ex.
//      LOG_DEBUG("IMU", "amostra %d", contador++)), esse efeito secundario
//      DEIXA DE ACONTECER quando LOG_LEVEL for baixado — tal como no
//      ESP-IDF/Zephyr, nao passar expressoes com efeitos secundarios como
//      argumentos de log.
//   3) O parametro "fmt" tem de ser um literal de string (tal como em
//      printf normal usado com concatenacao adjacente de strings) — a
//      implementacao interna concatena-o com literais fixos (o prefixo
//      "[timestamp][NIVEL][TAG] " e o sufixo "\r\n"). Passar aqui uma
//      "const char *" que so' e' conhecida em runtime (nao um literal) nao
//      compila.
//   4) Este modulo e' NOVO e independente — NAO substitui automaticamente
//      nenhum dos Serial.println/Serial.printf ja existentes no resto do
//      firmware (main.cpp e todos os outros modulos). Fica disponivel para
//      adopcao incremental, modulo a modulo, a' medida que cada um for
//      atualizado — nao foi feito (nem pedido) um refactor em massa do
//      firmware existente para o adotar.
//   5) Portabilidade: hoje este projeto so' compila para nRF52840 (ver
//      platformio.ini, unico target real de firmware). O backend de saida
//      esta isolado numa unica macro interna (LOG_IMPL_, mais abaixo) que
//      chama Serial.printf — se um dia for preciso outro backend (ex. RTT,
//      outro MCU), basta alterar essa unica macro, sem tocar nas 4 macros
//      publicas nem em nenhum call-site espalhado pelo firmware.
//
// ------------------------------------------------------------------------
// COMO CONFIGURAR O NIVEL (LOG_LEVEL)
// ------------------------------------------------------------------------
// Definir LOG_LEVEL ANTES de incluir este header (ex. via "build_flags" de
// um ambiente do platformio.ini: "-DLOG_LEVEL=LOG_LEVEL_DEBUG" num
// ambiente de desenvolvimento, ou "-DLOG_LEVEL=LOG_LEVEL_WARN" num
// ambiente de producao/release, para poupar flash). Se nao for definido,
// o valor por omissao e' LOG_LEVEL_INFO (mostra ERROR+WARN+INFO, omite
// DEBUG). Cada macro so' gera codigo se o seu nivel for <= LOG_LEVEL
// configurado; os niveis acima ficam definidos como no-op (ver secao
// acima) — zero custo de flash/tempo em builds onde estao desligados, nao
// apenas "silenciosos em runtime".
#ifndef LOGGER_H_
#define LOGGER_H_

#include <Arduino.h>

// Niveis de log, em ordem CRESCENTE de verbosidade (ERROR e' o mais grave
// e o mais "silencioso" por omissao; DEBUG e' o mais verboso). Os valores
// numericos importam (usados nas comparacoes "#if LOG_LEVEL >= LOG_LEVEL_X"
// abaixo) — nao reordenar sem rever essas comparacoes.
#define LOG_LEVEL_ERROR 0
#define LOG_LEVEL_WARN  1
#define LOG_LEVEL_INFO  2
#define LOG_LEVEL_DEBUG 3

// Nivel efetivo deste build: por omissao LOG_LEVEL_INFO se ninguem o tiver
// definido antes (ex. via build_flags) de incluir este header.
#ifndef LOG_LEVEL
#define LOG_LEVEL LOG_LEVEL_INFO
#endif

// ------------------------------------------------------------------------
// LOG_IMPL_ — implementacao interna partilhada pelas 4 macros publicas.
// NAO usar diretamente fora deste ficheiro (o sufixo "_" assinala isso).
//
// Formato de cada linha impressa: "[<millis>][<NIVEL>][<TAG>] <mensagem>",
// seguido de "\r\n". Exemplo real:
//   [123456][INFO][BLE] ligado, mtu=185
//
// Embrulhada em "do { ... } while (0)": e' o padrao classico para macros
// que expandem para MAIS DO QUE UMA expressao simples (aqui, tecnicamente
// so' uma chamada, mas o padrao mantem-se por seguranca/consistencia com as
// macros publicas abaixo) — sem isto, uma macro usada dentro de um "if"
// sem chavetas (ex. "if (x) LOG_INFO(...); else outraCoisa();") podia
// partir a estrutura do if/else ou, com varias instrucoes dentro da macro,
// so' a primeira ficar dentro do "if". O "do { } while (0)" comporta-se
// como uma unica instrucao (exige e aceita o ";" final do call-site) e
// evita esse problema classico de macros em C/C++.
#define LOG_IMPL_(level_str, tag, fmt, ...)                                  \
  do {                                                                       \
    Serial.printf("[%lu][" level_str "][%s] " fmt "\r\n",                    \
                   (unsigned long)millis(), (tag), ##__VA_ARGS__);           \
  } while (0)

// ------------------------------------------------------------------------
// LOG_ERROR — nivel mais grave, sempre ligado a menos que LOG_LEVEL seja
// explicitamente configurado abaixo de LOG_LEVEL_ERROR (nao ha nivel mais
// baixo definido hoje, por isso na pratica esta sempre disponivel).
#if LOG_LEVEL >= LOG_LEVEL_ERROR
#define LOG_ERROR(tag, fmt, ...) LOG_IMPL_("ERROR", tag, fmt, ##__VA_ARGS__)
#else
// Nivel desligado: no-op que NAO referencia tag/fmt/argumentos — garante
// que a string de formato nem chega a ser vista pelo compilador nesta
// expressao, logo nao ocupa flash (ver explicacao no topo do ficheiro).
#define LOG_ERROR(tag, fmt, ...) ((void)0)
#endif

// ------------------------------------------------------------------------
// LOG_WARN
#if LOG_LEVEL >= LOG_LEVEL_WARN
#define LOG_WARN(tag, fmt, ...) LOG_IMPL_("WARN", tag, fmt, ##__VA_ARGS__)
#else
#define LOG_WARN(tag, fmt, ...) ((void)0)
#endif

// ------------------------------------------------------------------------
// LOG_INFO
#if LOG_LEVEL >= LOG_LEVEL_INFO
#define LOG_INFO(tag, fmt, ...) LOG_IMPL_("INFO", tag, fmt, ##__VA_ARGS__)
#else
#define LOG_INFO(tag, fmt, ...) ((void)0)
#endif

// ------------------------------------------------------------------------
// LOG_DEBUG — nivel mais verboso; o unico desligado por omissao (ver
// LOG_LEVEL_INFO como default acima), para nao gastar flash com mensagens
// de debug num build normal enquanto nao for pedido explicitamente.
#if LOG_LEVEL >= LOG_LEVEL_DEBUG
#define LOG_DEBUG(tag, fmt, ...) LOG_IMPL_("DEBUG", tag, fmt, ##__VA_ARGS__)
#else
#define LOG_DEBUG(tag, fmt, ...) ((void)0)
#endif

#endif // LOGGER_H_
