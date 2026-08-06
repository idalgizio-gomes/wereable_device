/* ============================================================
   LIGAÇÃO AO BRIDGE (bridge/ble_bridge.py) — dados reais do wearable
   ------------------------------------------------------------
   Esta página não fala Bluetooth diretamente: liga-se por WebSocket a
   um pequeno serviço Python (ver bridge/ble_bridge.py) que por sua vez
   fala com o dispositivo via BLE e reencaminha cada registo já
   descodificado, em JSON. Se o bridge não estiver a correr (ex.: ao
   abrir esta página publicada como demonstração, sem nada instalado no
   PC), a ligação falha silenciosamente e o dashboard mantém os dados
   simulados — nada se parte por não haver bridge disponível.
============================================================ */
// TLS opcional (GDPR-004, ver SECURITY_STATUS.md): por omissão o bridge
// corre em ws:// simples. Para usar wss://, corre o bridge com
// CAREWEAR_WS_TLS=1 (gera um certificado autoassinado na 1ª vez),
// aceita manualmente o certificado em https://localhost:8765 no browser,
// e só depois define localStorage.setItem('carewear_ws_tls','1') aqui —
// sem os dois passos anteriores a ligação WSS falha silenciosamente.
const WS_URL = (localStorage.getItem('carewear_ws_tls') === '1' ? 'wss' : 'ws') + '://localhost:8765';
const LIVE_HR_WINDOW = 60; // amostras mantidas no gráfico de FC ao vivo

const liveState = {
  connected: false,
  // true quando o utilizador desligou manualmente a ligação BLE ao
  // wearable pelo botão da topbar (ver toggleBleConnection()) — distingue
  // "desligado porque o utilizador pediu" de "a tentar ligar/perdeu o
  // sinal", que usam a mesma liveState.connected=false.
  blePaused: false,
  // MAC do wearable realmente ligado por BLE (ver device_status no bridge,
  // ble_bridge.py::connected_device_mac), null enquanto não ligado. Usado
  // por TEMPLATES.dispositivo para saber se o paciente selecionado
  // (PATIENTS[].mac) é o mesmo dispositivo fisicamente ligado agora, e
  // assim mostrar dados reais em vez dos de demonstração.
  deviceMac: null,
  hr: null, spo2: null, steps: null, freefall: false, inactivity: false,
  // Índice 0-100 de "pacing"/curvas apertadas (ver Imu::detectPacing em
  // Imu.cpp), reencaminhado pelo bridge a partir de FullPlain.pacing_index.
  // null até chegar a primeira leitura real (0 é um valor válido — "sem
  // curvas apertadas na última janela" — por isso não se usa 0 como
  // sentinela de "sem dados", ao contrário de hr/spo2).
  pacing: null,
  lastRecordAt: 0,
  // dataLossFlag (2026-07-03): 0=normal, 1=ring buffer quase cheio (aviso
  // antecipado — ainda dá tempo de exportar), 2=já a substituir dados
  // antigos não consumidos. Vem de DumpStatusPacket::data_loss_flag via
  // o bridge (ver handleBridgeMessage() abaixo e Ble.cpp/ble_bridge.py).
  dataLossFlag: 0,
  // sentRecords (DumpStatusPacket::sent, via bridge): contagem cumulativa
  // de registos já transferidos do wearable nesta sessão de ligação.
  sentRecords: null,
  // ringCount (2026-07-21, DumpStatusPacket::ring_count via bridge — bump
  // de formato de 16 para 20 bytes, ver Ble.cpp/ble_bridge.py): quantos
  // registos continuam por enviar no ring buffer NESTE INSTANTE. Combinado
  // com sentRecords dá uma percentagem real de progresso da transferência
  // (sentRecords / (sentRecords + ringCount)) — antes desta correção não
  // havia como saber "quanto falta", só a contagem acumulada enviada.
  ringCount: null,
  // Nível de bateria (0-100, ver Battery Service BLE padrão 0x180F/0x2A19,
  // firmware a partir de 2026-07-19) — null até chegar a primeira leitura.
  // Ao contrário de hr/spo2, não se reinicia ao perder a ligação: é o
  // último valor conhecido, não um valor "ao vivo" sensível a segundos.
  batteryPercent: null,
  // Histórico REAL agregado por dia (base de dados local do bridge, ver
  // storage.get_daily_summary), pedido sob demanda na vista "Tendência
  // semanal" — []  até chegar a primeira resposta ou se a BD ainda não
  // tiver dados. Distinto de `currentTrendData()` (sempre sintético) — nunca são
  // misturados no mesmo gráfico, ver renderRealTrendTable().
  realTrend: [],
  // Classificação de atividade em tempo real (ver bridge/activity_inference.py,
  // 2026-07-20) — modelo treinado só com dados SINTÉTICOS, nunca validado
  // clinicamente (ver renderLiveActivityPanel(), que mostra o aviso sempre
  // junto ao resultado, nunca escondido). null até chegar a 1ª classificação.
  currentActivity: null, // {category, confidence, session, receivedAt}
  // Último bloco fechado com veredito do detetor de duração (ver
  // ml/duration_detector.py) — mostrado como aviso extra quando anómalo.
  lastActivityDurationFlag: null,
  // Correção manual do cuidador/equipa clínica à classificação da IA
  // (2026-07-22, pedido do utilizador: "falta o botão para contradizer o
  // que a ia acredita que o utente está a fazer"; ver cmd
  // "correct_activity"/kind "activity_correction" no bridge). Mostrada AO
  // LADO da classificação da IA, nunca a substituir silenciosamente — a
  // correção não realimenta o classificador (não há pipeline de
  // retreino em tempo real), é só um registo de auditoria + indicação
  // visual imediata de que o cuidador discorda do que está no ecrã.
  activityCorrection: null, // {category, originalCategory, correctedAtEpochS}
};
const liveHrBuffer = []; // {t: epoch_s, hr, label: "HH:MM:SS"}

function fmtClock(epochSeconds){
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString('pt-PT', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

// Mostra/esconde o aviso de armazenamento (ver liveState.dataLossFlag,
// preenchido a partir de DumpStatusPacket::data_loss_flag via bridge).
// Pedido do utilizador (2026-07-03): avisar A TEMPO de exportar os dados
// antes de começarem a ser substituídos, não só depois de já ter
// acontecido — por isso há dois níveis:
//   1 = ring buffer quase cheio, ainda sem perdas (aviso, cor de aviso)
//   2 = já a substituir dados antigos não consumidos (cor crítica)
function renderStorageWarningBanner(){
  const el = document.getElementById('storageWarningBanner');
  if (!el) return;
  // Ver comentário completo junto de #topbarDeviceStatusGroup em
  // index.html — este banner segue a mesma regra: só faz sentido em
  // destaque para Utente/Família, não para Médico/Técnico/Administrador.
  if (currentRole !== 'utente') { el.style.display = 'none'; return; }
  const flag = liveState.dataLossFlag;
  if (!flag) { el.style.display = 'none'; el.className = 'storage-warning-banner print-hide'; return; }
  el.className = `storage-warning-banner print-hide level-${flag}`;
  el.style.display = 'flex';
  // Progresso ao vivo (2026-07-21): antes só existia sentRecords
  // (contagem cumulativa desta ligação), sem noção de "quanto falta" — o
  // firmware não expunha o tamanho do buffer em anel por BLE. Desde o
  // bump de formato do DumpStatusPacket (16->20 bytes, ver Ble.cpp/
  // ble_bridge.py), ring_count chega também, permitindo uma percentagem
  // real: sentRecords / (sentRecords + ringCount).
  const haveBoth = liveState.sentRecords != null && liveState.ringCount != null;
  const totalKnown = haveBoth ? liveState.sentRecords + liveState.ringCount : null;
  const pctDone = haveBoth && totalKnown > 0 ? Math.round((liveState.sentRecords / totalKnown) * 100) : null;
  const progressText = liveState.sentRecords != null
    ? ` ${t('topbar.storageDrainProgressPrefix')} ${liveState.sentRecords.toLocaleString(currentLang)}${t('topbar.storageDrainProgressSuffix')}`
    : '';
  const progressBar = pctDone != null ? `
    <div class="meter-track" style="margin-top:6px;"><div class="meter-fill" style="width:${pctDone}%; background:var(--status-good)"></div></div>
    <span class="tabular" style="font-size:12px;">${pctDone}% ${t('topbar.storageDrainPctSuffix')} (${liveState.ringCount.toLocaleString(currentLang)} ${t('topbar.storageDrainRemainingSuffix')})</span>
  ` : '';
  el.innerHTML = flag === 2
    ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>
       <span>${t('topbar.storageFullWarning')}${progressText}${progressBar}</span>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>
       <span>${t('topbar.storageAlmostFullWarning')}${progressText}${progressBar}</span>`;
}

// Atualiza a pastilha de estado do dispositivo na barra superior.
function updateDeviceStatusUI(){
  const dot = document.getElementById('deviceDot');
  const text = document.getElementById('deviceStatusText');
  if (dot && text){
    if (liveState.connected){
      dot.classList.remove('warn');
      text.textContent = t('topbar.connected');
    } else if (liveState.blePaused){
      dot.classList.add('warn');
      text.textContent = t('topbar.pausedManual');
    } else {
      dot.classList.add('warn');
      text.textContent = t('topbar.disconnected');
    }
  }
  updateBleToggleUI();
}

// Botão da topbar que pede ao bridge para largar/retomar a ligação BLE ao
// wearable (comando "set_ble_enabled") — não fecha o WebSocket
// dashboard<->bridge, só a ligação Bluetooth ao dispositivo. Pedido do
// utilizador para poder libertar a placa (gravar firmware, outra
// ferramenta de série) sem ter de fechar o bridge no terminal.
function updateBleToggleUI(){
  const btn = document.getElementById('bleToggleBtn');
  const btnText = document.getElementById('bleToggleBtnText');
  if (!btn || !btnText) return;
  const enabled = !liveState.blePaused;
  btn.setAttribute('aria-pressed', String(enabled));
  btn.title = t('topbar.bleToggleHint');
  btnText.textContent = enabled ? t('topbar.bleDisconnect') : t('topbar.bleConnect');
}

// Atualiza o chip de bateria da topbar (ver liveState.batteryPercent,
// preenchido a partir de handleBridgeMessage ao receber {kind:'battery'}).
function updateBatteryUI(){
  const text = document.getElementById('batteryText');
  const chip = document.getElementById('batteryChip');
  if (!text || !chip) return;
  const pct = liveState.batteryPercent;
  text.textContent = pct != null ? `${pct}%` : '—';
  chip.title = pct != null ? t('topbar.batteryTitle', {pct}) : t('topbar.batteryUnknown');
}

// Painel "Atividade detetada (IA)" na vista Resumo — mostra a classificação
// em tempo real vinda do bridge (kind "activity_classification") e, quando
// existir, o veredito de duração do último bloco fechado (kind
// "activity_duration_flag"). O aviso de "não validado clinicamente" é
// SEMPRE visível junto ao resultado, nunca só no título do card — ver
// ACTIVITY_ML_DISCLAIMER em bridge/activity_inference.py, a mesma frase
// (traduzida) é repetida aqui de propósito.
// Estado efémero só de UI (não em liveState de propósito: não vem do
// bridge, não sobrevive a re-render por outro motivo que não seja o
// próprio clique do utilizador) — se o seletor de categorias do botão
// "Corrigir" está aberto.
let activityCorrectionPickerOpen = false;

function renderLiveActivityPanel(){
  const host = document.getElementById('liveActivityPanel');
  if (!host) return;

  if (!liveState.currentActivity){
    host.innerHTML = `<p class="activity-live-empty">${t('resumo.liveActivityWaiting')}</p>`;
    return;
  }

  const a = liveState.currentActivity;
  // Nomes de categoria (Dormir/Descanso/Atividade/Alimentação/Higiene) não
  // são traduzidos em lado nenhum do dashboard hoje (ver o mesmo padrão em
  // catMap, usado pela timeline de rotina simulada) — mostrados tal como o
  // bridge os envia, não por omissão, mas por consistência com o resto da
  // vista Resumo, que já faz o mesmo.
  const catLabel = escapeHtml(a.category);
  const pct = Math.round(a.confidence * 100);

  // "indicador de incerteza" (2026-08-05) — só aparece quando o bridge o
  // envia (a.isUncertain vem de is_uncertain, ver
  // UNCERTAINTY_MARGIN_THRESHOLD em bridge/activity_inference.py) e há uma
  // 2ª classe conhecida para nomear. Compatível com um bridge mais antigo
  // que ainda não envie estes campos (a.isUncertain fica undefined/false).
  let uncertaintyHtml = '';
  if (a.isUncertain && a.runnerUpCategory){
    const runnerUpPct = a.runnerUpConfidence != null ? Math.round(a.runnerUpConfidence * 100) : null;
    uncertaintyHtml = `<div class="activity-live-flag uncertain">${t('resumo.liveActivityUncertainIcon')} ${t('resumo.liveActivityUncertain', {cat: escapeHtml(a.runnerUpCategory), pct: runnerUpPct})}</div>`;
  }

  let flagHtml = '';
  const flag = liveState.lastActivityDurationFlag;
  if (flag){
    const flagCatLabel = escapeHtml(flag.category);
    // "explicação de alerta" (2026-08-05): linha extra com o PORQUÊ (frase
    // já composta no bridge, com números reais), não só o veredito. Só
    // aparece quando o bridge a envia — mantém compatibilidade com um
    // bridge mais antigo que ainda não a inclua na mensagem.
    const explanationHtml = flag.explanation
      ? `<div class="activity-live-flag-reason">${escapeHtml(flag.explanation)}</div>` : '';
    flagHtml = flag.isAnomaly
      ? `<div class="activity-live-flag anomaly">⚠ ${t('resumo.liveActivityDurationAnomaly', {cat: flagCatLabel, min: flag.durationMin})}</div>${explanationHtml}`
      : `<div class="activity-live-flag normal">✓ ${t('resumo.liveActivityDurationNormal', {cat: flagCatLabel, min: flag.durationMin})}</div>${explanationHtml}`;
  }

  // Correção manual do cuidador (2026-07-22, revisto a pedido do
  // utilizador: "não faz sentido ter um botão de correção se isso não
  // servir para... a correção sobrepor-se ao que ela [a IA] pensa"). A
  // correção passa a ser o valor PRINCIPAL mostrado (não só uma nota ao
  // lado) enquanto estiver "fresca" (< ACTIVITY_CORRECTION_OVERRIDE_S) —
  // a classificação em tempo real da IA continua visível, mas em segundo
  // plano, para nunca esconder por completo o que o classificador está
  // de facto a produzir (nem fingir que a correção "retreinou" o modelo —
  // não há pipeline de retreino em tempo real; a correção fica guardada
  // em bridge/storage.py::activity_corrections para uma eventual
  // reavaliação/retreino futuro do classificador, isso sim).
  const corr = liveState.activityCorrection;
  const correctionAgeS = corr && corr.correctedAtEpochS != null
    ? (Date.now() / 1000) - corr.correctedAtEpochS : null;
  const correctionActive = corr && correctionAgeS != null && correctionAgeS < ACTIVITY_CORRECTION_OVERRIDE_S;

  let correctionHtml = '';
  if (corr){
    const timeLabel = corr.correctedAtEpochS != null
      ? new Date(corr.correctedAtEpochS * 1000).toLocaleTimeString(currentLang, {hour: '2-digit', minute: '2-digit'})
      : '';
    correctionHtml = correctionActive
      ? `<div class="activity-live-correction ai-note">${t('resumo.liveActivityAiSaysNow', {cat: catLabel, pct})}</div>`
      : `<div class="activity-live-correction">✎ ${t('resumo.liveActivityCorrectedTo', {cat: escapeHtml(corr.category)})}${timeLabel ? ` · ${timeLabel}` : ''} — ${t('resumo.liveActivityCorrectionExpired')}</div>`;
  }

  const pickerHtml = activityCorrectionPickerOpen ? `
    <div class="activity-chips" style="margin:8px 0 0;">
      ${ACTIVITY_CORRECTION_CATEGORIES.map(cat => `
        <button type="button" class="activity-chip" style="border-color:${categoryColorVar(cat)}" onclick="submitActivityCorrection('${cat}')">${escapeHtml(cat)}</button>
      `).join('')}
    </div>
  ` : '';

  // Linha principal: se a correção está ativa, é ELA que aparece em
  // destaque (categoria + "confirmado pelo cuidador"); a IA passa para a
  // nota secundária acima (correctionHtml). Caso contrário, a IA volta a
  // ser a linha principal, como sempre foi.
  const mainCat = correctionActive ? escapeHtml(corr.category) : catLabel;
  const mainColor = correctionActive ? categoryColorVar(corr.category) : categoryColorVar(a.category);
  const mainSuffix = correctionActive
    ? `<span class="conf">${t('resumo.liveActivityConfirmedByCaregiver')}</span>`
    : `<span class="conf">${t('resumo.liveActivityConfidence', {pct})}</span>`;

  host.innerHTML = `
    <div class="activity-live-now">
      <span class="cat" style="color:${mainColor}">${mainCat}</span>
      ${mainSuffix}
      ${liveState.connected ? `<button type="button" class="btn-link activity-correct-btn" onclick="toggleActivityCorrectionPicker()">${t('resumo.liveActivityCorrectBtn')}</button>` : ''}
    </div>
    ${correctionHtml}
    ${uncertaintyHtml}
    ${pickerHtml}
    <p id="activityCorrectionStatus" class="activity-live-flag anomaly" style="display:none;"></p>
    ${flagHtml}
    <p class="activity-live-empty">${t('resumo.liveActivityDisclaimer')}</p>
  `;
}

// Quanto tempo uma correção do cuidador fica como valor PRINCIPAL exibido
// (sobrepondo-se à classificação da IA) antes de ser considerada antiga
// e o painel voltar a mostrar a IA em destaque — 30 min: suficiente para
// uma verificação do cuidador continuar válida por um bocado, mas sem
// ficar para sempre a apresentar como "atual" uma correção de horas atrás.
const ACTIVITY_CORRECTION_OVERRIDE_S = 1800;

// Vocabulário das 5 categorias (PT) usado pelo seletor de correção — o
// mesmo conjunto fechado de ACTIVITY_CLASS_COLOR_VAR, como array (a ordem
// de exibição dos botões é a mesma ordem em que o classificador as define,
// ver CLASS_TO_DB_CATEGORY em bridge/activity_inference.py).
const ACTIVITY_CORRECTION_CATEGORIES = ['Dormir', 'Descanso', 'Atividade', 'Alimentação', 'Higiene'];

function toggleActivityCorrectionPicker(){
  activityCorrectionPickerOpen = !activityCorrectionPickerOpen;
  renderLiveActivityPanel();
}

function submitActivityCorrection(category){
  activityCorrectionPickerOpen = false;
  const sent = sendWsCommandWithArgs('correct_activity', {category});
  if (!sent){
    renderLiveActivityPanel();
    return;
  }
  renderLiveActivityPanel();
}

// Mapeia a classe do classificador (PT, ver
// ml/models/activity_classifier_rf_labels.json) para a variável CSS de cor
// já usada nos blocos de rotina simulados (catMap) — mesma paleta, para a
// classificação real e a timeline simulada usarem as mesmas cores.
const ACTIVITY_CLASS_COLOR_VAR = {
  'Dormir': 'var(--cat-dormir)',
  'Descanso': 'var(--cat-descanso)',
  'Atividade': 'var(--cat-atividade)',
  'Alimentação': 'var(--cat-alimentacao)',
  'Higiene': 'var(--cat-higiene)',
};
function categoryColorVar(category){
  return ACTIVITY_CLASS_COLOR_VAR[category] || 'var(--text-primary)';
}

function toggleBleConnection(){
  const nextEnabled = liveState.blePaused; // estava pausado -> vamos ligar
  const sent = sendWsCommandWithArgs('set_ble_enabled', {enabled: nextEnabled});
  if (!sent) return; // sem ligação ao bridge — nada a pedir
  // Otimista: reflete já o pedido na UI, sem esperar pelo próximo
  // device_status (que pode demorar até ~1s, ver run_device_loop no bridge).
  liveState.blePaused = !nextEnabled;
  if (!nextEnabled) liveState.connected = false;
  updateDeviceStatusUI();
}

// Converte um valor vindo do bridge (não confiável — canal WebSocket sem
// autenticação) num número finito, ou null se não for um número válido.
// Usado antes de guardar em liveState, para nunca acabar em innerHTML.
//
// Corrigido 2026-08-06: `v == null` tem de ser verificado ANTES de
// `Number(v)`, porque `Number(null) === 0` em JavaScript (quirk da
// linguagem — `Number(undefined)` já dá `NaN`, mas `null` dá 0). O
// bridge envia `null` de propósito quando spo2/hr não têm leitura nova
// nesse registo (ver decode_full_plain em ble_bridge.py:531-534,
// comentário do próprio autor: "o dashboard deve ignorar zeros") — sem
// este guard, essa conversão explícita para null era desfeita aqui,
// reintroduzindo o mesmo 0 que o bridge já tinha removido.
function toFiniteNumber(v){
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// Escreve os últimos valores conhecidos (liveState) nos cartões de
// sinais vitais, se estiverem presentes na vista atualmente renderizada.
// Chamado tanto quando chega um registo novo como sempre que se navega
// para uma vista com estes cartões (ver AFTER_RENDER.resumo/vitais).
function applyLiveVitals(){
  if (!liveState.connected) return; // mantém os valores de demonstração já no HTML

  const setVal = (id, value) => { const el = document.getElementById(id); if (el && value != null) el.innerHTML = value; };
  const hrText = liveState.hr != null ? `${liveState.hr}<span class="unit">bpm</span>` : '—<span class="unit">bpm</span>';
  const spo2Text = liveState.spo2 != null ? `${liveState.spo2}<span class="unit">%</span>` : '—<span class="unit">%</span>';

  setVal('stat-hr', hrText); setVal('stat-hr-2', hrText);
  setVal('stat-spo2', spo2Text); setVal('stat-spo2-2', spo2Text);

  // Mensagem pedida pela utilizadora (2026-08-06): quando o HR está a
  // chegar (prova que o sensor tem contacto/dedo — ver
  // checkFingerPresentBrief() em Ppg.cpp) mas o SpO2 continua nulo, é
  // provável que o sensor ainda não tenha conseguido um sinal
  // suficientemente estável para o cálculo de SpO2 especificamente
  // (mais sensível a movimento/pressão do que o HR). Só aparece nesta
  // combinação exata — HR presente, SpO2 ausente — não noutros casos
  // (ex.: os dois nulos, que já é coberto pelo "—" normal dos tiles).
  const spo2NeedsHint = liveState.hr != null && liveState.spo2 == null;
  const spo2HintText = spo2NeedsHint
    ? 'A ler HR mas sem SpO2 ainda — tenta manter a placa bem encostada ao pulso, sem mover a mão.'
    : '';
  ['spo2-hint', 'spo2-hint-2'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = spo2HintText;
    el.style.display = spo2NeedsHint ? '' : 'none';
  });

  // Bug real corrigido aqui (2026-08-06, relatado pela utilizadora com
  // captura de ecrã: topbar mostrava "Wearable ligado" mas o painel de
  // Sinais Vitais continuava preso em "Sem ligação ao bridge — liga o
  // dispositivo primeiro."): esse texto só é escrito por
  // toggleContinuousHr()/resetPendingCommandButtons() no MOMENTO em que a
  // ligação cai — nada o repunha depois de a ligação voltar. applyLiveVitals()
  // só corre com liveState.connected true (ver early return no topo desta
  // função), por isso este é o sítio certo para repor o hint ao texto
  // normal — mas só se não houver uma medição/countdown genuíno em curso
  // (senão apagava "a medir…"/"modo contínuo ativo" por engano).
  const forceHintEl = document.getElementById('forceReadingHint');
  const forceBtnEl = document.getElementById('forceReadingBtn');
  const noCountdownActive = forceReadingIntervalId == null && continuousHrIntervalId == null;
  if (forceHintEl && forceBtnEl && !forceBtnEl.disabled && noCountdownActive) {
    forceHintEl.style.color = '';
    forceHintEl.textContent = t('vitais.forceReadingHint');
  }

  setVal('stat-steps-2', liveState.steps != null ? liveState.steps.toLocaleString(currentLang) : '—');
  setVal('stat-movement', liveState.inactivity ? t('resumo.movementStill') : t('resumo.movementActive'));
  setVal('stat-falls-2', liveState.freefall ? `⚠ ${t('resumo.fallsRecentDetection')}` : '0');
}

function handleBridgeMessage(msg){
  if (msg.kind === 'device_status'){
    liveState.connected = !!msg.connected;
    liveState.blePaused = !!msg.paused;
    // Canal sem autenticação — valida a forma antes de confiar (mesmo
    // padrão de toFiniteNumber/escapeHtml/allowlist já usado neste
    // ficheiro), nunca guarda msg.mac tal como vem.
    liveState.deviceMac = (typeof msg.mac === 'string' && /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(msg.mac))
      ? msg.mac.toUpperCase() : null;
    // "Reconhecer em qualquer conta" (2026-07-21): assim que o wearable
    // real se liga, associa-o à conta atualmente selecionada (ver
    // registeredMacFor/DEVICE_REGISTRY_KEY acima) — assim, mudar de
    // paciente e voltar a ligar o mesmo dispositivo físico passa a ser
    // reconhecido nessa conta também, em vez de ficar preso ao mac de
    // demonstração de um único paciente fixo.
    if (liveState.deviceMac && selectedPatientId) {
      const reg = loadDeviceRegistry();
      if (reg[selectedPatientId] !== liveState.deviceMac) {
        reg[selectedPatientId] = liveState.deviceMac;
        saveDeviceRegistry(reg);
      }
    }
    updateDeviceStatusUI();
    applyLiveVitals();
    return;
  }
  if (msg.kind === 'command_result'){
    handleCommandResult(msg);
    return;
  }
  if (msg.kind === 'status'){
    liveState.dataLossFlag = toFiniteNumber(msg.data_loss_flag) || 0;
    liveState.sentRecords = toFiniteNumber(msg.sent_records);
    liveState.ringCount = toFiniteNumber(msg.ring_count);
    renderStorageWarningBanner();
    return;
  }
  if (msg.kind === 'battery'){
    liveState.batteryPercent = toFiniteNumber(msg.percent);
    updateBatteryUI();
    return;
  }
  if (msg.kind === 'activity_classification'){
    // Canal não autenticado (ver toFiniteNumber() acima) — "category" é um
    // enum fechado de 5 valores (ver ACTIVITY_CLASS_COLOR_VAR); qualquer
    // outra coisa vinda do WebSocket é ignorada em vez de aceite às cegas.
    const conf = toFiniteNumber(msg.confidence);
    if (Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.category) && conf != null){
      // "indicador de incerteza" (2026-08-05) — 2ª classe mais provável e
      // a margem até ela (ver UNCERTAINTY_MARGIN_THRESHOLD em
      // bridge/activity_inference.py). runner_up_category valida contra o
      // mesmo enum fechado que 'category'; os restantes são só números/bool.
      const runnerUpCat = Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.runner_up_category)
        ? msg.runner_up_category : null;
      liveState.currentActivity = {
        category: msg.category, confidence: conf,
        runnerUpCategory: runnerUpCat,
        runnerUpConfidence: toFiniteNumber(msg.runner_up_confidence),
        confidenceMargin: toFiniteNumber(msg.confidence_margin),
        isUncertain: !!msg.is_uncertain,
      };
      renderLiveActivityPanel();
    }
    return;
  }
  if (msg.kind === 'activity_correction'){
    // Difundido pelo bridge a TODOS os dashboards ligados (cmd
    // "correct_activity") — canal não autenticado, mesma allowlist fechada
    // já usada para "activity_classification" (ACTIVITY_CLASS_COLOR_VAR).
    if (Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.category)){
      liveState.activityCorrection = {
        category: msg.category,
        originalCategory: Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.original_category) ? msg.original_category : null,
        correctedAtEpochS: toFiniteNumber(msg.corrected_at),
      };
      renderLiveActivityPanel();
    }
    return;
  }
  if (msg.kind === 'activity_duration_flag'){
    const durationMin = toFiniteNumber(msg.duration_min);
    if (Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.cls) && durationMin != null){
      liveState.lastActivityDurationFlag = {
        category: msg.cls,
        durationMin: Math.round(durationMin),
        isAnomaly: !!msg.is_anomaly,
        // "explicação de alerta" (2026-08-05) — frase já composta pelo
        // bridge (ml/duration_detector.py::explain_block) com os números
        // reais (duração observada vs. limites esperados), não só o
        // veredito booleano. Texto livre vindo do bridge local — sem
        // HTML própria (só entra via textContent-safe escapeHtml abaixo).
        explanation: typeof msg.explanation === 'string' ? msg.explanation : null,
      };
      renderLiveActivityPanel();
    }
    return;
  }
  if (msg.kind === 'emergency_alert'){
    onLiveEmergencyAlert(msg);
    return;
  }
  if (msg.kind === 'csv_export'){
    handleCsvExportResult(msg);
    return;
  }
  if (msg.kind === 'daily_trend'){
    liveState.realTrend = Array.isArray(msg.days_summary) ? msg.days_summary : [];
    renderRealTrendTable();
    return;
  }
  if (msg.kind === 'retention_days'){
    handleRetentionDaysResult(msg);
    return;
  }
  if (msg.kind === 'retention_days_result'){
    handleRetentionDaysSaveResult(msg);
    return;
  }
  if (msg.kind === 'consent_status'){
    handleConsentStatusResult(msg);
    return;
  }
  if (msg.kind === 'consent_result'){
    handleConsentResultMessage(msg);
    return;
  }
  if (msg.kind === 'thresholds'){
    handleThresholdsResult(msg);
    return;
  }
  if (msg.kind === 'thresholds_result'){
    handleThresholdsSaveResult(msg);
    return;
  }
  if (msg.kind === 'episode_timeline'){
    handleEpisodeTimelineResult(msg);
    return;
  }
  if (msg.kind === 'model_versions'){
    handleModelVersionsResult(msg);
    return;
  }
  if (msg.kind === 'model_version_result'){
    handleModelVersionResult(msg);
    return;
  }
  if (msg.kind === 'vital_alert'){
    // Canal não autenticado — 'vital' valida contra o par fechado
    // conhecido; o resto (value/limit/level) são só números/strings
    // vindos de escapeHtml() na renderização, nunca HTML cru.
    if (msg.vital === 'hr' || msg.vital === 'spo2'){
      liveState.vitalAlerts[msg.vital] = msg.cleared ? null : {
        level: msg.level, value: toFiniteNumber(msg.value), limit: toFiniteNumber(msg.limit),
        explanation: typeof msg.explanation === 'string' ? msg.explanation : '',
      };
      renderVitalAlertsPanel();
    }
    return;
  }
  if (msg.kind === 'live_record' || msg.kind === 'record'){
    applySensorRecordFields(msg, {isLive: msg.kind === 'live_record'});
    return;
  }
}

// Aplica os campos de um registo de sensores (hr/spo2/steps/freefall/
// inactivity/pacing_index) vindo do bridge a liveState — partilhado por
// dois canais distintos desde 2026-08-06 (ver PROJECT_STATUS.md, mesma
// data, para o problema real que motivou a separação):
//
//   - 'live_record' (liveSnapshotChar): instantâneo do registo mais
//     recente do dispositivo, NUNCA atrasado — é a fonte ÚNICA da UI "ao
//     vivo" (cartões de sinais vitais, gráfico de FC, pacing).
//   - 'record' (dumpDataChar): fluxo histórico, entrega tudo em ordem
//     cronológica do mais antigo para o mais recente, sem nunca perder
//     dados — mas pode estar minutos atrasado face a "agora" se o
//     dispositivo gravou sem BLE ligado (o problema original reportado
//     pela utilizadora: "os valores no gráfico estão desfasados 10m").
//
// Antes de 2026-08-06 só existia 'record', e por isso o gráfico/cartões
// mostravam sempre o que estivesse a ser reproduzido do histórico —
// atrasado sempre que houvesse um backlog por escoar. Agora só
// 'live_record' atualiza liveState.hr/spo2/steps/freefall/inactivity/
// pacing e o gráfico (isLive=true); 'record' continua a chegar (o
// bridge continua a difundi-lo, para uso futuro/depuração) mas já não
// tem qualquer efeito na UI — nunca compete com o instantâneo ao vivo
// pelos mesmos campos, mesmo durante um atraso grande a ser escoado.
function applySensorRecordFields(msg, {isLive}){
  // Espelha o payload real gravado pelo firmware (ImuPpgPayloadV1 /
  // FullPlain — ver storageTask em main.cpp e Ble.cpp): spo2/hr vêm a
  // 0 no wire format quando essa amostra em particular não trouxe
  // leitura nova (ImuPpgPayload.h:45-46), mas o bridge já converte
  // isso para `null` antes de reencaminhar (decode_full_plain,
  // ble_bridge.py:531-534) — o JSON que chega aqui já traz null, não
  // 0. O IMU produz amostras a ~54/s, mas o PPG só atualiza HR/SpO2
  // esporadicamente, por isso a esmagadora maioria dos registos
  // trazem hr=null/spo2=null.
  // O canal do bridge (ws://localhost:8765) não é autenticado (ver
  // PROJECT_STATUS.md) — qualquer valor vindo de `msg` é tratado como não
  // confiável e validado como número finito antes de entrar em liveState
  // (e, dali, em innerHTML), para evitar XSS via WebSocket. `toFiniteNumber`
  // preserva esse null corretamente desde 2026-08-06 (antes, `Number(null)
  // === 0` na própria função reintroduzia o zero que o bridge já tinha
  // removido — ver comentário em toFiniteNumber). hasNewHr/hasNewSpo2
  // abaixo exigem também `> 0` como rede de segurança extra (0 nunca é uma
  // leitura fisiológica real de HR/SpO2), no mesmo espírito defensivo de
  // clampToI16() em main.cpp — não porque ainda seja necessário hoje, mas
  // para não voltar a depender silenciosamente de null vs. 0 estarem
  // sempre corretos em todos os pontos do pipeline.
  const hrNum = toFiniteNumber(msg.hr);
  const spo2Num = toFiniteNumber(msg.spo2);
  const stepsNum = toFiniteNumber(msg.steps);
  const pacingNum = toFiniteNumber(msg.pacing_index);
  const hasNewHr = hrNum != null && hrNum > 0;
  const hasNewSpo2 = spo2Num != null && spo2Num > 0;

  if (!isLive) return; // 'record' (histórico) chega mas não toca na UI — ver comentário da função acima

  liveState.lastRecordAt = Date.now();
  if (hasNewHr) liveState.hr = hrNum;
  if (hasNewSpo2) liveState.spo2 = spo2Num;
  liveState.steps = stepsNum;
  liveState.freefall = !!msg.freefall;
  liveState.inactivity = !!msg.inactivity;
  if (pacingNum != null) liveState.pacing = pacingNum;

  if (hasNewHr && msg.ts){
    liveHrBuffer.push({t: msg.ts, hr: hrNum, label: fmtClock(msg.ts)});
    if (liveHrBuffer.length > LIVE_HR_WINDOW) liveHrBuffer.shift();
    // Só redesenha o gráfico se a vista "Sinais vitais" estiver ativa.
    if (document.getElementById('cvHr')) drawHrSeries('cvHr');
  }
  // Só re-renderiza o cartão de pacing se a vista "Rotina diária" estiver ativa.
  if (pacingNum != null && document.getElementById('pacingSummary')) renderPacingSummary();
  applyLiveVitals();
}

// Referência à ligação WebSocket ativa (ou null), para os botões de
// comando ("Medir agora", "Repor leituras") poderem enviar pedidos ao
// bridge sem precisar de gerir a sua própria ligação.
let bridgeWs = null;

function connectBridge(){
  let ws;
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.onopen = () => { bridgeWs = ws; };
  ws.onmessage = (ev) => {
    try { handleBridgeMessage(JSON.parse(ev.data)); }
    catch (e) { /* mensagem invalida do bridge - ignora */ }
  };
  ws.onclose = () => {
    if (bridgeWs === ws) bridgeWs = null;
    liveState.connected = false;
    updateDeviceStatusUI();
    resetPendingCommandButtons();
    // Sem ligação ao bridge deixamos de poder confiar na última
    // classificação de atividade recebida — limpa o painel para que
    // volte a mostrar "a aguardar" em vez de ficar preso numa leitura
    // antiga sem aviso (ver PROJECT_STATUS.md, item 8).
    liveState.currentActivity = null;
    liveState.lastActivityDurationFlag = null;
    liveState.activityCorrection = null;
    renderLiveActivityPanel();
    scheduleReconnect();
  };
  ws.onerror = () => { ws.close(); };
}

function scheduleReconnect(){
  // Tenta religar de tempos a tempos — cobre tanto "o bridge ainda não
  // tinha arrancado" como "o bridge caiu e voltou".
  setTimeout(connectBridge, 4000);
}

// Envia um comando ({"cmd":"..."}) ao bridge, que o traduz numa escrita
// BLE em dumpCtrlChar (ver ble_bridge.py). Devolve false de imediato se
// não houver ligação — quem chama deve tratar esse caso (ver
// onForceReadingClick/confirmReset).
function sendWsCommand(cmd){
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN) return false;
  bridgeWs.send(JSON.stringify({cmd}));
  return true;
}

// Variante que envia campos extra além de "cmd" (ex.: {cmd:"export_csv",
// hours:24}) — sendWsCommand() sozinho só serve pedidos sem parâmetros.
function sendWsCommandWithArgs(cmd, extra){
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN) return false;
  bridgeWs.send(JSON.stringify({cmd, ...extra}));
  return true;
}

/* ------------------------------------------------------------
   COMANDOS AO VIVO — "Medir agora" (FC+SpO2 forçados) e
   "Repor leituras" (destrutivo, com modal de confirmação)
------------------------------------------------------------ */
// Se a ligação ao bridge cair a meio de um pedido ("Medir agora" ou "Repor
// leituras"), o command_result correspondente nunca chega e os botões
// ficavam presos em "a carregar" até a página ser recarregada. Chamado a
// partir de ws.onclose para repor o estado assim que a ligação se perde.
// Duração real do pedido "Medir agora" no firmware/bridge (ver
// DUMP_CTRL_FORCE_READING_SECONDS em bridge/ble_bridge.py — mantido em
// sincronia manualmente, os dois lados não partilham este valor em
// tempo de execução).
//
// BUG CORRIGIDO (2026-07-07, reportado pelo utilizador: "o countdown não
// funciona"): a 1ª versão parava o countdown assim que chegava o
// command_result — mas o bridge envia esse command_result LOGO A SEGUIR
// à escrita GATT (handle_dashboard_command() em ble_bridge.py), não
// depois da janela real de 15s de medição no dispositivo. Na prática o
// countdown desaparecia quase instantaneamente (~1s), parecendo não
// funcionar. Agora o countdown corre sempre até ao fim dos
// FORCE_READING_SECONDS reais — command_result só interrompe cedo se
// ok=false (falha real, não há nada por onde esperar).
const FORCE_READING_SECONDS = 15;
let forceReadingIntervalId = null;
// Leitura contínua (pedido explícito do utilizador): sem mudar o firmware
// nem o bridge, o dashboard reenvia force_reading pouco antes de cada
// janela de 15s expirar, dando a sensação de "ligado até desligares" —
// o firmware já limita cada pedido individual a 30s no máximo por desenho
// (poupança de bateria, ver Ppg.cpp kManualHrMaxDurationMs), por isso
// reenviar em vez de pedir uma janela única enorme respeita esse limite.
let continuousHrIntervalId = null;

function stopForceReadingCountdown(){
  if (forceReadingIntervalId != null){ clearInterval(forceReadingIntervalId); forceReadingIntervalId = null; }
}

function finishForceReadingCountdown(message, isWarning){
  stopForceReadingCountdown();
  const btn = document.getElementById('forceReadingBtn');
  const hint = document.getElementById('forceReadingHint');
  if (btn) btn.disabled = false;
  if (hint){
    hint.style.color = isWarning ? 'var(--status-warning)' : 'var(--status-good)';
    hint.textContent = message;
  }
}

function startForceReadingCountdown(){
  stopForceReadingCountdown();
  const hint = document.getElementById('forceReadingHint');
  let remaining = FORCE_READING_SECONDS;
  const render = () => { if (hint) hint.textContent = `${t('vitais.measuringHint')} (~${remaining}${t('vitais.secondsRemainingSuffix')})`; };
  if (hint) hint.style.color = '';
  render();
  forceReadingIntervalId = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0){
      // Só chamado a partir de onForceReadingClick() (medição pontual) —
      // o modo contínuo (toggleContinuousHr()) já não usa este countdown.
      finishForceReadingCountdown(t('vitais.measurementDoneHint'), false);
      return;
    }
    render();
  }, 1000);
}

function toggleContinuousHr(){
  const btn = document.getElementById('continuousHrBtn');
  const hint = document.getElementById('forceReadingHint');
  const forceBtn = document.getElementById('forceReadingBtn');
  if (continuousHrIntervalId != null){
    clearInterval(continuousHrIntervalId);
    continuousHrIntervalId = null;
    stopForceReadingCountdown();
    if (btn){ btn.className = 'btn-secondary'; btn.setAttribute('aria-pressed', 'false'); btn.textContent = ''; btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg> ${t('vitais.continuousHrStartBtn')}`; }
    if (forceBtn) forceBtn.disabled = false;
    if (hint){ hint.style.color = ''; hint.textContent = t('vitais.forceReadingHint'); }
    return;
  }
  if (!liveState.connected){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.noBridgeConnectionHint'); }
    return;
  }
  // Em modo contínuo NÃO se repete o countdown de "Medir agora" a cada
  // reenvio (~14 em 14s) — reportado pelo utilizador como sem sentido
  // (2026-07-22): o countdown existe para uma medição pontual, não faz
  // sentido reaparecer/reiniciar em loop enquanto o modo contínuo está
  // ativo. Mostra-se um indicador fixo em vez disso; stopForceReadingCountdown()
  // continua a existir para o caso de já haver um countdown de uma medição
  // pontual anterior em curso quando o modo contínuo é ligado.
  const fire = () => {
    sendWsCommand('force_reading');
  };
  if (btn){ btn.className = 'btn-primary'; btn.setAttribute('aria-pressed', 'true'); btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg> ${t('vitais.continuousHrStopBtn')}`; }
  if (forceBtn) forceBtn.disabled = true;
  stopForceReadingCountdown();
  if (hint){ hint.style.color = 'var(--status-good)'; hint.textContent = t('vitais.continuousHrActiveHint'); }
  fire();
  continuousHrIntervalId = setInterval(fire, (FORCE_READING_SECONDS - 1) * 1000);
}

function resetPendingCommandButtons(){
  const forceBtn = document.getElementById('forceReadingBtn');
  const forceHint = document.getElementById('forceReadingHint');
  if (forceBtn && forceBtn.disabled){
    stopForceReadingCountdown();
    forceBtn.disabled = false;
    if (forceHint){
      forceHint.style.color = 'var(--status-warning)';
      forceHint.textContent = 'Ligação ao bridge perdida antes de haver resposta. Tenta novamente.';
    }
  }
  const resetBtn = document.getElementById('resetConfirmBtn');
  const resetStatus = document.getElementById('resetModalStatus');
  if (resetBtn && resetBtn.disabled){
    resetBtn.disabled = false;
    if (resetStatus){
      resetStatus.className = 'modal-status err';
      resetStatus.textContent = 'Ligação ao bridge perdida antes de haver resposta. Tenta novamente.';
    }
  }
}

function onForceReadingClick(){
  const btn = document.getElementById('forceReadingBtn');
  const hint = document.getElementById('forceReadingHint');
  if (!liveState.connected){
    hint.textContent = 'Sem ligação ao bridge — liga o dispositivo primeiro.';
    hint.style.color = 'var(--status-warning)';
    return;
  }
  const sent = sendWsCommand('force_reading');
  if (!sent){
    hint.textContent = 'Não foi possível contactar o bridge. Tenta novamente.';
    hint.style.color = 'var(--status-warning)';
    return;
  }
  btn.disabled = true;
  startForceReadingCountdown();
}

function handleCommandResult(msg){
  if (msg.cmd === 'force_reading'){
    // Só ok=false interrompe o countdown cedo (falha real confirmada
    // pelo bridge). ok=true só confirma que o comando FOI ENVIADO — a
    // medição em si continua a decorrer no dispositivo, por isso o
    // countdown continua a correr (ver nota acima).
    if (!msg.ok){
      finishForceReadingCountdown(`Falhou: ${msg.error || 'erro desconhecido'}.`, true);
    }
  }
  if (msg.cmd === 'correct_activity'){
    // Sucesso não precisa de tratamento aqui: o bridge difunde de volta um
    // "activity_correction" (ver handleBridgeMessage) que já atualiza o
    // painel para todos os clientes, incluindo este. Só o erro precisa de
    // feedback próprio, porque nesse caso não há nenhum broadcast a seguir.
    const status = document.getElementById('activityCorrectionStatus');
    if (status && !msg.ok){
      status.textContent = t('resumo.liveActivityCorrectionError', {error: msg.error || '—'});
      status.style.display = '';
      setTimeout(() => { status.style.display = 'none'; }, 4000);
    }
  }
  if (msg.cmd === 'reset_readings'){
    const status = document.getElementById('resetModalStatus');
    const confirmBtn = document.getElementById('resetConfirmBtn');
    if (confirmBtn) confirmBtn.disabled = false;
    if (status){
      status.className = 'modal-status ' + (msg.ok ? 'ok' : 'err');
      status.textContent = msg.ok ? 'Leituras apagadas com sucesso.' : `Falhou: ${msg.error || 'erro desconhecido'}.`;
    }
    if (msg.ok) setTimeout(closeResetModal, 1600);
  }
}

function openResetModal(){
  const status = document.getElementById('resetModalStatus');
  status.className = 'modal-status'; status.textContent = '';
  document.getElementById('resetConfirmBtn').disabled = false;
  document.getElementById('resetModalOverlay').style.display = 'flex';
}
function closeResetModal(){
  document.getElementById('resetModalOverlay').style.display = 'none';
}
function confirmReset(){
  const status = document.getElementById('resetModalStatus');
  const confirmBtn = document.getElementById('resetConfirmBtn');
  if (!liveState.connected){
    status.className = 'modal-status err';
    status.textContent = 'Sem ligação ao bridge — não é possível repor agora.';
    return;
  }
  const sent = sendWsCommand('reset_readings');
  if (!sent){
    status.className = 'modal-status err';
    status.textContent = 'Não foi possível contactar o bridge.';
    return;
  }
  confirmBtn.disabled = true;
  status.className = 'modal-status';
  status.textContent = 'A apagar…';
}

/* ------------------------------------------------------------
   EXPORTAÇÃO CLÍNICA — FHIR (JSON) e PDF (via impressão do browser)
   ------------------------------------------------------------
   Pedido do backlog de investigação (item nº7): exportação clínica em
   formato interoperável. Diferente da exportação de dados brutos acima
   (que precisa da BD ainda por implementar) — cobre só os alertas,
   anomalias e identidade do paciente atualmente visíveis nesta sessão.
------------------------------------------------------------ */

// Constrói um "Bundle" FHIR simplificado (Patient + Observation por cada
// alerta/anomalia atual). NOTA HONESTA: isto usa a FORMA de recursos FHIR
// (resourceType, campos reconhecíveis) para dar uma base de arranque
// realista, mas não é uma implementação certificada — não usa códigos
// LOINC/SNOMED reais (os "code.text" são descrição livre), e não passou
// por validação contra o standard completo. Serve como ponto de partida
// para uma integração real, não como substituto dela.
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
function requestRetentionSettings(){
  const hint = document.getElementById('retentionHint');
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = 'Sem ligação ao bridge — a mostrar o valor por omissão do protótipo (30 dias).'; }
    return;
  }
  sendWsCommand('get_retention_days');
}

function handleRetentionDaysResult(msg){
  const input = document.getElementById('retentionDaysInput');
  const hint = document.getElementById('retentionHint');
  if (input && msg.days != null) input.value = msg.days;
  if (input && msg.min_days != null) input.min = msg.min_days;
  if (input && msg.max_days != null) input.max = msg.max_days;
  if (hint){ hint.style.color = ''; hint.textContent = ''; }
}

function saveRetentionDays(){
  const input = document.getElementById('retentionDaysInput');
  const hint = document.getElementById('retentionHint');
  const days = input ? Number(input.value) : NaN;
  if (!Number.isFinite(days) || days <= 0){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = 'Introduz um número de dias válido (maior que 0).'; }
    return;
  }
  const sent = sendWsCommandWithArgs('set_retention_days', { days });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = 'Sem ligação ao bridge — não é possível guardar agora.'; }
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = 'A guardar…'; }
}

function handleRetentionDaysSaveResult(msg){
  const hint = document.getElementById('retentionHint');
  if (!hint) return;
  if (msg.ok){
    hint.style.color = 'var(--status-good)';
    hint.textContent = `Guardado — a manter registos dos últimos ${msg.days} dias.`;
  } else {
    hint.style.color = 'var(--status-warning)';
    hint.textContent = `Falhou: ${msg.error || 'erro desconhecido'}.`;
  }
}

/* ------------------------------------------------------------
   CONSENTIMENTO GRANULAR POR ÂMBITO (RGPD, 2026-08-05)
   ------------------------------------------------------------
   Distinto do `.consent-toggle` de "definicoes" (partilha com
   cuidadores, guardado em localStorage) — isto lê/grava
   `ConsentRecord` real na base de dados do bridge (ver
   bridge/storage_advanced.py::CONSENT_SCOPES), por isso a função
   chama-se setConsentScope() e não setConsent(), para não colidir com
   a já existente. Cada âmbito é independente: sensor_data, analytics,
   export, research (mesma ordem de sa.CONSENT_SCOPES).
------------------------------------------------------------- */
const CONSENT_SCOPES_UI = [
  { scope: 'sensor_data', labelKey: 'consentimento.scopeSensorData', descKey: 'consentimento.scopeSensorDataDesc' },
  { scope: 'analytics', labelKey: 'consentimento.scopeAnalytics', descKey: 'consentimento.scopeAnalyticsDesc' },
  { scope: 'export', labelKey: 'consentimento.scopeExport', descKey: 'consentimento.scopeExportDesc' },
  { scope: 'research', labelKey: 'consentimento.scopeResearch', descKey: 'consentimento.scopeResearchDesc' },
];

// null = ainda não chegou nenhuma resposta do bridge (distinto de "{}" =
// chegou mas nenhum âmbito foi decidido, o que get_consent_status também
// pode devolver por scope).
liveState.consentStatus = null;

function requestConsentStatus(){
  const hint = document.getElementById('consentHint');
  renderConsentScopes(); // desenha a lista já (estado "a carregar/sem ligação")
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('consentimento.noBridgeConnectionHint'); }
    return;
  }
  sendWsCommand('get_consent_status');
}

function handleConsentStatusResult(msg){
  const hint = document.getElementById('consentHint');
  liveState.consentStatus = (msg.status && typeof msg.status === 'object') ? msg.status : {};
  if (hint){
    if (msg.error){ hint.style.color = 'var(--status-warning)'; hint.textContent = msg.error; }
    else { hint.style.color = ''; hint.textContent = ''; }
  }
  renderConsentScopes();
}

function setConsentScope(scope, granted){
  const hint = document.getElementById('consentHint');
  const repNameInput = document.getElementById('consentRepName');
  const representativeName = repNameInput && repNameInput.value.trim() ? repNameInput.value.trim() : undefined;
  const sent = sendWsCommandWithArgs('set_consent', { scope, granted, representative_name: representativeName });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('consentimento.noBridgeConnectionHint'); }
    renderConsentScopes(); // repõe o toggle visualmente (o pedido nem saiu)
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('consentimento.savingHint'); }
}

function handleConsentResultMessage(msg){
  const hint = document.getElementById('consentHint');
  if (!msg.ok){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = `Falhou: ${msg.error || 'erro desconhecido'}.`; }
    renderConsentScopes(); // repõe o toggle para o estado real (a gravação falhou)
    return;
  }
  if (hint){ hint.style.color = 'var(--status-good)'; hint.textContent = 'Guardado.'; }
  // Pede o estado completo de novo em vez de compor localmente — a base de
  // dados (signed_at/version reais) é sempre a fonte de verdade, o mesmo
  // raciocínio de handleRetentionDaysSaveResult acima.
  sendWsCommand('get_consent_status');
}

function renderConsentScopes(){
  const list = document.getElementById('consentScopesList');
  if (!list) return; // vista "exportar" não está aberta
  const status = liveState.consentStatus;
  list.innerHTML = CONSENT_SCOPES_UI.map(({ scope, labelKey, descKey }) => {
    const entry = status ? status[scope] : null;
    const granted = !!(entry && entry.granted);
    let statusText, statusColor;
    if (!entry){ statusText = t('consentimento.statusNeverDecided'); statusColor = 'var(--text-secondary)'; }
    else if (entry.granted){ statusText = t('consentimento.statusGranted'); statusColor = 'var(--status-good)'; }
    else { statusText = t('consentimento.statusRevoked'); statusColor = 'var(--status-warning)'; }
    return `
      <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
        <label class="consent-toggle"><input type="checkbox" aria-label="${t(labelKey)}" ${granted ? 'checked' : ''} ${status ? '' : 'disabled'} onchange="setConsentScope('${scope}', this.checked)"><span></span></label>
        <div style="min-width:180px;">
          <div style="font-size:13px; font-weight:600;">${t(labelKey)}</div>
          <div style="font-size:12px; color:var(--text-secondary);">${t(descKey)}</div>
        </div>
        <span style="font-size:12px; font-weight:600; color:${statusColor};">${statusText}</span>
      </div>`;
  }).join('');
}

/* ------------------------------------------------------------
   BASELINE COMPORTAMENTAL PERSONALIZADA (2026-08-05)
   ------------------------------------------------------------
   Lê/grava PersonalizedThreshold real (ver bridge/storage_advanced.py) e
   mostra alertas de FC/SpO2 em tempo real (bridge/vital_alerts.py) quando
   uma leitura sai dos limiares definidos. Só expõe no formulário os 3
   campos com avaliação em tempo real de facto ligada (heart_rate_min/max,
   spo2_min) — o esquema tem mais 4 campos (inactivity_threshold_seconds,
   sleep/activity_target_minutes, steps_target_daily) sem nenhuma rotina a
   avaliá-los ainda; não expor um controlo que não faz nada é a mesma
   disciplina já aplicada ao resto do dashboard (ex.: ACTIVITY_ML_DISCLAIMER).
------------------------------------------------------------- */
function requestThresholds(){
  const hint = document.getElementById('thresholdsHint');
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.noBridgeConnectionHint'); }
    return;
  }
  sendWsCommand('get_thresholds');
}

function handleThresholdsResult(msg){
  const th = msg.thresholds || {};
  const hrMin = document.getElementById('thresholdHrMin');
  const hrMax = document.getElementById('thresholdHrMax');
  const spo2Min = document.getElementById('thresholdSpo2Min');
  if (hrMin && th.heart_rate_min != null) hrMin.value = th.heart_rate_min;
  if (hrMax && th.heart_rate_max != null) hrMax.value = th.heart_rate_max;
  if (spo2Min && th.spo2_min != null) spo2Min.value = th.spo2_min;
  const hint = document.getElementById('thresholdsHint');
  if (hint){
    hint.style.color = '';
    hint.textContent = th.is_default ? t('vitais.baselineIsDefaultHint') : '';
  }
}

function saveThresholds(){
  const hint = document.getElementById('thresholdsHint');
  const hrMin = Number(document.getElementById('thresholdHrMin')?.value);
  const hrMax = Number(document.getElementById('thresholdHrMax')?.value);
  const spo2Min = Number(document.getElementById('thresholdSpo2Min')?.value);
  if (![hrMin, hrMax, spo2Min].every(Number.isFinite)){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.baselineInvalidHint'); }
    return;
  }
  const sent = sendWsCommandWithArgs('set_thresholds', {
    heart_rate_min: hrMin, heart_rate_max: hrMax, spo2_min: spo2Min,
  });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.noBridgeConnectionHint'); }
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('consentimento.savingHint'); }
}

function handleThresholdsSaveResult(msg){
  const hint = document.getElementById('thresholdsHint');
  if (!hint) return;
  if (msg.ok){
    hint.style.color = 'var(--status-good)';
    hint.textContent = t('vitais.baselineSavedHint');
    handleThresholdsResult({ thresholds: msg.thresholds });
  } else {
    hint.style.color = 'var(--status-warning)';
    hint.textContent = `${t('vitais.baselineSaveFailedPrefix')} ${msg.error || t('vitais.baselineUnknownError')}.`;
  }
}

// Estado ao vivo dos alertas de sinal vital (ver kind "vital_alert" em
// handleBridgeMessage) — indexado por sinal ('hr'/'spo2'), cada entrada é
// o payload do alerta ativo ou null (dentro dos limiares).
liveState.vitalAlerts = { hr: null, spo2: null };

function renderVitalAlertsPanel(){
  const host = document.getElementById('vitalAlertsPanel');
  if (!host) return;
  const active = Object.values(liveState.vitalAlerts).filter(Boolean);
  if (!active.length){ host.innerHTML = ''; return; }
  host.innerHTML = active.map(a => `
    <div class="activity-live-flag anomaly" style="margin-bottom:8px;">⚠ ${escapeHtml(a.explanation)}</div>
  `).join('');
}

/* ------------------------------------------------------------
   VERSIONAMENTO E ROLLBACK DO MODELO ML (2026-08-05)
   ------------------------------------------------------------
   Lista as versões registadas do classificador de atividade
   (bridge/storage_advanced.py::MlModelVersion) e permite ativar
   (rollback/promoção) uma delas em runtime, sem reiniciar o bridge — ver
   cmds "list_model_versions"/"activate_model_version" em ble_bridge.py.
------------------------------------------------------------- */
liveState.modelVersions = null; // null = ainda não chegou nenhuma resposta

function requestModelVersions(){
  const hint = document.getElementById('modelVersionsHint');
  renderModelVersionsList();
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('modeloVersao.noBridgeConnectionHint'); }
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('modeloVersao.loadingHint'); }
  sendWsCommand('list_model_versions');
}

function handleModelVersionsResult(msg){
  const hint = document.getElementById('modelVersionsHint');
  liveState.modelVersions = Array.isArray(msg.versions) ? msg.versions : [];
  if (hint){
    if (msg.error){ hint.style.color = 'var(--status-warning)'; hint.textContent = msg.error; }
    else { hint.style.color = ''; hint.textContent = ''; }
  }
  renderModelVersionsList();
}

function activateModelVersion(version){
  const hint = document.getElementById('modelVersionsHint');
  const sent = sendWsCommandWithArgs('activate_model_version', { version });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('modeloVersao.noBridgeConnectionHint'); }
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('modeloVersao.activatingHint'); }
}

function handleModelVersionResult(msg){
  const hint = document.getElementById('modelVersionsHint');
  if (!msg.ok){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = `${t('vitais.baselineSaveFailedPrefix')} ${msg.error || t('episodio.unknownError')}.`; }
    return;
  }
  if (hint){
    hint.style.color = msg.reloaded ? 'var(--status-good)' : 'var(--status-warning)';
    hint.textContent = msg.reloaded ? t('modeloVersao.activatedHint') : t('modeloVersao.reloadFailedHint');
  }
  // Pede o estado completo de novo — a base de dados é sempre a fonte de
  // verdade, mesmo raciocínio de handleConsentResultMessage/
  // handleThresholdsSaveResult acima.
  sendWsCommand('list_model_versions');
}

function renderModelVersionsList(){
  const list = document.getElementById('modelVersionsList');
  if (!list) return; // vista "exportar" não está aberta
  const versions = liveState.modelVersions;
  if (versions === null){ list.innerHTML = ''; return; }
  if (!versions.length){ list.innerHTML = `<p class="empty-hint">${t('modeloVersao.emptyHint')}</p>`; return; }
  list.innerHTML = `
    <table class="data-table">
      <tbody>
        ${versions.map(v => `
          <tr>
            <td><b>${escapeHtml(String(v.version))}</b>${v.is_active ? ` ${pillHtml('good', t('modeloVersao.activeLabel'))}` : ''}</td>
            <td class="table-subtext" style="padding:8px 0;">${escapeHtml(v.notes || v.file_path || '')}</td>
            <td>${v.is_active ? '' : `<button class="btn-secondary" onclick="activateModelVersion('${escapeHtml(String(v.version))}')">${t('modeloVersao.activateBtn')}</button>`}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

/* ------------------------------------------------------------
   HISTÓRICO REAL NA VISTA "TENDÊNCIA SEMANAL" — ligado à BD SQLite do
   bridge (bridge/storage.py::get_daily_summary), pedido de
   PROJECT_STATUS.md ("Base de dados" — próximo passo natural depois de
   `get_history`/CSV). Deliberadamente um cartão SEPARADO do gráfico
   `currentTrendData()` (sempre sintético) em vez de misturado na mesma linha —
   evita qualquer ambiguidade sobre o que é real e o que é simulado.
------------------------------------------------------------ */
function requestRealTrend(days){
  const status = document.getElementById('realTrendStatus');
  if (!liveState.connected){
    if (status) status.textContent = 'Sem ligação ao bridge — liga o dispositivo para veres histórico real.';
    return;
  }
  const sent = sendWsCommandWithArgs('get_daily_trend', { days });
  if (!sent){
    if (status) status.textContent = 'Não foi possível contactar o bridge.';
    return;
  }
  if (status) status.textContent = 'A carregar histórico real…';
}

function renderRealTrendTable(){
  const body = document.getElementById('realTrendBody');
  const status = document.getElementById('realTrendStatus');
  if (!body) return; // vista "Tendência semanal" não está ativa agora
  const rows = liveState.realTrend || [];
  if (!rows.length){
    body.innerHTML = '';
    if (status){
      status.textContent = liveState.connected
        ? t('tendencia.realHistoryNotEnoughData')
        : t('tendencia.realHistoryNotConnected');
    }
    return;
  }
  if (status) status.textContent = '';
  // Bug de segurança corrigido (S03 frontend-security): `d.day` e
  // `d.record_count` vêm de `msg.days_summary` (mensagem `daily_trend` do
  // bridge via WebSocket, canal não autenticado — ver toFiniteNumber()
  // acima) e entravam diretamente em innerHTML sem qualquer validação,
  // ao contrário do padrão já usado para hr/spo2/steps em applyLiveVitals().
  // `d.day` passa a ser escapado (texto livre do ponto de vista do
  // browser) e os campos numéricos a passar por toFiniteNumber(), tal como
  // os outros valores vindos do bridge.
  body.innerHTML = rows.map(d => {
    const hrSamples = toFiniteNumber(d.hr_samples) || 0;
    const avgHr = toFiniteNumber(d.avg_hr) || 0;
    const hrText = hrSamples > 0 ? `${Math.round(avgHr)} bpm (${hrSamples} ${t('tendencia.realHistoryReadings')})` : t('tendencia.realHistoryNoReadings');
    const maxSteps = toFiniteNumber(d.max_steps), minSteps = toFiniteNumber(d.min_steps);
    const stepsText = (maxSteps != null && minSteps != null) ? `${maxSteps - minSteps}` : '—';
    const recordCount = toFiniteNumber(d.record_count) || 0;
    return `<tr><td>${escapeHtml(d.day)}</td><td class="num">${recordCount}</td><td>${hrText}</td><td class="num">${stepsText}</td></tr>`;
  }).join('');
}

// Monta a folha de impressão (#clinicalPrintSheet, só visível em
// @media print — ver CSS) com o resumo clínico atual, e chama
// window.print(). É o browser que trata da conversão para PDF (a maioria
// tem "Guardar como PDF" no diálogo de impressão) — evita depender de
// bibliotecas externas de geração de PDF, que a CSP do Artifact bloqueia
// de qualquer forma.
// Folha de impressão melhorada (2026-07-03, pedido do utilizador): cabeçalho
// com marca "CW" (a mesma da barra lateral, para consistência visual),
// rodapé com nota de confidencialidade + numeração de página (via
// contador CSS em @page, ver .print-only mais abaixo — suportado pelo
// motor de impressão do Chromium; noutros motores a página simplesmente
// não mostra o número, sem quebrar o resto do documento), e tabelas com
// alinhamento e espaçamento consistentes (células com padding, cabeçalhos
// com fundo, linhas alternadas para facilitar a leitura).
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
let resizeRedrawTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeRedrawTimer);
  resizeRedrawTimer = setTimeout(() => {
    const active = document.querySelector('.nav-item.active');
    if (active && document.getElementById('view-app').classList.contains('active')){
      AFTER_RENDER[active.dataset.view] && AFTER_RENDER[active.dataset.view]();
    }
  }, 150);
});
