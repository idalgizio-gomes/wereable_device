/**
 * medication-reminders.js — Sistema de lembretes de medicação com alarmes e notificações
 * 
 * Funcionalidades:
 * - Agendamento de lembretes (notificações do browser a cada 5 min)
 * - Histórico de adesão com persistência em localStorage
 * - Correlação com vitais/atividade (análise manual por data)
 * - Integração com bridge (futuro: sincronizar com BD SQL)
 * 
 * Uso no dashboard (index.html):
 *   const reminders = new MedicationReminder();
 *   reminders.start();  // inicia o loop de verificação
 *   reminders.addReminder(patientId, medicationId, time);  // ex: "08:00"
 */

class MedicationReminder {
  constructor(options = {}) {
    this.checkInterval = options.checkInterval || 5 * 60 * 1000; // 5 min
    this.reminderWindow = options.reminderWindow || 30 * 60 * 1000; // 30 min antes/depois
    this.intervalId = null;
    this.notificationPermission = 'default';
    this.shownNotifications = new Set(); // evita notificações duplicadas
    
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
    if (this.intervalId) return; // já em execução
    console.log('[MedicationReminder] Iniciando verificação de lembretes...');
    this.intervalId = setInterval(() => this.checkAndNotify(), this.checkInterval);
    // Verificar também na primeira execução (não esperar 5 min)
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

    if (!currentPatient) return; // sem paciente selecionado

    const meds = (typeof patientMedications === 'function') ? patientMedications(currentPatient) : [];

    // Data de hoje (AAAA-MM-DD local), incluída em notifKey abaixo — sem
    // isto, a chave repetia-se todos os dias à mesma hora e
    // shownNotifications.has(notifKey) impedia PARA SEMPRE qualquer
    // lembrete futuro dessa dose a partir do 2º dia (bug real, corrigido
    // 2026-07-07: um dashboard deixado aberto, uso normal de um painel de
    // monitorização contínua, parava de notificar já no dia seguinte).
    const todayStr = `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`;
    // Limpa chaves de dias anteriores (evita o Set crescer sem limite ao
    // longo de muitos dias com o dashboard sempre aberto).
    for (const key of this.shownNotifications) {
      if (!key.endsWith(`_${todayStr}`)) this.shownNotifications.delete(key);
    }

    for (const med of meds) {
      if (!med.times || !Array.isArray(med.times)) continue;

      for (const time of med.times) {
        // Construir hora prevista de hoje
        const [h, m] = time.split(':').map(Number);
        const scheduledTime = new Date();
        scheduledTime.setHours(h, m, 0, 0);

        // Verificar se está dentro da janela de lembrança
        const timeDiff = Math.abs(now - scheduledTime);
        if (timeDiff <= this.reminderWindow) {
          const notifKey = `${currentPatient.id}_${med.id}_${time}_${todayStr}`;

          // Só notificar uma vez por dia
          if (!this.shownNotifications.has(notifKey)) {
            // Verificar se já foi marcada como tomada
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
      // Fallback: mostrar um cartão de alerta na UI
      this.showFallbackAlert(medication, time, patient);
      return;
    }

    const title = `💊 Hora de tomar medicação`;
    const options = {
      body: `${medication.name} ${medication.dose} às ${time}`,
      tag: `med_${patient.id}_${medication.id}_${time}`,
      badge: '💊',
      icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y="50%" x="50%" dominant-baseline="middle" text-anchor="middle" font-size="50">💊</text></svg>',
      requireInteraction: true, // mantém a notificação visível até o utilizador interagir
    };
    // NOTA (bug corrigido): `options.actions` + `notification.onaction` só
    // são entregues através do evento `notificationclick` de um Service
    // Worker (`ServiceWorkerRegistration.showNotification()`) — esta app
    // não tem nenhum Service Worker, por isso a instância simples
    // `Notification` nunca dispara `onaction`; os botões "Tomei agora/
    // Lembrar/Fechar" da notificação nativa nunca funcionavam. Em vez de
    // fingir que existem, mostra-se sempre também o cartão com ação real
    // (`showFallbackAlert`), que tem um botão funcional testado.
    const notification = new Notification(title, options);

    notification.onclick = () => {
      notification.close();
      // Focar a aba do dashboard e levar o cuidador à vista de Medicação,
      // onde pode de facto marcar a dose como tomada.
      if (window.focus) window.focus();
      const medNavItem = document.querySelector('.nav-item[data-view="medicacao"]:not([style*="display: none"])')
        || document.querySelector('.nav-item[data-view="medicacao"]');
      if (medNavItem && typeof activateNavItem === 'function') activateNavItem(medNavItem);
    };

    this.showFallbackAlert(medication, time, patient);
  }

  showFallbackAlert(medication, time, patient) {
    // Se notificações do browser não estiverem disponíveis (ou mesmo
    // quando estão, ver showNotification() — os botões da notificação
    // nativa nunca funcionam sem Service Worker), mostrar um cartão de
    // alerta no topo da página. Cada dose tem o seu próprio cartão (ID
    // único por paciente+medicamento+hora) dentro de um contentor
    // empilhável — evita que uma segunda dose com a mesma hora prevista
    // seja silenciosamente descartada por já existir um cartão de OUTRA
    // dose (bug corrigido: antes usava um único ID fixo partilhado).
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
    if (document.getElementById(bannerId)) return; // já mostrado, não duplicar

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

    const content = `
      <strong>💊 ${medication.name}</strong><br>
      ${medication.dose} às ${time}<br>
      <button onclick="if(typeof markDoseTaken==='function') { markDoseTaken('${patient.id}', '${medication.id}', '${time}'); } document.getElementById('${bannerId}').remove();" style="margin-top:8px; padding:6px 12px; background:var(--accent,#3FD6C0); border:none; border-radius:4px; cursor:pointer; color:var(--accent-ink,#04211D);">
        ✓ Tomei agora
      </button>
    `;

    div.innerHTML = content;
    stack.appendChild(div);

    // Auto-fechar após 30 segundos
    setTimeout(() => {
      if (div.parentElement) div.remove();
    }, 30 * 1000);
  }

  // Adicionar CSS animation se ainda não existir
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

/**
 * AdherenceAnalytics — Análise de tendências de adesão
 * 
 * Correlaciona adesão com atividade/vitais por data.
 * Recomendação: usar com dados reais da BD depois.
 */
class AdherenceAnalytics {
  constructor() {
    this.logKey = 'carewear_adherence_analytics';
  }

  /**
   * Recordar: {date: "2026-07-04", adherence_pct: 100, activity_level: "high", hr_avg: 72}
   */
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

  /**
   * Análise de 7 dias: adesão média, padrões, correlação com atividade
   */
  getWeekSummary(patientId) {
    const logs = this.loadLogs(patientId);
    const entries = Object.entries(logs)
      .sort(([dateA], [dateB]) => dateB.localeCompare(dateA))
      .slice(0, 7);

    if (entries.length === 0) {
      return { avg_adherence: 0, patterns: 'Sem dados', alert: 'Sem histórico ainda' };
    }

    const adherences = entries.map(([_, v]) => v.adherence_pct);
    const avgAdherence = Math.round(adherences.reduce((a, b) => a + b, 0) / adherences.length);

    let alert = '';
    if (avgAdherence < 50) {
      alert = '⚠️ Adesão muito baixa esta semana';
    } else if (avgAdherence < 80) {
      alert = '✓ Adesão moderada — aumentar atenção';
    } else {
      alert = '✅ Ótima adesão esta semana';
    }

    // Correlação simplificada: dias com alta atividade mas baixa adesão
    const lowAdherenceHighActivity = entries.filter(([_, v]) =>
      v.adherence_pct < 80 && v.activity_level === 'high'
    ).length;

    let patterns = 'Padrão normal';
    if (lowAdherenceHighActivity > 2) {
      patterns = 'Possível correlação: menor adesão em dias mais ativos';
    }

    return {
      avg_adherence: avgAdherence,
      patterns,
      alert,
      entries: entries.map(([date, v]) => ({ date, ...v }))
    };
  }

  /**
   * Recomendações baseadas em análise
   */
  getRecommendations(patientId, patient) {
    const summary = this.getWeekSummary(patientId);
    const recs = [];

    if (summary.avg_adherence < 50) {
      recs.push('🔴 Adesão crítica: contactar cuidador ou clínico');
      recs.push('💡 Sugestão: usar alarmes do relógio/telemóvel como lembretes extras');
    } else if (summary.avg_adherence < 80) {
      recs.push('🟡 Adesão moderada: melhorar atenção aos horários');
      recs.push('💡 Sugestão: marcar medicação imediatamente após as refeições');
    } else {
      recs.push('🟢 Excelente adesão: manter a rotina');
    }

    if (summary.patterns.includes('correlação')) {
      recs.push('💡 Sugestão: agendar medicação para períodos de menor atividade');
    }

    return recs;
  }
}

// Inicialização automática quando o dashboard carrega
document.addEventListener('DOMContentLoaded', () => {
  MedicationReminder.injectStyles();
  
  // Criar instância global
  window.medicationReminder = new MedicationReminder();
  window.adherenceAnalytics = new AdherenceAnalytics();
  
  // Iniciar verificação de lembretes
  window.medicationReminder.start();
  
  console.log('[MedicationReminder] Sistema de lembretes ativo');
});
