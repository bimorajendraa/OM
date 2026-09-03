"""Koneksi tulis ke schema `predictive` - TERPISAH dari `core.data_reader`
(yang tetap read-only, membaca data operasional).

Hanya kode di paket `partrisk.predictive` yang boleh menulis ke database -
lihat docs/DATABASE.md untuk batasan schema `predictive` vs operasional.
"""

from __future__ import annotations

import sys

import psycopg

from partrisk.core import config

MIGRATIONS_DIR = config.PACKAGE_DIR / "migrations" / "predictive"


def connect() -> psycopg.Connection:
    return psycopg.connect(
        **config.db_settings(),
        application_name="production_ml_predictive_writer",
        connect_timeout=10,
        options=f"-c search_path={config.PREDICTIVE_SCHEMA},public",
    )


def migrate() -> list[str]:
    applied = []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"Tidak ada file migrasi di {MIGRATIONS_DIR}")

    with connect() as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            applied.append(path.name)
    return applied


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "migrate":
        print("Pemakaian: python -m partrisk.predictive.db migrate")
        return 1
    applied = migrate()
    for name in applied:
        print(f"OK - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
