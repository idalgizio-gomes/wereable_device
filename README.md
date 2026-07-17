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

## Arrancar com dados reais do wearable

Duplo-clique em [`start_carewear.bat`](start_carewear.bat) — arranca o
bridge (liga-se sozinho por BLE ao dispositivo) e abre o dashboard no
browser, já ligado. Requer `bridge/device_key.env` com a chave AES do
dispositivo (ver cabeçalho de `bridge/ble_bridge.py`) e as dependências de
`bridge/requirements.txt` instaladas (`pip install -r bridge/requirements.txt`).
Sem wearable ligado, o dashboard funciona à mesma com dados simulados —
basta abrir `web/dashboard/index.html` diretamente.

## Base científica

O firmware e a estrutura de dados seguem o artigo *"Routine-Aware
Behavioural Monitoring Framework for Dementia Care Using Wearable-Derived
Synthetic Daily Routines"* (pipeline XGBoost + LSTM Autoencoder + detetor
de duração baseado em regras).
