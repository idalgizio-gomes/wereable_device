// Imu.h
//
// Modulo responsavel por tudo o que envolve o sensor IMU (Inertial
// Measurement Unit) LSM6DS3, um sensor de 6 eixos que combina um
// acelerometro (mede aceleracao linear/gravidade) e um giroscopio
// (mede velocidade angular/rotacao).
//
// Este modulo:
//  - Inicializa e configura o sensor (via I2C).
//  - Faz a calibracao do sensor (remove os "offsets" de fabrico/montagem,
//    ou seja, os pequenos erros sistematicos de leitura quando o
//    dispositivo esta parado).
//  - Fornece leituras (raw e calibradas) dos eixos de aceleracao (ax, ay, az)
//    e de rotacao (gx, gy, gz).
//  - Corre uma tarefa (task) FreeRTOS em segundo plano que le o sensor a
//    ~52 Hz e, a partir dessas leituras, deteta padroes de movimento
//    relevantes para um dispositivo de monitorizacao de pessoas com
//    demencia: contagem de passos (pedometro), quedas (freefall) e
//    inatividade prolongada.
//
// Este header expoe a API publica usada pelo resto do firmware para
// arrancar/consultar o IMU sem precisar de conhecer os detalhes internos
// (registos do sensor, filtros, thresholds, etc.), que estao implementados
// em Imu.cpp.

#ifndef IMU_H_
#define IMU_H_

#include <Arduino.h>
#include "Storage/Storage.h"

namespace Imu {

  // Amostra (snapshot) de uma leitura do IMU num dado instante, ja
  // enriquecida com o resultado da deteccao de movimento feita pela task
  // de aquisicao. E este o "pacote" que o resto do sistema consome para
  // saber o que o utilizador esta a fazer (a andar, a cair, parado, etc.).
  struct Sample {
    uint32_t timestamp_ms; // Instante (millis()) em que a amostra foi lida.
    float ax;               // Aceleracao no eixo X, em g (raw, sem calibracao).
    float ay;               // Aceleracao no eixo Y, em g.
    float az;               // Aceleracao no eixo Z, em g.
    float gx;               // Velocidade angular no eixo X, em graus/seg (dps).
    float gy;               // Velocidade angular no eixo Y, em dps.
    float gz;               // Velocidade angular no eixo Z, em dps.
    uint32_t step_count;    // Numero total de passos contados desde o arranque da task.
    bool freefall;           // true se foi detetada uma possivel queda livre.
    bool inactivity;         // true se o dispositivo esta parado ha varios segundos seguidos.
  };

  // Inicializa a comunicacao com o sensor LSM6DS3 (I2C, endereco 0x6A) e
  // aplica as definicoes de sample rate/range do acelerometro e giroscopio.
  // Deve ser chamada uma unica vez, no arranque do sistema, antes de
  // qualquer outra funcao deste modulo (ensureCalibrated, readRaw, etc.).
  // Devolve true se o sensor respondeu e ficou pronto a usar; false em
  // caso de erro de comunicacao/hardware.
  bool begin();

  // Garante que existem offsets de calibracao validos prontos a usar.
  // Fluxo: tenta primeiro carregar uma calibracao previamente guardada no
  // sistema de ficheiros (Storage); se nao existir, corre a rotina de
  // calibracao (pede ao utilizador para pousar o dispositivo e recolhe
  // amostras) e grava o resultado para uso futuro. Durante o processo
  // mostra mensagens no display ("Iniciar Calibracao" / "IMU Calibrado").
  // Deve ser chamada depois de begin() e antes de usar readCalibrated()
  // ou iniciar a task com startTask(). Devolve true se, no final, existem
  // offsets validos disponiveis (quer tenham sido carregados quer
  // calculados de novo); false se falhar a inicializacao, a calibracao ou
  // a gravacao.
  bool ensureCalibrated();

  // Le os valores atuais do sensor e subtrai-lhes os offsets de calibracao
  // guardados (ver offsets()), devolvendo assim uma leitura "limpa", mais
  // proxima do valor real. Usar esta funcao sempre que se pretenda o
  // valor fisico correto (ex.: para calculos de orientacao/movimento).
  // Devolve false se o IMU ainda nao foi inicializado (begin() nao foi
  // chamado ou falhou).
  bool readCalibrated(float &ax, float &ay, float &az,
                      float &gx, float &gy, float &gz);

  // Le os valores diretamente do sensor, sem qualquer compensacao de
  // offsets (valores "em bruto"). E a funcao de mais baixo nivel; e usada
  // internamente tanto por readCalibrated() como pela rotina de
  // calibracao e pela task de aquisicao (que aplica os offsets manualmente
  // para tambem poder calcular a magnitude de aceleracao raw). Devolve
  // false se o IMU ainda nao foi inicializado.
  bool readRaw(float &ax, float &ay, float &az,
               float &gx, float &gy, float &gz);

  // Devolve uma referencia aos offsets de calibracao atualmente em uso
  // (os mesmos aplicados por readCalibrated()). Util para inspecionar ou
  // apresentar os valores de calibracao sem desencadear uma nova leitura.
  const ImuCalibration &offsets();

  // Cria e arranca a task FreeRTOS "imu_task", que passa a ler o sensor
  // periodicamente (~52 Hz) em segundo plano e a atualizar a ultima
  // amostra disponivel (ver getLatestSample) e a contagem de passos,
  // deteccao de queda livre e deteccao de inatividade. Deve ser chamada
  // apenas depois de begin() e ensureCalibrated() terem sido executados
  // com sucesso. Chamar mais do que uma vez e seguro: se a task ja existe,
  // a funcao limita-se a devolver true sem criar uma nova. Devolve false
  // se o IMU nao estiver inicializado ou se a criacao da task falhar
  // (ex.: memoria insuficiente).
  bool startTask();

  // Indica se a task de aquisicao do IMU foi criada e esta efetivamente a
  // correr (ja executou pelo menos uma iteracao do seu loop). Util para
  // outros modulos verificarem se podem confiar nas amostras devolvidas
  // por getLatestSample() antes de as consumirem.
  bool isTaskRunning();

  // Copia para 'out' a ultima amostra (Sample) produzida pela task de
  // aquisicao do IMU. A copia e protegida por uma secao critica para
  // evitar que a task escreva na amostra ao mesmo tempo que este metodo a
  // le a partir de outra task/contexto (leitura/escrita concorrente).
  // Devolve false se a task de aquisicao ainda nao estiver a correr
  // (ver isTaskRunning()), caso em que 'out' nao e alterado.
  bool getLatestSample(Sample &out);

  // Devolve o numero total de passos contados pelo detetor de pedometro
  // desde que a task foi iniciada (ou reiniciada). Pode ser chamada a
  // qualquer momento; nao depende de getLatestSample().
  uint32_t stepCount();

  // *** DIAGNOSTICO TEMPORARIO (otimizacao de RAM) ***
  // Devolve a menor quantidade de stack livre (em palavras de 32 bits)
  // que a imu_task alguma vez teve desde que arrancou — o "high water
  // mark" do FreeRTOS. Serve para decidir, com dados reais em vez de
  // adivinhar, se IMU_TASK_STACK_WORDS pode ser reduzido com seguranca.
  // Devolve 0 se a task ainda nao estiver a correr.
  uint32_t taskStackHighWaterMarkWords();
}

#endif
