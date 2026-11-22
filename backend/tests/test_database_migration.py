from sqlalchemy import create_engine, select, func, inspect
from pathlib import Path
import os
import subprocess
import sys
import pytest

from app.database import Base
from app import models as m
from app.migrate_database import migrate_database


def test_sqlite_database_migration_preserves_records_and_ids(tmp_path):
    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"
    source_url = f"sqlite:///{source_path.as_posix()}"
    target_url = f"sqlite:///{target_path.as_posix()}"

    source = create_engine(source_url)
    Base.metadata.create_all(source)
    with source.begin() as connection:
        connection.execute(m.School.__table__.insert(), {
            "id": 7, "name": "Migration Test School", "academic_year": "2026-2027",
            "working_days": "Monday,Tuesday", "periods_per_day": 4,
        })
        connection.execute(m.ClassSection.__table__.insert(), {
            "id": 19, "school_id": 7, "class_name": "Class 1", "section": "A", "strength": 25,
        })
    source.dispose()

    copied = migrate_database(source_url, target_url)
    target = create_engine(target_url)
    with target.connect() as connection:
        school = connection.execute(select(m.School.__table__).where(m.School.id == 7)).mappings().one()
        cls = connection.execute(select(m.ClassSection.__table__).where(m.ClassSection.id == 19)).mappings().one()
        assert school["name"] == "Migration Test School"
        assert cls["school_id"] == 7
        assert connection.execute(select(func.count()).select_from(m.School.__table__)).scalar_one() == 1
    target.dispose()
    assert copied["schools"] == 1
    assert copied["class_sections"] == 1


def test_sqlite_database_migration_refuses_nonempty_destination(tmp_path):
    source_url = f"sqlite:///{(tmp_path / 'source.db').as_posix()}"
    target_url = f"sqlite:///{(tmp_path / 'target.db').as_posix()}"
    source = create_engine(source_url)
    target = create_engine(target_url)
    Base.metadata.create_all(source)
    Base.metadata.create_all(target)
    with source.begin() as connection:
        connection.execute(m.School.__table__.insert(), {
            "id": 1, "name": "Source", "academic_year": "2026-2027",
            "working_days": "Monday", "periods_per_day": 1,
        })
    with target.begin() as connection:
        connection.execute(m.School.__table__.insert(), {
            "id": 2, "name": "Already There", "academic_year": "2026-2027",
            "working_days": "Monday", "periods_per_day": 1,
        })
    source.dispose(); target.dispose()

    with pytest.raises(ValueError, match="Target database is not empty"):
        migrate_database(source_url, target_url)


def test_alembic_baseline_builds_empty_schema(tmp_path):
    target_db = tmp_path / "alembic.db"
    env = os.environ.copy()
    env["APP_ENV"] = "testing"
    env["DATABASE_URL"] = f"sqlite:///{target_db.as_posix()}"
    backend_dir = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir, env=env, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    engine = create_engine(env["DATABASE_URL"])
    table_names = set(inspect(engine).get_table_names())
    assert {
        "schools", "class_sections", "teachers", "subjects", "rooms",
        "teacher_subjects", "availability", "timetable_entries",
        "period_timings", "weekly_requirements", "teacher_preferences",
        "subject_scheduling_profiles", "alembic_version",
    }.issubset(table_names)
    engine.dispose()
