// Battery.h
//
// Modulo responsavel por estimar o nivel de bateria (Li-Po de 1 celula) do
// wearable, lido via ADC no pino dedicado da placa Seeed XIAO nRF52840
// Sense Plus, e disponibilizado ao resto do firmware (ver Ble.cpp, que o
// publica na Battery Service BLE padrao 0x180F/0x2A19).
//
// ------------------------------------------------------------------------
// PROVENIENCIA DO PINOUT (2026-07-19) — como foi confirmado, nao adivinhado
// ------------------------------------------------------------------------
// A placa alvo deste projeto ("seeed-xiao-afruitnrf52-nrf52840-sense-plus",
// ver platformio.ini) usa o BSP Adafruit/Seeed cujo variant esta instalado
// localmente em:
//   C:\Users\<user>\.platformio\packages\framework-arduinoadafruitnrf52\
//     variants\Seeed_XIAO_nRF52840_Sense_Plus\variant.h
//     variants\Seeed_XIAO_nRF52840_Sense_Plus\variant.cpp
// Esse variant.h (fonte de verdade MAIS forte que qualquer forum/wiki,
// porque e o codigo que realmente compila para esta placa) define:
//   #define VBAT_ENABLE  (14)   // Output LOW to enable reading of the BAT voltage.
//   #define PIN_VBAT     (35)   // Read the BAT voltage. (mapeia para P0.31,
//                                // confirmado em variant.cpp: "D18 is P0.31 (VBAT)")
//   #define ADC_RESOLUTION (12)
// e variant.cpp mostra que initVariant() (chamado automaticamente pelo core
// Arduino ANTES do setup() do utilizador) ja poe VBAT_ENABLE a OUTPUT HIGH
// por omissao — ou seja, o percurso de leitura da bateria vem DESATIVADO
// por omissao de fabrica, e so deve ser ativado (LOW) momentaneamente para
// ler, replicando esse mesmo padrao aqui (ver sample() em Battery.cpp).
//
// Este ficheiro/variant local confirma tambem, via comentarios com URL,
// as duas fontes publicas usadas para validar o resto do desenho:
//   https://wiki.seeedstudio.com/XIAO_BLE/ (cobre explicitamente a Sense
//     Plus na mesma pagina que a Sense/Plus/base — sem distincao de
//     circuito de deteccao de bateria entre variantes)
//   https://wiki.seeedstudio.com/battery_charging_considerations/
//     (aviso de seguranca: nao deixar VBAT_ENABLE/P0.14 em HIGH durante o
//     carregamento — risco de queimar o pino P0.31; e a indicacao de que o
//     divisor resistivo da XIAO e "aproximadamente 1/3", sem publicar os
//     valores exatos das resistencias)
//
// O padrao de leitura ADC (referencia AR_INTERNAL_3_0, resolucao 12 bits,
// constante de mV/LSB) segue o exemplo OFICIAL da Adafruit incluido no
// mesmo pacote local:
//   .../Bluefruit52Lib/examples/Hardware/adc_vbat/adc_vbat.ino
// (o divisor resistivo desse exemplo é especifico das placas Feather/
// CircuitPlayground — NAO se aplica aqui — so a parte "ADC bruto -> mV" é
// reaproveitada).
//
// ------------------------------------------------------------------------
// O QUE FICA POR VALIDAR EM HARDWARE REAL (nao foi possivel confirmar
// nesta sessao — a placa fisica estava ocupada com um teste de LoRa)
// ------------------------------------------------------------------------
//   1) O RATIO EXATO do divisor resistivo da Sense Plus. Usa-se aqui 3.0
//      (i.e. "cerca de 1/3") por ser o unico valor documentado
//      publicamente — NAO os valores exatos das resistencias (Seeed nunca
//      os publicou para esta variante). Validar comparando o valor
//      reportado por Battery::sample() com um multimetro real na bateria,
//      em pelo menos 3 niveis de carga distintos (ex.: bateria cheia,
//      ~50%, quase vazia) e, se necessario, ajustar kBatteryDividerRatio
//      em Battery.cpp.
//   2) O tempo de assentamento (settle time) apos ativar VBAT_ENABLE antes
//      de ler o ADC — kAdcSettleDelayMs em Battery.cpp usa uma margem
//      conservadora (10ms) por nao haver um valor oficial documentado para
//      esta variante; pode ser afinado com dados reais.
//   3) A curva de conversao tensao->percentagem (voltageToPercent() em
//      Battery.cpp) e uma aproximacao generica de descarga de Li-Po de 1
//      celula (valores de referencia amplamente usados na industria/
//      comunidade, nao medidos na bateria especifica deste projeto) — da
//      apenas uma estimativa aproximada, nunca um valor de precisao
//      "fuel-gauge".
#ifndef BATTERY_H_
#define BATTERY_H_

#include <Arduino.h>

namespace Battery {

// Resultado de uma leitura de bateria num dado instante.
struct Reading {
  uint16_t raw_adc;      // valor bruto do ADC (0-4095 @ 12 bits), antes de qualquer conversao.
  uint16_t voltage_mv;   // tensao estimada da bateria (ja compensada pelo divisor), em milivolts.
  uint8_t percent;       // estimativa 0-100% de carga (ver voltageToPercent(), aproximado — ver aviso acima).
  uint32_t timestamp_ms; // millis() no momento desta leitura.
  bool valid;            // false ate existir pelo menos uma leitura bem sucedida.
};

// Configura os pinos usados (VBAT_ENABLE como saida, inicialmente HIGH =
// leitura desativada, tal como o initVariant() do proprio BSP faz por
// omissao — ver aviso de seguranca no topo deste ficheiro). Deve ser
// chamada uma unica vez no arranque, antes de qualquer chamada a sample().
// Retorna true (nao ha caminho de falha critico nesta inicializacao).
bool begin();

// Faz uma leitura pontual da tensao da bateria: ativa o divisor
// (VBAT_ENABLE=LOW) so durante o tempo estritamente necessario, le o ADC,
// desativa o divisor de novo (VBAT_ENABLE=HIGH) e converte o valor bruto
// em tensao/percentagem estimadas. Preenche 'out' e atualiza tambem o
// valor devolvido por latest(). Pensada para ser chamada esporadicamente
// (ex.: a cada 30-60s, ver main.cpp) — nao ha necessidade de chamar mais
// frequentemente, o nivel de bateria varia lentamente. Retorna sempre true
// nesta implementacao (analogRead() no nRF52 nao tem um modo de erro
// distinto de "0"); o campo 'valid' de 'out' e' que assinala se ja houve
// alguma leitura.
bool sample(Reading &out);

// Devolve a ultima leitura feita por sample() (util para outros modulos,
// ex. Ble.cpp, sem forcarem uma nova leitura ADC). Antes da primeira
// chamada a sample(), latest().valid é false e os restantes campos ficam a
// zero.
const Reading &latest();

} // namespace Battery

#endif
