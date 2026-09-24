// SharedI2cBus.h - GNSS (Gnss.cpp) e PPG (Ppg.cpp) correm em tasks FreeRTOS
// separadas e usam o mesmo barramento I2C fisico (Wire). O driver TWI do
// nRF52/Arduino nao e reentrante: duas tasks a fazer transacoes I2C ao mesmo
// tempo corrompem o estado do barramento e podem ficar bloqueadas
// indefinidamente. Serializa o acesso; mutex recursivo porque algumas
// funcoes de Ppg.cpp chamam-se entre si (ex.: measureSpo2 -> sensorIdle).

#ifndef SHARED_I2C_BUS_H_
#define SHARED_I2C_BUS_H_

namespace SharedI2cBus {

  // Cria o mutex. Chamar uma vez a partir de setup(), antes de qualquer task
  // arrancar (nao e' thread-safe criar o mutex a partir de duas tasks ao
  // mesmo tempo).
  void init();

  void lock();
  void unlock();

  // RAII: lock() no construtor, unlock() no destrutor.
  class Guard {
  public:
    Guard() { lock(); }
    ~Guard() { unlock(); }
    Guard(const Guard &) = delete;
    Guard &operator=(const Guard &) = delete;
  };

}

#endif
