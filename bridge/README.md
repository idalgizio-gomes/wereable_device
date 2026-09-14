# Bridge BLE ↔ WebSocket

Liga o wearable (Bluetooth Low Energy) ao dashboard web (`web/dashboard/index.html`),
que corre no browser e não consegue falar BLE diretamente em todos os browsers.

## Diagrama de fluxo

```
Wearable (BLE) <--> ble_bridge.py (Python) <--> WebSocket ws://localhost:8765 <--> dashboard (browser)
```

## Instalação e arranque

```
pip install -r requirements.txt
```

1. Liga a placa (long-press físico, ou o bypass `WAKE` pela porta série
   enquanto o botão estiver partido — ver `PROJECT_STATUS.md`).
2. Corre:
   ```
   python ble_bridge.py
   ```
3. Abre `web/dashboard/index.html` num browser. A página tenta ligar-se
   sozinha a `ws://localhost:8765`; se conseguir, os cartões de sinais
   vitais passam a mostrar dados reais em vez da demonstração simulada.

## Sincronização automática de hora

O bridge escreve automaticamente a hora atual (UTC) na characteristic
*Current Time* do dispositivo, se ele ainda estiver à espera dela — isto
substitui ter de usar o nRF Connect manualmente.

## Configuração de segurança

Os dados dos sensores vão cifrados com AES-CTR pelo ar (ver
`encryptRecord()` em `src/Ble/Ble.cpp`) — o bridge só consegue decifrá-los
se souber a MESMA chave AES trocada com o dispositivo, através da variável
de ambiente `CAREWEAR_AES_KEY_HEX` (hexadecimal, 32/48/64 caracteres =
16/24/32 bytes):

```
export CAREWEAR_AES_KEY_HEX=<a mesma chave escrita no dispositivo>
python ble_bridge.py
```

Sem essa variável definida, o bridge não interpreta os registos de
sensores (evita mostrar valores fabricados) — regista um aviso e ignora-os.

**Limitação honesta**: não existe (ainda) uma app de provisioning que
entregue a chave ao bridge automaticamente — quem provisiona o dispositivo
tem de configurar o bridge manualmente com a mesma chave. Ver o cabeçalho
de `ble_bridge.py` para o desenho completo do protocolo.

## Protótipo de BD/API avançada

`storage_advanced.py` é um protótipo **ainda não integrado** em
`ble_bridge.py` (que continua a usar `storage.py`, a versão simples já em
produção) — ver `PROJECT_STATUS.md`, secção "Base de Dados SQL Completa".
Para experimentar isoladamente:

```
pip install -r requirements_db.txt
cd bridge && python -m pytest tests/ -v   # 46 testes, SQLite em memória
```

### Migrações (Alembic)

O schema é gerido pelo Alembic a partir de `storage_advanced.Base.metadata`
— nunca editar tabelas diretamente numa BD já em uso.

```
export DATABASE_URL=sqlite:///./carewear.db   # ou postgresql://... em produção
alembic upgrade head          # aplica todas as migrações pendentes
alembic revision --autogenerate -m "descrição da alteração"   # depois de mudar um modelo
```

### Cifra dos campos sensíveis (NIF, morada)

`Patient.nif`/`Patient.address` cifram automaticamente com AES-256-GCM
(chave derivada via Argon2id, ver `crypto_utils.py`) antes de gravar. Sem
estas duas variáveis, os valores ficam em **texto simples** (com aviso no
arranque) — nunca finge cifrar com uma chave previsível:

```
export CAREWEAR_DB_ENCRYPTION_KEY=<frase-passe longa e secreta>
export CAREWEAR_DB_ENCRYPTION_SALT_HEX=$(python3 -c "import os; print(os.urandom(16).hex())")
```

Guardar as duas variáveis em local seguro — perder a frase-passe ou o sal
torna os campos já cifrados **irrecuperáveis** (não há forma de recuperar
uma chave Argon2id sem os dois inputs originais).

### API REST (`api.py`, protótipo)

Expõe as queries analíticas (`Analytics.*`) por HTTP, em vez de só serem
chamáveis diretamente em Python.

```
pip install -r requirements_db.txt
export CAREWEAR_API_KEY=<chave escolhida>
cd bridge && uvicorn api:app --host 127.0.0.1 --port 8766
```

Endpoints (todos exigem o cabeçalho `X-API-Key`, exceto `/health`):

- `GET /health` — sem autenticação, só confirma que o serviço está de pé.
- `GET /api/devices/{device_id}/heart-rate-trends?days=7`
- `GET /api/patients/{patient_id}/medication-adherence?days=30`
- `GET /api/devices/{device_id}/activity-distribution?date=AAAA-MM-DD`
- `POST /api/medications/{medication_id}/adherence` — primeiro endpoint de
  escrita. Corpo JSON:
  ```json
  {
    "scheduled_datetime": "AAAA-MM-DDTHH:MM:SS",
    "taken": true,
    "method": "manual_entry",
    "notes": "..."
  }
  ```
  `method` aceita `manual_entry`, `wearable_detection` ou `ai_inference`.
  Idempotente por `(medication_id, scheduled_datetime)` — repetir o pedido
  para a mesma dose atualiza o registo em vez de duplicar, garantido por
  uma `UniqueConstraint` real na BD (ver comentário em
  `MedicationAdherence.__table_args__`, `storage_advanced.py`). Cada
  escrita fica registada em `AuditLog` (ação sensível).

**Comportamento fail-closed**: sem `CAREWEAR_API_KEY` configurada, a API
falha fechada — todos os pedidos autenticados devolvem 503. Ao contrário
da cifra acima (que degrada de forma visível mas continua a funcionar),
aqui os dados expostos são PII de saúde servidos por rede, por isso o
comportamento por omissão é bloquear, não deixar passar.

**Limitação honesta**: chave estática única (sem rotação, sem
por-utilizador, sem rate-limiting) — protótipo, não autenticação de
produção. **Ainda não integrada** com `web/dashboard/index.html` (que hoje
só fala com `ble_bridge.py` via WebSocket, e cujo botão "Marcar como
tomado" só escreve em `localStorage`) nem com `ble_bridge.py` (que
continua a usar `storage.py`, não `storage_advanced.py`) — ver
`bridge/api.py` (cabeçalho) e `PROJECT_STATUS.md` para o detalhe completo.
