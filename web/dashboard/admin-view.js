/**
 * admin-view.js — Vista "Administrador de hospital/clínica"
 * ------------------------------------------------------------
 * Mesmo padrão de medication-reminders.js: script clássico global (sem
 * imports/exports ES, sem build step), carregado DEPOIS do <script>
 * principal de index.html — pode por isso assumir que t(), escapeHtml(),
 * PATIENTS, TEMPLATES, AFTER_RENDER, isAdminUser(), ADMIN_EMAIL,
 * loadClinicianAssignments(), selectPatient(), activateNavItem(),
 * statTile(), pillHtml() e activeAlertsCount() já existem no escopo global.
 *
 * DECISÕES DE DESIGN (para a utilizadora rever):
 *
 * 1) "Lista de médicos/técnicos" — antes desta funcionalidade não existia
 *    nenhum registo de quem são os clínicos, só o email usado como chave
 *    em CLINICIAN_ASSIGNMENTS_KEY (ver index.html). Criei um registo novo,
 *    ALL_CLINICIANS_KEY, preenchido quando alguém cria conta como
 *    Médico/Técnico (ver chamada a registerClinicianAccount() em
 *    submitSignup(), em index.html) — guarda nome, instituição e cédula
 *    profissional por email. allCliniciansList() junta este registo com
 *    quaisquer emails que só existam em CLINICIAN_ASSIGNMENTS_KEY (ex.:
 *    dados de sessões anteriores a esta funcionalidade existir, ou
 *    atribuições feitas sem passar pelo ecrã de signup), para o admin
 *    nunca perder visibilidade sobre uma conta só por faltar o nome —
 *    nesses casos mostra o próprio email como "nome".
 *
 * 2) REVERTIDO (2026-08-06, pedido explícito da utilizadora): esta vista
 *    tinha originalmente um botão "Ver dados" que reaproveitava
 *    selectPatient() + a barra lateral clínica (#navClinico) para o admin
 *    abrir o dossiê clínico completo de qualquer paciente (sinais vitais,
 *    anomalias, medicação, exportações). A utilizadora identificou isto
 *    como invasão de privacidade — o perfil Administrador serve para
 *    NAVEGAR/ORGANIZAR (ver quem são os médicos, os pacientes, quem trata
 *    quem), não para ler dados clínicos de ninguém. #navClinico deixou de
 *    ser concedido a este perfil (ver login() em index.html) e a função
 *    adminViewPatient()/o botão correspondente foram removidos — a tabela
 *    de pacientes abaixo mostra só metadados organizacionais (nome,
 *    dispositivo, médicos atribuídos, contagem agregada de alertas ativos
 *    — um número, não o conteúdo dos alertas).
 *
 * LIMITAÇÃO ASSUMIDA (design já existente do projeto, não um bug desta
 * funcionalidade): sem backend real, "registar um clínico" é só gravar em
 * localStorage deste browser — outro browser/dispositivo não vê a mesma
 * lista. Mesma limitação que já existia em CLINICIAN_ASSIGNMENTS_KEY.
 */

const ALL_CLINICIANS_KEY = 'carewear_all_clinicians';

function loadAllClinicians(){
  try {
    const raw = localStorage.getItem(ALL_CLINICIANS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  // Valor por omissão (2026-08-06): mesmo espírito do fallback em
  // loadClinicianAssignments() (pacientes-alertas-medicacao.js) — sem
  // isto, o Dr. Ricardo aparecia na tabela só pelo email (sem nome),
  // porque nunca passou por submitSignup() num browser novo.
  return { [DEFAULT_CLINICIAN_EMAIL]: { name: 'Dr. Ricardo Alves', institution: 'Centro de Saúde de Barcelos', license: 'OM-12345' } };
}
function saveAllClinicians(map){
  try { localStorage.setItem(ALL_CLINICIANS_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}

// Chamada por submitSignup() (perfil Médico/Técnico) em index.html — é o
// único sítio que cria/atualiza este registo (mesmo espírito de
// registerOwnPatient() para o perfil Utente/Família: só quem se regista
// de facto fica com um nome associado ao email).
function registerClinicianAccount(email, name, institution, license){
  if (!email) return;
  const key = email.trim().toLowerCase();
  const map = loadAllClinicians();
  map[key] = {
    name: (name || '').trim() || key,
    institution: (institution || '').trim(),
    license: (license || '').trim(),
  };
  saveAllClinicians(map);
}

// Lista consolidada de todos os clínicos "conhecidos" pelo sistema — ver
// decisão de design (1) no cabeçalho deste ficheiro.
function allCliniciansList(){
  const registered = loadAllClinicians();
  const assignments = loadClinicianAssignments();
  const emails = new Set([...Object.keys(registered), ...Object.keys(assignments)]);
  return Array.from(emails).sort().map(email => {
    const info = registered[email] || {};
    return {
      email,
      name: info.name || email,
      institution: info.institution || '—',
      license: info.license || '—',
      patientIds: assignments[email] || [],
    };
  });
}

// Nomes (ou emails, se não registados) dos clínicos atribuídos a um
// paciente — usado na tabela "Todos os pacientes" abaixo.
function clinicianNamesForPatient(patientId){
  const assignments = loadClinicianAssignments();
  const registered = loadAllClinicians();
  return Object.keys(assignments)
    .filter(email => (assignments[email] || []).includes(patientId))
    .map(email => (registered[email] && registered[email].name) || email);
}

// Relatório de paciente (2026-08-06, ver comentário junto do modal
// #adminPatientReportOverlay em index.html) — SIMPLIFICADO a pedido
// explícito da utilizadora: "um administrador não tem de ver os dados
// reais de um paciente, o relatório exportável que os médicos têm
// basta." A versão anterior deste modal mostrava tabelas de alertas e
// medicação em modo só-leitura — removido; fica só o acesso às mesmas 3
// exportações (FHIR/CSV/PDF) que qualquer Médico/Técnico já tem, que
// SÃO o "relatório" a que o admin tem direito, não uma vista adicional
// dos dados brutos.
function openAdminPatientReportModal(id){
  selectPatient(id);
  const p = selectedPatient();

  document.getElementById('adminPatientReportTitle').textContent =
    `${t('admin.reportTitle')} — ${p.name}`;

  document.getElementById('adminPatientReportBody').innerHTML = `
    <p class="empty-hint">${t('admin.reportExportOnlyNote')}</p>
    <div class="modal-actions" style="justify-content:flex-start;">
      <button class="btn-secondary" onclick="exportFhirSummary()">${t('exportar.exportFhirBtn')}</button>
      <button class="btn-secondary" onclick="exportRealCsv(EXPORT_ALL_HOURS)">${t('exportar.exportAllBtn')}</button>
      <button class="btn-secondary" onclick="exportClinicalPdf()">${t('exportar.printPdfBtn')}</button>
    </div>
  `;
  document.getElementById('adminPatientReportOverlay').style.display = 'flex';
}

function closeAdminPatientReportModal(){
  document.getElementById('adminPatientReportOverlay').style.display = 'none';
}

/* ============================================================
   TEMPLATE — VISTA "ADMINISTRAÇÃO"
============================================================ */
TEMPLATES.admin = () => {
  const clinicians = allCliniciansList();
  const patients = PATIENTS;
  const totalActiveAlerts = patients.reduce((sum, p) => sum + activeAlertsCount(p), 0);
  const unassignedCount = patients.filter(p => clinicianNamesForPatient(p.id).length === 0).length;

  return `
  <div class="stat-row">
    ${statTile('walk', t('admin.statClinicians'), clinicians.length, '', 'var(--accent)')}
    ${statTile('heart', t('admin.statPatients'), patients.length, '', 'var(--link)')}
    ${statTile('warn', t('admin.statActiveAlerts'), totalActiveAlerts, '', 'var(--status-critical)')}
    ${statTile('zap', t('admin.statUnassigned'), unassignedCount, '', 'var(--status-warning)')}
  </div>

  <div class="card">
    <div class="card-head"><div><h3>${t('admin.cliniciansCardTitle')}</h3><div class="card-sub">${t('admin.cliniciansCardSubtitle')}</div></div></div>
    ${clinicians.length ? `
    <table class="data-table">
      <thead><tr>
        <th>${t('admin.thName')}</th><th>${t('admin.thEmail')}</th><th>${t('admin.thInstitution')}</th>
        <th>${t('admin.thLicense')}</th><th>${t('admin.thPatientsCount')}</th>
      </tr></thead>
      <tbody>
        ${clinicians.map(c => {
          const isAdminAccount = c.email === ADMIN_EMAIL;
          return `
          <tr>
            <td>${escapeHtml(c.name)}${isAdminAccount ? ` ${pillHtml('good', t('admin.adminBadge'))}` : ''}</td>
            <td class="num">${escapeHtml(c.email)}</td>
            <td>${escapeHtml(c.institution)}</td>
            <td class="num">${escapeHtml(c.license)}</td>
            <td class="num">${isAdminAccount ? patients.length : c.patientIds.length}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    ` : `<p class="empty-hint">${t('admin.noCliniciansEmpty')}</p>`}
  </div>

  <div class="card">
    <div class="card-head"><div><h3>${t('admin.patientsCardTitle')}</h3><div class="card-sub">${patients.length} ${t(patients.length === 1 ? 'admin.patientsSingular' : 'admin.patientsPlural')}</div></div></div>
    <table class="data-table">
      <thead><tr>
        <th>${t('pacientes.thPatient')}</th><th>${t('pacientes.thDevice')}</th>
        <th>${t('admin.thAssignedClinicians')}</th><th>${t('pacientes.thActiveAlerts')}</th><th></th>
      </tr></thead>
      <tbody>
        ${patients.map(p => {
          const names = clinicianNamesForPatient(p.id);
          const n = activeAlertsCount(p);
          return `
          <tr>
            <td><b>${escapeHtml(p.name)}</b> · ${p.age} anos</td>
            <td class="num">${escapeHtml(p.deviceName)}</td>
            <td>${names.length ? names.map(escapeHtml).join(', ') : `<span class="empty-hint" style="padding:0;">${t('admin.noAssignedClinician')}</span>`}</td>
            <td>${n > 0 ? pillHtml('critical', n + ' ' + t(n > 1 ? 'pacientes.activeAlertsPlural' : 'pacientes.activeAlertsSingular')) : pillHtml('good', t('pacientes.statusNone'))}</td>
            <!-- Só de leitura (ver openAdminPatientReportModal) — não é
                 o botão "Ver dados" antigo, que dava acesso à vista
                 clínica interativa completa (removido por pedido da
                 utilizadora); este abre um modal sem ações. -->
            <td><button class="btn-secondary" onclick="openAdminPatientReportModal('${p.id}')">${t('admin.viewReportBtn')}</button></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    <p class="empty-hint">${t('admin.reportsHint')}</p>
  </div>
`;
};
