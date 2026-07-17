<#
.SYNOPSIS
    Lançador de um clique do CareWear: arranca o bridge (liga-se sozinho ao
    wearable por BLE) e abre o dashboard no browser, sem passos manuais.

.DESCRIPTION
    Antes disto, era preciso abrir um terminal, definir CAREWEAR_AES_KEY_HEX
    à mão e correr "python ble_bridge.py", e só depois abrir o index.html —
    pedido explícito do utilizador para reduzir isto a um duplo-clique.

    O que este script faz, por esta ordem:
      1. Lê bridge/device_key.env (se existir) e define as variáveis de
         ambiente nele listadas (ex.: CAREWEAR_AES_KEY_HEX) só para o
         processo do bridge que vai arrancar — nunca escreve nada de volta
         no ficheiro, nunca imprime o valor da chave no ecrã.
      2. Arranca bridge/ble_bridge.py numa janela de terminal própria
         (fica visível e a correr — fechar essa janela desliga o bridge).
      3. Espera alguns segundos para o WebSocket ficar a ouvir, depois abre
         web/dashboard/index.html no browser por omissão — o dashboard já
         se liga sozinho a ws://localhost:8765 assim que a página carrega.

    Não faz nada de novo a nível de protocolo: é só automação dos dois
    passos manuais já existentes (ver bridge/ble_bridge.py, cabeçalho
    "UTILIZAÇÃO"). Sem bridge/device_key.env, o bridge arranca à mesma mas
    avisa que não consegue decifrar os registos (comportamento já existente,
    documentado em ble_bridge.py).

.EXAMPLE
    Duplo-clique em start_carewear.bat (que chama este script), ou:
    powershell -ExecutionPolicy Bypass -File start_carewear.ps1
#>

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$bridgeDir = Join-Path $repoRoot 'bridge'
$envFile = Join-Path $bridgeDir 'device_key.env'
$dashboardPath = Join-Path $repoRoot 'web\dashboard\index.html'

Write-Host '=== CareWear — arranque de um clique ===' -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $bridgeDir 'ble_bridge.py'))) {
    Write-Host "ERRO: não encontrei bridge\ble_bridge.py a partir de $repoRoot — corre este script a partir da raiz do projeto." -ForegroundColor Red
    exit 1
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host 'ERRO: "python" não foi encontrado no PATH. Instala o Python 3 e garante que está no PATH antes de tentar novamente.' -ForegroundColor Red
    exit 1
}

# Passo 1 — carregar bridge/device_key.env (KEY=VALUE por linha, comentários
# com '#') para variáveis de ambiente só desta sessão do PowerShell. O
# processo do bridge arrancado a seguir herda-as automaticamente.
$envVarNames = @()
if (Test-Path $envFile) {
    Write-Host "A carregar variáveis de $envFile..." -ForegroundColor DarkGray
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { return }
        $parts = $line.Split('=', 2)
        if ($parts.Count -ne 2) { return }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        $envVarNames += $name
    }
    if ($envVarNames.Count -gt 0) {
        Write-Host ("Variáveis carregadas: {0} (valores não mostrados)" -f ($envVarNames -join ', ')) -ForegroundColor DarkGray
    }
} else {
    Write-Host "AVISO: $envFile não existe — o bridge arranca na mesma, mas sem CAREWEAR_AES_KEY_HEX não consegue decifrar os registos (ver bridge/ble_bridge.py)." -ForegroundColor Yellow
}

# Passo 2 — arrancar o bridge numa janela própria, visível (não em
# background silencioso), para o utilizador ver logs/erros de ligação BLE
# em tempo real, tal como já acontecia ao correr "python ble_bridge.py" à
# mão. -NoExit mantém a janela aberta depois do script terminar (e depois
# de um eventual erro), em vez de fechar sozinha.
Write-Host 'A arrancar o bridge (nova janela)...' -ForegroundColor Cyan
Start-Process -FilePath 'powershell' `
    -ArgumentList '-NoExit', '-Command', "cd '$bridgeDir'; python ble_bridge.py" `
    -WorkingDirectory $bridgeDir

# Passo 3 — dar tempo ao WebSocket para começar a ouvir antes de abrir o
# dashboard (evita a primeira tentativa de ligação falhar só por chegar
# demasiado cedo — o dashboard tenta religar sozinho de qualquer forma,
# isto é só para a primeira impressão ficar já "ligado").
Write-Host 'A aguardar o bridge arrancar...' -ForegroundColor DarkGray
Start-Sleep -Seconds 3

if (-not (Test-Path $dashboardPath)) {
    Write-Host "ERRO: não encontrei $dashboardPath." -ForegroundColor Red
    exit 1
}
Write-Host 'A abrir o dashboard no browser...' -ForegroundColor Cyan
Start-Process $dashboardPath

Write-Host '=== Pronto. Fecha a janela do bridge quando quiseres desligar. ===' -ForegroundColor Green
