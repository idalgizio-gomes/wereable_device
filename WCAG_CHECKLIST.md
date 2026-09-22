# Checklist WCAG 2.1 — Dashboard CareWear (C26)

Auditoria manual (sem `lighthouse`/`axe` instalados neste ambiente), baseada
em leitura direta do código (`web/dashboard/`). Cada item indica evidência
concreta (ficheiro:linha) — "OK" só quando confirmado no código, nunca por
suposição. Nível-alvo: **AA**.

Páginas/vistas cobertas (SPA de página única, `index.html` + `vista-*.js`):
resumo, rotina, timeline, vitais/tendência, definições/ajuda, e as vistas
de médico (pacientes, medicação, perfil) + admin.

## 1. Percetível

| Critério | Estado | Evidência |
|---|---|---|
| 1.1.1 Conteúdo não-texto (gráficos canvas) | **OK** | Todos os `<canvas>` de gráfico têm `role="img"` + `aria-label` traduzido (`vista-vitais-tendencia.js:29,36,68,77`; `vista-timeline.js:570-571`). |
| 1.1.1 Imagens (`<img>`) | **A confirmar** | 4 `<img>` no total, todos com `alt=`; não verifiquei se o texto alt é descritivo ou só decorativo por página — rever ao apresentar. |
| 1.3.1 Estrutura semântica (headings) | **Parcial** | Hierarquia h1→h3 presente e sem saltos óbvios (2×h1, 2×h2, vários h3 nas vistas), mas nunca corri um validador de outline — não há h4+ nalgumas vistas mais longas (ex. `vista-medico-perfil.js` com 10 headings, só até h3); rever se alguma secção longa precisaria de h4. |
| 1.3.1 Formulários/labels | **Parcial** | `<label>` presente em várias vistas (`vista-medico-perfil.js`:13, `admin-view.js`:5, etc.), mas não confirmei associação `for`/`id` campo a campo — a fazer no follow-up. |
| 1.4.3 Contraste de cor (texto sobre fundo, tema claro e escuro) | **OK, documentado no próprio CSS** | `index.html` linhas 77-182: rácios calculados e comentados para `--status-good/warning/serious/critical` em ambos os temas (claro: mín. 4.91:1; escuro: mín. 5.12:1 ou corrigido onde falhava — ex. `--status-critical` escuro corrigido de 3.57:1 para 5.17:1). Este é o item mais maduro da auditoria — já foi trabalho de sessão anterior, não desta. |
| 1.4.11 Contraste de componentes não-textuais (bordas, ícones de estado) | **Não verificado** | `.status-dot`, `.ble-toggle-btn` usam as mesmas variáveis de cor documentadas acima, mas contraste de elementos não-texto (rácio mínimo 3:1) não tem cálculo explícito registado — só o de texto foi documentado. |
| 1.4.4 Redimensionar texto até 200% sem perda de funcionalidade | **Não testado** | Precisa de teste manual no browser (zoom 200%), não é verificável só por leitura de código. |

## 2. Operável

| Critério | Estado | Evidência |
|---|---|---|
| 2.1.1 Tudo operável por teclado | **Parcial** | `tabindex="0"` presente em 5 pontos, incl. o canvas da timeline (`vista-timeline.js:570`). |
| 2.1.2 Sem armadilha de teclado (Escape) | **OK — corrijo o que disse antes** | Reli com mais cuidado: o handler de `Escape` em `canvas-graficos.js:58-66` (dentro de `initAccessibility()`) **é global**, não é só do modal de zoom — ouve `keydown` no `document`, procura qualquer `.modal-overlay` visível (`style.display !== 'none'`) e clica no seu `.modal-actions .btn-secondary`. Confirmei que os 4 modais de `index.html` têm ambas as classes: `resetModalOverlay`(1248)/`emergencyCancelOverlay`(1271)/`adminPatientReportOverlay`(1309)/`episodeTimelineOverlay`(1320), todos com `.modal-actions .btn-secondary` (linhas 1259, 1288, 1315, 1326). **Os 5 modais fecham com Escape.** A minha entrega anterior estava errada ao dizer que só 1 tinha este tratamento — peço desculpa pelo erro, calculei mal o escopo do handler ao ler só a linha onde o grep apanhou `'Escape'`, sem ler a função à volta. |
| 2.1.2 Focus-trap (Tab preso dentro do modal aberto) | **Gap real, este sim confirmado** | Procurei por `activeElement`/`trapFocus`/`focusableEls` em todo o `web/dashboard/*.js` — zero ocorrências. Ou seja: Escape fecha o modal, mas enquanto está aberto, a tecla Tab não está impedida de sair do modal para elementos da página por trás (não é bloqueante para 2.1.2 nível A, que exige só que não fique preso — aqui não fica preso, mas é uma falha de 2.4.3/boas práticas de modal, vale a pena registar para o relatório). |
| 2.4.1 Bloco de salto ("saltar para o conteúdo") | **OK** | `.skip-link` implementado e funcional (`index.html:230,236,1043`, `focusMainContent()`). |
| 2.4.3 Ordem de foco | **Não testado** | Requer teste manual (Tab sequencial) por vista; não verificável só por leitura estática do DOM gerado dinamicamente. |
| 2.4.6 Cabeçalhos e rótulos descritivos | **Provável OK** | Todos os `aria-label`/`aria-labelledby` observados usam chaves i18n (`t('...')`), não texto genérico tipo "botão 1" — mas não revi o conteúdo de cada chave em `i18n-strings.js`. |
| 2.5.3 Nome acessível = rótulo visível | **Não verificado** | Não confirmei se `aria-label` diverge do texto visível em nenhum botão. |

## 3. Compreensível

| Critério | Estado | Evidência |
|---|---|---|
| 3.1.1 Idioma da página | **OK, agora confirmado** | `<html lang="pt">` no HTML estático (`index.html:11`) E atualizado em runtime: `canvas-graficos.js:52-55` regista um listener `change` no `#langSelect` que faz `document.documentElement.lang = langSel.value`, com comentário próprio no código a citar o critério: `//SC 3.1.1 — mantém <html lang> em sintonia com o idioma escolhido` (linha 51). Também é aplicado uma vez no arranque (linha 56). Ou seja, isto já foi pensado deliberadamente por quem escreveu o código, não é acaso. |
| 3.2.2 Ao introduzir valor não muda de contexto inesperadamente | **Não testado** | Requer teste manual (fora do alcance de leitura estática de código). |
| 3.3.1 / 3.3.2 Identificação e rótulo de erros em formulários | **Gap real confirmado** | `grep -rn "aria-describedby\|aria-invalid"` a todo o `web/dashboard/` devolveu **zero resultados**. Não existe nenhum padrão de associação acessível entre um campo e a respetiva mensagem de erro. Se um formulário validar algo (ex. campos do perfil médico em `vista-medico-perfil.js`, criação de utilizador em `admin-view.js`), um utilizador de leitor de ecrã não é avisado programaticamente de qual campo falhou nem porquê. |
| Rótulos: técnica de associação (label⇄campo) | **OK, revisto em detalhe** | Duas técnicas usadas corretamente: (a) `for="id"` explícito com `id` correspondente no campo — confirmado em `vista-medico-perfil.js` (linhas 17,31,35,39,46,50,62,66,70,155,189,204), `vista-vitais-tendencia.js` (43,47,51), `vista-rotina.js:25`, `admin-view.js` (247,249,251,253,257, todas com classe `sr-only` — visualmente ocultas mas lidas por leitor de ecrã, técnica válida); (b) associação implícita por aninhamento (`<label>texto<select>...</select></label>`) em `componentes-alertas-ui.js:319-324` — também válida, não precisa de `for=`. Não encontrei nenhum `<label>` órfão (sem `for=` e sem campo aninhado).

## 4. Robusto

| Critério | Estado | Evidência |
|---|---|---|
| 4.1.2 Nome, função, valor (componentes custom) | **Parcial/OK** | Botões de toggle usam `aria-pressed` (15 ocorrências) — padrão correto para estado on/off custom (ex. `.ble-toggle-btn`, `index.html:447`). |
| 4.1.3 Mensagens de estado (aria-live) | **Gap real identificado** | Apenas **1 `aria-live`** em todo o dashboard (`vista-timeline.js:565`, label da janela de tempo). Atualizações de estado importantes — novos alertas, mudança de estado da ligação BLE, resultado de leitura forçada de HR/SpO2 — não têm região `aria-live` confirmada, o que significa que um leitor de ecrã não é notificado automaticamente destas mudanças. **Este é provavelmente o gap mais impactante para um utilizador cego/com baixa visão neste dashboard clínico**, dado o contexto (alertas de saúde). |

## Resumo — o que falta fechar (por ordem de impacto, revisto com evidência completa)

1. **`aria-live` em alertas/estado da ligação** — o dashboard existe para
   vigilância clínica; um alerta que só aparece visualmente não serve um
   utilizador de leitor de ecrã. Só 1 `aria-live` em todo o projeto
   (`vista-timeline.js:565`). Candidatos a corrigir: `componentes-alertas-ui.js`
   (painel de alertas), indicador de estado BLE, resultado de "forçar
   leitura HR/SpO2". **Continua o gap mais importante — nada mudou aqui.**
2. **Mensagens de erro de formulário sem `aria-describedby`/`aria-invalid`**
   — confirmado, zero ocorrências no projeto (3.3.1/3.3.2).
3. **Focus-trap (Tab) nos modais** — Escape já fecha todos os 5 modais
   (corrigido — ver critério 2.1.2 acima), mas Tab não está contido dentro
   do modal enquanto aberto. Impacto menor que os itens 1-2, mas vale
   registar.
4. Testes manuais que código estático não confirma: zoom 200% (1.4.4),
   ordem de tabulação (2.4.3), mudança de contexto em inputs (3.2.2),
   contraste de componentes não-textuais (1.4.11).

**Já resolvidos, não repetir trabalho:** 1.4.3 contraste de texto
(documentado com rácios no próprio CSS), 2.4.1 skip link, 2.1.2 Escape
fecha modal, 3.1.1 `lang` acompanha troca de idioma em runtime, técnica de
associação label⇄campo (for= explícito e aninhamento implícito, ambos
usados corretamente e sem órfãos encontrados).

**Nota de transparência**: a primeira versão deste documento tinha um erro
— disse que só 1 dos 5 modais tratava `Escape`, por ter lido a linha do
grep isolada sem ler a função `initAccessibility()` à volta, que na
verdade regista um listener global para todos os `.modal-overlay`. Corrigido
acima depois de reler o ficheiro com mais cuidado.
