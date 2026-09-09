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
  run_id, model_version, started_at, completed_at,
  status (RUNNING/SUCCEEDED/FAILED), row_count, error_message

predictive.item_prediction   -- APPEND-ONLY, tidak pernah di-UPDATE/DELETE
  prediction_id, run_id -> model_run,
  terminal_serial_code  -- serial code FISIK terminal (frame["terminal_label"]),
                         -- BUKAN ID internal terminal_inventory_item_id yang
                         -- dipakai live/filtering di serving/batch.py (§30)
  item_serial_code NOT NULL   -- serial code FISIK part (§35, SEBELUMNYA
                               -- host_serial_code - rename, arti TIDAK
                               -- berubah) - satu-satunya identitas PART di
                               -- tabel ini sejak §40; item_id DIBUANG (§40) -
                               -- append-only + join ke prediction_id cuma
                               -- sesaat setelah scoring, item_serial_code
                               -- sudah cukup. NOT NULL konsisten DB+Python+
                               -- API sejak §41.
  p30, p60, p90, p120, risk_level, gate_flagged,
  scored_at, model_version
  PENTING: baris di sini bisa berasal dari model_run yang UJUNGNYA FAILED -
  record_predictions() commit SEBELUM complete_run() selesai (§37). View
  predictive.valid_item_prediction yang dulu jadi
  penyaring ini sudah DIHAPUS (konsumen eksternal ternyata baca tabel ini
  langsung, bukan view) - konsumen yang butuh "prediksi yang sah" HARUS
  JOIN model_run WHERE status='SUCCEEDED' sendiri di query mereka.

predictive.item_prediction   -- (kolom lain lihat definisi di atas)
  ...
  alert_flagged BOOLEAN NOT NULL DEFAULT false   -- Milestone 5, ditambah §53
  -- Dihitung SEKALI saat INSERT (predictive/scoring.py::record_predictions()
  -- -> alerts.py::compute_alert_flagged()), TIDAK PERNAH diubah lagi -
  -- tetap konsisten dengan sifat append-only tabel ini. BEDA dari
  -- gate_flagged (murni sinyal model, dipakai build_work_queue()/evaluasi
  -- capacity-precision, TIDAK boleh berubah makna): alert_flagged =
  -- gate_flagged DAN belum ada alert terbuka yang belum di-inspect untuk
  -- item_serial_code ini DAN tidak sedang di-suppress (kecuali emergency
  -- override) - versi "benar-benar perlu ditindak SEKARANG".

predictive.inspection_history     -- APPEND-ONLY, DIGABUNG DARI TABEL ALERT §53
  inspection_id, item_serial_code NOT NULL (GANTIKAN cycle_id;
                                              SEBELUMNYA host_serial_code),
  inspection_seq (UNIK per item_serial_code),
  prediction_id NOT NULL, UNIQUE -> item_prediction (§53, SEBELUMNYA
                                     alert_id nullable -> tabel alert
                                     yang sekarang sudah dihapus),
  created_at
  UNIQUE(item_serial_code, inspection_seq)
  -- Satu-satunya catatan "penutupan alert" - baik MANUAL (teknisi lewat
  -- POST /api/v1/inspections, alerts.py::resolve_with_inspection()) MAUPUN
  -- OTOMATIS (cycle sudah tertutup di data operasional, alerts.py::
  -- auto_resolve_closed_cycles() - dulu UPDATE tabel alert, sekarang INSERT
  -- ke sini juga). "Belum ada baris di sini untuk prediction_id X" berarti
  -- alert itu masih terbuka - tidak ada kolom outcome/closed_reason yang
  -- membedakan cara penutupan (sengaja, sama seperti resolution_reason yang
  -- dulu dibuang dari tabel alert - kalau perlu tahu kenapa, cek silang ke
  -- data operasional).
  -- Sengaja TIDAK ADA outcome/action_code/remark - body POST
  -- /api/v1/inspections cuma host_serial_code, tidak ada apa pun lain
  -- untuk diisi ke kolom itu.
  -- item_id DIBUANG (keputusan user) - identitas stabil PART sekarang
  -- DIDERIVE dari bagian tengah item_serial_code (format MODEL-PAIRING-
  -- REPAIRSEQ) lewat alerts.py::_pairing_code(), bukan kolom fisik.
  -- item_serial_code BUKAN FK (tabel item_cycle dihapus) - lihat "Cycle" di
  -- bawah untuk cara cycle dibaca sekarang.
```

**Tabel `predictive.alert` DIHAPUS (§53, 2026-09-08)** - digabung ke
`item_prediction`(`alert_flagged`) + `inspection_history`(`prediction_id`).
Alasan: sinyal mentahnya (`gate_flagged`) sudah ada di `item_prediction`,
jadi status "perlu ditindak" tidak butuh tabel/entitas mutable terpisah -
cukup satu kolom yang dihitung sekali saat insert (tetap append-only) plus
anti-join ke `inspection_history` untuk tahu "sudah ditindak atau belum".
Constraint "satu physical item maksimal satu alert OPEN" yang dulu
ditegakkan `UNIQUE INDEX` (`ux_alert_one_open_per_item`) sekarang
ditegakkan di `alerts.py::compute_alert_flagged()` (`_has_open_alert()`) -
logic aplikasi, bukan lagi constraint database, karena "OPEN" sekarang
konsep DERIVED (tidak ada kolom status untuk di-constrain).

Sengaja TIDAK ADA tabel `alert_event` (event-sourcing audit log terpisah) -
dibuang karena tidak ada kode yang membacanya (murni ditulis) dan
informasinya sudah lengkap di `item_prediction.scored_at`/`alert_flagged`
+ keberadaan baris `inspection_history` (kenapa/gimana ditutup tidak lagi
disimpan, cukup tahu "sudah ada baris inspection_history-nya").

### Cycle - dibaca langsung dari data operasional, TIDAK ADA tabel mirror (§30)

SEBELUM §30 ada tabel `predictive.item_cycle` yang menyalin riwayat cycle
dari data operasional ke schema `predictive` (idempotent upsert). Tabel itu
**dihapus** - `predictive/cycles.py::ensure_active_cycle(item_id)`/
`cycle_status(item_id, host_serial_code)` sekarang membaca `core.data_reader.
get_cycles()` LANGSUNG tiap dibutuhkan, tanpa menyalin apa pun. Alasannya:
volume operasi alert kecil (~1/bulan) sehingga query berulang bukan
masalah performa, dan satu-satunya alasan tabel mirror itu ada sebelumnya
(butuh baris yang bisa dikunci `SELECT ... FOR UPDATE` - schema operasional
read-only, tidak bisa dikunci) sudah tergantikan Postgres **advisory
lock** (`cycles.py::lock_item()`, `pg_advisory_xact_lock(hashtext(item_id))`)
yang tidak butuh baris/tabel sama sekali.

Konsekuensi: `inspection_history.item_serial_code`/`alert.item_serial_code`
(dulu kolom terpisah `cycle_id`, digabung §38/§40; keduanya SEBELUMNYA
bernama `host_serial_code`) sekarang TEXT biasa, bukan lagi FK ke tabel
lokal - integritasnya dijamin oleh kode (selalu diisi dari
`ensure_active_cycle()`), bukan constraint database.

**Identitas cycle = `host_serial_code`** (§38, sejak 2026-09-04) - BUKAN
lagi `"<item_id>:<urutan>"`. `get_cycles()` tetap menghitung
`installation_cycle_id` internal (dipakai UTUH oleh feature engineering/
training - `core/features.py`, `engines/failure/gate.py` - TIDAK
disentuh sama sekali oleh perubahan ini), tapi `predictive/cycles.py`
sekarang membaca kolom `host_serial_code_clean` (ditambahkan ke output
`get_cycles()`) sebagai identitas cycle yang dipakai locking/alert/
inspection - divalidasi 100% populated pada event INSTALLED dan 99,986%
selaras dengan cycle_id internal (2 dari 13.857 cycle aktif berbeda,
keduanya kasus dua event INSTALLED tercatat pada timestamp identik -
lihat docs/DECISIONS.md §38 untuk detail validasi).

`RIGHT_CENSORED_AT_DATA_END` (artinya "belum ada event penutup sampai batas
data operasional terakhir", BUKAN penutupan fisik) - kalau itu
`cycle_end_reason` sebuah cycle, `ensure_active_cycle()`/`cycle_status()`
menganggapnya `is_active=true`. `end_reason` yang dikembalikan `cycle_status()`
selalu kejadian fisik nyata (FAILURE/RETURNED/DISMANTLED).

### `inspection` - minor repair tidak membuka cycle baru

`predictive/inspections.py::record_inspection(item_id, ...)` selalu
mencatat ke cycle AKTIF item saat ini (`ensure_active_cycle()`), menaikkan
`inspection_seq` DALAM cycle itu - TIDAK PERNAH membuka cycle baru sendiri
(itu murni konsekuensi data operasional). Item ini dikunci (`cycles.py::
lock_item()`, advisory lock - lihat "Cycle" di atas) selama penghitungan
`inspection_seq` berikutnya, supaya dua inspection untuk item yang sama
tidak bisa saling tabrak nomor urut.

### Alert - tidak ada tabel/status terpisah, murni turunan item_prediction + inspection_history

`predictive/alerts.py` (§53, ditulis ulang total - lihat "Tabel `predictive.
alert` DIHAPUS" di atas):

- `compute_alert_flagged(cur, frame, scored_at)` - **satu-satunya** yang
  MENENTUKAN sebuah prediction "menjadi alert", dipanggil DI DALAM
  transaksi yang sama dengan INSERT `item_prediction`
  (`scoring.py::record_predictions()`), TIDAK PERNAH dari jalur baca live.
  Per baris yang `gate_flagged`: lewati kalau `item_serial_code` ini SUDAH
  punya prediction `alert_flagged=true` yang belum ada `inspection_history`-
  nya (`_has_open_alert()` - mencegah alert ditumpuk tiap scoring selama
  yang lama belum ditindak) -> lewati kalau masih dalam masa suppression
  (KECUALI emergency override, `_is_suppressed()`) -> `alert_flagged=true`.
- `open_alerts_by_item(item_ids=None)` - MURNI BACA: prediction
  `alert_flagged=true` TERBARU per `item_serial_code` yang BELUM ada
  `inspection_history`-nya (anti-join), dikelompokkan per item_id (pairing
  code). Kalau (seharusnya tidak pernah lewat jalur normal) ada dua
  kandidat untuk pairing code yang sama, log error dan pakai yang
  `scored_at` terbaru. Dipakai `auto_resolve_closed_cycles()` dan
  `resolve_by_item()`.
- `auto_resolve_closed_cycles(item_ids=None)` - jalur resolve
  **OTOMATIS**, untuk alert yang episode-nya sudah selesai lewat kejadian
  operasional biasa (worktype corrective/preventive berujung dismantle,
  dsb - tercatat sistem lewat `journal`, ditutup jadi `cycle_end_reason`).
  Untuk tiap alert OPEN, baca status cycle-nya LANGSUNG dari data
  operasional (`cycles.py::cycle_status()`); kalau cycle sudah tertutup,
  INSERT `inspection_history` (bukan UPDATE status lagi, tabelnya sudah
  tidak ada) - TANPA inspeksi asli, TANPA panggilan API
  (`ON CONFLICT (prediction_id) DO NOTHING` menjaga idempoten kalau ada
  race). Tidak menyentuh alert yang cycle-nya masih aktif. Dipanggil dari
  DUA jalur: `scoring.py::run_and_persist()` (SEBELUM scoring baru, supaya
  cycle lama yang sudah tertutup tidak salah dianggap masih terbuka) DAN
  `python -m partrisk.cli resolve-closed-alerts` (murah, boleh dijadwalkan
  lebih sering - mis. harian - karena tidak perlu skor ulang armada).
- `resolve_by_item(item_id, host_serial_code)` (validasi host_serial_code
  di bawah) - **titik masuk** endpoint `POST /api/v1/inspections` (body
  `host_serial_code`, diresolve ke `item_id` lewat
  `core.data_reader.resolve_item_by_host_serial_code()`). SEBELUM
  diproses, `host_serial_code` yang dikirim caller WAJIB cocok cycle AKTIF
  item ini sekarang (`cycle_store.ensure_active_cycle()`) - kalau tidak,
  raise `HostSerialNotCurrent` (HTTP 409 `HOST_SERIAL_NOT_CURRENT`) supaya
  serial code LAMA (dari sebelum perbaikan terakhir) tidak bisa dipakai
  meresolve alert cycle BARU yang tidak ada hubungannya. Item WAJIB
  SEDANG punya alert OPEN (keputusan user) - kalau tidak, raise
  `NoOpenAlert` (HTTP 409 `NO_OPEN_ALERT`); kalau ada, delegasi ke
  `resolve_with_inspection()`.
- `resolve_with_inspection(prediction_id)` - jalur resolve **MANUAL** yang
  sesungguhnya, untuk perbaikan kecil yang TIDAK PERNAH tercatat di data
  operasional (mis. mengencangkan baut - item tetap di cycle yang sama).
  SELALU lewat inspection tercatat (resolve BUKAN set probability=0 -
  `item_prediction` historis tidak pernah disentuh, hanya keberadaan baris
  `inspection_history` yang berubah). Transaksional penuh. Kalau ternyata
  cycle alert sudah tertutup operasional saat fungsi ini dipanggil
  (skenario yang seharusnya sudah ditangkap `auto_resolve_closed_cycles()`,
  tapi belum sempat berjalan), fungsi ini mencoba auto-resolve dulu lalu
  melempar `AlertNotOpen` (bukan `AlertCycleMismatch` mentah) supaya
  caller tahu alert sudah selesai, bukan error yang tidak jelas maknanya.

**Identitas alert** = `(item_id, item_serial_code, inspection_seq)` secara
konsep, BUKAN cuma `item_id` - `item_id` di sini DIDERIVE dari
`item_serial_code` (`_pairing_code()`), bukan kolom fisik. `inspection_seq`
untuk inspection yang menyelesaikan alert dihitung SAAT resolve
(`_next_inspection_seq()`), bukan lagi direservasi di muka saat alert
dibuka (tidak perlu lagi sejak tidak ada kolom `alert.inspection_seq`
terpisah) - invariant "re-alert = baris/episode baru, bukan buka ulang
yang lama" sekarang datang dari `prediction_id` yang selalu baru per
episode.

**Suppression & emergency override**: `ALERT_SUPPRESSION_DAYS`,
`ALERT_EMERGENCY_SCORE_JUMP`, `ALERT_EMERGENCY_SCORE_ABSOLUTE` di
`core/config.py` - nilai PLACEHOLDER awal, belum divalidasi data
resolve/re-alert nyata. Emergency override membandingkan skor SEKARANG
terhadap `p30` prediction yang ditutup oleh baris `inspection_history`
TERAKHIR untuk `item_serial_code` yang sama (`alerts.py::_last_closure()`) -
bukan terhadap kolom `suppression_until` tersimpan (sudah tidak ada,
dihitung on-the-fly dari `inspection_history.created_at + ALERT_SUPPRESSION_DAYS`).

## Menulis prediksi (scheduled scoring)

```bash
python -m partrisk.cli score-and-persist
```

Satu siklus: tutup alert yang cycle-nya sudah berakhir
(`alerts.py::auto_resolve_closed_cycles()`), skor seluruh PART aktif
(`serving.batch.score_active_parts(force_refresh=True)`), simpan sebagai
satu `model_run` baru + satu baris `item_prediction` per PART
(`alert_flagged` ikut dihitung di langkah INSERT yang sama - lihat bagian
"Alert" di atas). **Dipanggil scheduler eksternal secara berkala**
(cron/Task Scheduler) - satu-satunya jalur yang menulis riwayat prediksi.
Sengaja TERPISAH dari pemanggilan `score_active_parts()` lain (CLI
`predict`, `golden-batch`, test) - jalur-jalur itu tidak ikut menulis
riwayat prediksi setiap kali dipanggil, supaya tabel prediction history
tidak terisi baris uji coba. (Sebelum §29: API GET live juga memanggil
`score_active_parts()` tanpa `force_refresh` untuk melayani dashboard -
endpoint itu sudah dibuang, satu-satunya konsumen live sekarang tinggal
`POST /api/v1/inspections`, yang tidak memanggil `score_active_parts()`
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
