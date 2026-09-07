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

  const tableHtml = (headers, rows) => `
    <table class="print-table">
      <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>
        ${rows.length ? rows.map(cells => `<tr>${cells.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')
          : `<tr><td colspan="${headers.length}" class="print-empty">Sem registos.</td></tr>`}
      </tbody>
    </table>`;

  sheet.innerHTML = `
    <header class="print-header">
      <div class="print-logo"><img src="assets/logo.png" alt="CareWear"></div>
      <div class="print-header-text">
        <h1>CareWear — Resumo clínico</h1>
        <p class="print-meta">Gerado em ${generatedAt} · Protótipo — dados de demonstração, não substituem avaliação clínica direta.</p>
      </div>
    </header>
    <section class="print-patient">
      <h2>${p.name} · ${p.age} anos</h2>
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
    <footer class="print-footer">CareWear · Documento gerado localmente, confidencial · ${p.name}</footer>
  `;
  // BUG CORRIGIDO (2026-07-16, reportado pelo utilizador): window.print()
  // era chamado logo a seguir a sheet.innerHTML=..., que acabou de criar
  // um <img> novo — inserir HTML não garante que a imagem já está
  // decodificada/pintada nesse instante, mesmo vindo de cache. Nalguns
  // casos o PDF/impressão saía com a caixa do logótipo em branco. Agora
  // espera-se por img.decode() (ou o evento load, se decode() falhar/não
  // existir) antes de imprimir; timeout de segurança de 800ms garante que
  // a impressão nunca fica bloqueada para sempre se a imagem não carregar.
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
