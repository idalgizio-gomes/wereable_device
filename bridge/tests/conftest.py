"""Configuração partilhada dos testes do bridge (`bridge/tests/`).

Força `storage_advanced` a usar SQLite em memória (nunca o `carewear.db`
real de desenvolvimento) — tem de acontecer ANTES do primeiro import do
módulo, porque `DB_URL`/`engine` são criados a nível de módulo.
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
