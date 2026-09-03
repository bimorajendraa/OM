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

## Tabel

```
predictive.model_run                                            -- Milestone 2
  run_id, model_version, feature_version, started_at, completed_at,
  status (RUNNING/SUCCEEDED/FAILED), row_count, error_message

predictive.item_prediction   -- APPEND-ONLY, tidak pernah di-UPDATE/DELETE
  prediction_id, run_id -> model_run,
  terminal_id, part_type, item_id,
  cycle_id, intervention_seq        -- NULL sampai intervensi tercatat untuk item itu
  p30, p60, p90, p120, risk_level, gate_flagged,
  scored_at, model_version, feature_version

predictive.item_cycle                                            -- Milestone 4
  cycle_id (PK, REUSE installation_cycle_id operasional apa adanya,
            format "<item_id>:<urutan>" - lihat predictive/cycles.py)
  item_id, cycle_no, started_at, ended_at,
  start_reason, end_reason (NULL selama masih aktif),
  is_active (partial unique index: satu item, satu cycle aktif), synced_at

predictive.intervention                                          -- Milestone 4, APPEND-ONLY
  intervention_id, item_id, cycle_id -> item_cycle, intervention_seq (UNIK per cycle),
  alert_id (nullable - FK ditambahkan Milestone 5, tabel alert belum ada),
  outcome, action_code, remark   -- bebas isi, TIDAK ADA kolom klasifikasi
                                  -- jenis (type) - satu baris = satu perbaikan
                                  -- terjadi, apa pun bentuknya (§25 update)
  external_system, external_work_order_id, external_inspection_id, external_event_id,
  performed_at, created_at
  UNIQUE(cycle_id, intervention_seq); partial UNIQUE(external_system, external_event_id)
  untuk idempotency (docs §23) - hanya kalau keduanya terisi.

predictive.alert, predictive.alert_event   -- menyusul Milestone 5, belum ada di schema.
```

### `item_cycle` - mencerminkan data operasional, bukan mencatat sendiri

`item_cycle` BUKAN sumber kebenaran siklus fisik - itu tetap data operasional
(`core.data_reader.get_cycles()`, dibangun dari event install/dismantle/
return/failure). `predictive/cycles.py::sync_item_cycles(item_id)` menyalin
riwayat cycle satu item dari sana ke `predictive.item_cycle`
(`ON CONFLICT ... DO UPDATE`, idempotent), murni supaya `intervention`/
`alert` (Milestone 5) punya foreign key yang stabil untuk ditempel - operasi
tulis TIDAK PERNAH mengubah kapan/kenapa sebuah cycle berakhir, itu selalu
ikut apa yang sudah tercatat di data operasional.

Disinkron **on-demand per item** (dipanggil dari `ensure_active_cycle()`
sebelum mencatat intervention), BUKAN disinkron massal untuk seluruh armada -
baris `item_cycle` hanya ada untuk item yang benar-benar disentuh sistem ini.

`RIGHT_CENSORED_AT_DATA_END` (artinya "belum ada event penutup sampai batas
data operasional terakhir", BUKAN penutupan fisik) tidak pernah ditulis
sebagai `end_reason` - kalau itu status cycle-nya, `item_cycle` tetap
`is_active=true`, `ended_at`/`end_reason` tetap NULL. `end_reason` yang
tersimpan selalu kejadian fisik nyata (FAILURE/RETURNED/DISMANTLED).

### `intervention` - minor repair tidak membuka cycle baru

`predictive/interventions.py::record_intervention(item_id, type, ...)`
selalu mencatat ke cycle AKTIF item saat ini (`ensure_active_cycle()`),
menaikkan `intervention_seq` DALAM cycle itu - TIDAK PERNAH membuka cycle
baru sendiri (itu murni konsekuensi data operasional lewat sync di atas).
Baris `item_cycle` yang jadi target intervention dikunci (`SELECT ... FOR
UPDATE`) selama penghitungan `intervention_seq` berikutnya, supaya dua
intervention untuk cycle yang sama tidak bisa saling tabrak nomor urut.

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
