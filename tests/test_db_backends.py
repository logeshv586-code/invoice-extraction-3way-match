from extractor.config import Settings
from extractor.db import PostgresERPRepository, SQLiteERPRepository, build_erp_repository


def test_sqlite_is_default_assessment_backend(tmp_path):
    db = tmp_path / "erp.db"
    db.touch()
    repo = build_erp_repository(Settings(db_backend="sqlite", erp_db=db))
    assert isinstance(repo, SQLiteERPRepository)


def test_postgres_adapter_is_optional_and_lazy():
    repo = build_erp_repository(
        Settings(
            db_backend="postgres",
            postgres_dsn="postgresql://demo:demo@localhost:5432/demo",
        )
    )
    assert isinstance(repo, PostgresERPRepository)
