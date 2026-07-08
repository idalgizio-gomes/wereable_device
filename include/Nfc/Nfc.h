// Nfc.h
//
// Modulo preparatorio para o periferico NFC-A (NFCT) nativo do nRF52840,
// disponivel nos pinos P0.09/P0.10 (NFC1/NFC2) do SoC — partilhados com
// GPIO de uso geral. Usar estes pinos como NFC exige alterar
// UICR.NFCPINS (no core Adafruit/Arduino) para os retirar do modo GPIO,
// operacao que NAO E reversivel por software (fica assim ate o UICR ser
// reprogramado) — ver aviso completo abaixo.
//
// *** ESTADO: PREPARACAO — SEM CONFIRMACAO DE HARDWARE ***
// Ao contrario do modulo Lora (onde a existencia da antena Wio-SX1262 na
// placa "Pulseira" ja foi confirmada pelo utilizador, faltando so afinar
// o pinout exato), para o NFC nao ha, ate esta execucao, NENHUMA
// confirmacao de que:
//   (a) existe uma antena NFC fisica ligada a P0.09/P0.10 no esquematico
//       custom desta placa;
//   (b) esses pinos estao sequer expostos/acessiveis nesta variante
//       (Seeed XIAO nRF52840 Sense Plus) — o fabricante indica que a
//       "Sense Plus" expõe os pinos NFC1/NFC2 de forma mais acessivel do
//       que a "Sense" original, mas isso NAO confirma que o design custom
//       desta placa os liga a alguma coisa;
//   (c) esses pinos nao estao a ser usados como GPIO por outra função da
//       placa (ex.: os botões BT1/BT2 do esquematico, ligados a AD0/AD1 —
//       ainda nao confirmado se coincidem com P0.09/P0.10).
// Por isso, e seguindo a mesma logica "nao escrever driver as cegas" ja
// aplicada ao LoRa e ao GPS neste projeto: begin() abaixo NAO toca em
// UICR.NFCPINS nem em qualquer registo do periferico NFCT. Serve so para
// fixar a forma da API e o ponto de integracao em main.cpp, para quando
// a antena for confirmada. Perguntas em aberto para o utilizador e
// proposta de desenho completa em PROJECT_STATUS.md, seccao "NFC".
//
// Caso de uso alvo (a validar com o utilizador, ver PROJECT_STATUS.md):
// usar o NFC apenas para iniciar/emparelhar o BLE por toque
// ("tap-to-pair" / handover Out-Of-Band) e/ou identificar o dispositivo —
// NUNCA para transportar dados clinicos ou PII. Esta decisao coordena com
// a rotina de seguranca NFC deste projeto.

#ifndef NFC_H_
#define NFC_H_

#include <Arduino.h>

namespace Nfc {

// Verifica (sem alterar nada) se ha condicoes para tentar inicializar o
// NFC e regista o resultado no Serial. Nesta fase de preparacao devolve
// sempre false — nao ativa UICR.NFCPINS nem configura o periferico NFCT,
// porque a existencia da antena ainda nao foi confirmada (ver aviso
// acima e PROJECT_STATUS.md). Nao bloqueia nem atrasa o resto do
// arranque em caso algum: o resto do firmware (BLE/IMU/PPG/storage/
// emergencia) nunca deve depender de Nfc::begin() ter sucesso.
bool begin();

// Placeholder para o trabalho periodico do NFC (ex.: deteccao de campo
// externo para o caso de uso tap-to-pair). Nao faz nada enquanto begin()
// nao passar a inicializar hardware real. Chamada e mantida ja no
// loop principal para fixar o ponto de integracao (padrao dos outros
// modulos deste firmware, ex. Emergency::update()).
void update();

// Indica se o NFC foi inicializado com sucesso e esta pronto a
// responder a um leitor. Enquanto o hardware nao for confirmado, devolve
// sempre false.
bool isReady();

} // namespace Nfc

#endif
