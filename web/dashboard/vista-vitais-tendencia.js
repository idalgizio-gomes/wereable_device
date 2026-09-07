TEMPLATES.vitais = () => `
  <div class="card" style="padding:14px 17px;">
    <div style="display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap;">
      <p class="empty-hint" id="forceReadingHint" style="padding:0; margin:0;">${t('vitais.forceReadingHint')}</p>
      <div style="display:flex; gap:8px; flex-wrap:wrap;">
        <button class="btn-secondary" id="forceReadingBtn" onclick="onForceReadingClick()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>
          ${t('vitais.measureNowBtn')}
        </button>
        <button class="${continuousHrIntervalId != null ? 'btn-primary' : 'btn-secondary'}" id="continuousHrBtn" aria-pressed="${continuousHrIntervalId != null}" onclick="toggleContinuousHr()" title="${t('vitais.continuousHrHint')}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg>
          ${continuousHrIntervalId != null ? t('vitais.continuousHrStopBtn') : t('vitais.continuousHrStartBtn')}
        </button>
      </div>
    </div>
  </div>

  <div class="stat-row">
    ${statTile('heart',t('vitais.hrCardTitle'),'—','bpm', 'var(--status-good)', 'stat-hr-2')}
    ${statTile('zap',t('vitais.spo2CardTitle'),'—','%', 'var(--status-good)', 'stat-spo2-2', false, 'spo2-hint-2')}
    ${statTile('walk',t('vitais.stepsCardTitle'),'—','', 'var(--cat-atividade)', 'stat-steps-2')}
    ${statTile('warn',t('vitais.fallsCardTitle'),'—','', 'var(--status-good)', 'stat-falls-2')}
  </div>
  <div id="vitalAlertsPanel"></div>
  <div class="card">
    <div class="card-head">
      <div><h3>${t('vitais.hrCardTitle')} <span id="hrChartLabel">${t('vitais.hrChartDemo')}</span></h3><div class="card-sub">${t('vitais.hrCardSubtitle')}</div></div>
    </div>
    <canvas id="cvHr" height="170" role="img" aria-label="${t('vitais.hrChartAria')}"></canvas>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('vitais.baselineTitle')}</h3><div class="card-sub">${t('vitais.baselineSubtitle')}</div></div></div>
    <div style="display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end;">
      <div>
        <label for="thresholdHrMin" style="display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${t('vitais.hrMinLabel')}</label>
        <input id="thresholdHrMin" type="number" min="20" max="150" style="width:90px;" class="row-input">
      </div>
      <div>
        <label for="thresholdHrMax" style="display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${t('vitais.hrMaxLabel')}</label>
        <input id="thresholdHrMax" type="number" min="40" max="220" style="width:90px;" class="row-input">
      </div>
      <div>
        <label for="thresholdSpo2Min" style="display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${t('vitais.spo2MinLabel')}</label>
        <input id="thresholdSpo2Min" type="number" min="70" max="100" style="width:90px;" class="row-input">
      </div>
      <button class="btn-secondary" onclick="saveThresholds()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>
        ${t('common.save')}
      </button>
      <span class="empty-hint" id="thresholdsHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('vitais.baselineNoteEmpty')}</p>
  </div>
`;
AFTER_RENDER.vitais = () => { drawHrSeries('cvHr'); applyLiveVitals(); requestThresholds(); renderVitalAlertsPanel(); };

TEMPLATES.tendencia = () => `
  <div class="card">
    <div class="card-head"><div><h3>${t('tendencia.trendCardTitle')}</h3></div></div>
    <canvas id="cvTrend2" height="220" role="img" aria-label="${t('tendencia.trendChartAria')}"></canvas>
    <div class="legend">
      <span class="legend-item"><span class="legend-swatch" style="background:var(--cat-atividade)"></span>${t('tendencia.legendSteps')}</span>
      <span class="legend-item"><span class="legend-swatch" style="background:var(--cat-dormir)"></span>${t('tendencia.legendSleep')}</span>
      <span class="legend-item"><span class="legend-swatch" style="background:var(--status-critical)"></span>${t('tendencia.legendAvgHr')}</span>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><div><h3>${t('tendencia.weeklyPatternCardTitle')}</h3></div></div>
    <canvas id="cvHeatmap2" height="200" role="img" aria-label="${t('tendencia.heatmapChartAria')}"></canvas>
  </div>
  <div class="card">
    <div class="card-head">
      <div><h3>${t('tendencia.realHistoryCardTitle')}</h3>
      <div class="card-sub">${t('tendencia.realHistoryCardSubtitle')}</div></div>
      <button class="btn-secondary" onclick="requestRealTrend(7)">${t('tendencia.refreshBtn')}</button>
    </div>
    <table class="data-table">
      <thead><tr><th>${t('tendencia.thDay')}</th><th>${t('tendencia.thRecords')}</th><th>${t('tendencia.thAvgHr')}</th><th>${t('tendencia.thStepsDelta')}</th></tr></thead>
      <tbody id="realTrendBody"></tbody>
    </table>
    <p class="empty-hint" id="realTrendStatus"></p>
  </div>
`;
AFTER_RENDER.tendencia = () => { drawTrend('cvTrend2'); drawHeatmap('cvHeatmap2'); renderRealTrendTable(); requestRealTrend(7); };
AFTER_RENDER.exportar = () => { requestRetentionSettings(); requestConsentStatus(); requestModelVersions(); };
