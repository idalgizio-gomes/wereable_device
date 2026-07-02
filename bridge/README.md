# Bridge BLE ↔ WebSocket

Liga o wearable (Bluetooth Low Energy) ao dashboard web (`web/dashboard/index.html`),
que corre no browser e não consegue falar BLE diretamente em todos os browsers.

```
Wearable (BLE) <--> ble_bridge.py (Python) <--> WebSocket ws://localhost:8765 <--> dashboard (browser)
```

## Instalar

```
pip install -r requirements.txt
```

## Correr

1. Liga a placa (long-press físico, ou o bypass `WAKE` pela porta série
   enquanto o botão estiver partido — ver `PROJECT_STATUS.md`).
2. Corre:
   ```
   python ble_bridge.py
   ```
3. Abre `web/dashboard/index.html` num browser. A página tenta ligar-se
   sozinha a `ws://localhost:8765`; se conseguir, os cartões de sinais
   vitais passam a mostrar dados reais em vez da demonstração simulada.

O bridge também escreve automaticamente a hora atual (UTC) na
characteristic *Current Time* do dispositivo, se ele ainda estiver à
espera dela — isso substitui ter de usar o nRF Connect manualmente.

## Nota sobre segurança

Os dados dos sensores são transmitidos **sem cifra** nesta fase do firmware
(ver comentário `FullPlain` em `src/Ble/Ble.cpp`) — apesar de existir troca
de chave AES para outros fins, essa cifra ainda não está aplicada ao
streaming de dados. Não usar em rede não confiável sem essa lacuna ser
fechada primeiro.
