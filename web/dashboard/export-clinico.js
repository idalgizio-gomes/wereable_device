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
  // respeita o interruptor "Alertas e registo de anomalias" do Consentimento (antes ignorado por este export)
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

// Blob + <a download> temporário, sem deixar o elemento no DOM
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

// texto CSV já pronto (recebido do bridge) — mesma técnica de downloadJson(), sem JSON.stringify
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

// CSV de dados reais — pede ao bridge o histórico persistido em SQLite (não só o visível nesta sessão)
const EXPORT_ALL_HOURS = 87600; // 10 anos, proxy de "sem limite" (a API não tem esse conceito)

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

function exportClinicalPdf(){
  const p = selectedPatient();
  const sheet = document.getElementById('clinicalPrintSheet');
  if (!sheet) return;
  const consented = loadConsent(p.id).shareAlerts; // mesmo lapso da exportação FHIR — respeita consentimento
  const alerts = consented ? currentAlerts() : [];
  const anomalies = consented ? currentAnomalyLog() : [];
  const consentNote = consented ? '' : '<p class="print-meta"><b>Nota:</b> o utente/família não autorizou a partilha de alertas e anomalias com a equipa clínica (ver Definições → Consentimento) — omitidos deste resumo.</p>';
  const generatedAt = new Date().toLocaleString('pt-PT');

  // printTableHtml() partilhada (versão local anterior não escapava as células, e este texto pode vir de utilizadores)
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

// Espera por img.decode() (ou load) antes de imprimir — window.print() logo após innerHTML
// podia sair com o logótipo em branco (imagem ainda não decodificada). Timeout de 800ms evita
// bloqueio se a imagem falhar. Função própria porque o relatório semanal usa o mesmo mecanismo.
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

// Escapa sempre as células — inclui texto vindo da API (alertas gravados por outros componentes),
// que não é de confiança para innerHTML direto.
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

// RF-12 — relatório semanal por paciente: reutiliza a mesma folha/mecanismo de impressão do
// "Resumo clínico" (não um segundo exportador). Fonte de dados em dois níveis: API
// (GET /api/patients/{id}/weekly-report, agrega o realmente persistido em SQLite — mesma fonte
// que o Cron usa) ou, se indisponível/paciente sem correspondência numérica, os dados desta
// sessão do dashboard — o PDF diz sempre qual das duas usou, secção a secção. Agendamento
// periódico fica do lado do Cron já existente; este botão é só a geração a pedido.
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

// Pacientes do dashboard têm ids de demonstração ('p1'/'p2'/'p3'); a API usa a PK inteira de
// `patients`, sem tabela de correspondência entre os dois. Em vez de inventar um mapeamento
// (ex.: 'p1' -> 1), que arriscaria dar o relatório do paciente errado, devolve-se null e cai-se
// nos dados locais (dito no PDF). `p.dbId` já é lido para quando a correspondência existir.
function weeklyReportApiPatientId(p){
  if (p && p.dbId != null && /^\d+$/.test(String(p.dbId))) return Number(p.dbId);
  if (p && /^\d+$/.test(String(p.id))) return Number(p.id);
  return null;
}

// Devolve null (nunca lança) sem id numérico, sem sessão, ou se a API falhar — o relatório
// sai à mesma, com dados locais.
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

// Agrega os dados desta sessão na mesma forma que a API devolve, para o resto do código só
// lidar com um formato.
function buildLocalWeeklyReport(p){
  // Rotina: minutos por categoria do último dia disponível
  const rotina = {};
  Object.keys(WEEKLY_LOCAL_ROUTINE_LABELS).forEach(k => { rotina[k] = 0; });
  (typeof currentRoutineToday === 'function' ? currentRoutineToday() : []).forEach(b => {
    if (rotina[b.cat] === undefined) rotina[b.cat] = 0;
    rotina[b.cat] += Math.max(0, (b.end || 0) - (b.start || 0));
  });

  // Sinais vitais: série de tendência dos últimos 7 dias
  const trend = (typeof currentTrendData === 'function' ? currentTrendData() : []).slice(-WEEKLY_REPORT_DAYS);
  const media = (arr) => arr.length ? Math.round((arr.reduce((a, b) => a + b, 0) / arr.length) * 10) / 10 : null;
  const vitais = {
    dias: trend.length,
    fcMedia: media(trend.map(d => d.fc).filter(v => v != null)),
    passosMedia: media(trend.map(d => d.passos).filter(v => v != null)),
    sonoMedio: media(trend.map(d => d.sono).filter(v => v != null)),
  };

  // Alertas respeitam o consentimento, como em buildFhirBundle()/exportClinicalPdf()
  const consented = loadConsent(p.id).shareAlerts;
  const alertas = consented ? (typeof currentAlerts === 'function' ? currentAlerts() : []) : [];
  const anomalias = consented ? (typeof currentAnomalyLog === 'function' ? currentAnomalyLog() : []) : [];

  const historico = (p.adherenceHistory || []).slice(-WEEKLY_REPORT_DAYS);
  const hoje = (typeof todayAdherencePct === 'function') ? todayAdherencePct(p) : null;
  const meds = (typeof patientMedications === 'function') ? patientMedications(p) : (p.medications || []);

  return { rotina, vitais, alertas, anomalias, consented, historico, hoje, meds };
}

function weeklyReportSheetHtml(p, local, remote){
  const geradoEm = new Date().toLocaleString('pt-PT');
  const fonte = remote
    ? 'Dados reais da base de dados (API, últimos 7 dias).'
    : 'Dados desta sessão do dashboard — a API não respondeu ou este paciente não tem correspondência na base de dados. Trate como demonstração, não como registo clínico.';
  const periodo = remote && remote.period
    ? `${new Date(remote.period.start).toLocaleDateString('pt-PT')} a ${new Date(remote.period.end).toLocaleDateString('pt-PT')}`
    : `últimos ${WEEKLY_REPORT_DAYS} dias`;

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

// async: tenta a API primeiro; a folha só é escrita depois de saber qual fonte usar, para
// nunca haver um instante em que o PDF mostra dados locais rotulados como reais.
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

// Liga já ao carregar a página (antes do login), para o estado do dispositivo estar correto
// quando o utilizador entrar.
connectBridge();

populateLangSelect();
applyI18n();

// Só redesenha os canvases (AFTER_RENDER), sem recriar o HTML via renderView() — isso apagava
// silenciosamente texto ainda não submetido (ex: notas do cuidador) ao redimensionar a janela.
