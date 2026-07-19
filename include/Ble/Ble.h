// ============================================================
// Ble.h - Modulo de comunicacao Bluetooth Low Energy (BLE)
// ============================================================
// Responsabilidade deste modulo:
//   - Criar e configurar o(s) servico(s) e characteristics GATT usados
//     pelo wearable para falar com a app no telemovel (emparelhamento
//     "provisioning", troca da chave AES, sincronizacao de hora/data,
//     e o "modo de dados" onde os registos de sensores (IMU/PPG) sao
//     enviados por notificacoes BLE em pacotes fragmentados).
//   - Controlar quando o dispositivo esta em "advertising" (a anunciar-se
//     para poder ser encontrado e ligado por um telemovel).
//
// Fluxo tipico de utilizacao (ver main.cpp):
//   1) Bluefruit.begin() é chamado pelo firmware principal.
//   2) Ble::begin() cria os servicos/characteristics e arranca o
//      advertising de "provisioning" (ligacao aberta, sem dados ainda).
//   3) Ble::ensureAesKey() bloqueia a app ate existir uma chave AES
//      valida (ou porque ja estava guardada em flash, ou porque acabou
//      de chegar via BLE, escrita pela app no telemovel).
//   4) Ble::ensureTimeSync() bloqueia ate a app enviar a hora atual via
//      a characteristic padrao "Current Time" (UUID 0x2A2B do Bluetooth
//      SIG), para o relogio interno (Clock) ficar sincronizado.
//   5) Ble::startBroadcast() / stopBroadcast() ligam/desligam o
//      advertising usado no "modo de dados", onde os sensores sao
//      transmitidos continuamente para o telemovel via notify().
//
// Nota sobre seguranca: as characteristics usam SECMODE_OPEN (sem
// pairing/bonding BLE nativo) — a seguranca dos dados sensiveis (por
// exemplo a chave AES) é garantida pela logica da aplicacao (guardada
// uma unica vez em flash) e nao pelo emparelhamento BLE em si.
// Desde 2026-07-07 o proprio conteudo dos registos do "modo de dados"
// tambem vai cifrado (AES-CTR, ver encryptRecord() em Ble.cpp) — antes ia
// em texto simples apesar de a chave AES ja ser trocada/guardada.
#ifndef BLE_H_
#define BLE_H_

#include <Arduino.h>

namespace Ble {

// Inicializa o modulo BLE: cria o servico principal do wearable e as
// suas characteristics (chave AES, controlo/dados/estado do "dump" de
// sensores), cria o servico padrao "Current Time", e arranca o
// advertising de "provisioning" (para o telemovel conseguir encontrar
// e ligar-se ao dispositivo pela primeira vez).
// Deve ser chamada uma unica vez, depois de Bluefruit.begin() (feito em
// main.cpp). Cria tambem a tarefa FreeRTOS responsavel por transmitir os
// registos de sensores em modo de dados (gattDumpTask).
// Retorna true (a inicializacao atual nao tem caminho de falha critico).
bool begin();

// Garante que existe uma chave AES valida disponivel para o resto do
// firmware.
// Comportamento: se ja existir uma chave guardada na flash (Storage),
// carrega-a e retorna de imediato. Caso contrario, BLOQUEIA a execucao
// (loop de espera) ate a app no telemovel escrever a chave na
// characteristic dedicada via BLE. Durante a espera atualiza o ecra
// (display) com as mensagens "Receber" / "AES key" e, ao terminar,
// "AES key" / "recebida".
// Chamar durante o arranque, antes de qualquer funcionalidade que
// precise de cifrar/decifrar dados com AES.
bool ensureAesKey();

// Garante que o relogio interno (Clock) fica sincronizado com a hora
// real, recebida da app via a characteristic BLE padrao "Current Time"
// (UUID 0x2A2B do Bluetooth SIG).
// Comportamento: invalida qualquer timestamp anterior e BLOQUEIA a
// execucao ate chegar um valor valido via BLE. Depois de receber a
// hora, fecha as ligacoes de "provisioning" e para o advertising
// desse modo, preparando a transicao para o "modo de dados".
// Deve ser chamada em cada arranque do dispositivo, tipicamente a
// seguir a um "long-press" do utilizador (ver main.cpp), porque o
// relogio interno nao tem bateria/RTC persistente fiavel.
bool ensureTimeSync();

// Devolve o ultimo timestamp UTC (epoch, segundos desde 1970) recebido
// e validado via a characteristic "Current Time". So deve ser
// considerado valido depois de ensureTimeSync() ter retornado ou de
// hasTimestamp() confirmar que ja chegou um valor.
uint32_t timestamp();

// Indica se ja foi recebido (e validado) algum timestamp via BLE desde
// o ultimo ensureTimeSync(). Util para verificar o estado sem bloquear.
bool hasTimestamp();

// Ativa o advertising "conectavel" usado no modo de dados por GATT
// (o dispositivo anuncia-se para o telemovel se ligar e comecar a
// receber os dados dos sensores via notificacoes). Ao ligar, a
// transmissao dos registos arranca automaticamente (ver
// periphConnectCallback no .cpp).
// Os nomes "startBroadcast"/"stopBroadcast" sao mantidos por
// compatibilidade com o codigo existente em main.cpp, mesmo nao
// havendo "broadcast" classico (advertising nao-conectavel) neste modo.
// Retorna true se o advertising arrancou com sucesso.
bool startBroadcast();

// Para o advertising do modo de dados e sinaliza a tarefa de "dump" de
// sensores para parar de transmitir (ver gattDumpTask no .cpp).
void stopBroadcast();

// Indica se o modo de dados esta ativo, isto é, se o advertising do
// modo de dados foi pedido (startBroadcast chamado) E o Bluefruit
// confirma que o advertising esta mesmo a correr.
bool isBroadcastActive();

// *** DIAGNOSTICO TEMPORARIO (otimizacao de RAM) ***
// Devolve a menor quantidade de stack livre (em palavras de 32 bits) que
// a gattDumpTask alguma vez teve desde que arrancou. Serve para decidir,
// com dados reais, se kGattDumpTaskStackWords pode ser reduzido com
// seguranca. Devolve 0 se a task ainda nao estiver a correr.
uint32_t dumpTaskStackHighWaterMarkWords();

// Tipos de alerta de emergencia enviados via notifyEmergencyAlert().
// Mantidos aqui (em vez de dentro do modulo Emergency) para a app/bridge
// poder incluir este header sem depender do resto da logica de deteccao.
enum EmergencyAlertType : uint8_t {
  kEmergencyAlertSosManual   = 1,  // Gesto SOS manual (cliques) confirmado.
  kEmergencyAlertFallInactivity = 2,  // Queda + inatividade prolongada sem resposta.
};

// Envia (via write local + notify, se ligado) um alerta de emergencia
// pela characteristic dedicada 'emergencyAlertChar'. 'alertType' identifica
// a causa (ver EmergencyAlertType) e 'timestampUtc' e o instante do evento
// (Clock::nowUtc(), ou 0 se o relogio ainda nao estiver sincronizado).
// Nao bloqueia nem falha de forma critica: se nao houver ligacao BLE ativa
// no momento, o valor fica apenas escrito na characteristic (disponivel
// para leitura numa ligacao futura) — o canal LoRa (ver Lora.h) e' o
// caminho pensado para cobrir esse caso sem depender de um telemovel por
// perto. Deve ser chamada pelo modulo Emergency quando deteta um gesto SOS
// confirmado ou uma queda com inatividade prolongada.
void notifyEmergencyAlert(uint8_t alertType, uint32_t timestampUtc);

// Publica o nivel de bateria atual (0-100%) na Battery Service BLE padrao
// do Bluetooth SIG (servico 0x180F / characteristic "Battery Level"
// 0x2A19), usando a classe BLEBas ja fornecida pela biblioteca Bluefruit
// (ver services/BLEBas.h no pacote framework-arduinoadafruitnrf52) em vez
// de uma characteristic custom — usar o UUID padrao permite que qualquer
// app/ferramenta BLE genérica (ex.: nRF Connect) reconheca e mostre o
// nivel de bateria sem precisar de conhecer o protocolo proprio do
// wearable. Escreve sempre o valor (fica disponivel por leitura mesmo sem
// ligacao ativa) e tambem notifica se houver uma ligacao BLE no momento.
// 'percent' deve vir de Battery::sample()/Battery::latest() (ver
// Battery.h) — este modulo nao faz a leitura do ADC, so publica o valor
// que lhe e' passado. Chamar apos Ble::begin() (que e' quem cria o
// servico); ver main.cpp para o ponto onde e' chamada periodicamente
// (nao a cada iteracao do loop — o nivel de bateria varia devagar).
void updateBatteryLevel(uint8_t percent);

} // namespace Ble

#endif
