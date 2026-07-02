#ifndef DISPLAY_UI_H_
#define DISPLAY_UI_H_

// Mostra até duas linhas centradas no display.
// Definido em main.cpp para ter acesso ao objeto Adafruit_SSD1351.
void uiMessage(const char *line1, const char *line2 = nullptr);

#endif
