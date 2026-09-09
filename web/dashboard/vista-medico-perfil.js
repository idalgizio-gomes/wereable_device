// Constrói o campo HTML de um dado sensível (morada, NIF): se houver uma
// alteração pendente de aprovação, mostra-a claramente em vez do campo
// editável normal, para não parecer que a alteração já foi aplicada.
function sensitiveProfileFieldHtml(role, field, label, currentValue, inputType){
  const pending = loadPendingProfileChanges();
  const pendingChange = pending[role] && pending[role][field];
  if (pendingChange) {
    return `
    <div class="field">
      <label>${label}</label>
      <div class="empty-hint" style="padding:8px 10px; background:var(--status-warning-bg); border-radius:var(--radius-sm); color:var(--status-warning);">
        ${t('perfil.currentValueLabel')} <b>${escapeHtml(currentValue)}</b><br>
        ${t('perfil.pendingChangeLabel')} <b>${escapeHtml(pendingChange.newValue)}</b>
      </div>
    </div>`;
  }
  return `
    <div class="field">
      <label for="profile_${field}">${label} <span class="empty-hint" style="padding:0; display:inline;">— ${t('perfil.requiresApprovalNote')}</span></label>
      <input id="profile_${field}" type="${inputType || 'text'}" value="${escapeHtml(currentValue)}">
    </div>`;
}

// BUG DE XSS CORRIGIDO (2026-09-07): os campos do próprio perfil são texto
// livre escrito pelo utilizador (ver submitProfileForm(), que grava
// el.value.trim() tal e qual em localStorage) e entravam sem escaping
// dentro de value="..." — um nome como  Maria" onfocus="…" autofocus x="
// fecha o atributo e executa código ao voltar a abrir a vista Perfil. Este
// mesmo ficheiro já usava escapeHtml() nos dois sítios em texto (valor
// pendente e tabela de aprovações), e vista-definicoes-ajuda.js já o usa
// exatamente neste padrão value="${escapeHtml(...)}" — faltava só aqui.
// escapeHtml() escapa " (mas não '), por isso os atributos têm de
// continuar delimitados por aspas duplas, como estão.
TEMPLATES.perfil = () => {
  const isUtente = currentRole === 'utente';
  const role = isUtente ? 'utente' : 'clinico';
  const p = loadProfile()[role];
  return `
  <div class="card">
    <div class="card-head"><div><h3>${t('perfil.myProfileTitle')}</h3><div class="card-sub">${t('perfil.myProfileSubtitle')}</div></div></div>
    <div class="field">
      <label for="profile_name">${t('perfil.nameLabel')}</label>
      <input id="profile_name" type="text" value="${escapeHtml(p.name)}">
    </div>
    <div class="field">
      <label for="profile_email">${t('login.email')}</label>
      <input id="profile_email" type="email" value="${escapeHtml(p.email)}">
    </div>
    <div class="field">
      <label for="profile_phone">${t('perfil.phoneLabel')}</label>
      <input id="profile_phone" type="tel" value="${escapeHtml(p.phone || '')}">
    </div>
    ${sensitiveProfileFieldHtml(role, 'nif', t('perfil.nifLabel'), p.nif || '')}
    ${isUtente ? sensitiveProfileFieldHtml(role, 'address', t('perfil.addressLabel'), p.address || '') : ''}
    ${!isUtente ? `
    <div class="field">
      <label for="profile_institution">${t('perfil.institutionLabel')}</label>
      <input id="profile_institution" type="text" value="${escapeHtml(p.institution || '')}">
    </div>
    <div class="field">
      <label for="profile_license">${t('perfil.licenseLabel')}</label>
      <input id="profile_license" type="text" value="${escapeHtml(p.license || '')}">
    </div>` : ''}
    <button class="btn-primary" onclick="submitProfileForm()" style="width:auto; padding:8px 20px;">${t('perfil.saveChangesBtn')}</button>
    <p class="modal-status" id="profileSaveStatus"></p>
    <p class="empty-hint">${t('perfil.prototypeStorageEmpty')}</p>
  </div>

  ${isUtente ? `
  <div class="card">
    <div class="card-head"><div><h3>${t('perfil.emergencyContactTitle')}</h3><div class="card-sub">${t('perfil.emergencyContactSubtitle')}</div></div></div>
    <div class="field">
      <label for="profile_caregiverName">${t('perfil.caregiverNameLabel')}</label>
      <input id="profile_caregiverName" type="text" value="${escapeHtml(p.caregiverName || '')}">
    </div>
    <div class="field">
      <label for="profile_caregiverPhone">${t('perfil.caregiverPhoneLabel')}</label>
      <input id="profile_caregiverPhone" type="tel" value="${escapeHtml(p.caregiverPhone || '')}">
    </div>
    <div class="field">
      <label for="profile_caregiverRelation">${t('perfil.caregiverRelationLabel')}</label>
      <input id="profile_caregiverRelation" type="text" value="${escapeHtml(p.caregiverRelation || '')}">
    </div>
    <button class="btn-primary" onclick="submitProfileForm()" style="width:auto; padding:8px 20px;">${t('perfil.saveChangesBtn')}</button>
  </div>` : ''}

  ${!isUtente ? (() => {
    const pending = loadPendingProfileChanges();
    const utentePending = pending.utente || {};
    const entries = Object.entries(utentePending);
    const fieldLabels = { nif: t('perfil.nifLabel'), address: t('perfil.addressLabel') };
    return `
  <div class="card">
    <div class="card-head"><div><h3>${t('perfil.pendingApprovalsTitle')}</h3><div class="card-sub">${t('perfil.pendingApprovalsSubtitle')}</div></div></div>
    ${entries.length ? `
    <table class="data-table">
      <thead><tr><th>${t('perfil.thField')}</th><th>${t('perfil.thRequestedValue')}</th><th>${t('perfil.thRequestedAt')}</th><th></th></tr></thead>
      <tbody>
        ${entries.map(([field, change]) => `
          <tr>
            <td>${fieldLabels[field] || field}</td>
            <td>${escapeHtml(change.newValue)}</td>
            <td class="num">${new Date(change.requestedAt).toLocaleString('pt-PT')}</td>
            <td style="display:flex; gap:8px;">
              <button class="btn-secondary" onclick="approveProfileFieldChange('utente','${field}')">${t('perfil.approveBtn')}</button>
              <button class="btn-secondary" onclick="rejectProfileFieldChange('utente','${field}')">${t('perfil.rejectBtn')}</button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    ` : `<p class="empty-hint">${t('perfil.pendingEmpty')}</p>`}
  </div>`;
  })() : ''}
`;
};

TEMPLATES.limites = () => `
  <div class="card">
    <div class="card-head"><div><h3>${t('limites.templateTitle')}</h3><div class="card-sub">${t('limites.templateSubtitle')}</div></div></div>
    <table class="data-table" id="limitsTable">
      <thead><tr><th>${t('limites.thStep')}</th><th>${t('limites.thTime')}</th><th>${t('limites.thActivity')}</th><th>${t('limites.thDmin')}</th><th>${t('limites.thDmax')}</th></tr></thead>
      <tbody>
        ${[
          [1,'00:00','Deitado (sono)',60,120],[2,'07:00','De pé',1,2],[3,'07:06','Higiene oral',1,3],
          [4,'07:18','Duche',5,10],[5,'07:48','Caminhar',2,5],[6,'08:00','Sentado',10,20],
          [7,'08:30','Comer à mão',5,10],[9,'09:30','Sentado',60,90],[13,'13:30','Sentado',60,90],
          [17,'19:00','Comer com talheres',10,20],[21,'22:00','Deitado (sono)',60,120],
        ].map(([step,time,act,dmin,dmax]) => `
          <tr>
            <td class="num">${step}</td><td class="num">${time}</td><td>${act}</td>
            <td class="num"><input class="row-input" type="number" value="${dmin}"></td>
            <td class="num"><input class="row-input" type="number" value="${dmax}"></td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    <p class="empty-hint">${t('limites.prototypeEmpty')}</p>
  </div>
`;

TEMPLATES.exportar = () => `
  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('exportar.fhirCardTitle')}</h3><div class="card-sub">${t('exportar.fhirCardSubtitle')}</div></div></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap;">
      <button class="btn-secondary" onclick="exportFhirSummary()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.exportFhirBtn')}
      </button>
      <button class="btn-secondary" onclick="exportClinicalPdf()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M6 9V2h9l5 5v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-2M6 14h4M6 18h2"/></svg>
        ${t('exportar.printPdfBtn')}
      </button>
    </div>
    <p class="empty-hint">${t('exportar.fhirNoteEmpty')}</p>
  </div>

  <!-- RF-12 (2026-09-07) — Relatório semanal automático.
       Texto em português literal (não passa por t()/i18n-strings.js) por
       decisão de âmbito: o ficheiro de traduções está a ser alterado por
       outro trabalho em paralelo e acrescentar chaves lá daria conflito.
       Passar estas 4 strings para i18n é trabalho por fazer, registado no
       relatório do requisito.
       O botão chama exportWeeklyReportPdf() (web/dashboard/export-clinico.js),
       que reutiliza a MESMA folha de impressão #clinicalPrintSheet no fim
       deste template — não há um segundo mecanismo de exportação. -->
  <div class="card print-hide">
    <div class="card-head"><div><h3>Relatório semanal</h3><div class="card-sub">Rotina, sinais vitais, alertas e adesão à medicação dos últimos 7 dias, num só PDF.</div></div></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
      <button class="btn-secondary" onclick="exportWeeklyReportPdf()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M6 9V2h9l5 5v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-2M6 14h4M6 18h2"/></svg>
        Gerar relatório semanal (PDF)
      </button>
      <label for="weeklyReportEnd" style="font-size:13px; color:var(--text-secondary);">Semana terminada em</label>
      <input id="weeklyReportEnd" type="date" class="row-input" style="width:150px;">
      <button class="btn-secondary" onclick="exportWeeklyReportPdf(document.getElementById('weeklyReportEnd').value || undefined)">
        Gerar para essa semana
      </button>
      <span class="empty-hint" id="weeklyReportHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">O mesmo relatório é gerado automaticamente pela tarefa periódica (Cron) do bridge; este botão serve para o obter a pedido. Quando a API não está acessível, o PDF sai com os dados desta sessão e diz-o no cabeçalho.</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('exportar.realCsvTitle')}</h3><div class="card-sub">${t('exportar.realCsvSubtitle')}</div></div></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
      <button class="btn-secondary" onclick="exportRealCsv(24)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.export24hBtn')}
      </button>
      <button class="btn-secondary" onclick="exportRealCsv(24*7)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.export7dBtn')}
      </button>
      <button class="btn-secondary" onclick="exportRealCsv(EXPORT_ALL_HOURS)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.exportAllBtn')}
      </button>
      <span class="empty-hint" id="csvExportHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('exportar.csvRequiresBridgeEmpty')}</p>
    <p class="empty-hint">${t('exportar.exportAllNote')}</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('exportar.retentionTitle')}</h3><div class="card-sub">${t('exportar.retentionSubtitle')}</div></div></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
      <label for="retentionDaysInput" style="font-size:13px; color:var(--text-secondary);">${t('exportar.retentionDaysLabel')}</label>
      <input id="retentionDaysInput" type="number" min="1" max="3650" step="1" style="width:90px;" class="row-input" placeholder="—">
      <button class="btn-secondary" onclick="saveRetentionDays()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>
        ${t('common.save')}
      </button>
      <span class="empty-hint" id="retentionHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('exportar.retentionNoteEmpty')}</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('consentimento.title')}</h3><div class="card-sub">${t('consentimento.subtitle')}</div></div></div>
    <div id="consentScopesList" style="display:flex; flex-direction:column; gap:10px;"></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:10px;">
      <label for="consentRepName" style="font-size:13px; color:var(--text-secondary);">${t('consentimento.repNameLabel')}</label>
      <input id="consentRepName" type="text" style="width:220px;" class="row-input" placeholder="${t('consentimento.repNamePlaceholder')}">
      <span class="empty-hint" id="consentHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('consentimento.noteEmpty')}</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('modeloVersao.title')}</h3><div class="card-sub">${t('modeloVersao.subtitle')}</div></div></div>
    <div id="modelVersionsList"></div>
    <span class="empty-hint" id="modelVersionsHint" style="margin:0;"></span>
    <p class="empty-hint">${t('modeloVersao.noteEmpty')}</p>
  </div>

  <div class="print-only" id="clinicalPrintSheet"></div>
`;
