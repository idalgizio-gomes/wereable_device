// ============================================================================
// Ui.h
// ----------------------------------------------------------------------------
// Mini-interface partilhada de "UI" do dispositivo: expõe apenas a função
// uiMessage(), usada por vários módulos (Imu, Ble, main) para mostrar
// mensagens curtas ao utilizador no ecrã OLED SSD1351 (ex.: "IMU ERRO",
// "Receber / AES key", hora/data).
//
// Porque é que a implementação vive em main.cpp e não aqui num Ui.cpp:
// o objeto global do ecrã (Adafruit_SSD1351 `display`) e o barramento SPI
// dedicado (SPIM3) são construídos e inicializados em main.cpp — mover a
// função para um módulo próprio obrigaria a partilhar esse objeto global
// (extern) sem ganho real, dado que esta é a única função de UI existente.
// Se a UI crescer (mais ecrãs/páginas), aí sim justifica-se criar um
// módulo Display/Ui.cpp completo com o objeto do ecrã lá dentro.
// ============================================================================

#ifndef DISPLAY_UI_H_
#define DISPLAY_UI_H_

// Mostra até duas linhas de texto centradas horizontalmente no ecrã.
// - line1: primeira linha (obrigatória).
// - line2: segunda linha (opcional); se for nullptr, line1 é desenhada
//   sozinha, centrada verticalmente.
// Limpa sempre o ecrã antes de desenhar (não "acumula" texto anterior).
// Definida em main.cpp para ter acesso ao objeto Adafruit_SSD1351 global.
void uiMessage(const char *line1, const char *line2 = nullptr);

#endif
