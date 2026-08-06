// Nfc.h
//
// Modulo preparatorio para o periferico NFC-A (NFCT) nativo do nRF52840,
// disponivel nos pinos P0.09/P0.10 (NFC1/NFC2) do SoC — partilhados com
// GPIO de uso geral. Usar estes pinos como NFC exige alterar
// UICR.NFCPINS (no core Adafruit/Arduino) para os retirar do modo GPIO,
// operacao que NAO E reversivel por software (fica assim ate o UICR ser
// reprogramado) — ver aviso completo abaixo.
//
// *** ESTADO: HARDWARE CONFIRMADO (2026-08-06) — DRIVER AINDA POR ESCREVER ***
// Atualizacao 2026-08-06: a utilizadora forneceu o esquematico interno do
// proprio modulo XIAO nRF52840 Sense Plus (Seeed), confirmando as tres
// duvidas que este ficheiro tinha antes:
//   (a) EXISTE antena NFC fisica ligada a P0.09/NFC1 e P0.10/NFC2, atraves
//       de uma rede de adaptacao ate ao conector ANT1 — soldada de fabrica
//       dentro do proprio modulo XIAO, nao algo que a placa custom
//       "Pulseira" tenha de adicionar;
//   (b) os pinos NFC1/NFC2 sao, por isso, internos ao modulo XIAO — nao
//       aparecem na lista de pinos castellated expostos para a placa
//       custom os reaproveitar, o que resolve tambem;
//   (c) nao ha reaproveitamento como GPIO de outra funcao da placa —
//       precisamente porque o modulo nao os expoe para fora.
// Isto substitui a incerteza anterior (registada em baixo, para historico)
// por uma confirmacao vinda de fonte primaria (esquematico do fabricante),
// nao de uma suposicao. Falta ainda, antes de begin() poder inicializar
// hardware real:
//   - decidir e documentar o desenho final (ja fechado em PROJECT_STATUS.md,
//     seccao NFC 2026-07-31: handover/pairing BLE via NDEF minimo, NUNCA
//     dados clinicos/PII no NDEF — NFC-003/NFC-007 em SECURITY_STATUS.md);
//   - escrever o driver real, incluindo a alteracao a UICR.NFCPINS — que
//     e' IRREVERSIVEL por software (fica assim ate o UICR ser reprogramado
//     de fabrica) — por isso begin() abaixo CONTINUA, por agora, a nao
//     tocar em UICR.NFCPINS nem em qualquer registo do periferico NFCT,
//     ate essa alteracao ser feita conscientemente, nao por defeito.
//
// --- Texto original (2026-07-xx), mantido para historico do que estava
// por confirmar antes desta atualizacao ---
// Ao contrario do modulo Lora (onde a existencia da antena Wio-SX1262 na
// placa "Pulseira" ja foi confirmada pelo utilizador, faltando so afinar
// o pinout exato), para o NFC nao havia, ate 2026-08-06, NENHUMA
// confirmacao de que exista uma antena fisica ligada a P0.09/P0.10, de
// que esses pinos estejam expostos nesta variante, ou de que nao estejam
// a ser usados como GPIO por outra funcao da placa (ex.: os botões
// BT1/BT2). As tres duvidas ficaram resolvidas pela atualizacao acima.
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
// NFC e regista o resultado no Serial. Devolve sempre false por agora —
// NAO por a antena estar por confirmar (esta' confirmada, ver aviso
// acima), mas porque ativar UICR.NFCPINS e' uma alteracao IRREVERSIVEL
// por software, que deve ser feita conscientemente ao escrever o driver
// real, nao como efeito colateral de reler este ficheiro. Nao bloqueia
// nem atrasa o resto do arranque em caso algum: o resto do firmware
// (BLE/IMU/PPG/storage/emergencia) nunca deve depender de Nfc::begin()
// ter sucesso.
bool begin();

// Placeholder para o trabalho periodico do NFC (ex.: deteccao de campo
// externo para o caso de uso tap-to-pair). Nao faz nada enquanto begin()
// nao passar a inicializar hardware real. Chamada e mantida ja no
// loop principal para fixar o ponto de integracao (padrao dos outros
// modulos deste firmware, ex. Emergency::update()).
void update();

// Indica se o NFC foi inicializado com sucesso e esta pronto a
// responder a um leitor. Enquanto o driver real nao for escrito, devolve
// sempre false.
bool isReady();

} // namespace Nfc

#endif
