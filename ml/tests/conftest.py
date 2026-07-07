"""Configuração partilhada dos testes de `ml/` (`ml/tests/`).

Só adiciona `ml/` ao `sys.path` — os módulos testados aqui (`features.py`,
`duration_detector.py`, `synthetic_data.py`, `synthetic_sequences.py`) usam
imports relativos ao próprio diretório (`from features import ...`), tal
como os scripts de treino já assumem ao serem corridos com `cd ml`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
