-- ============================================================
-- CareWear — Schema de Base de Dados SQL Completo
-- ============================================================
-- Motor: SQLite (desenvolvimento local) / PostgreSQL (produção)
-- Versionado via Alembic. Este ficheiro é de referência; aplicar
-- migrações via `alembic upgrade head`.

-- Tabelas Base: Utilizadores, Pacientes, Dispositivos

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,  -- GUID para referência externa
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,  -- bcrypt, nunca em plaintext
    role TEXT NOT NULL CHECK (role IN ('family', 'clinician', 'admin')),
    name TEXT NOT NULL,
    phone TEXT,
    institution TEXT,  -- Hospital, clínica, etc. se role='clinician'
    professional_id TEXT,  -- Cédula profissional (clinician)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP  -- Soft delete para auditoria
);

CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    date_of_birth DATE NOT NULL,
    nif TEXT UNIQUE,  -- Número de Identificação Fiscal (sensível, aprovação obrigatória)
    address TEXT,  -- (sensível)
    phone TEXT,
    emergency_contact_name TEXT,
    emergency_contact_phone TEXT,
    emergency_contact_relation TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    patient_id INTEGER NOT NULL,
    mac_address TEXT UNIQUE NOT NULL,  -- MAC BLE da pulseira
    firmware_version TEXT,
    hardware_variant TEXT,  -- "nrf52840-sense-plus", etc.
    battery_percent INTEGER,
    last_sync TIMESTAMP,
    storage_used_bytes INTEGER,
    storage_total_bytes INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients (id)
);

-- Tabela: Consentimento (GDPR/HIPAA)

CREATE TABLE IF NOT EXISTS consent_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    scope TEXT NOT NULL,  -- 'sensor_data', 'analytics', 'export', 'research'
    granted BOOLEAN NOT NULL,
    version TEXT NOT NULL,  -- Versão do documento de consentimento
    signed_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP,  -- NULL = sem expiração
    notes TEXT,  -- Razão de rejeição, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients (id),
    FOREIGN KEY (user_id) REFERENCES users (id),
    UNIQUE (patient_id, scope, version)  -- Uma versão por scope
);

-- Dados de Sensores: Registos em Tempo Real

CREATE TABLE IF NOT EXISTS sensor_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    timestamp_utc INTEGER NOT NULL,  -- Unix timestamp (segundos)
    accel_x REAL, accel_y REAL, accel_z REAL,  -- m/s²
    gyro_x REAL, gyro_y REAL, gyro_z REAL,    -- rad/s
    steps_count INTEGER,
    freefall_detected BOOLEAN,
    inactivity_detected BOOLEAN,
    heart_rate INTEGER,  -- BPM, NULL se não disponível
    spo2_percent INTEGER,  -- %, NULL se não disponível
    pacing_index INTEGER,  -- 0-100
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices (id)
);

-- Índices para queries rápidas (sensor_records é a tabela maior)
CREATE INDEX IF NOT EXISTS idx_sensor_records_device_timestamp
    ON sensor_records (device_id, timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_sensor_records_received_at
    ON sensor_records (received_at DESC);

-- Agregados de Rotina: Janelas Diárias por Atividade

CREATE TABLE IF NOT EXISTS activity_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    activity_date DATE NOT NULL,  -- Data em UTC
    activity_category TEXT NOT NULL,  -- 'sleep', 'rest', 'activity', 'eating', 'hygiene'
    start_time INTEGER,  -- Minutos desde início do dia
    end_time INTEGER,
    duration_minutes INTEGER,
    confidence REAL,  -- 0.0-1.0 (confiança do classificador)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices (id)
);

CREATE INDEX IF NOT EXISTS idx_activity_windows_device_date
    ON activity_windows (device_id, activity_date DESC);

-- Medicação: Prescrições e Aderência

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    patient_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    dosage TEXT NOT NULL,  -- "500mg", "1 comprimido", etc.
    frequency TEXT NOT NULL,  -- "twice_daily", "once_at_night", etc.
    start_date DATE NOT NULL,
    end_date DATE,  -- NULL = contínua
    prescribed_by_user_id INTEGER,  -- Clínico que prescreveu
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients (id),
    FOREIGN KEY (prescribed_by_user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS medication_adherence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    medication_id INTEGER NOT NULL,
    scheduled_datetime TIMESTAMP NOT NULL,
    taken BOOLEAN,
    taken_at TIMESTAMP,  -- Quando foi de facto tomada
    method TEXT,  -- 'manual_entry', 'wearable_detection', 'ai_inference'
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (medication_id) REFERENCES medications (id)
);

CREATE INDEX IF NOT EXISTS idx_medication_adherence_medication_scheduled
    ON medication_adherence (medication_id, scheduled_datetime DESC);

-- Alertas e Anomalias

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    device_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,  -- 'abnormal_vitals', 'fall_detected', 'inactivity', 'medication_missed'
    severity TEXT NOT NULL,  -- 'info', 'warning', 'serious', 'critical'
    title TEXT NOT NULL,
    description TEXT,
    raw_data JSONB,  -- Dados brutos do sensor/evento
    read_by_user_id INTEGER,
    read_at TIMESTAMP,
    silenced BOOLEAN DEFAULT FALSE,
    silenced_until TIMESTAMP,
    escalated_to_severity TEXT,  -- Se foi escalado automaticamente
    escalated_at TIMESTAMP,
    resolved_by_user_id INTEGER,
    resolved_at TIMESTAMP,
    resolution_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices (id),
    FOREIGN KEY (read_by_user_id) REFERENCES users (id),
    FOREIGN KEY (resolved_by_user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_device_created
    ON alerts (device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity_read
    ON alerts (severity, read_at);

CREATE TABLE IF NOT EXISTS emergency_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    device_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,  -- 'sos_manual', 'fall_inactivity'
    sequence_number INTEGER,  -- Do firmware, para dedup
    timestamp_utc INTEGER NOT NULL,
    responded_at TIMESTAMP,
    response_user_id INTEGER,
    response_action TEXT,  -- 'confirmed', 'false_positive', 'no_response'
    confirmation_code TEXT,  -- OTP para cancelamento
    confirmation_attempts INTEGER DEFAULT 0,
    confirmation_blocked_until TIMESTAMP,  -- TTL 5 min após 3 tentativas
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices (id),
    FOREIGN KEY (response_user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS idx_emergency_alerts_device_timestamp
    ON emergency_alerts (device_id, timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_emergency_alerts_responded
    ON emergency_alerts (responded_at) WHERE responded_at IS NULL;

-- Anomalias de Rotina (LSTM Autoencoder — futura)

CREATE TABLE IF NOT EXISTS anomaly_detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    anomaly_type TEXT NOT NULL,  -- 'routine_shift', 'activity_disruption', 'vital_outlier'
    score REAL,  -- 0.0-1.0 (anomaly likelihood)
    start_datetime TIMESTAMP NOT NULL,
    end_datetime TIMESTAMP,
    description TEXT,
    potential_cause TEXT,  -- Hipótese inicial
    severity TEXT,  -- 'minor', 'moderate', 'severe'
    investigated BOOLEAN DEFAULT FALSE,
    investigation_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices (id)
);

CREATE INDEX IF NOT EXISTS idx_anomaly_detections_device_datetime
    ON anomaly_detections (device_id, start_datetime DESC);

-- Auditoria: Todas as Ações Sensíveis

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,  -- NULL se ação do sistema
    action TEXT NOT NULL,  -- 'view_sensitive_data', 'export_data', 'modify_medication'
    resource_type TEXT,  -- 'patient', 'medication', 'alert'
    resource_id INTEGER,
    details JSONB,  -- Contexto adicional (valores antigos/novos, etc.)
    ip_address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user_created
    ON audit_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_resource
    ON audit_log (resource_type, resource_id);

-- Configuração de Limiares Personalizados

CREATE TABLE IF NOT EXISTS personalized_thresholds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL UNIQUE,
    heart_rate_min INTEGER,
    heart_rate_max INTEGER,
    spo2_min INTEGER,  -- %
    inactivity_threshold_seconds INTEGER,
    sleep_target_minutes INTEGER,
    activity_target_minutes INTEGER,
    steps_target_daily INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients (id)
);

-- Estatísticas em Cache (para Dashboards Rápidos)

CREATE TABLE IF NOT EXISTS daily_statistics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    stat_date DATE NOT NULL,
    total_steps INTEGER,
    avg_heart_rate INTEGER,
    min_heart_rate INTEGER,
    max_heart_rate INTEGER,
    avg_spo2 INTEGER,
    sleep_duration_minutes INTEGER,
    activity_duration_minutes INTEGER,
    rest_duration_minutes INTEGER,
    eating_duration_minutes INTEGER,
    hygiene_duration_minutes INTEGER,
    alerts_count INTEGER,
    anomalies_count INTEGER,
    medication_adherence_percent REAL,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices (id),
    UNIQUE (device_id, stat_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_statistics_device_date
    ON daily_statistics (device_id, stat_date DESC);

-- Política de Retenção de Dados

CREATE TABLE IF NOT EXISTS data_retention_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    retention_days INTEGER NOT NULL,
    soft_delete BOOLEAN DEFAULT TRUE,  -- Se TRUE, marca deleted_at; se FALSE, apaga mesmo
    enabled BOOLEAN DEFAULT TRUE,
    last_cleanup TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (table_name)
);

-- Definições Padrão de Retenção
INSERT INTO data_retention_policies (table_name, retention_days, soft_delete)
VALUES
    ('sensor_records', 365, FALSE),  -- 1 ano de dados brutos, depois apaga
    ('activity_windows', 1825, FALSE),  -- 5 anos
    ('alerts', 2555, TRUE),  -- 7 anos, soft delete
    ('emergency_alerts', 3650, TRUE),  -- 10 anos, soft delete
    ('anomaly_detections', 1825, FALSE),  -- 5 anos
    ('medication_adherence', 1095, FALSE)  -- 3 anos
ON CONFLICT DO NOTHING;
