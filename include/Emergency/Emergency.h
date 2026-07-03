// Emergency.h
//
// Modulo de deteção de emergência do CareWear. Implementa as duas formas
// de alerta desenhadas com o utilizador (ver PROJECT_STATUS.md, secção
// "Deteção de emergência"):
//
//   1) SOS manual: o utilizador carrega várias vezes seguidas no botão
//      físico (BTN_PIN) dentro de uma janela de tempo curta ("gesto de
//      cliques"). Depois de detetado, NÃO dispara de imediato — espera um
//      período de confirmação (editável) antes de enviar o alerta, para
//      dar tempo a cancelar (um novo clique durante essa espera cancela).
//   2) Deteção automática: queda (freefall, já detetada pelo módulo Imu)
//      seguida de inatividade sustentada sem o utilizador voltar a
//      mexer-se, durante um período configurável (por omissão 60s).
//
// Quando qualquer uma das duas condições é confirmada, o módulo despacha
// o alerta pelos dois canais já decididos com o utilizador: BLE (via
// Ble::notifyEmergencyAlert(), para quem tiver a app/bridge por perto) e
// LoRa (via Lora::sendTest(), para cobrir o caso de não haver telemóvel
// por perto) — este segundo canal só funciona depois de o pinout real do
// rádio LoRa desta placa estar confirmado (ver Lora.h); até lá,
// Lora::isReady() devolve false e o envio por LoRa é simplesmente omitido,
// sem bloquear o resto do alerta.
//
// IMPORTANTE — o que este módulo NÃO faz (decisões fora do meu alcance):
// não envia SMS/email/push a contactos reais (isso pertence ao bridge,
// que precisaria de credenciais de um provedor como o Twilio, ainda por
// decidir/configurar pelo utilizador) — este módulo só entrega o alerta
// ao BLE/LoRa; é responsabilidade de uma camada externa (bridge/app) usar
// esse alerta para notificar pessoas reais.
//
// Este módulo ainda não foi testado em hardware real (ver bloqueio de
// deteção USB em PROJECT_STATUS.md) — a lógica foi escrita com cuidado
// para ser não-bloqueante e segura por omissão, mas os valores por
// omissão (janelas de tempo, número de cliques) devem ser validados
// assim que a placa voltar a estar acessível.

#ifndef EMERGENCY_H_
#define EMERGENCY_H_

#include <Arduino.h>

namespace Emergency {

// Parâmetros configuráveis do gesto SOS manual e da deteção automática.
// Todos têm valores por omissão razoáveis mas são pensados para poderem
// vir a ser ajustados (ex.: por um ecrã de configuração futuro, ou pela
// app via uma characteristic BLE dedicada — ainda não implementada).
struct Config {
  // Número de cliques do botão físico necessários para armar o SOS.
  uint8_t sosClickCount = 3;
  // Janela de tempo (ms) dentro da qual os cliques têm de acontecer para
  // contarem para o mesmo gesto. Se passar mais tempo que isto entre dois
  // cliques, a contagem recomeça do zero.
  uint32_t sosClickWindowMs = 1200;
  // Tempo de confirmação (ms) depois de atingir sosClickCount, antes de o
  // alerta ser mesmo enviado. Um novo clique durante esta janela cancela
  // o SOS pendente (decisão de implementação: permite ao utilizador
  // desfazer um gesto acidental repetindo o clique).
  uint32_t sosConfirmDelayMs = 2500;
  // Tempo (ms) de inatividade sustentada, a contar do instante da queda,
  // que tem de decorrer sem o utilizador voltar a mexer-se para o alerta
  // automático de queda ser disparado. Documentado como "60s" no desenho
  // original com o utilizador, mas mantido editável aqui.
  uint32_t fallInactivityTimeoutMs = 60000;
};

// Inicializa o módulo com a configuração por omissão (ver struct Config).
// 'buttonPin' é o pino do botão físico a observar para o gesto SOS (o
// mesmo BTN_PIN usado em main.cpp para o long-press de ligar/desligar;
// passado como parâmetro em vez de redefinido aqui para não haver duas
// fontes de verdade sobre qual é o pino do botão). Configura o pino como
// INPUT_PULLUP (idempotente — seguro mesmo que main.cpp já o tenha
// feito). Deve ser chamada uma vez no arranque, depois de Imu::startTask()
// e de Ble::begin()/Ble::startBroadcast(), porque update() depende de
// ambos.
void begin(uint8_t buttonPin);

// Substitui a configuração atual (cliques/janelas/timeout) por 'cfg'.
// Pode ser chamada a qualquer momento, incluindo depois de begin().
void setConfig(const Config &cfg);

// Devolve a configuração atualmente em vigor.
const Config &config();

// Deve ser chamada a cada iteração do loop principal (main.cpp). Lê o
// estado atual do botão físico (BTN_PIN) e a última amostra do IMU (via
// Imu::getLatestSample()) para avançar as duas máquinas de estado (SOS
// manual e queda+inatividade). Não bloqueia nunca — todas as esperas são
// feitas por comparação de millis(), nunca por delay().
void update();

// Força o disparo imediato de um SOS manual, ignorando a contagem de
// cliques e a janela de confirmação — usado pelo bypass de série
// enquanto o botão físico estiver por confirmar/testar (ver
// DEBUG_SERIAL_WAKE em main.cpp), de forma semelhante ao comando WAKE/
// SLEEP já existente. Não deve ser exposto num build de produção sem
// alguma forma de autenticação, à semelhança dos outros comandos DEBUG_*.
void triggerTestAlert();

} // namespace Emergency

#endif
