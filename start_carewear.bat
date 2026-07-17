@echo off
REM Lançador de um clique do CareWear — duplo-clique neste ficheiro.
REM Só chama start_carewear.ps1 (ver esse ficheiro para o que realmente
REM acontece); -ExecutionPolicy Bypass evita o erro comum "scripts
REM desativados neste sistema" sem precisar de mudar a política global do
REM PowerShell do utilizador.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_carewear.ps1"
