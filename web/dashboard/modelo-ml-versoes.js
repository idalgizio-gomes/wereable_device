/* Gestao de versoes do modelo de ML - extraido de bridge-exportacao.js */

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
