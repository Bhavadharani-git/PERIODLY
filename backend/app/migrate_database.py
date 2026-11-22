"""Safely copy an existing PERIODLY SQLite database into an empty PostgreSQL DB.

Run from the backend directory with the target DATABASE_URL configured:
    python -m app.migrate_database --source-url sqlite:////path/to/periodly_migration_source.db

The script refuses to overwrite any target containing application rows.
"""
from __future__ import annotations

import argparse
import os
from collections import OrderedDict
from typing import Any

# Load model metadata without enforcing the API server's production startup checks.
os.environ["APP_ENV"] = "migration"

from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import Engine

from .database import Base
from . import models as _models  # noqa: F401 - registers all mapped tables


def _engine(url: str) -> Engine:
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=args, pool_pre_ping=True)


def migrate_database(source_url: str, target_url: str) -> dict[str, int]:
    if source_url == target_url:
        raise ValueError("Source and target database URLs must be different.")

    source_engine = _engine(source_url)
    target_engine = _engine(target_url)
    summary: OrderedDict[str, int] = OrderedDict()
    try:
        source_inspector = inspect(source_engine)
        if not source_inspector.has_table("schools"):
            raise ValueError("Source database does not contain the PERIODLY schools table.")
        if source_engine.dialect.name == "sqlite":
            with source_engine.connect() as source:
                integrity = source.execute(text("PRAGMA integrity_check")).scalar_one()
                if integrity != "ok":
                    raise ValueError(f"Source SQLite integrity check failed: {integrity}")

        Base.metadata.create_all(bind=target_engine)
        target_inspector = inspect(target_engine)
        nonempty_tables = []
        with target_engine.connect() as target:
            for table in Base.metadata.sorted_tables:
                if target_inspector.has_table(table.name):
                    count = target.execute(select(text("count(*)")).select_from(table)).scalar_one()
                    if count:
                        nonempty_tables.append(f"{table.name} ({count} rows)")
        if nonempty_tables:
            raise ValueError(
                "Target database is not empty. Migration aborted without copying data: "
                + ", ".join(nonempty_tables)
            )

        source_metadata = MetaData()
        source_metadata.reflect(bind=source_engine)
        with source_engine.connect() as source, target_engine.begin() as target:
            for target_table in Base.metadata.sorted_tables:
                source_table = source_metadata.tables.get(target_table.name)
                if source_table is None:
                    summary[target_table.name] = 0
                    continue

                source_column_names = set(source_table.c.keys())
                target_column_names = set(target_table.c.keys())
                common_columns = [column.name for column in target_table.columns if column.name in source_column_names]
                if "id" not in common_columns:
                    raise ValueError(f"Source table {target_table.name} has no id column; cannot preserve IDs safely.")

                # Current model defaults/nullable flags fill columns added after an older SQLite backup.
                rows = source.execute(select(source_table)).mappings()
                copied = 0
                batch: list[dict[str, Any]] = []
                for source_row in rows:
                    record: dict[str, Any] = {name: source_row[name] for name in common_columns}
                    for column in target_table.columns:
                        if column.name in record:
                            continue
                        if column.default is not None:
                            default = column.default.arg
                            record[column.name] = default() if callable(default) else default
                        elif column.nullable:
                            record[column.name] = None
                        elif column.server_default is None:
                            raise ValueError(
                                f"Source table {target_table.name} is missing required column {column.name}; "
                                "provide an updated backup or an explicit data migration."
                            )
                    batch.append(record)
                    if len(batch) >= 500:
                        target.execute(target_table.insert(), batch)
                        copied += len(batch)
                        batch.clear()
                if batch:
                    target.execute(target_table.insert(), batch)
                    copied += len(batch)
                summary[target_table.name] = copied

            # Keep PostgreSQL auto-increment sequences aligned with imported IDs.
            if target_engine.dialect.name == "postgresql":
                for table in Base.metadata.sorted_tables:
                    if "id" not in table.c:
                        continue
                    sequence = target.execute(
                        text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                        {"table_name": table.name},
                    ).scalar_one_or_none()
                    if not sequence:
                        continue
                    max_id = target.execute(select(text("MAX(id)")).select_from(table)).scalar_one_or_none()
                    if max_id is None:
                        target.execute(
                            text("SELECT setval(CAST(:sequence AS regclass), 1, false)"),
                            {"sequence": sequence},
                        )
                    else:
                        target.execute(
                            text("SELECT setval(CAST(:sequence AS regclass), :max_id, true)"),
                            {"sequence": sequence, "max_id": int(max_id)},
                        )
        return dict(summary)
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy PERIODLY data from SQLite into an empty target database.")
    parser.add_argument("--source-url", required=True, help="SQLite source URL, e.g. sqlite:////backup/periodly.db")
    parser.add_argument(
        "--target-url",
        default=os.getenv("DATABASE_URL", ""),
        help="Target SQLAlchemy URL; defaults to DATABASE_URL in the environment.",
    )
    args = parser.parse_args()
    if not args.target_url:
        parser.error("Set DATABASE_URL or pass --target-url for the PostgreSQL destination.")
    result = migrate_database(args.source_url, args.target_url)
    print("PERIODLY database migration completed.")
    for table_name, count in result.items():
        print(f"{table_name}: {count} row(s)")
    print(f"Total rows copied: {sum(result.values())}")


if __name__ == "__main__":
    main()
