# Database

Dua schema, satu server Postgres yang sama (per keputusan 2026-09-03 -
sebelumnya direncanakan server terpisah, tapi database yang tersedia untuk
proyek ini sudah menampung salinan data operasional yang di-refresh
scheduler tim lain, jadi tidak perlu server Postgres kedua):

```
DB yang dikonfigurasi di .env (DB_HOST/DB_NAME/dst)
├── schema operasional (core, inventory, journal, log, master, ...)
│     - salinan data production, di-refresh scheduler EKSTERNAL (bukan
│       repo ini) - lihat catatan di bawah.
│     - HANYA DIBACA oleh partrisk. core/data_reader.py memaksa
│       default_transaction_read_only=on di level sesi.
│
└── schema predictive
      - satu-satunya tempat partrisk menulis.
      - dikelola migrations/predictive/*.sql + src/partrisk/predictive/db.py
```

## Kenapa satu server, bukan dua

Rencana awal (Milestone 2) adalah server Postgres terpisah khusus predictive
(lihat riwayat di `docs/DECISIONS.md`). Diubah setelah klarifikasi user:
scheduler tim lain sudah/akan pull data dari production ke database yang
sama yang dipakai proyek ini (`OMNEW` di `.env` lokal) - jadi database itu
sendiri BUKAN production langsung, dan cukup ditambah satu schema baru
(`predictive`) untuk output ML, tanpa infra database kedua. Kalau nanti
databasenya perlu dipisah lagi (skala, izin akses berbeda per tim), migrasi
schema `predictive` ke server lain hanya butuh `pg_dump --schema=predictive`
+ ubah `.env` - tidak ada perubahan kode.

## Menjalankan migrasi

```bash
python -m partrisk.predictive.db migrate
```

Idempotent (`CREATE ... IF NOT EXISTS`) - aman dijalankan ulang. File baru
ditambahkan sebagai `migrations/predictive/000N_*.sql` bernomor urut,
jangan mengedit file lama yang sudah pernah dijalankan di production.

## Tabel (Milestone 2 - baru `model_run` + `item_prediction`)

```
predictive.model_run
  run_id, model_version, feature_version, started_at, completed_at,
  status (RUNNING/SUCCEEDED/FAILED), row_count, error_message

predictive.item_prediction   -- APPEND-ONLY, tidak pernah di-UPDATE/DELETE
  prediction_id, run_id -> model_run,
  terminal_id, part_type, item_id,
  cycle_id, intervention_seq        -- NULL sampai Milestone 4 (lifecycle)
  p30, p60, p90, p120, risk_level, gate_flagged,
  scored_at, model_version, feature_version
```

Tabel `item_cycle`, `intervention`, `alert`, `alert_event` menyusul di
migrasi Milestone 4/5 - belum ada di schema saat ini.

## Menulis prediksi (scheduled scoring)

```bash
python -m partrisk.cli score-and-persist
```

Satu siklus: skor seluruh PART aktif (`serving.batch.score_active_parts
(force_refresh=True)`), simpan sebagai satu `model_run` baru + satu baris
`item_prediction` per PART. **Dipanggil scheduler eksternal secara berkala**
(cron/Task Scheduler) - bukan otomatis di setiap request API/dashboard
(lihat `docs/DECISIONS.md` - dashboard tidak boleh memicu scoring, hanya
membaca cache). Sengaja TERPISAH dari cache batch in-memory yang dipakai API
(`serving/batch.py`) - batch ad-hoc (API on-demand, CLI `predict`, test,
`golden-batch`) tidak ikut menulis riwayat prediksi setiap kali dipanggil,
supaya tabel prediction history tidak terisi baris uji coba.

## Kredensial dan akses

Satu set kredensial (`.env`) dipakai untuk kedua schema saat ini - dibedakan
lewat `search_path` di level koneksi (`core/data_reader.py` set
`default_transaction_read_only=on`; `predictive/db.py` set
`search_path=predictive,public`, tanpa read-only). Kalau akun database yang
dipakai proyek ini bukan superuser (beda dari environment dev saat ini),
role itu WAJIB dapat grant eksplisit `USAGE, CREATE` di schema `predictive`
saja - jangan pernah `GRANT ... ON SCHEMA public` atau grant tulis ke schema
operasional.
