//sistema de lembretes de medicação com alarmes e notificações
class MedicationReminder {
  constructor(options = {}) {
    this.checkInterval = options.checkInterval || 5 * 60 * 1000; // 5 min
    this.reminderWindow = options.reminderWindow || 30 * 60 * 1000; // 30 min antes/depois
    this.intervalId = null;
    this.notificationPermission = 'default';
    this.shownNotifications = new Set(); //evita notificações duplicadas
    
    this.requestNotificationPermission();
  }

  requestNotificationPermission() {
    if (!('Notification' in window)) {
      console.warn('[MedicationReminder] Notificações do browser não disponíveis');
      return;
    }
    if (Notification.permission === 'granted') {
      this.notificationPermission = 'granted';
    } else if (Notification.permission !== 'denied') {
      Notification.requestPermission().then(perm => {
        this.notificationPermission = perm;
      });
    }
  }

  start() {
    if (this.intervalId) return;
    console.log('[MedicationReminder] Iniciando verificação de lembretes...');
    this.intervalId = setInterval(() => this.checkAndNotify(), this.checkInterval);
    this.checkAndNotify();
  }

  stop() {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
      console.log('[MedicationReminder] Parado');
    }
  }

  checkAndNotify() {
    const now = new Date();
    const currentPatient = (typeof getCurrentPatient === 'function') ? getCurrentPatient()
      : (typeof selectedPatient === 'function') ? selectedPatient() : null;

    if (!currentPatient) return;

    const meds = (typeof patientMedications === 'function') ? patientMedications(currentPatient) : [];

    const todayStr = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`; //incluído em notifKey para não bloquear lembretes futuros do dia seguinte
    for (const key of this.shownNotifications) { //limpa chaves de dias anteriores
      if (!key.endsWith(`_${todayStr}`)) this.shownNotifications.delete(key);
    }

    for (const med of meds) {
      if (!med.times || !Array.isArray(med.times)) continue;

      for (const time of med.times) {
        const [h, m] = time.split(':').map(Number);
        const scheduledTime = new Date();
        scheduledTime.setHours(h, m, 0, 0);

        const timeDiff = Math.abs(now - scheduledTime);
        if (timeDiff <= this.reminderWindow) {
          const notifKey = `${currentPatient.id}_${med.id}_${time}_${todayStr}`;

          if (!this.shownNotifications.has(notifKey)) {
            const taken = (typeof isDoseTakenToday === 'function') ? isDoseTakenToday(currentPatient.id, med.id, time) : false;

            if (!taken) {
              this.showNotification(med, time, currentPatient);
              this.shownNotifications.add(notifKey);
            }
          }
        }
      }
    }
  }

  showNotification(medication, time, patient) {
    if (this.notificationPermission !== 'granted') {
      this.showFallbackAlert(medication, time, patient);
      return;
    }

    const title = `💊 ${t('medrem.notifTitle')}`;
    const options = {
      body: `${medication.name} ${medication.dose} ${t('medrem.at')} ${time}`,
      tag: `med_${patient.id}_${medication.id}_${time}`,
      badge: '💊',
      icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y="50%" x="50%" dominant-baseline="middle" text-anchor="middle" font-size="50">💊</text></svg>',
      requireInteraction: true,
    };
    //sem Service Worker, options.actions/onaction nunca disparam; mostra-se sempre o fallback com ação real
    const notification = new Notification(title, options);

    notification.onclick = () => {
      notification.close();
      if (window.focus) window.focus();
      //sincronizarMenu() resolve o grupo pelo currentRole, evita acender botão escondido de outro perfil
      if (typeof sincronizarMenu === 'function') sincronizarMenu('medicacao');
      if (typeof renderView === 'function') renderView('medicacao');
    };

    this.showFallbackAlert(medication, time, patient);
  }

  showFallbackAlert(medication, time, patient) {
    //cartão de alerta empilhável, ID único por paciente+medicamento+hora
    let stack = document.getElementById('medicationReminderStack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'medicationReminderStack';
      stack.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 9999;
        display: flex;
        flex-direction: column;
        gap: 12px;
        max-width: 300px;
      `;
      document.body.appendChild(stack);
    }

    const bannerId = `medicationReminder_${patient.id}_${medication.id}_${time}`.replace(/[^a-zA-Z0-9_]/g, '_');
    if (document.getElementById(bannerId)) return;

    const div = document.createElement('div');
    div.id = bannerId;
    div.style.cssText = `
      background: var(--status-warning-bg, rgba(250,178,25,0.14));
      border: 1px solid var(--status-warning, #fab219);
      border-radius: var(--radius-md, 10px);
      padding: 16px;
      font-family: var(--font-ui, sans-serif);
      color: var(--text-primary, white);
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      animation: slideIn 0.3s ease-out;
    `;

    //createElement/textContent em vez de innerHTML: medication.name/dose/time são texto livre editável (XSS)
    const title = document.createElement('strong');
    title.textContent = `💊 ${medication.name}`;
    div.appendChild(title);
    div.appendChild(document.createElement('br'));
    div.appendChild(document.createTextNode(`${medication.dose} ${t('medrem.at')} ${time}`));
    div.appendChild(document.createElement('br'));

    const btn = document.createElement('button');
    btn.textContent = `✓ ${t('medrem.takenNowBtn')}`;
    btn.style.cssText = 'margin-top:8px; padding:6px 12px; background:var(--accent,#3FD6C0); border:none; border-radius:4px; cursor:pointer; color:var(--accent-ink,#04211D);';
    btn.addEventListener('click', () => {
      if (typeof markDoseTaken === 'function') markDoseTaken(patient.id, medication.id, time);
      div.remove();
    });
    div.appendChild(btn);

    stack.appendChild(div);

    setTimeout(() => {
      if (div.parentElement) div.remove();
    }, 30 * 1000);
  }

  static injectStyles() {
    if (document.getElementById('medicationReminderStyles')) return;
    
    const style = document.createElement('style');
    style.id = 'medicationReminderStyles';
    style.textContent = `
      @keyframes slideIn {
        from {
          transform: translateX(400px);
          opacity: 0;
        }
        to {
          transform: translateX(0);
          opacity: 1;
        }
      }
      
      .medication-reminder-badge {
        display: inline-block;
        background: var(--status-warning, #fab219);
        color: #000;
        border-radius: 12px;
        padding: 2px 8px;
        font-size: 12px;
        font-weight: bold;
        margin-left: 8px;
      }
    `;
    document.head.appendChild(style);
  }
}

//correlaciona adesão com atividade/vitais por data
class AdherenceAnalytics {
  constructor() {
    this.logKey = 'carewear_adherence_analytics';
  }

  recordDay(patientId, adherencePct, activityLevel = null, hrAvg = null) {
    const today = new Date().toISOString().split('T')[0];
    const logs = this.loadLogs(patientId);
    logs[today] = { adherence_pct: adherencePct, activity_level: activityLevel, hr_avg: hrAvg };
    this.saveLogs(patientId, logs);
  }

  loadLogs(patientId) {
    try {
      const data = localStorage.getItem(`${this.logKey}_${patientId}`);
      return data ? JSON.parse(data) : {};
    } catch (e) {
      return {};
    }
  }

  saveLogs(patientId, logs) {
    try {
      localStorage.setItem(`${this.logKey}_${patientId}`, JSON.stringify(logs));
    } catch (e) {
      console.warn('[AdherenceAnalytics] localStorage indisponível');
    }
  }

  getWeekSummary(patientId) {
    const logs = this.loadLogs(patientId);
    const entries = Object.entries(logs)
      .sort(([dateA], [dateB]) => dateB.localeCompare(dateA))
      .slice(0, 7);

    if (entries.length === 0) {
      //patternsKey/alertKey: identificadores estáveis para getRecommendations(), nunca comparar a string traduzida
      return { avg_adherence: 0, patterns: t('medrem.patternsNoData'), patternsKey: 'no_data', alert: t('medrem.noHistoryYet'), alertKey: 'no_history' };
    }

    const adherences = entries.map(([_, v]) => v.adherence_pct);
    const avgAdherence = Math.round(adherences.reduce((a, b) => a + b, 0) / adherences.length);

    let alert = '', alertKey = '';
    if (avgAdherence < 50) {
      alert = `⚠️ ${t('medrem.alertLow')}`; alertKey = 'low';
    } else if (avgAdherence < 80) {
      alert = `✓ ${t('medrem.alertModerate')}`; alertKey = 'moderate';
    } else {
      alert = `✅ ${t('medrem.alertGreat')}`; alertKey = 'great';
    }

    const lowAdherenceHighActivity = entries.filter(([_, v]) =>
      v.adherence_pct < 80 && v.activity_level === 'high'
    ).length;

    let patterns = t('medrem.patternsNormal'), patternsKey = 'normal';
    if (lowAdherenceHighActivity > 2) {
      patterns = t('medrem.patternsCorrelation'); patternsKey = 'correlation';
    }

    return {
      avg_adherence: avgAdherence,
      patterns, patternsKey,
      alert, alertKey,
      entries: entries.map(([date, v]) => ({ date, ...v }))
    };
  }

  getRecommendations(patientId, patient) {
    const summary = this.getWeekSummary(patientId);
    const recs = [];

    if (summary.avg_adherence < 50) {
      recs.push(`🔴 ${t('medrem.recCriticalContact')}`);
      recs.push(`💡 ${t('medrem.recCriticalAlarms')}`);
    } else if (summary.avg_adherence < 80) {
      recs.push(`🟡 ${t('medrem.recModerateAttention')}`);
      recs.push(`💡 ${t('medrem.recModerateMealTiming')}`);
    } else {
      recs.push(`🟢 ${t('medrem.recExcellent')}`);
    }

    if (summary.patternsKey === 'correlation') {
      recs.push(`💡 ${t('medrem.recLowActivityScheduling')}`);
    }

    return recs;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  MedicationReminder.injectStyles();
  window.medicationReminder = new MedicationReminder();
  window.adherenceAnalytics = new AdherenceAnalytics();
  window.medicationReminder.start();
  
  console.log('[MedicationReminder] Sistema de lembretes ativo');
});
