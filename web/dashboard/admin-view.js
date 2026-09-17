/**
 * admin-view.js — Vista "Administrador de hospital/clínica"
 * Script clássico global (mesmo padrão de medication-reminders.js, sem
 * imports/build, carregado após o <script> principal de index.html) —
 * assume t(), escapeHtml(), PATIENTS, TEMPLATES, AFTER_RENDER,
 * isAdminUser(), ADMIN_EMAIL, loadClinicianAssignments(), selectPatient(),
 * activateNavItem(), statTile(), pillHtml(), activeAlertsCount() globais.
 *
 * ALL_CLINICIANS_KEY é um registo próprio (nome/instituição/cédula por
 * email), preenchido por registerClinicianAccount() no signup. allCliniciansList()
 * funde-o com CLINICIAN_ASSIGNMENTS_KEY para não perder contas sem nome
 * (sessões anteriores a esta funcionalidade) — mostra o email como nome nesse caso.
 *
 * Privacidade (decisão explícita da utilizadora): o Admin NÃO vê dados
 * clínicos de pacientes. Existiu um botão "Ver dados" que abria o dossiê
 * clínico completo via #navClinico — removido; #navClinico já não é
 * concedido a este perfil (login() em index.html). A tabela de pacientes
 * abaixo mostra só metadados organizacionais (nome, dispositivo, médicos
 * atribuídos, contagem de alertas ativos — não o conteúdo dos alertas).
 *
 * Backend tem dois perfis de admin: `admin` (Sistema — sem dados clínicos,
 * 404 em `_authorize_patient`, bridge/api.py — é o que esta vista serve) e
 * `admin_clinical` (Suporte Autorizado — lê dados clínicos com `reason`,
 * expiração `privileged_access_expires_at` e auditoria própria). O
 * dashboard ainda não suporta o segundo perfil — falta em
 * API_ROLE_TO_DASHBOARD_ROLE (auth-navegacao.js), UI de login, diálogo de
 * motivo, exibição de expiração, e WS_COMMAND_ROLES (ble_bridge.py, que
 * hoje bloqueia esse papel por omissão — fail-closed, mas só via REST).
 * Fora do âmbito desta alteração.
 *
 * Sem backend real: registo de clínicos só em localStorage deste browser
 * (mesma limitação já existente em CLINICIAN_ASSIGNMENTS_KEY).
 */

const ALL_CLINICIANS_KEY = 'carewear_all_clinicians';

function loadAllClinicians(){
  try {
    const raw = localStorage.getItem(ALL_CLINICIANS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  // Fallback: sem isto o Dr. Ricardo aparecia só pelo email (nunca passou por submitSignup())
  return { [DEFAULT_CLINICIAN_EMAIL]: { name: 'Dr. Ricardo Alves', institution: 'Centro de Saúde de Barcelos', license: 'OM-12345' } };
}
function saveAllClinicians(map){
  try { localStorage.setItem(ALL_CLINICIANS_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}

// Único sítio que cria/atualiza este registo — chamado por submitSignup() (perfil Médico/Técnico) em index.html
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

// Lista consolidada de todos os clínicos "conhecidos" pelo sistema (ver cabeçalho)
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

// Nomes (ou emails, se não registados) dos clínicos atribuídos a um paciente
function clinicianNamesForPatient(patientId){
  const assignments = loadClinicianAssignments();
  const registered = loadAllClinicians();
  return Object.keys(assignments)
    .filter(email => (assignments[email] || []).includes(patientId))
    .map(email => (registered[email] && registered[email].name) || email);
}

// Simplificado a pedido da utilizadora: sem tabelas de alertas/medicação, só as
// mesmas 3 exportações (FHIR/CSV/PDF) que um Médico/Técnico já tem (ver cabeçalho).
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
            <!-- Só leitura — não o antigo botão "Ver dados" com acesso à vista clínica completa (removido, ver cabeçalho) -->
            <td><button class="btn-secondary" onclick="openAdminPatientReportModal('${p.id}')">${t('admin.viewReportBtn')}</button></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    <p class="empty-hint">${t('admin.reportsHint')}</p>
  </div>
`;
};
