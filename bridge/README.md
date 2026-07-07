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

**Atualizado 2026-07-07**: os dados dos sensores já vão cifrados com
AES-CTR pelo ar (ver `encryptRecord()` em `src/Ble/Ble.cpp`) — mas este
bridge só consegue decifrá-los se souber a MESMA chave AES trocada com o
dispositivo, através da variável de ambiente `CAREWEAR_AES_KEY_HEX`
(hexadecimal, 32/48/64 caracteres = 16/24/32 bytes):

```
export CAREWEAR_AES_KEY_HEX=<a mesma chave escrita no dispositivo>
python ble_bridge.py
```

Sem essa variável definida, o bridge não interpreta os registos de
sensores (evita mostrar valores fabricados) — regista um aviso e ignora-os.
**Limitação honesta**: não existe (ainda) uma app de provisioning que
entregue a chave ao bridge automaticamente — quem provisiona o
dispositivo tem de configurar o bridge manualmente com a mesma chave.
Ver o cabeçalho de `ble_bridge.py` para o desenho completo do protocolo.

## Base de dados avançada (`storage_advanced.py`, protótipo)

**Ainda não integrada em `ble_bridge.py`** (que continua a usar
`storage.py`, a versão simples já em produção) — ver PROJECT_STATUS.md,
secção "Base de Dados SQL Completa". Para experimentar isoladamente:

```
pip install -r requirements_db.txt
cd bridge && python -m pytest tests/ -v   # 34 testes, SQLite em memória
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
(chave derivada via Argon2id, ver `crypto_utils.py`) antes de gravar.
Sem estas duas variáveis, os valores ficam em **texto simples** (com aviso
no arranque) — nunca finge cifrar com uma chave previsível:

```
export CAREWEAR_DB_ENCRYPTION_KEY=<frase-passe longa e secreta>
export CAREWEAR_DB_ENCRYPTION_SALT_HEX=$(python3 -c "import os; print(os.urandom(16).hex())")
```

Guardar as duas variáveis em local seguro — perder a frase-passe ou o sal
torna os campos já cifrados **irrecuperáveis** (não há forma de recuperar
uma chave Argon2id sem os dois inputs originais).
