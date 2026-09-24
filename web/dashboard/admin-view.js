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
 * expiração `privileged_access_expires_at` e auditoria própria). Ambos já
 * suportados no dashboard (API_ROLE_TO_DASHBOARD_ROLE em auth-navegacao.js,
 * login próprio, banner de expiração) — ver "Gestão de contas" abaixo para
 * onde se concede/revoga o segundo perfil.
 *
 * Registo de clínicos/pacientes acima ("Clínicos"/"Pacientes") continua só
 * em localStorage deste browser — protótipo de demonstração, intencional.
 * O cartão "Gestão de contas (base de dados)" abaixo é a parte NOVA e real:
 * fala com /api/admin/users (bridge/api.py), CRUD de contas verdadeiras —
 * criar, mudar de perfil (inclui conceder acesso clínico temporal a
 * admin_clinical) e revogar (soft delete + derruba sessões/chaves ativas).
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
            <td><b>${escapeHtml(p.name)}</b> · ${p.age} ${t('common.yearsOld')}</td>
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

  <div class="card" id="adminUsersCard"></div>
`;
};

AFTER_RENDER.admin = () => {
  loadAdminUsers();
};

// RF: "o sistema deve permitir a administração de utilizadores e respetivos perfis" — CRUD
// real sobre /api/admin/users (bridge/api.py), distinto do registo de demonstração acima.
const ADMIN_USER_ROLE_LABELS = {
  family: 'Família/Cuidador', clinician: 'Clínico', admin: 'Admin de Sistema', admin_clinical: 'Admin Clínico/Suporte',
};
let adminUsersCache = null;
let adminUsersError = null;

async function loadAdminUsers(){
  if (typeof apiFetch !== 'function') { adminUsersError = 'offline'; renderAdminUsersCard(); return; }
  try {
    const res = await apiFetch('/api/admin/users');
    if (!res.ok) { adminUsersError = res.status === 403 ? 'sem_permissao' : 'erro'; adminUsersCache = null; renderAdminUsersCard(); return; }
    const data = await res.json();
    adminUsersCache = Array.isArray(data.users) ? data.users : [];
    adminUsersError = null;
  } catch (e) {
    adminUsersError = 'offline';
    adminUsersCache = null;
  }
  renderAdminUsersCard();
}

function renderAdminUsersCard(){
  const host = document.getElementById('adminUsersCard');
  if (!host) return; // navegou para outra vista entretanto

  const errorHint = {
    offline: t('admin.usersErrOffline'),
    sem_permissao: t('admin.usersErrNoPermission'),
    erro: t('admin.usersErrGeneric'),
  }[adminUsersError];

  const rows = (adminUsersCache || []).map(u => `
    <tr>
      <td><b>${escapeHtml(u.name)}</b>${u.active ? '' : ` ${pillHtml('critical', t('admin.revokedBadge'))}`}</td>
      <td class="num">${escapeHtml(u.email)}</td>
      <td>
        <select id="adminRoleSelect-${u.id}" aria-label="${escapeHtml(t('admin.roleAriaPrefix', {name: u.name}))}" ${u.active ? '' : 'disabled'}>
          ${Object.keys(ADMIN_USER_ROLE_LABELS).map(r => `<option value="${r}" ${r === u.role ? 'selected' : ''}>${ADMIN_USER_ROLE_LABELS[r]}</option>`).join('')}
        </select>
      </td>
      <td>${u.privileged_access_expires_at ? new Date(u.privileged_access_expires_at).toLocaleString('pt-PT', {dateStyle:'short', timeStyle:'short'}) : '—'}</td>
      <td style="display:flex; gap:6px; flex-wrap:wrap;">
        <button type="button" class="btn-secondary" ${u.active ? '' : 'disabled'} onclick="submitAdminUserRoleChange(${u.id})" aria-label="${escapeHtml(t('admin.saveRoleAria', {name: u.name}))}">${t('admin.saveRoleBtn')}</button>
        <button type="button" class="btn-secondary" ${u.active ? '' : 'disabled'} onclick="grantPrivilegedAccess(${u.id})" aria-label="${escapeHtml(t('admin.grant8hAria', {name: u.name}))}">${t('admin.grant8hBtn')}</button>
        <button type="button" class="btn-secondary" ${u.active ? '' : 'disabled'} onclick="revokeAdminUser(${u.id}, '${escapeHtml(u.name).replace(/'/g, '&#39;')}')" aria-label="${escapeHtml(t('admin.revokeAria', {name: u.name}))}">${t('admin.revokeBtn')}</button>
      </td>
    </tr>`).join('');

  host.innerHTML = `
    <div class="card-head"><div><h3>${t('admin.usersCardTitle')}</h3><div class="card-sub">${t('admin.usersCardSubtitle')}</div></div></div>
    ${errorHint ? `<p class="empty-hint">${escapeHtml(errorHint)}</p>` : ''}
    ${adminUsersCache && adminUsersCache.length ? `
    <table class="data-table">
      <thead><tr><th>${t('admin.thName')}</th><th>${t('admin.thEmail')}</th><th>${t('admin.thUserRole')}</th><th>${t('admin.thPrivilegedUntil')}</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>` : ''}
    <form class="note-form" style="margin-top:14px; display:grid; gap:8px; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));" onsubmit="event.preventDefault(); submitCreateAdminUser();">
      <label class="sr-only" for="newUserName">${t('admin.thName')}</label>
      <input type="text" id="newUserName" placeholder="${escapeHtml(t('admin.thName'))}" aria-label="${escapeHtml(t('admin.thName'))}">
      <label class="sr-only" for="newUserEmail">${t('admin.thEmail')}</label>
      <input type="email" id="newUserEmail" placeholder="${escapeHtml(t('admin.thEmail'))}" aria-label="${escapeHtml(t('admin.thEmail'))}">
      <label class="sr-only" for="newUserPassword">${t('admin.newUserPasswordAria')}</label>
      <input type="password" id="newUserPassword" placeholder="${escapeHtml(t('admin.newUserPasswordPlaceholder'))}" aria-label="${escapeHtml(t('admin.newUserPasswordAria'))}">
      <label class="sr-only" for="newUserRole">${t('admin.thUserRole')}</label>
      <select id="newUserRole" aria-label="${escapeHtml(t('admin.thUserRole'))}">
        ${Object.keys(ADMIN_USER_ROLE_LABELS).map(r => `<option value="${r}">${ADMIN_USER_ROLE_LABELS[r]}</option>`).join('')}
      </select>
      <label class="sr-only" for="newUserInstitution">${t('admin.newUserInstitutionPlaceholder')}</label>
      <input type="text" id="newUserInstitution" placeholder="${escapeHtml(t('admin.newUserInstitutionPlaceholder'))}" aria-label="${escapeHtml(t('admin.newUserInstitutionPlaceholder'))}">
      <button type="submit" class="btn-secondary">${t('signup.submit')}</button>
    </form>
    <p class="empty-hint" id="adminUsersFormHint"></p>
  `;
}

async function submitCreateAdminUser(){
  const hint = document.getElementById('adminUsersFormHint');
  const name = document.getElementById('newUserName').value.trim();
  const email = document.getElementById('newUserEmail').value.trim();
  const password = document.getElementById('newUserPassword').value;
  const role = document.getElementById('newUserRole').value;
  const institution = document.getElementById('newUserInstitution').value.trim();
  if (!name || !email || !password){
    hint.textContent = t('admin.formFillHint');
    hint.style.color = 'var(--status-warning)';
    return;
  }
  try {
    const res = await apiFetch('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({ name, email, password, role, institution: institution || undefined }),
    });
    if (!res.ok){
      const body = await res.json().catch(() => ({}));
      hint.textContent = t('admin.createFailedPrefix', {detail: body.detail || res.status});
      hint.style.color = 'var(--status-warning)';
      return;
    }
    hint.textContent = t('admin.createdOk');
    hint.style.color = 'var(--status-good)';
    await loadAdminUsers();
  } catch (e) {
    hint.textContent = t('admin.offlineErr');
    hint.style.color = 'var(--status-warning)';
  }
}

async function submitAdminUserRoleChange(userId){
  const select = document.getElementById(`adminRoleSelect-${userId}`);
  if (!select) return;
  const res = await apiFetch(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify({ role: select.value }),
  }).catch(() => null);
  if (!res || !res.ok){
    const body = res ? await res.json().catch(() => ({})) : {};
    alert(t('admin.roleSaveFailedPrefix', {detail: body.detail || 'erro de ligação'}));
    return;
  }
  await loadAdminUsers();
}

async function grantPrivilegedAccess(userId){
  const res = await apiFetch(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify({ privileged_access_hours: 8 }),
  }).catch(() => null);
  if (!res || !res.ok){
    alert(t('admin.grantAccessFailed'));
    return;
  }
  await loadAdminUsers();
}

async function revokeAdminUser(userId, name){
  if (!confirm(t('admin.revokeConfirm', {name}))) return;
  const res = await apiFetch(`/api/admin/users/${userId}/revoke`, { method: 'POST' }).catch(() => null);
  if (!res || !res.ok){
    alert(t('admin.revokeFailed'));
    return;
  }
  await loadAdminUsers();
}
