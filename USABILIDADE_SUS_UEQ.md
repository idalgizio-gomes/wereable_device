# C22 — Protocolo de usabilidade (SUS + UEQ-S)

Pronto a distribuir a n=5-8 participantes (cuidadores/familiares = perfil
`utente`, e profissionais de saúde = perfil `clinico`, do dashboard real).
Correr os dois perfis separadamente dá mais sinal do que misturar, porque as
tarefas e prioridades são diferentes.

## 1. Guião de tarefas (antes do questionário)

Dar ao participante o dashboard já com dados de demonstração carregados
(`demo-data.js`), sem explicação prévia de onde estão os botões — é a própria
usabilidade que está a ser testada.

### Perfil `utente` (cuidador/familiar)
1. Encontrar o valor mais recente de frequência cardíaca do paciente.
2. Perceber se o paciente está atualmente a usar o dispositivo ("wear
   status").
3. Consultar a rotina/timeline de hoje e identificar um episódio assinalado.
4. Ativar/desativar uma permissão de outro cuidador (ecrã de definições).
5. Encontrar onde reportar um alerta de emergência de teste.

### Perfil `clinico` (profissional de saúde)
1. Abrir a lista de pacientes e localizar um paciente específico pelo nome.
2. Consultar a tendência de SpO2 dos últimos dias de um paciente.
3. Abrir o relatório/timeline de um episódio de alerta e perceber a
   correlação de sinais vitais à volta do evento.
4. Ajustar os limiares (thresholds) de alerta de frequência cardíaca de um
   paciente.
5. Exportar/gerar um relatório clínico.

Registar, por tarefa: sucesso/insucesso, tempo (opcional), e observações
livres do avaliador (onde hesitou, o que perguntou em voz alta).

## 2. Questionário SUS (System Usability Scale)

Aplicar depois do guião de tarefas, mesmo que nem todas as tarefas tenham
tido sucesso. Escala de concordância 1 (discordo totalmente) a 5 (concordo
totalmente).

1. Acho que gostaria de usar este dashboard com frequência.
2. Achei o dashboard desnecessariamente complexo.
3. Achei o dashboard fácil de usar.
4. Acho que precisaria do apoio de alguém com conhecimentos técnicos para
   conseguir usar este dashboard.
5. Achei que as várias funcionalidades do dashboard estavam bem integradas.
6. Achei que havia demasiada inconsistência neste dashboard.
7. Imagino que a maioria das pessoas aprenderia a usar este dashboard muito
   rapidamente.
8. Achei o dashboard muito complicado de usar.
9. Senti-me muito confiante a usar o dashboard.
10. Precisei de aprender muita coisa antes de conseguir usar bem o
    dashboard.

## 3. Questionário UEQ-S (versão curta, 8 itens bipolares)

Pedir para marcar de 1 a 7 entre os dois adjetivos opostos, o mais depressa
possível (reação espontânea, não análise longa).

| Obstrutivo (1) | | | | | | | Apoiante (7) |
|---|---|---|---|---|---|---|---|---|
| Complicado (1) | | | | | | | Simples (7) |
| Ineficiente (1) | | | | | | | Eficiente (7) |
| Confuso (1) | | | | | | | Claro (7) |
| Aborrecido (1) | | | | | | | Cativante (7) |
| Pouco interessante (1) | | | | | | | Interessante (7) |
| Convencional (1) | | | | | | | Inventivo (7) |
| Comum (1) | | | | | | | Pioneiro (7) |

(Os 4 primeiros itens formam a subescala **Pragmática** — eficácia da
tarefa; os 4 últimos a subescala **Hedónica** — prazer de uso. É a divisão
oficial do UEQ-S.)

## 4. Folha de scoring

### SUS
- Itens ímpares (1,3,5,7,9): pontuação = valor dado − 1
- Itens pares (2,4,6,8,10): pontuação = 5 − valor dado
- Somar os 10, multiplicar por 2.5 → score de 0 a 100.
- Referência: >68 acima da média; >80 considerado "excelente" na literatura
  (Bangor et al., benchmark habitual).

### UEQ-S
- Subescala Pragmática = média dos itens 2,3,4,5 (posições da tabela acima,
  por ordem: Complicado/Simples, Ineficiente/Eficiente, Confuso/Claro —
  nota: reconfirmar mapeamento oficial UEQ-S ao formatar em Excel, a ordem
  exata dos 8 itens no UEQ-S oficial não é 1:1 com "primeiros 4 = pragmática"
  em todas as versões traduzidas — usar a chave de correção oficial
  (ueq-online.org) antes de publicar números no relatório).
- Subescala Hedónica = média dos restantes 4.
- Escala final: −3 (péssimo) a +3 (excelente); >0.8 considerado positivo.

### Ficheiro de trabalho (CSV pronto a preencher)
Ver `USABILIDADE_RESPOSTAS_TEMPLATE.csv` — uma linha por participante, uma
coluna por item (SUS1..SUS10, UEQ1..UEQ8), mais colunas de metadados
(perfil, data, tarefas com sucesso/total). Abrir em Excel/LibreOffice e
aplicar as fórmulas acima, ou usar `scoring_sus_ueq.py` (neste mesmo
diretório) para calcular automaticamente a partir do CSV.

## Nota sobre n pequeno (5-8 participantes)

Com este n, os números do SUS/UEQ servem como **sinal direcional**, não como
prova estatística — não fazer testes de significância nem comparar médias
entre os dois perfis com testes-t (n insuficiente). No relatório (C30), a
apresentação correta é: valores + intervalo observado (mín-máx) + citações
qualitativas das observações do guião de tarefas, não "p<0.05".
