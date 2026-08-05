"""Testes de `bridge/vital_alerts.py` — avaliação de FC/SpO2 contra a
baseline comportamental personalizada (2026-08-05). Função pura, sem BD
nem I/O, testável isoladamente."""
import vital_alerts as va


THRESHOLDS = {"heart_rate_min": 50, "heart_rate_max": 100, "spo2_min": 92}


class TestEvaluateHr:
    def test_within_range_is_not_flagged(self):
        assert va.evaluate_hr(75, THRESHOLDS) is None

    def test_at_exact_boundaries_is_not_flagged(self):
        assert va.evaluate_hr(50, THRESHOLDS) is None
        assert va.evaluate_hr(100, THRESHOLDS) is None

    def test_below_minimum_is_flagged_low(self):
        alert = va.evaluate_hr(40, THRESHOLDS)
        assert alert == {"vital": "hr", "level": "low", "value": 40, "limit": 50}

    def test_above_maximum_is_flagged_high(self):
        alert = va.evaluate_hr(130, THRESHOLDS)
        assert alert == {"vital": "hr", "level": "high", "value": 130, "limit": 100}

    def test_none_reading_is_not_flagged(self):
        assert va.evaluate_hr(None, THRESHOLDS) is None

    def test_missing_threshold_field_never_flags(self):
        assert va.evaluate_hr(500, {"heart_rate_min": None, "heart_rate_max": None}) is None


class TestEvaluateSpo2:
    def test_within_range_is_not_flagged(self):
        assert va.evaluate_spo2(96, THRESHOLDS) is None

    def test_below_minimum_is_flagged_low(self):
        alert = va.evaluate_spo2(85, THRESHOLDS)
        assert alert == {"vital": "spo2", "level": "low", "value": 85, "limit": 92}

    def test_high_spo2_is_never_flagged(self):
        # Ao contrário de evaluate_hr, SpO2 não tem ramo 'high' — um valor
        # alto nunca é clinicamente um problema.
        assert va.evaluate_spo2(100, THRESHOLDS) is None

    def test_none_reading_is_not_flagged(self):
        assert va.evaluate_spo2(None, THRESHOLDS) is None


class TestExplainVitalAlert:
    def test_low_hr_explanation_mentions_value_and_limit(self):
        text = va.explain_vital_alert({"vital": "hr", "level": "low", "value": 40, "limit": 50})
        assert "40" in text
        assert "50" in text

    def test_high_hr_explanation_mentions_value_and_limit(self):
        text = va.explain_vital_alert({"vital": "hr", "level": "high", "value": 130, "limit": 100})
        assert "130" in text
        assert "100" in text

    def test_low_spo2_explanation_mentions_spo2(self):
        text = va.explain_vital_alert({"vital": "spo2", "level": "low", "value": 85, "limit": 92})
        assert "SpO2" in text or "spo2" in text.lower()


class TestExplainVitalCleared:
    def test_mentions_the_vital_that_cleared(self):
        text = va.explain_vital_cleared("hr", THRESHOLDS)
        assert "cardíaca" in text.lower() or "hr" in text.lower()
