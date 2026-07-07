#ifndef STORAGE_H_
#define STORAGE_H_

#include <Arduino.h>

// =============================================================================
// Storage
// -----------------------------------------------------------------------------
// Este módulo é responsável por guardar e ler, na memória flash interna do
// nRF52840 (através do sistema de ficheiros LittleFS), os dados que o
// dispositivo precisa de manter mesmo depois de desligar/reiniciar:
//   - calibração do IMU (giroscópio + acelerómetro);
//   - a chave AES usada para cifrar/decifrar as comunicações BLE;
//   - um contador persistente usado como nonce/IV nas mensagens BLE.
//
// A ideia é dar ao resto da aplicação uma API simples (guardar/ler/verificar
// existência) sem que o resto do código precise de saber como o LittleFS
// funciona por dentro (ficheiros, caminhos, etc.).
//
// Existem funções com nomes "duplicados" (ex.: saveCalibration/cal_save)
// porque partes diferentes do projeto podem chamar por nomes diferentes;
// as versões curtas (cal_*, aes_*) são apenas "aliases" que chamam as
// funções principais.
// =============================================================================

// Estrutura dos offsets de calibração do IMU
struct ImuCalibration {
  float gyro_x;
  float gyro_y;
  float gyro_z;
  float accel_x;
  float accel_y;
  float accel_z;
};

// Comprimentos mínimo e máximo (em bytes) aceites para a chave AES.
// AES-128 usa 16 bytes, AES-256 usa 32 bytes; qualquer valor fora deste
// intervalo é considerado inválido e é recusado.
#define AES_KEY_MIN_LEN 16
#define AES_KEY_MAX_LEN 32

namespace Storage {

  // Inicializa o sistema de ficheiros interno (LittleFS).
  // Deve ser chamada uma vez, normalmente no setup(), antes de qualquer
  // outra função deste módulo.
  // Devolve true se OK.
  bool begin();

  // Calibração IMU

  // Guarda a estrutura de calibração do IMU na flash, substituindo
  // qualquer calibração anterior.
  // Devolve true se a escrita foi bem sucedida.
  bool saveCalibration(const ImuCalibration &cal);

  // Lê a calibração do IMU guardada na flash para "cal".
  // Devolve true se existir um ficheiro de calibração válido e a leitura
  // for bem sucedida; false caso contrário (ex.: nunca foi calibrado).
  bool loadCalibration(ImuCalibration &cal);

  // Verifica, sem carregar os dados, se existe uma calibração guardada
  // e com o tamanho esperado. Útil para decidir se é preciso pedir uma
  // nova calibração ao utilizador.
  bool hasCalibration();

  // Alias de saveCalibration(), com nome mais curto.
  bool cal_save(const ImuCalibration &cal);

  // Alias de loadCalibration(), com nome mais curto.
  bool cal_load(ImuCalibration &cal);

  // Chave AES

  // Guarda a chave AES (usada para cifrar/decifrar mensagens BLE) na
  // flash. "len" tem de estar entre AES_KEY_MIN_LEN e AES_KEY_MAX_LEN,
  // caso contrário a chave é rejeitada e a função devolve false.
  bool saveAesKey(const uint8_t *key, size_t len);

  // Lê a chave AES guardada para "buf" (que tem capacidade "bufLen").
  // O comprimento efetivo lido é devolvido em "outLen".
  // Devolve false se não existir chave, se o tamanho guardado for
  // inválido, ou se "buf" for demasiado pequeno para a receber.
  bool loadAesKey(uint8_t *buf, size_t bufLen, size_t &outLen);

  // Verifica se existe uma chave AES guardada com um tamanho válido,
  // sem a carregar para memória. Útil para decidir se é preciso
  // provisionar/gerar uma nova chave (ex.: no primeiro emparelhamento).
  bool hasAesKey();

  // Alias de saveAesKey(), com nome mais curto.
  bool aes_save(const uint8_t *key, size_t len);

  // Alias de loadAesKey(), com nome mais curto.
  bool aes_load(uint8_t *buf, size_t bufLen, size_t &outLen);

  // Contador persistente para nonce/IV das mensagens BLE

  // Lê o valor atual do contador persistente para "counter".
  // Devolve false se ainda não existir contador guardado (ex.: antes da
  // primeira transferência BLE), deixando "counter" a 0.
  // Se 'corrupted' não for nullptr, é posto a true quando o ficheiro
  // EXISTE mas está corrompido/incompleto (magic/checksum não batem certo)
  // — distinto de "nunca guardado". Quem chama não deve tratar os dois
  // casos da mesma forma quando o contador protege nonces AES-CTR (ver
  // reserveNonceBatch() em Ble.cpp): assumir 0 silenciosamente num
  // ficheiro corrompido pode reutilizar um nonce já usado com a mesma
  // chave.
  bool counter_load(uint64_t &counter, bool *corrupted = nullptr);

  // Guarda o valor de "counter" na flash, substituindo o valor anterior.
  bool counter_save(uint64_t counter);

  // Lê o contador atual, incrementa-o em 1, guarda o novo valor na flash
  // e devolve-o em "counter". Usado para garantir que cada mensagem BLE
  // cifrada usa um nonce/IV diferente do anterior (evita reutilização).
  bool counter_inc(uint64_t &counter);

  // Apaga tudo (útil em testes / factory reset)

  // Remove da flash todos os ficheiros geridos por este módulo
  // (calibração, chave AES e contador). Usado em testes ou num
  // "factory reset" do dispositivo.
  // Devolve true se pelo menos um dos ficheiros existia e foi removido.
  bool clearAll();

  // Validação não-destrutiva do FS para fase 2:
  // verifica presença e legibilidade de calibração/AES.
  // Reporta resultado por Serial.
  //
  // Não apaga nem altera nada; serve apenas para diagnóstico durante o
  // desenvolvimento/depuração (ex.: confirmar que a flash não está
  // corrompida). Devolve true se não foram encontrados problemas.
  bool validate();
}

#endif
