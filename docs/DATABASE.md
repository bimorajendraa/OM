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
  terminal_id           -- serial code FISIK terminal (frame["terminal_label"]),
                         -- BUKAN ID internal terminal_inventory_item_id yang
                         -- dipakai live/filtering di serving/batch.py (§28)
  part_type, item_id,
  cycle_id, intervention_seq        -- NULL sampai intervensi tercatat untuk item itu
  p30, p60, p90, p120, risk_level, gate_flagged,
  scored_at, model_version, feature_version

predictive.item_cycle                                            -- Milestone 4
  cycle_id (PK, REUSE installation_cycle_id operasional apa adanya,
            format "<item_id>:<urutan>" - lihat predictive/cycles.py)
  item_id, cycle_no, started_at, ended_at,
  start_reason, end_reason (NULL selama masih aktif),
  is_active (partial unique index: satu item, satu cycle aktif), synced_at

predictive.intervention                       -- Milestone 4, APPEND-ONLY, DIPANGKAS §28
  intervention_id, item_id, cycle_id -> item_cycle, intervention_seq (UNIK per cycle),
  alert_id (nullable), performed_at, created_at
  UNIQUE(cycle_id, intervention_seq)
  -- Sengaja TIDAK ADA outcome/action_code/remark/external_* (dibuang §28) -
  -- body POST /api/v1/interventions cuma host_serial_code, tidak ada apa pun
  -- lain untuk diisi ke kolom itu. Tidak ada lagi idempotency eksternal.

predictive.alert                                                  -- Milestone 5
  alert_id, terminal_id (serial code fisik terminal, sama seperti item_prediction,
  lihat §28), part_type, item_id, cycle_id -> item_cycle,
  intervention_seq (seq yang AKAN dipakai intervention yang menyelesaikan alert ini),
  prediction_id -> item_prediction (nullable),
  opened_at, opened_score, status (OPEN/ACKNOWLEDGED/RESOLVED/SUPPRESSED),
  acknowledged_at, resolved_at, resolution_reason, suppression_until,
  created_at, updated_at
  partial UNIQUE(item_id, cycle_id, intervention_seq) WHERE status='OPEN'
  - satu episode tidak boleh punya lebih dari satu alert OPEN.
```

Sengaja TIDAK ADA tabel `alert_event` (event-sourcing audit log terpisah) -
SUPERSEDED §28, dibuang karena tidak ada kode yang membacanya (murni
ditulis) dan informasinya sudah lengkap di kolom `alert.opened_at`/
`resolved_at`/`resolution_reason`.

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

`predictive/interventions.py::record_intervention(item_id, ...)` selalu
mencatat ke cycle AKTIF item saat ini (`ensure_active_cycle()`), menaikkan
`intervention_seq` DALAM cycle itu - TIDAK PERNAH membuka cycle baru sendiri
(itu murni konsekuensi data operasional lewat sync di atas). Baris
`item_cycle` yang jadi target intervention dikunci (`SELECT ... FOR UPDATE`)
selama penghitungan `intervention_seq` berikutnya, supaya dua intervention
untuk cycle yang sama tidak bisa saling tabrak nomor urut.

### Alert - dipisah tegas: model memutuskan skor, alert engine memutuskan
### perlu-tidaknya jadi alert, teknisi mencatat tindakan

`predictive/alerts.py` - LIMA fungsi, tanggung jawab terpisah:

- `evaluate_and_open(frame, scored_at)` - **satu-satunya** yang
  MEMBUKA alert baru. Dipanggil SEKALI per siklus scheduled scoring
  (`predictive/scoring.py::run_and_persist()`, lihat `python -m partrisk.cli
  score-and-persist`), TIDAK PERNAH dari jalur baca live. Langkah pertama:
  `auto_resolve_closed_cycles()` (di bawah) menyapu alert OPEN yang basi
  sebelum membuka alert baru. Lalu per PART yang `gate_flagged`: sinkron
  cycle -> lewati kalau sudah ada alert OPEN untuk episode yang sama
  (`item_id`+`cycle_id`+`intervention_seq` berikutnya) -> lewati kalau masih
  dalam masa suppression (KECUALI emergency override) -> INSERT alert.
- `open_alerts_by_item()` - MURNI BACA, dipakai `auto_resolve_closed_cycles()`
  dan `resolve_by_item()` (§28/§29) untuk mencari alert OPEN milik satu/
  beberapa item. Tidak pernah menulis apa pun. (Sebelum §29: juga dipakai
  `serving/batch.py` untuk menandai status alert di jalur live API/dashboard -
  pemakaian itu sudah dibuang bersama endpoint GET-nya.)
- `auto_resolve_closed_cycles(item_ids=None)` (docs §27) - jalur resolve
  **OTOMATIS**, untuk alert yang episode-nya sudah selesai lewat kejadian
  operasional biasa (worktype corrective/preventive berujung dismantle,
  dsb - tercatat sistem lewat `journal`, ditutup jadi `cycle_end_reason`
  oleh §20). Untuk tiap alert OPEN, sinkron `item_cycle` dari data
  operasional lalu cek `is_active`; kalau cycle sudah tertutup, UPDATE
  langsung ke RESOLVED dengan `resolution_reason=f"OPERATIONAL_CYCLE_
  CLOSED:{end_reason}"` - TANPA intervention row, TANPA panggilan API.
  Tidak menyentuh alert yang cycle-nya masih aktif.
- `resolve_by_item(item_id, performed_at)` (docs §28) - **titik masuk**
  endpoint `POST /api/v1/interventions` (body cuma `host_serial_code`,
  diresolve ke `item_id` lewat `core.data_reader.
  resolve_item_by_host_serial_code()`). Kalau item ini SEDANG punya alert
  OPEN, delegasi ke `resolve_with_intervention()`; kalau tidak, tetap catat
  intervention tanpa alert (satu POST tetap berarti ada perbaikan, §25).
- `resolve_with_intervention(alert_id, performed_at)` - jalur resolve
  **MANUAL** yang sesungguhnya, untuk perbaikan kecil yang TIDAK PERNAH
  tercatat di data operasional (mis. mengencangkan baut - item tetap di
  cycle yang sama). SELALU lewat intervention tercatat (docs §19 master
  prompt: resolve BUKAN set probability=0 - `item_prediction` historis
  tidak pernah disentuh, hanya `alert.status` yang berubah). Transaksional
  penuh (docs §22). Kalau ternyata cycle alert sudah tertutup operasional
  saat fungsi ini dipanggil (skenario yang seharusnya sudah ditangkap
  `auto_resolve_closed_cycles()` di §26, tapi belum sempat berjalan),
  fungsi ini mencoba auto-resolve dulu lalu melempar `AlertNotOpen` (bukan
  `AlertCycleMismatch` mentah) supaya caller tahu alert sudah selesai,
  bukan error yang tidak jelas maknanya.

**Identitas alert** = `(item_id, cycle_id, intervention_seq)`, BUKAN cuma
`item_id` (docs §16). `intervention_seq` pada alert SAMA DENGAN seq yang
akan didapat intervention yang menyelesaikannya - invariant ini yang
membuat re-alert otomatis jadi ALERT_ID BARU (episode intervention_seq
yang lebih tinggi), bukan membuka ulang baris lama.

**Suppression & emergency override** (docs §24/§25): `ALERT_SUPPRESSION_DAYS`,
`ALERT_EMERGENCY_SCORE_JUMP`, `ALERT_EMERGENCY_SCORE_ABSOLUTE` di
`core/config.py` - nilai PLACEHOLDER awal (lihat WHY di sana), belum
divalidasi data resolve/re-alert nyata karena datanya belum ada sama
sekali sebelum Milestone 5 ini berjalan. Emergency override membandingkan
skor SEKARANG terhadap `opened_score` alert yang terakhir di-resolve untuk
item+cycle yang sama (bukan terhadap `suppression_until`).

## Menulis prediksi (scheduled scoring)

```bash
python -m partrisk.cli score-and-persist
```

Satu siklus: skor seluruh PART aktif (`serving.batch.score_active_parts
(force_refresh=True)`), simpan sebagai satu `model_run` baru + satu baris
`item_prediction` per PART. **Dipanggil scheduler eksternal secara berkala**
(cron/Task Scheduler) - satu-satunya jalur yang menulis riwayat prediksi.
Sengaja TERPISAH dari pemanggilan `score_active_parts()` lain (CLI
`predict`, `golden-batch`, test) - jalur-jalur itu tidak ikut menulis
riwayat prediksi setiap kali dipanggil, supaya tabel prediction history
tidak terisi baris uji coba. (Sebelum §29: API GET live juga memanggil
`score_active_parts()` tanpa `force_refresh` untuk melayani dashboard -
endpoint itu sudah dibuang, satu-satunya konsumen live sekarang tinggal
`POST /api/v1/interventions`, yang tidak memanggil `score_active_parts()`
sama sekali.)

## Kredensial dan akses

Satu set kredensial (`.env`) dipakai untuk kedua schema saat ini - dibedakan
lewat `search_path` di level koneksi (`core/data_reader.py` set
`default_transaction_read_only=on`; `predictive/db.py` set
`search_path=predictive,public`, tanpa read-only). Kalau akun database yang
dipakai proyek ini bukan superuser (beda dari environment dev saat ini),
role itu WAJIB dapat grant eksplisit `USAGE, CREATE` di schema `predictive`
saja - jangan pernah `GRANT ... ON SCHEMA public` atau grant tulis ke schema
operasional.
