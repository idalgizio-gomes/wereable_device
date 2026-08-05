"""Testes de `bridge/storage_advanced.py` — versionamento e rollback do
modelo ML (`MlModelVersion`/register_model_version/list_model_versions/
activate_model_version/get_active_model_version, 2026-08-05).

Corre inteiramente contra SQLite em memória (ver conftest.py), com schema
próprio recriado antes/depois de cada teste (mesmo padrão de
`test_storage_advanced.py::_fresh_schema`) — isolamento de
`MlModelVersion` entre testes, independente do que outros ficheiros de
teste tenham deixado na BD partilhada (o engine/SessionLocal do módulo é
um singleton para toda a sessão de pytest).
"""
import pytest

import storage_advanced as sa


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Recria o schema do zero antes/depois de cada teste (isolamento) —
    mesmo padrão de test_storage_advanced.py."""
    sa.Base.metadata.drop_all(bind=sa.engine)
    sa.Base.metadata.create_all(bind=sa.engine)
    yield
    sa.Base.metadata.drop_all(bind=sa.engine)


@pytest.fixture
def db():
    session = sa.get_db_session()
    try:
        yield session
    finally:
        session.close()


class TestRegisterModelVersion:
    def test_register_creates_row_not_active_by_default(self, db):
        result = sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/activity_classifier_rf.joblib",
            labels_path="models/activity_classifier_rf_labels.json",
        )
        assert result["model_name"] == "activity_classifier_rf"
        assert result["version"] == "1"
        assert result["is_active"] is False
        assert result["metrics"] is None

    def test_register_with_activate_true_marks_active(self, db):
        result = sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/x.joblib", labels_path="models/x_labels.json",
            activate=True,
        )
        assert result["is_active"] is True

    def test_register_serializes_metrics_to_json_and_back(self, db):
        result = sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/x.joblib", labels_path="models/x_labels.json",
            metrics={"accuracy": 0.87, "f1": 0.85},
        )
        assert result["metrics"] == {"accuracy": 0.87, "f1": 0.85}

    def test_duplicate_model_name_and_version_raises_value_error_not_integrity_error(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/x.joblib", labels_path="models/x_labels.json",
        )
        with pytest.raises(ValueError):
            sa.register_model_version(
                db, "activity_classifier_rf", version="1",
                file_path="models/y.joblib", labels_path="models/y_labels.json",
            )
        # A sessão continua utilizável depois do ValueError (rollback feito
        # internamente, não deixa a sessão "suja" para o resto do teste).
        assert len(sa.list_model_versions(db, "activity_classifier_rf")) == 1

    def test_same_version_different_model_name_does_not_collide(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/a.joblib", labels_path="models/a_labels.json",
        )
        # Mesmo "version", modelo diferente — UniqueConstraint é composta
        # (model_name, version), não deve rebentar.
        result = sa.register_model_version(
            db, "outro_modelo", version="1",
            file_path="models/b.joblib", labels_path="models/b_labels.json",
        )
        assert result["model_name"] == "outro_modelo"


class TestListModelVersions:
    def test_list_returns_most_recent_first(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/v1.joblib", labels_path="models/v1_labels.json",
        )
        sa.register_model_version(
            db, "activity_classifier_rf", version="2",
            file_path="models/v2.joblib", labels_path="models/v2_labels.json",
        )
        versions = sa.list_model_versions(db, "activity_classifier_rf")
        assert [v["version"] for v in versions] == ["2", "1"]

    def test_list_only_returns_versions_of_requested_model(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/a.joblib", labels_path="models/a_labels.json",
        )
        sa.register_model_version(
            db, "outro_modelo", version="1",
            file_path="models/b.joblib", labels_path="models/b_labels.json",
        )
        versions = sa.list_model_versions(db, "activity_classifier_rf")
        assert len(versions) == 1
        assert versions[0]["model_name"] == "activity_classifier_rf"

    def test_list_empty_when_nothing_registered(self, db):
        assert sa.list_model_versions(db, "activity_classifier_rf") == []


class TestActivateModelVersion:
    def test_activating_second_version_deactivates_first(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/v1.joblib", labels_path="models/v1_labels.json",
            activate=True,
        )
        sa.register_model_version(
            db, "activity_classifier_rf", version="2",
            file_path="models/v2.joblib", labels_path="models/v2_labels.json",
        )
        activated = sa.activate_model_version(db, "activity_classifier_rf", "2")
        assert activated["version"] == "2"
        assert activated["is_active"] is True

        versions = {v["version"]: v for v in sa.list_model_versions(db, "activity_classifier_rf")}
        assert versions["1"]["is_active"] is False
        assert versions["2"]["is_active"] is True

    def test_activating_unknown_version_raises_value_error(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/v1.joblib", labels_path="models/v1_labels.json",
        )
        with pytest.raises(ValueError):
            sa.activate_model_version(db, "activity_classifier_rf", "999")

    def test_activating_version_of_different_model_does_not_affect_others(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/a.joblib", labels_path="models/a_labels.json",
            activate=True,
        )
        sa.register_model_version(
            db, "outro_modelo", version="1",
            file_path="models/b.joblib", labels_path="models/b_labels.json",
            activate=True,
        )
        # As duas versões ativas pertencem a modelos diferentes — não há
        # conflito (a UniqueConstraint/lógica de desativação é por
        # model_name, não global).
        assert sa.get_active_model_version(db, "activity_classifier_rf")["version"] == "1"
        assert sa.get_active_model_version(db, "outro_modelo")["version"] == "1"


class TestGetActiveModelVersion:
    def test_returns_none_when_nothing_registered(self, db):
        assert sa.get_active_model_version(db, "activity_classifier_rf") is None

    def test_returns_none_when_versions_exist_but_none_active(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/v1.joblib", labels_path="models/v1_labels.json",
        )
        assert sa.get_active_model_version(db, "activity_classifier_rf") is None

    def test_returns_the_active_version(self, db):
        sa.register_model_version(
            db, "activity_classifier_rf", version="1",
            file_path="models/v1.joblib", labels_path="models/v1_labels.json",
        )
        sa.register_model_version(
            db, "activity_classifier_rf", version="2",
            file_path="models/v2.joblib", labels_path="models/v2_labels.json",
            activate=True,
        )
        active = sa.get_active_model_version(db, "activity_classifier_rf")
        assert active["version"] == "2"
        assert active["file_path"] == "models/v2.joblib"
