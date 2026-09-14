# CareWear — Plataforma Wearable para Monitorização de Rotina em Demência

## O que é o CareWear

Wearable (Seeed XIAO nRF52840 Sense Plus) + firmware + dashboard web, para
monitorização contínua e não invasiva de rotina diária em contexto de
cuidados de demência. O dispositivo tem IMU (acelerómetro + giroscópio),
PPG (SpO2/HR), ecrã OLED, flash externa e BLE; o firmware envia os dados
por Bluetooth, um bridge Python liga-os a um dashboard web (área
utente/família e área médico/técnico).

**Base científica**: o firmware e a estrutura de dados seguem o artigo
*"Routine-Aware Behavioural Monitoring Framework for Dementia Care Using
Wearable-Derived Synthetic Daily Routines"*, cujo pipeline de deteção de
rotina/anomalias combina três partes complementares: um classificador de
atividades (XGBoost no artigo; também avaliado com Random Forest no
`ml/` deste projeto, por ser mais viável para embarcar), um LSTM
Autoencoder para deteção de anomalias comportamentais, e um detetor de
duração baseado em regras. Estado atual: os três passos estão
implementados e avaliados sobre **dados sintéticos** — ver
[`ml/README.md`](ml/README.md) para os resultados e limitações honestas de
cada um.

## Estrutura do repositório

- `src/`, `include/` — firmware do wearable (PlatformIO, framework Arduino).
- `web/dashboard/` — protótipo de dashboard web (login, área
  utente/família, área médico/técnico).
- `bridge/` — ponte Python (BLE → WebSocket + API REST) que liga o
  dashboard a dados reais do dispositivo.
- `ml/` — pipeline de machine learning (classificador de atividades,
  LSTM Autoencoder, detetor de duração) treinado e avaliado sobre dados
  sintéticos.
- `test/` — sketches de teste isolados por sensor/funcionalidade (não
  fazem parte do build principal do firmware).

## Como arrancar

### Com wearable real

Duplo-clique em [`start_carewear.bat`](start_carewear.bat) (chama
`start_carewear.ps1`). Por esta ordem, o script:

1. Lê `bridge/device_key.env` (se existir) e carrega as variáveis nele
   listadas (ex.: `CAREWEAR_AES_KEY_HEX`, a chave AES do dispositivo) só
   para os processos que vai arrancar — nunca imprime a chave no ecrã nem
   a escreve de volta no ficheiro.
2. Arranca `bridge/ble_bridge.py` (liga-se por BLE ao dispositivo, expõe
   WebSocket em `ws://localhost:8765`).
3. Arranca `bridge/api.py` via uvicorn, porta 8766 (API REST usada pelo
   login e dados clínicos do dashboard).
4. Abre `web/dashboard/index.html` no browser, já ligado a ambos.

Requer as dependências de `bridge/requirements.txt` instaladas
(`pip install -r bridge/requirements.txt`) e Python 3 no PATH. Sem
`bridge/device_key.env`, o bridge arranca à mesma mas não consegue
decifrar os registos de sensores (evita mostrar valores fabricados).

### Com dados simulados

Sem wearable ligado, o dashboard funciona à mesma — basta abrir
`web/dashboard/index.html` diretamente no browser, com dados de
demonstração simulados.

## Onde procurar mais detalhe

- [`DOCUMENTACAO_TECNICA_CODIGO.md`](DOCUMENTACAO_TECNICA_CODIGO.md) —
  documentação técnica do código (firmware + bridge + dashboard + ML),
  com cada afirmação ligada a `ficheiro:linha` real.
- [`SECURITY_STATUS.md`](SECURITY_STATUS.md) — estado de segurança
  (BLE, cifra, autenticação, achados e mitigações).
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) — estado do projeto,
  histórico de decisões e roadmap; ponto de partida para retomar o
  trabalho sem reler toda a conversa.
- [`PERGUNTAS_REPETIDAS.md`](PERGUNTAS_REPETIDAS.md) — registo de
  perguntas técnicas que ficaram sem resposta por compactação da
  conversa, guardadas aqui para não se perderem outra vez.
- [`bridge/README.md`](bridge/README.md) — detalhe do bridge BLE ↔
  WebSocket, cifra AES dos dados de sensores, base de dados avançada e
  API REST.
- [`ml/README.md`](ml/README.md) — pipeline de ML completo: dados
  sintéticos, features, os três passos do modelo, resultados,
  limitações honestas e próximos passos.
