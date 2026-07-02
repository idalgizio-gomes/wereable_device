# CareWear — Plataforma Wearable para Monitorização de Rotina em Demência

Wearable (Seeed XIAO nRF52840 Sense Plus) + firmware + dashboard web, para
monitorização contínua e não invasiva de rotina diária em contexto de
cuidados de demência.

**Para retomar o estado do projeto sem reler todo o histórico, ver
[`PROJECT_STATUS.md`](PROJECT_STATUS.md).**

## Estrutura

- `src/`, `include/` — firmware (PlatformIO, framework Arduino).
- `web/dashboard/` — protótipo de dashboard web (login, área utente/família, área médico/técnico).
- `bridge/` — ponte Python (BLE → WebSocket) que liga o dashboard a dados reais do dispositivo.
- `test/` — sketches de teste isolados por sensor/funcionalidade (não fazem parte do build principal).

## Base científica

O firmware e a estrutura de dados seguem o artigo *"Routine-Aware
Behavioural Monitoring Framework for Dementia Care Using Wearable-Derived
Synthetic Daily Routines"* (pipeline XGBoost + LSTM Autoencoder + detetor
de duração baseado em regras).
