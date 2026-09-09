/* Exportacao clinica (FHIR/CSV/PDF) - extraido de bridge-exportacao.js */

function buildFhirBundle(){
  const p = selectedPatient();
  const nowIso = new Date().toISOString();
  const patientResource = {
    resourceType: 'Patient',
    id: p.id,
    name: [{ text: p.name }],
    extension: [{ url: 'age', valueInteger: p.age }],
  };
  // Bug corrigido: esta exportação ignorava por completo o interruptor de
  // "Alertas e registo de anomalias" do cartão de Consentimento — as
  // vistas "Pacientes"/"Anomalias detetadas" já escondiam essa informação
  // corretamente quando desligado, mas o Médico/Técnico continuava a
  // conseguir obtê-la de qualquer forma através deste export FHIR.
  const consented = loadConsent(p.id).shareAlerts;
  const observations = consented ? currentAlerts().map((a, i) => ({
    resourceType: 'Observation',
    id: `alert-${a.key || i}`,
    status: 'final',
    code: { text: alertField(a,'title') },
    subject: { reference: `Patient/${p.id}` },
    effectiveDateTime: nowIso,
    valueString: alertField(a,'desc'),
    note: a.plain ? [{ text: alertField(a,'plain') }] : undefined,
    extension: [{ url: 'severity', valueString: a.sev }],
  })) : [{
    resourceType: 'Observation',
    id: 'consent-withheld',
    status: 'unknown',
    code: { text: 'Alertas/anomalias não incluídos: utente/família não autorizou a partilha (ver Definições → Consentimento).' },
    subject: { reference: `Patient/${p.id}` },
    effectiveDateTime: nowIso,
  }];
  return {
    resourceType: 'Bundle',
    type: 'collection',
    timestamp: nowIso,
    entry: [{ resource: patientResource }, ...observations.map(o => ({ resource: o }))],
  };
}

// Descarrega um ficheiro JSON gerado em memória (sem backend) — mesma
// técnica usada noutros exportadores client-side simples: Blob + <a
// download> temporário, sem deixar o elemento no DOM.
function downloadJson(filenamePrefix, data){
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${filenamePrefix}-${new Date().toISOString().slice(0,10)}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportFhirSummary(){
  downloadJson('carewear-fhir-resumo', buildFhirBundle());
}

// Descarrega texto CSV já pronto (recebido do bridge) — mesma técnica de
// Blob + <a download> usada em downloadJson(), mas sem o JSON.stringify.
function downloadCsvText(filenamePrefix, csvText){
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${filenamePrefix}-${new Date().toISOString().slice(0,10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ------------------------------------------------------------
   EXPORTAÇÃO CSV DE DADOS REAIS — ligado à base de dados SQLite do
   bridge (bridge/storage.py), pedido do utilizador (2026-07-03):
   "quero que dê para exportar os dados também em CSV" — CSV é lido
   diretamente por praticamente qualquer ferramenta SQL/de dados
   (SQLite .import, PostgreSQL COPY, MySQL LOAD DATA, Excel, pandas).
   Diferente do FHIR/PDF acima (que só cobre o que está visível nesta
   sessão): isto pede ao bridge o histórico real persistido em disco.
------------------------------------------------------------ */
// 87600h = 10 anos — não há "sem limite" real na API (hours é sempre um
// filtro de corte), por isso usa-se um valor grande o suficiente para
// cobrir qualquer instalação real deste protótipo como proxy de "tudo".
const EXPORT_ALL_HOURS = 87600;

function exportRealCsv(hours){
  const hint = document.getElementById('csvExportHint');
  if (!liveState.connected && !(bridgeWs && bridgeWs.readyState === WebSocket.OPEN)){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('exportar.noBridgeConnectionHint'); }
    return;
  }
  const sent = sendWsCommandWithArgs('export_csv', { hours });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('exportar.bridgeUnreachableHint'); }
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('exportar.generatingCsvHint'); }
}

function handleCsvExportResult(msg){
  const hint = document.getElementById('csvExportHint');
  if (msg.error || !msg.csv){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = `${t('exportar.csvFailedPrefix')} ${msg.error || t('exportar.csvNoDataFallback')}.`; }
    return;
  }
  downloadCsvText('carewear-dados-reais', msg.csv);
  if (hint){ hint.style.color = 'var(--status-good)'; hint.textContent = t('exportar.csvDownloadedHint'); }
}

/* ------------------------------------------------------------
   RETENÇÃO DE DADOS CONFIGURÁVEL (item pendente do backlog,
   PROJECT_STATUS.md, Prioridade 4 — "expor DEFAULT_RETENTION_DAYS como
   opção configurável pelo utilizador em vez de constante fixa no
   código"). Lê/grava o valor efetivo guardado pelo bridge (tabela
   `settings` em bridge/storage.py), não um valor local ao browser —
   afeta a limpeza automática real de `sensor_records`.
------------------------------------------------------------ */
function exportClinicalPdf(){
  const p = selectedPatient();
  const sheet = document.getElementById('clinicalPrintSheet');
  if (!sheet) return;
  // Bug corrigido: mesmo lapso da exportação FHIR acima — esta folha de
  // impressão/PDF ignorava o consentimento do paciente e incluía sempre
  // alertas/anomalias, mesmo com "Alertas e registo de anomalias"
  // desligado em Definições → Consentimento.
  const consented = loadConsent(p.id).shareAlerts;
  const alerts = consented ? currentAlerts() : [];
  const anomalies = consented ? currentAnomalyLog() : [];
  const consentNote = consented ? '' : '<p class="print-meta"><b>Nota:</b> o utente/família não autorizou a partilha de alertas e anomalias com a equipa clínica (ver Definições → Consentimento) — omitidos deste resumo.</p>';
  const generatedAt = new Date().toLocaleString('pt-PT');

  // 2026-09-07: esta tabela era uma arrow function local idêntica a
  // printTableHtml() (definida mais abaixo), mas SEM escaping das células.
  // Passou a usar a partilhada — uma só tabela de impressão no ficheiro, e
  // as células passam a ser escapadas. Os textos que entram aqui vêm de
  // alertField()/anomalyDetailText(), que já incluem conteúdo escrito por
  // utilizadores noutras vistas.
  const tableHtml = printTableHtml;

  sheet.innerHTML = `
    <header class="print-header">
      <div class="print-logo"><img src="assets/logo.png" alt="CareWear"></div>
      <div class="print-header-text">
        <h1>CareWear — Resumo clínico</h1>
        <p class="print-meta">Gerado em ${generatedAt} · Protótipo — dados de demonstração, não substituem avaliação clínica direta.</p>
      </div>
    </header>
    <section class="print-patient">
      <h2>${escapeHtml(p.name)} · ${p.age} anos</h2>
      <p class="print-meta">Dispositivo: ${p.deviceName} · ${p.mac} · Última sincronização: ${p.lastSync}</p>
      ${consentNote}
    </section>
    <h3 class="print-section-title">Alertas ativos</h3>
    ${tableHtml(
      ['Título', 'Detalhe', 'Severidade', 'Quando'],
      alerts.map(a => [alertField(a,'title'), alertField(a,'desc'), a.sev, alertField(a,'time')])
    )}
    <h3 class="print-section-title">Registo de anomalias recente</h3>
    ${tableHtml(
      ['ID', 'Tipo', 'Detalhe', 'Quando'],
      anomalies.map(a => [a.id, anomalyTypeText(a), anomalyDetailText(a), a.time])
    )}
    <footer class="print-footer">CareWear · Documento gerado localmente, confidencial · ${escapeHtml(p.name)}</footer>
  `;
  printClinicalSheet(sheet);
}

// Espera pelo logótipo e só depois imprime.
//
// BUG CORRIGIDO (2026-07-16, reportado pelo utilizador): window.print()
// era chamado logo a seguir a sheet.innerHTML=..., que acabou de criar
// um <img> novo — inserir HTML não garante que a imagem já está
// decodificada/pintada nesse instante, mesmo vindo de cache. Nalguns
// casos o PDF/impressão saía com a caixa do logótipo em branco. Agora
// espera-se por img.decode() (ou o evento load, se decode() falhar/não
// existir) antes de imprimir; timeout de segurança de 800ms garante que
// a impressão nunca fica bloqueada para sempre se a imagem não carregar.
//
// EXTRAÍDO PARA FUNÇÃO PRÓPRIA (2026-09-07, RF-12): o relatório semanal
// automático precisa exatamente do mesmo comportamento. Duplicar este
// bloco era garantir que a correção acima só seria aplicada a metade das
// exportações da próxima vez que alguém lhe mexesse — há UM mecanismo de
// impressão neste ficheiro, e é este.
function printClinicalSheet(sheet){
  const logoImg = sheet.querySelector('.print-logo img');
  const printNow = () => window.print();
  if (logoImg && logoImg.decode) {
    let printed = false;
    const done = () => { if (!printed) { printed = true; printNow(); } };
    setTimeout(done, 800);
    logoImg.decode().catch(() => {}).then(done);
  } else {
    printNow();
  }
}

// Tabela de impressão — a mesma que exportClinicalPdf() já construía
// inline. Extraída (2026-09-07) para o relatório semanal poder reutilizá-la
// em vez de trazer um segundo estilo de tabela para o mesmo PDF.
// Escapa sempre as células: o relatório semanal inclui texto vindo da API
// (títulos/descrições de alertas gravados por outros componentes), que não
// é de confiança para injeção direta em innerHTML.
function printTableHtml(headers, rows){
  return `
    <table class="print-table">
      <thead><tr>${headers.map(h => `<th>${escapeHtml(String(h))}</th>`).join('')}</tr></thead>
      <tbody>
        ${rows.length
          ? rows.map(cells => `<tr>${cells.map(c => `<td>${escapeHtml(String(c ?? '—'))}</td>`).join('')}</tr>`).join('')
          : `<tr><td colspan="${headers.length}" class="print-empty">Sem registos.</td></tr>`}
      </tbody>
    </table>`;
}

/* ============================================================
   RF-12 — RELATÓRIO SEMANAL AUTOMÁTICO POR PACIENTE (2026-09-07)
   ------------------------------------------------------------
   Critério de aceitação: relatório semanal por paciente com rotina,
   sinais vitais, alertas e adesão à medicação.

   DECISÃO — REUTILIZAR, NÃO DUPLICAR: o relatório sai pela MESMA folha
   de impressão (`#clinicalPrintSheet`) e pelo MESMO mecanismo de
   impressão (`printClinicalSheet`) que o "Resumo clínico" já usava. Não
   se criou um segundo exportador em paralelo; a única diferença é o
   conteúdo da folha e a janela temporal (7 dias em vez de "agora").

   FONTE DOS DADOS — dois níveis, por esta ordem:
     1. API (`GET /api/patients/{id}/weekly-report`, bridge/api.py) —
        agrega os 7 dias a partir do que está REALMENTE persistido em
        SQLite (ActivityWindow, SensorRecord, Alert, MedicationAdherence).
        É esta a fonte boa, e é a mesma que o Cron usa.
     2. Dados desta sessão do dashboard (tendência semanal, alertas,
        registo de medicação em localStorage) — usados quando a API não
        responde ou quando o paciente selecionado não tem correspondência
        numérica na base de dados (ver `weeklyReportApiPatientId`).
   O PDF diz SEMPRE de qual das duas veio, secção a secção. Um relatório
   clínico que não distingue dados reais de dados de demonstração é pior
   do que não haver relatório nenhum.

   AGENDAMENTO PERIÓDICO: fica do lado do Cron que o projeto já tem
   (relatório do projeto, cap. 6 — hoje usado para limpeza de registos
   antigos). A tarefa semanal chama o endpoint por paciente e arquiva o
   JSON; este botão é a geração a pedido, do mesmo relatório. Não se
   introduziu nenhum agendador novo — nem no browser (um setInterval só
   corre com o separador aberto, o que não é agendamento) nem no bridge.
============================================================ */
const WEEKLY_REPORT_DAYS = 7;

// Categorias de rotina: chave da API (inglês, ver o CheckConstraint de
// ActivityWindow em storage_advanced.py) -> etiqueta do dashboard.
const WEEKLY_ROUTINE_LABELS = {
  sleep: 'Dormir', rest: 'Descanso', activity: 'Atividade',
  eating: 'Alimentação', hygiene: 'Higiene',
};
// E o inverso, para as chaves locais de ROUTINE_CATS (demo-data-baseline.js).
const WEEKLY_LOCAL_ROUTINE_LABELS = {
  dormir: 'Dormir', descanso: 'Descanso', atividade: 'Atividade',
  alimentacao: 'Alimentação', higiene: 'Higiene',
};

// LIMITAÇÃO CONHECIDA E DELIBERADA (2026-09-07): os pacientes do
// dashboard têm ids de demonstração ('p1'/'p2'/'p3'), enquanto a API usa
// a chave primária inteira de `patients`. Não existe hoje nenhuma tabela
// de correspondência entre os dois — o dashboard nunca precisou dela
// porque fala com o bridge por WebSocket, não por esta API. Em vez de
// inventar um mapeamento (ex.: 'p1' -> 1), que daria relatórios do
// PACIENTE ERRADO na primeira base de dados real em que os ids não
// coincidissem, devolve-se null e cai-se nos dados locais, dizendo-o no
// PDF. `p.dbId` já é lido aqui para que ligar isto seja só passar a
// preencher esse campo quando a correspondência existir.
function weeklyReportApiPatientId(p){
  if (p && p.dbId != null && /^\d+$/.test(String(p.dbId))) return Number(p.dbId);
  if (p && /^\d+$/.test(String(p.id))) return Number(p.id);
  return null;
}

// Vai buscar o relatório à API. Devolve null (nunca lança) quando não há
// id numérico, quando não há sessão iniciada, ou quando a API responde
// erro/não responde — o relatório sai à mesma, só que com dados locais.
async function fetchWeeklyReportFromApi(p, endDate){
  const apiId = weeklyReportApiPatientId(p);
  if (apiId === null || typeof apiFetch !== 'function') return null;
  try {
    const query = endDate ? `?end=${encodeURIComponent(endDate)}` : '';
    const res = await apiFetch(`/api/patients/${apiId}/weekly-report${query}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    return null;
  }
}

// Agrega os dados que o dashboard já tem nesta sessão, na mesma forma que
// a API devolve — para o resto do código só ter de lidar com um formato.
function buildLocalWeeklyReport(p){
  // --- Rotina: minutos por categoria do último dia disponível ---
  const rotina = {};
  Object.keys(WEEKLY_LOCAL_ROUTINE_LABELS).forEach(k => { rotina[k] = 0; });
  (typeof currentRoutineToday === 'function' ? currentRoutineToday() : []).forEach(b => {
    if (rotina[b.cat] === undefined) rotina[b.cat] = 0;
    rotina[b.cat] += Math.max(0, (b.end || 0) - (b.start || 0));
  });

  // --- Sinais vitais: a série de tendência dos últimos 7 dias ---
  const trend = (typeof currentTrendData === 'function' ? currentTrendData() : []).slice(-WEEKLY_REPORT_DAYS);
  const media = (arr) => arr.length ? Math.round((arr.reduce((a, b) => a + b, 0) / arr.length) * 10) / 10 : null;
  const vitais = {
    dias: trend.length,
    fcMedia: media(trend.map(d => d.fc).filter(v => v != null)),
    passosMedia: media(trend.map(d => d.passos).filter(v => v != null)),
    sonoMedio: media(trend.map(d => d.sono).filter(v => v != null)),
  };

  // --- Alertas: respeitam o consentimento, tal como o resumo clínico ---
  // Mesmo lapso que já tinha sido corrigido em buildFhirBundle() e em
  // exportClinicalPdf(): se o utente/família não autorizou a partilha de
  // alertas, este relatório não os pode incluir só por ser "automático".
  const consented = loadConsent(p.id).shareAlerts;
  const alertas = consented ? (typeof currentAlerts === 'function' ? currentAlerts() : []) : [];
  const anomalias = consented ? (typeof currentAnomalyLog === 'function' ? currentAnomalyLog() : []) : [];

  // --- Adesão à medicação ---
  const historico = (p.adherenceHistory || []).slice(-WEEKLY_REPORT_DAYS);
  const hoje = (typeof todayAdherencePct === 'function') ? todayAdherencePct(p) : null;
  const meds = (typeof patientMedications === 'function') ? patientMedications(p) : (p.medications || []);

  return { rotina, vitais, alertas, anomalias, consented, historico, hoje, meds };
}

// Monta o HTML da folha de impressão do relatório semanal.
function weeklyReportSheetHtml(p, local, remote){
  const geradoEm = new Date().toLocaleString('pt-PT');
  const fonte = remote
    ? 'Dados reais da base de dados (API, últimos 7 dias).'
    : 'Dados desta sessão do dashboard — a API não respondeu ou este paciente não tem correspondência na base de dados. Trate como demonstração, não como registo clínico.';
  const periodo = remote && remote.period
    ? `${new Date(remote.period.start).toLocaleDateString('pt-PT')} a ${new Date(remote.period.end).toLocaleDateString('pt-PT')}`
    : `últimos ${WEEKLY_REPORT_DAYS} dias`;

  // --- Rotina ---
  const rotinaRows = remote
    ? Object.keys(WEEKLY_ROUTINE_LABELS).map(k => {
        const r = (remote.rotina || {})[k] || {};
        return [WEEKLY_ROUTINE_LABELS[k], `${r.total_minutes || 0} min`,
                `${r.daily_average_minutes || 0} min/dia`, r.windows_count || 0];
      })
    : Object.keys(WEEKLY_LOCAL_ROUTINE_LABELS).map(k => [
        WEEKLY_LOCAL_ROUTINE_LABELS[k], `${local.rotina[k] || 0} min`, '—', '—',
      ]);
  const rotinaNota = remote ? '' :
    '<p class="print-meta">Distribuição do último dia disponível no dashboard (os blocos de rotina simulados cobrem um dia, não a semana).</p>';

  // --- Sinais vitais ---
  const vitaisRows = remote
    ? [
        ['Frequência cardíaca (bpm)', remote.sinais_vitais.heart_rate],
        ['SpO₂ (%)', remote.sinais_vitais.spo2_percent],
        ['Passos (contador)', remote.sinais_vitais.steps],
      ].map(([nome, v]) => v
        ? [nome, v.avg, v.min, v.max, v.count]
        : [nome, 'não medido', '—', '—', 0])
    : [
        ['Frequência cardíaca (bpm)', local.vitais.fcMedia ?? 'não medido', '—', '—', local.vitais.dias],
        ['Passos por dia', local.vitais.passosMedia ?? 'não medido', '—', '—', local.vitais.dias],
        ['Sono por noite (h)', local.vitais.sonoMedio ?? 'não medido', '—', '—', local.vitais.dias],
      ];

  // --- Alertas ---
  let alertasHtml;
  if (remote) {
    const sev = remote.alertas.por_severidade || {};
    alertasHtml = `
      <p class="print-meta">Total na semana: <b>${remote.alertas.total}</b> —
        crítico ${sev.critical || 0}, grave ${sev.serious || 0},
        aviso ${sev.warning || 0}, informativo ${sev.info || 0}.</p>
      ${printTableHtml(['Quando', 'Severidade', 'Título', 'Detalhe', 'Resolvido'],
        (remote.alertas.recentes || []).map(a => [
          a.created_at ? new Date(a.created_at).toLocaleString('pt-PT') : '—',
          a.severity, a.title, a.description, a.resolved ? 'Sim' : 'Não',
        ]))}`;
  } else {
    alertasHtml = `
      ${printTableHtml(['Título', 'Detalhe', 'Severidade', 'Quando'],
        local.alertas.map(a => [alertField(a, 'title'), alertField(a, 'desc'), a.sev, alertField(a, 'time')]))}
      <h3 class="print-section-title">Anomalias detetadas</h3>
      ${printTableHtml(['ID', 'Tipo', 'Detalhe', 'Quando'],
        local.anomalias.map(a => [a.id, anomalyTypeText(a), anomalyDetailText(a), a.time]))}`;
  }
  const consentNote = local.consented ? '' :
    '<p class="print-meta"><b>Nota:</b> o utente/família não autorizou a partilha de alertas e anomalias com a equipa clínica (ver Definições → Consentimento) — omitidos deste relatório.</p>';

  // --- Adesão ---
  const adesaoHtml = remote
    ? `<p class="print-meta">Adesão global na semana: <b>${Math.round(remote.adesao_medicacao.overall_percent)}%</b></p>
       ${printTableHtml(['Medicamento', 'Doses tomadas', 'Doses agendadas', 'Adesão'],
         (remote.adesao_medicacao.medications || []).map(m => [
           m.medication_name, m.taken, m.total, `${Math.round(m.percent)}%`,
         ]))}`
    : `<p class="print-meta">Adesão de hoje: <b>${local.hoje == null ? 'sem doses agendadas' : local.hoje + '%'}</b></p>
       ${printTableHtml(['Medicamento', 'Dose', 'Horas'],
         local.meds.map(m => [m.name, m.dose, (m.times || []).join(', ')]))}
       <h3 class="print-section-title">Histórico de adesão (últimos dias)</h3>
       ${printTableHtml(['Dia', 'Adesão'], local.historico.map(h => [h.day, `${h.pct}%`]))}`;

  return `
    <header class="print-header">
      <div class="print-logo"><img src="assets/logo.png" alt="CareWear"></div>
      <div class="print-header-text">
        <h1>CareWear — Relatório semanal</h1>
        <p class="print-meta">Período: ${escapeHtml(periodo)} · Gerado em ${escapeHtml(geradoEm)}</p>
        <p class="print-meta">Origem: ${escapeHtml(fonte)}</p>
      </div>
    </header>
    <section class="print-patient">
      <h2>${escapeHtml(p.name)} · ${p.age} anos</h2>
      <p class="print-meta">Dispositivo: ${escapeHtml(p.deviceName)} · ${escapeHtml(p.mac)} · Última sincronização: ${escapeHtml(p.lastSync)}</p>
      ${consentNote}
    </section>

    <h3 class="print-section-title">1. Rotina diária</h3>
    ${printTableHtml(
      remote ? ['Categoria', 'Total na semana', 'Média por dia', 'Blocos'] : ['Categoria', 'Duração', 'Média por dia', 'Blocos'],
      rotinaRows)}
    ${rotinaNota}

    <h3 class="print-section-title">2. Sinais vitais</h3>
    ${printTableHtml(['Sinal', 'Média', 'Mínimo', 'Máximo', 'Amostras'], vitaisRows)}

    <h3 class="print-section-title">3. Alertas</h3>
    ${alertasHtml}

    <h3 class="print-section-title">4. Adesão à medicação</h3>
    ${adesaoHtml}

    <footer class="print-footer">CareWear · Relatório semanal gerado localmente, confidencial · ${escapeHtml(p.name)}</footer>
  `;
}

// Ponto de entrada do botão "Relatório semanal (PDF)".
//
// É `async` porque tenta primeiro a API; a folha só é escrita depois de se
// saber qual das duas fontes se vai usar, para nunca haver um instante em
// que o PDF mostra dados locais rotulados como reais.
async function exportWeeklyReportPdf(endDate){
  const p = selectedPatient();
  const sheet = document.getElementById('clinicalPrintSheet');
  if (!sheet) return;
  const hint = document.getElementById('weeklyReportHint');
  if (hint){ hint.style.color = ''; hint.textContent = 'A preparar relatório…'; }

  const remote = await fetchWeeklyReportFromApi(p, endDate);
  const local = buildLocalWeeklyReport(p);
  sheet.innerHTML = weeklyReportSheetHtml(p, local, remote);

  if (hint){
    hint.style.color = remote ? 'var(--status-good)' : 'var(--status-warning)';
    hint.textContent = remote
      ? 'Relatório com dados reais da base de dados.'
      : 'Relatório com dados desta sessão (API indisponível ou paciente sem correspondência na base de dados).';
  }
  printClinicalSheet(sheet);
}

// Tenta ligar assim que a página carrega (mesmo antes do login, para o
// estado do dispositivo já estar correto quando o utilizador entrar).
connectBridge();

// Idioma: preenche o seletor e aplica as traduções guardadas (ou
// Português por omissão) assim que a página carrega.
populateLangSelect();
applyI18n();

// Só volta a desenhar os canvases (via AFTER_RENDER), sem recriar o HTML da
// vista inteira com renderView() — recriar o HTML apagava silenciosamente
// texto ainda não submetido (ex: "Notas do cuidador") e edições em curso
// (ex: limites de duração na área Médico/Técnico) sempre que a janela era
// redimensionada.
