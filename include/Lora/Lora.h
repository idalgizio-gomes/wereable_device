// Lora.h
//
// Modulo responsavel pela antena LoRa Wio-SX1262, presente na placa custom
// deste projeto (ver esquematico "Pulseira_Esquematico.pdf", fornecido
// pelo utilizador — NAO e um kit de terceiros, e um design proprio a volta
// da XIAO nRF52840 Sense Plus).
//
// *** ESTADO: EXPERIMENTAL / PINOUT PARCIALMENTE POR CONFIRMAR ***
// Ligacoes confirmadas com confianca alta no esquematico:
//   VCC   -> 3.3V         GND    -> GND
//   DIO1  -> D7            RF_SW  -> AD2 (controlo da antena, via R13 10k)
//   SPI_MISO/MOSI/SCK -> barramento SPI partilhado (MISO/MOSI/SCK)
//   BUSY  -> D8
// Ligacoes com confianca BAIXA (texto demasiado pequeno no esquematico
// para ler com certeza, apesar de varias tentativas com recortes):
//   NRST e SPI_NSS (chip-select) — assume-se, como hipotese mais provavel
//   eletricamente (o SX1262 precisa de framing por CS para responder por
//   SPI, por isso NSS quase de certeza liga a um pino do MCU, nao a GND
//   fixo), que SPI_NSS -> AD3 e que NRST fica sem controlo direto do
//   firmware (RADIOLIB_NC), assumindo um pull-up/circuito de reset
//   passivo no proprio modulo — padrao comum em breakouts LoRa simples.
//
// *** SEGURANCA DESTA HIPOTESE ***: se AD3 nao for mesmo o NSS correto,
// o pior cenario e o radio nunca responder (begin() falha de forma
// detetada e registada em log) — nao ha risco de dano fisico, porque
// nenhum destes pinos e' de alimentacao e todos ja estavam configurados
// como GPIO de proposito geral. begin() e chamado de forma NAO bloqueante
// e o resultado e sempre reportado; o resto do firmware nunca depende de
// o LoRa ter inicializado com sucesso.
//
// Este modulo nao esta (ainda) ligado a nenhuma logica de emergencia —
// serve apenas para validar a deteccao do chip e uma transmissao de
// teste. Ver PROJECT_STATUS.md para o desenho completo da deteccao de
// emergencia (gesto SOS, queda+inatividade, notificacao dupla BLE+LoRa).

#ifndef LORA_H_
#define LORA_H_

#include <Arduino.h>

namespace Lora {

// Inicializa o radio SX1262 (SPI + parametros LoRa: 868MHz, banda ISM
// europeia — Portugal). Deve ser chamada uma unica vez no arranque.
// Nao bloqueia a inicializacao do resto do sistema em caso de falha:
// devolve false e regista o codigo de erro do RadioLib no Serial, mas
// nao trava o firmware nem impede os restantes modulos de arrancarem.
bool begin();

// Indica se o radio foi inicializado com sucesso (begin() == true) e
// esta pronto a transmitir. Util para o resto do firmware verificar
// antes de tentar enviar algo, sem repetir a logica de erro.
bool isReady();

// Envia uma mensagem de teste curta (texto simples, sem cifra) para
// validar que o radio consegue transmitir. So funciona se isReady()
// for true. Devolve true se a transmissao foi aceite pelo radio (nao
// confirma receção do lado oposto — LoRa ponto-a-ponto simples nao tem
// handshake nesta fase experimental).
bool sendTest(const char *message);

} // namespace Lora

#endif
