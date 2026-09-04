# Code Notes

Komentar penjelasan (termasuk docstring panjang berisi alasan/riwayat desain) yang sebelumnya berada di file kode - Python dalam `src/` dan `tests/`, SQL dalam `migrations/` - dipusatkan di sini. Nomor baris adalah lokasi sebelum pemindahan dan dipertahankan untuk audit historis; gunakan nama scope sebagai rujukan stabil ketika kode berubah. String deskripsi runtime yang benar-benar dibaca konsumen (mis. deskripsi endpoint FastAPI yang muncul di `/docs`) tetap berada di kode, tapi dalam bentuk ringkas - versi panjangnya (alasan desain, riwayat keputusan) dipindah ke sini juga. Directive teknis seperti `# noqa` dan `# type: ignore` tetap berada di kode. Entri untuk file yang sudah dihapus total dari repo (dashboard/, survival/scrap model, dll - lihat docs/DECISIONS.md untuk riwayatnya) dibuang dari sini, bukan diarsip - riwayat keputusan sudah cukup terekam di docs/DECISIONS.md dan docs/EXPERIMENTS.md.

## `migrations/predictive/0001_init.sql`

### `module` — SQL, former lines 1

> Dijalankan lewat: python -m partrisk.predictive.db migrate

### `predictive.item_prediction` — SQL, former lines 16

> item_prediction APPEND-ONLY

### `predictive.item_prediction` — SQL, former lines 25-26

> Diisi mulai Milestone 4 (installation cycle/inspection) - NULL sampai saat itu, bukan data yang dikarang.

## `migrations/predictive/0002_lifecycle.sql`

### `module` — SQL, former lines 1-7

> Sengaja TIDAK ada tabel item_cycle (SUPERSEDED, docs/DECISIONS.md §30) - info cycle dibaca LANGSUNG dari data operasional (core.data_reader.get_cycles()) tiap dibutuhkan, tidak lagi disalin. Concurrency saat menghitung inspection_seq dijaga lewat Postgres advisory lock (pg_advisory_xact_lock, per item_id - lihat predictive/cycles.py::lock_item()) yang tidak butuh baris/tabel untuk dikunci, jadi tabel mirror ini tidak diperlukan lagi.

### `predictive.inspection` — SQL, former lines 9-19

> "inspection" (SEBELUMNYA "intervention" - rename istilah, arti TIDAK berubah, docs/DECISIONS.md §31): satu baris di sini SUDAH BERARTI satu perbaikan terjadi, apa pun bentuknya (keputusan user, docs/DECISIONS.md §25 update) - BUKAN sekadar "diperiksa", walau namanya "inspection". Sengaja TIDAK ada kolom klasifikasi jenis (type). Sengaja TIDAK ada outcome/action_code/remark/external_* juga - body POST /api/v1/inspections cuma host_serial_code (docs/DECISIONS.md §28), jadi tidak ada lagi apa pun untuk diisi ke kolom itu; idempotency eksternal via external_event_id SENGAJA dilepas (trade-off yang disetujui eksplisit demi kesederhanaan body request - retry menghasilkan baris inspection baru, bukan ditolak).

## `migrations/predictive/0003_alerts.sql`

### `predictive.alert` — SQL, former lines 33-36 (`ux_alert_one_per_prediction`)

> Satu prediction menghasilkan NOL atau SATU alert, tidak pernah lebih (docs/DECISIONS.md §32) - NULL diperbolehkan berulang (alert sintetis di test, atau alert lama dari sebelum §32 yang belum ditautkan), tapi kalau prediction_id TERISI, harus unik.

### `predictive.alert` — SQL, former lines 41-46 (sebelum `ALTER TABLE predictive.inspection`)

> Sengaja TIDAK ada tabel alert_event terpisah (event-sourcing audit log) - SUPERSEDED, lihat docs/DECISIONS.md §28. Tidak ada satu pun kode yang MEMBACA tabel itu (murni ditulis, tidak pernah dipakai keputusan apa pun) - informasinya sudah lengkap di kolom alert.opened_at/resolved_at/resolution_reason, jadi tabel terpisah cuma menduplikasi data tanpa consumer nyata.

## `src/partrisk/api/app.py`

### `module` — Python, former lines 44

> ── LOGGING_CONFIG ──

### `module` — Python, former lines 73

> ── SETTINGS ──

### `module` — Python, former lines 95-101

> WHY: opt-in seperti CORS_ALLOW_ORIGINS di atas - kalau API_KEY tidak
> diisi di .env, endpoint tetap terbuka seperti sebelumnya (dev lokal/CI
> tanpa konfigurasi tambahan berjalan tanpa berubah). Begitu API_KEY
> diisi (deployment di luar localhost), setiap request ke router
> ber-auth wajib kirim header X-API-Key yang cocok - docs/DECISIONS.md
> §15. /health TIDAK ikut digerbang (dipakai health-checker/orkestrator
> tanpa kredensial, tidak membocorkan data bisnis).

### `module` — Python, former lines 114

> ── DB_POOL ──

### `db_pool_install` — Python, former lines 147-148

> WHY: atexit, BUKAN __del__ garbage collector - __del__ bisa
> terpanggil setelah stdout sudah ditutup dan gagal saat logging.

### `module` — Python, former lines 173

> ── ROUTES: HEALTH ──

### `health` — directive note, former lines 203

> detailnya tidak boleh bocor ke client

### `module` — Python, former lines 222

> ── ROUTES: MODEL_INFO ──

### `module` — Python, former lines 237

> ── ROUTES: PREDICTION ──

### `module` — Python, former lines 323

> ── ROUTES: RECOMMENDATIONS ──

### `module` — Python, former lines 442

> ── ROUTES: LOCATIONS ──

### `module` — Python, former lines 496

> ── ROUTES: MONITORING ──

### `module` — Python, former lines 517

> ── MAIN ──

### `lifespan` — directive note, former lines 547

> start tidak boleh gagal karenanya

### `record_inspection` — Python, former lines 193-212 (docs/DECISIONS.md §31/§32)

> Catat satu perbaikan terhadap satu PART - jalur MANUAL untuk tindakan yang TIDAK PERNAH masuk data operasional (mis. sekadar mengencangkan baut). "inspection" (SEBELUMNYA "intervention" - rename istilah, arti TIDAK berubah, docs/DECISIONS.md §31): tetap berarti ada PERBAIKAN, bukan sekadar "diperiksa". Kalau perbaikannya berupa work order corrective/preventive yang berakhir dismantle, itu sudah tercatat di data operasional dan alert mati SENDIRI (jalur otomatis, lihat predictive/alerts.py::auto_resolve_closed_cycles()) - endpoint ini tidak perlu dipanggil untuk kasus itu. Diidentifikasi lewat `host_serial_code` (label fisik PART), BUKAN alert_id internal - aplikasi eksternal tidak pernah tahu alert_id (tidak ada GET /alerts, lihat docs/DECISIONS.md §26/§28). Kalau PART ini SEDANG punya alert OPEN, alert itu ikut di-RESOLVE; kalau tidak, inspection tetap dicatat (satu POST SUDAH BERARTI satu perbaikan terjadi) tanpa ada alert yang ditutup. `performed_at` diambil dari waktu server menerima request, TIDAK ada idempotency key eksternal - retry akan membuat baris inspection baru (docs/DECISIONS.md §28, trade-off disetujui eksplisit demi kesederhanaan body request).

### `module` — Python, former lines 210-213 (`DESCRIPTION`)

> API predictive maintenance - satu-satunya endpoint publik yang dibutuhkan aplikasi eksternal adalah `POST /api/v1/inspections` (docs/DECISIONS.md §28/§29/§31). Tidak ada endpoint GET untuk data prediksi/rekomendasi/terminal - aplikasi eksternal baca schema `predictive` langsung dari database.

## `src/partrisk/api/schemas.py`

### `InspectionRequest` — Python, former lines 30-37 (docs/DECISIONS.md §28/§31)

> Satu perbaikan yang dilaporkan aplikasi eksternal/teknisi terhadap satu PART - lihat docs/DECISIONS.md §28/§31. "inspection" (SEBELUMNYA "intervention" - rename istilah, arti TIDAK berubah): tetap berarti ada PERBAIKAN, bukan sekadar "diperiksa". Diidentifikasi lewat `host_serial_code` (label fisik PART, BUKAN alert_id internal - aplikasi eksternal tidak pernah tahu alert_id). Tidak ada field lain - satu POST di sini SUDAH BERARTI satu perbaikan terjadi, waktunya diambil dari saat server menerima request.

## `src/partrisk/cli.py`

### `module` — Python, former lines 37

> ── PIPELINE ──

### `module` — Python, former lines 70

> ── PREDICT ──

### `module` — Python, former lines 108

> ── GOLDEN_BATCH ──

### `module` — Python, former lines 210

> ── BASELINE_PERFORMANCE ──

### `module` — Python, former lines 296

> ── BASELINE_COMPARISON ──

### `_baseline_comparison_main` — Python, former lines 347-349

> WHY: log_prior_corrective_90d monoton terhadap hitungan mentahnya
> (log1p) - argsort menghasilkan urutan IDENTIK, jadi aman dipakai
> langsung untuk ranking tanpa perlu hitung ulang count mentah.

### `module` — Python, former lines 380-385

> ── ROLLING_BACKTEST ──
>
> WHY: fold di sini SENGAJA tidak dipakai training_failure.assign_split()
> (yang test_start-nya tetap "1 Jan tahun data_end") - dipakai KHUSUS untuk
> backtest FASE 7 P0-1, jalur produksi tidak tersentuh sama sekali. Model
> yang dilatih di sini TIDAK PERNAH disimpan/dipromosikan - murni evaluasi.

### `_assign_rolling_split` — Python, former lines 405-407

> WHY: np.timedelta64(N, "D"), BUKAN pd.Timedelta(days=N) - sama seperti
> training_failure.assign_split(), dan menghindari DeprecationWarning
> unit "generic" dari kombinasi numpy/pandas di lingkungan ini.

### `module` — Python, former lines 528-540

> ── ROLLING_LIFECYCLE_BACKTEST (FASE 8) ──
>
> WHY: _rolling_backtest_main() di atas mengevaluasi ROW-level (FASE 7
> P0-1, khusus perbandingan v3-vs-v4 lama). Sesi FASE 8 (2026-08-26)
> berulang kali menemukan kandidat yang menang di SATU TEST split tapi
> gagal di rolling backtest lifecycle-based (E-55, E-59, E-60, E-61,
> docs/EXPERIMENTS.md) - reuse langsung _rolling_fold_windows()/
> _assign_rolling_split() (bukan implementasi baru), tapi evaluasi lewat
> gate.select_lifecycle_threshold()/lifecycle_metrics() (E-49), BUKAN
> training_failure.full_metrics(). Perintah ini WAJIB dijalankan untuk
> kandidat mana pun (fitur/teknik training/model baru) sebelum diklaim
> sebagai perbaikan - satu TEST split TIDAK CUKUP pada skala alert
> serendah ini (docs/EXPERIMENTS.md E-55).

### `module` — Python, former lines 634

> ── EVALUATE_SURVIVAL ──

### `module` — Python, former lines 636-639

> WHY: ARTIFACTS_DIR di-reuse dari engines.survival.predict (resolusi CURRENT
> tunggal), BUKAN dihitung ulang di sini - dua implementasi terpisah untuk
> hal yang sama pernah jadi sumber bug di modul lain (docs/METHODOLOGY.md
> `data_reader._recon_context()`), jangan diulang di sini.

### `load_artifacts` — Python, former lines 647

> WHY: n_jobs=-1 ter-unpickle bikin predict_survival_function() hang tanpa error

### `module` — Python, former lines 757-764

> ── BOOTSTRAP_CI ──
>
> WHY: metadata.json ketiga model DITULIS ULANG di sini (field baru
> ditambahkan, tidak ada yang dihapus/diubah) - field yang dipakai scoring
> (risk_cutoffs, features, part_model_support, calibrator) tidak tersentuh,
> jadi golden_batch tetap byte-identik. Ini FASE 7 P0-2, bukan retrain -
> model/calibrator yang sudah ada di-skor ulang APA ADANYA, bukan dilatih
> ulang.

### `_bootstrap_classification_ci` — Python, former lines 792-795

> WHY: resample bisa kebetulan tidak punya satu kelas sama
> sekali (base rate kecil, mis. scrap 21/323) - roc_auc_score
> tidak terdefinisi saat itu. Resample itu DIBUANG, bukan
> dipaksa jadi 0/NaN yang mengarang informasi.

### `module` — Python, former lines 939

> ── GERBANG PRESISI (Q2) ──

### `module` — Python, former lines 1070-1080

> ── GERBANG PRESISI - LIFECYCLE (FASE 8, Langkah A) ──
>
> WHY: precision-gate-experiment (E-46) mencari threshold di TINGKAT BARIS
> snapshot - satu PART aktif lama bisa menyumbang puluhan baris, dan kalau
> beberapa di antaranya kebetulan sama-sama lolos threshold, itu terhitung
> sebagai beberapa "alert" terpisah padahal production (serving/alerts.py)
> cuma membuka SATU alert per lifecycle sampai diselesaikan. Perintah ini
> mengulang pencarian yang SAMA (VALIDATION-only, diuji sekali jujur di
> TEST) tapi dengan gate.lifecycle_metrics()/select_lifecycle_threshold() -
> precision/recall dihitung per installation_cycle_id (first-alert), bukan
> per baris. Lihat docs/EXPERIMENTS.md E-49.

### `module` — Python, former lines 1133

> ── MAIN ──

## `src/partrisk/core/config.py`

### `module` — Python, former lines 16

> ── PATHS ──

### `module` — Python, former lines 25

> ── DATABASE ──

### `module` — Python, former lines 43-48 (`ALERT_SUPPRESSION_DAYS`)

> Setelah alert di-resolve (inspection tercatat), berapa lama re-alert DITAHAN pada episode berikutnya (docs §24 master prompt) - PLACEHOLDER awal, belum divalidasi lewat data operasional nyata (beda dari FAILURE_GATE_TARGET_PRECISION yang lewat eksperimen panjang, lihat docs/EXPERIMENTS.md) - revisit begitu ada riwayat resolve/re-alert nyata untuk dievaluasi.

### `module` — Python, former lines 51-54 (`ALERT_EMERGENCY_SCORE_JUMP`/`ALERT_EMERGENCY_SCORE_ABSOLUTE`)

> Emergency override (docs §25 master prompt): re-alert BOLEH menembus masa suppression kalau risiko naik drastis dibanding skor saat alert terakhir di-resolve - salah satu syarat berikut cukup. Sama-sama PLACEHOLDER, sama alasannya seperti ALERT_SUPPRESSION_DAYS di atas.

### `module` — Python, former lines 47

> ── FAILURE ──

### `module` — Python, former lines 106

> WHY: tetap, bukan early stopping - AUC early stopping pada validasi positif-sedikit terbukti berhenti prematur, resolusi probabilitas jadi sangat kasar.

### `module` — Python, former lines 140-145

> WHY: 0,40 - BUKAN 0,85 - dipilih setelah presisi>=85% terbukti TIDAK
> genuinely generalize di TEST untuk model/fitur/horizon manapun yang
> diuji (docs/EXPERIMENTS.md E-46/E-47/E-48: threshold presisi tinggi
> selalu jatuh ke <10 baris VALIDATION paling ekstrem, kolaps ke 0 alert
> di TEST). 0,40 adalah target tertinggi yang diverifikasi TEST-jujur
> menghasilkan alert non-degenerate (bukan cuma 1-2 baris kebetulan).

### `module` — Python, former lines 148

> ── SCRAP ──

### `module` — Python, former lines 179

> ── TEXT ──

## `src/partrisk/core/data_reader.py`

### `_build_text_maps` — Python, former lines 202-209

> WHY: get_cycles/get_events/get_failure_episodes/get_terminal_context
> dipanggil PARALEL oleh serving/batch.py (lihat WHY di sana) dan
> masing-masing memanggil fungsi ini - tanpa lock, beberapa thread bisa
> sama-sama lolos pengecekan cache kosong dan menghitung ulang secara
> redundan (boros, walau tidak salah hasilnya - dict yang dihasilkan
> tetap sama). Double-checked locking: cek cepat tanpa lock dulu (jalur
> normal setelah cache terisi), baru kunci kalau benar-benar perlu
> menghitung.

### `_chain_sql` — embedded SQL, former lines 309

> Event journey yang sudah dinormalisasi dan dikanonikalisasi.

### `_chain_sql` — embedded SQL, former lines 341-342

> RECON administratif tidak mencerminkan kondisi teknis PART, jadi tidak
> boleh ikut menghitung durasi/urutan operasional.

### `module` — embedded SQL, former lines 371-373

> Dasar kedua: pembongkaran preventif yang TERNYATA berakhir rusak
> sebelum PART itu dipasang lagi. Jumlahnya sedikit, tapi mengabaikannya
> berarti melabeli kerusakan nyata sebagai bukan-kerusakan.

### `get_terminal_context` — embedded SQL, former lines 473-479

> WHY: terminal_inventory_item_id dan terminal_serial_code_clean sudah
> dihitung di CTE di atas sejak awal (dipakai parent_link_quality_status),
> tapi dulu tidak pernah di-SELECT keluar - satu-satunya konsumen
> (features_survival.attach_terminal_extra) cuma butuh TIPE terminal
> (terminal_type_clean), bukan ID fisiknya. Sekarang di-SELECT juga untuk
> pengelompokan Terminal->PART (serving/batch.py::_attach_terminal) yang
> butuh identitas FISIK, bukan cuma tipe/model.

### `get_cycles` — embedded SQL, former lines 518-519

> RECON yang muncul SETELAH item terakhir terlihat aktif menandakan ada yang
> perlu direkonsiliasi: masa diam sebelumnya belum tentu benar-benar aman.

### `get_cycles` — embedded SQL, former lines 531

> Siklus hanya dibuka oleh pemasangan (INSTALLED) PART.

### `get_cycles` — embedded SQL, former lines 545-548

> Setiap event penutup dipasangkan ke siklus yang sedang berjalan saat itu.
> Satu event DISMANTLED dapat sekaligus menjadi failure onset; FAILURE diberi
> prioritas pada timestamp/journey yang sama agar label kerusakannya tidak
> hilang. Event setelah penutup pertama diabaikan sampai INSTALLED berikutnya.

### `get_cycles` — embedded SQL, former lines 560-561

> Di histori lama, penerimaan kembali PART dicatat sebagai
> OK/RECEPTION (bukan RETURNED) pada identifier PART itu sendiri.

### `get_cycles` — embedded SQL, former lines 605-609

> Snapshot boleh jadi negatif kalau siklusnya berakhir karena failure
> (yang bisa saja di luar horizon) atau masih berjalan TANPA jejak
> RECON belakangan. Siklus yang ditutup pemasangan ulang tanpa failure
> tercatat tidak boleh otomatis dianggap negatif - tidak ada yang tahu
> apa yang sebenarnya terjadi pada PART itu.

### `get_cycles` — embedded SQL, former lines 635

> Rata-rata umur siklus SEBELUMNYA untuk item yang sama (bukan siklus ini).

### `get_cycles` — embedded SQL, former lines 641

> Batas akhir observasi yang hasil negatifnya masih bisa dipastikan.

### `resolve_item_by_host_serial_code` — Python, former lines 674-683 (docs/DECISIONS.md §28)

> Cari item_id internal (item_identifier_clean, dipakai seluruh schema predictive - cycle/inspection/alert) dari host_serial_code: label fisik format MODEL-PAIRINGCODE-REPAIRSEQ yang dibaca teknisi/aplikasi eksternal dari kode PART (journal.t_item_journey.host_serial_code) - lihat docs/DECISIONS.md §28. Ambil catatan journal TERBARU yang cocok, bukan yang pertama - host_serial_code menyertakan repair_seq yang berubah tiap perbaikan besar, jadi PART fisik yang sama bisa punya beberapa host_serial_code berbeda sepanjang riwayatnya. Return None kalau tidak ada journal yang cocok sama sekali.

## `src/partrisk/core/features.py`

### `module` — Python, former lines 10

> ── TRANSFORMS ──

### `module` — Python, former lines 26

> ── SUPPORT ──

### `module` — Python, former lines 52

> ── HISTORY ──

### `attach_degradation_history` — Python, former lines 138-145

> WHY: konversi ke log1p SEGERA, JANGAN simpan nama kolom RAW
> (cumulative_prior_cycle_days/previous_cycle_count) di sini -
> features_survival.py::attach_dynamic_extra() butuh nama RAW
> yang SAMA PERSIS untuk merge internalnya sendiri, dan observations/
> full_snapshot sering objek yang sama dipakai ulang lintas model
> (serving_batch.py). Kalau RAW ikut ditempel di sini, merge
> survival belakangan bentrok nama (pandas diam-diam jadi _x/_y,
> KeyError senyap) - kelas bug yang sama dengan docs/EXPERIMENTS.md E-28.

### `module` — Python, former lines 177

> ── FLEET ──

### `fleet_snapshot` — Python, former lines 224-228

> WHY: dihitung lewat attach_fleet yang SAMA PERSIS dengan jalur
> training, BUKAN rumus terpisah - dua implementasi yang seharusnya
> sama pernah jadi bug nyata: satu menghitung siklus aktif sebagai
> "berakhir >= sekarang", yang lain "berakhir > sekarang", sehingga
> seluruh PART aktif terhitung nol dan lajunya meledak.

### `module` — Python, former lines 343

> ── OBSERVATIONS ──

### `_still_installed` — Python, former lines 386-389

> Defense-in-depth terhadap status baru yang belum menjadi terminator di
> get_cycles(): populasi live tetap wajib punya status event terbaru
> INSTALLED. RETURNED (termasuk OK/RECEPTION) dan DISMANTLED sekarang
> sudah menutup cycle di data_reader.get_cycles() (E-71/E-81).

### `module` — Python, former lines 416

> ── FAILURE ──

### `module` — Python, former lines 522

> ── SCRAP ──

## `src/partrisk/engines/failure/gate.py`

### `module` — Python, former lines 7-9

> WHY: precision_recall_curve menambah satu titik sintetis di ujung
> (precision=1, recall=0, tanpa threshold yang cocok) - selalu dipotong
> supaya precision/recall sejajar 1:1 dengan thresholds.

### `module` — Python, former lines 85-96

> ── LIFECYCLE (first-alert, bukan per-snapshot) ──
>
> WHY: select_precision_constrained_threshold()/honest_test_evaluation() di
> atas menghitung presisi/recall PER BARIS snapshot (satu PART aktif lama
> menyumbang puluhan baris, satu per 30 hari). Itu tidak sama dengan "berapa
> kali PART yang SAMA benar-benar dipromosikan" - production (serving/
> alerts.py) hanya membuka SATU alert per lifecycle (installation_cycle_id)
> sampai diselesaikan, bukan alert baru tiap siklus batch. Fungsi di bawah
> meniru persis perilaku itu untuk evaluasi: "first alert" = baris paling
> awal (urut observation_on) yang skornya >= threshold untuk satu
> installation_cycle_id. Precision/recall dihitung per LIFECYCLE (dedup),
> bukan per baris.

## `src/partrisk/engines/failure/train.py`

### `module` — Python, former lines 20

> ── VERSIONING ──

### `capacity_metrics` — Python, former lines 47-50

> WHY: days_per_month beda SENGAJA antara train.py (30 - grid observasi
> tetap setiap 30 hari) dan train_scrap.py (30,44 - rata-rata kalender,
> karena kerusakan scrap tersebar bebas di waktu nyata). Jangan
> disamakan - itu bukan bug.

### `decide_promotion` — Python, former lines 88-91

> WHY: split_label cuma label untuk pesan/metadata - dia TIDAK mengubah
> cara metrik dihitung. Pemanggil yang menentukan split mana yang
> sungguh dipakai (lihat komentar WHY di main() failure/train.py soal
> kenapa VALIDATION, bukan TEST, sejak docs/DECISIONS.md §10/§13).

### `module` — Python, former lines 140

> ── FAILURE_CLASSIFICATION ──

### `evaluate_incumbent` — Python, former lines 269-273

> WHY: incumbent lama dilatih dengan daftar fitur BEKU miliknya sendiri
> (metadata["features"]) - build_features() selalu mengembalikan skema
> kandidat SAAT INI (bisa lebih lebar). Tanpa mempersempit ke sini,
> CatBoost menerima kolom tambahan yang tidak pernah dilihatnya saat
> training - diam-diam SALAH (bukan error).

### `compute_gate` — Python, former lines 329-333

> WHY: threshold dicari HANYA dari VALIDATION (select_precision_constrained_
> threshold tidak tahu konsep TEST sama sekali), lalu diuji SEKALI di TEST
> (honest_test_evaluation tidak pernah mencari ulang) - metodologi yang
> sama seperti EXPERIMENTS.md E-46/E-47/E-48, supaya threshold produksi
> tidak overfit ke VALIDATION seperti yang terbukti terjadi di target 0,85.

### `compute_gate` — Python, former lines 360-367

> WHY: blok TERPISAH, INFORMASIONAL SAJA - threshold yang MEMUTUSKAN
> gate_flagged di atas TETAP dicari row-level (tidak diam-diam diganti
> di sini). E-49 (docs/EXPERIMENTS.md) membuktikan threshold row-level
> dan lifecycle IDENTIK pada threshold production saat ini (0,375) -
> blok ini menambah metrik lifecycle (precision/recall/lead-time di
> tingkat PART, sesuai objective baru "maximize recall @ precision>=85%,
> dievaluasi per lifecycle/first-alert") untuk monitoring/keterlacakan,
> TANPA mengubah keputusan threshold produksi dalam retrain rutin.

### `save_version` — Python, former lines 421-423

> WHY: CSV, bukan JSON - kode model seperti "0120201" akan dibaca ulang
> sebagai angka 120201 oleh pembaca JSON, nol di depannya hilang, dan
> seluruh pencocokan gagal diam-diam.

### `main` — Python, former lines 493-498

> WHY: kandidat dievaluasi ULANG dengan dukungan BEKU miliknya sendiri
> (support_totals), BUKAN raw_test (dukungan point-in-time dari
> build_dataset()) - supaya adil dibanding incumbent (yang juga dievaluasi
> dengan dukungan beku miliknya sendiri, persis cara predict.py melayani
> production). Tanpa ini, kandidat tampak lebih baik semata karena
> metodologi fitur yang berbeda, bukan model yang sungguh berbeda.

### `main` — Python, former lines 509-512

> WHY: gerbang presisi dicari dari VALIDATION (dukungan beku sama seperti
> kandidat TEST di atas, supaya konsisten) lalu diuji SEKALI di TEST -
> lihat docs/EXPERIMENTS.md E-46/E-47/E-48 untuk kenapa target_precision
> default (config.FAILURE_GATE_TARGET_PRECISION) BUKAN 0,85.

### `main` — Python, former lines 553-560

> WHY: keputusan promosi digerbang di VALIDATION, bukan TEST. E-44
> (docs/EXPERIMENTS.md) menangkap bukti langsung model beradaptasi ke
> TEST split yang sama dipakai berulang kali untuk keputusan promosi
> (VALIDATION PR-AUC turun sementara TEST naik lintas 4 promosi
> berturut-turut) - persis pola overfitting-lewat-mengintip-TEST-
> berkali-kali yang jadi alasan docs/DECISIONS.md §10 dibuka dan §13
> sekarang menutupnya. TEST tetap dihitung dan disimpan penuh di bawah,
> MURNI untuk laporan/audit - tidak lagi jadi dasar keputusan promosi.

## `src/partrisk/engines/predict.py`

### `module` — Python, former lines 14

> ── RISK ──

### `module` — Python, former lines 29

> ── FAILURE ──

### `_fleet_snapshot` — Python, former lines 57-58

> WHY: dtype=str WAJIB - kode model punya nol di depan yang hilang
> kalau dibaca sebagai angka, dan pencocokan gagal tanpa suara.

### `_covers_known_models` — Python, former lines 71-72

> WHY: kegagalan di sini SENYAP - kalau kode model tidak cocok, fitur
> armada diam-diam jadi nol dan prediksi tetap keluar, hanya salah.

### `predict` — Python, former lines 136-140

> WHY: build_features()/project_features() selalu mengembalikan
> config.FEATURE_COLUMNS (skema TERKINI) - model CURRENT bisa versi
> lama dengan fitur lebih sempit (metadata["features"]). Tanpa
> dipersempit di sini, CatBoost diam-diam menerima kolom lebih
> banyak dari yang dilihatnya saat training (kegagalan senyap).

### `module` — Python, former lines 175

> ── SCRAP ──

## `src/partrisk/predictive/alerts.py`

### `module` — Python, former lines 1-30

> Alert lifecycle persisten (predictive.alert) - menggantikan serving/alerts.py in-memory. Lihat docs/DATABASE.md dan docs §16-25 master prompt refactor.
>
> Pemisahan tanggung jawab (docs §2 master prompt):
> - FAILURE MODEL memutuskan skor (serving/batch.py, tidak berubah).
> - ALERT ENGINE (modul ini) memutuskan apakah skor itu perlu jadi alert.
> - TEKNISI/aplikasi eksternal mencatat tindakan (predictive/inspections.py).
>
> Alert HANYA dibuka dari siklus scheduled scoring (`evaluate_and_open`, dipanggil dari predictive/scoring.py::run_and_persist()) - TIDAK PERNAH dari jalur baca live (serving/batch.py hanya membaca status alert yang sudah ada, lihat open_alerts_by_item()).
>
> DUA jalan untuk mematikan alert (klarifikasi user 2026-09-03):
> 1. OTOMATIS (`auto_resolve_closed_cycles`) - work order corrective/preventive yang berakhir dismantle SUDAH tercatat di data operasional (`core.data_reader.get_cycles()`) - itu sendiri sudah bukti PART ditangani, alert mati sendiri tanpa laporan terpisah.
> 2. MANUAL lewat inspection (`resolve_by_item` -> `resolve_with_inspection`, endpoint `POST /api/v1/inspections`, body cuma `host_serial_code`) - untuk perbaikan KECIL yang TIDAK PERNAH masuk data operasional (mis. cuma kencangkan baut) - satu-satunya cara sistem tahu itu terjadi adalah laporan eksplisit ini. Diidentifikasi lewat item (docs/DECISIONS.md §28), BUKAN alert_id - aplikasi eksternal tidak pernah tahu alert_id internal (tidak ada GET /alerts, lihat §26).
>
> "inspection" (SEBELUMNYA "intervention" - rename istilah, arti TIDAK berubah, docs/DECISIONS.md §31): satu POST tetap berarti ada PERBAIKAN, BUKAN sekadar "diperiksa".

### `AlertCycleMismatch` — Python, former lines 63-68

> Item sudah pindah cycle sejak alert ini dibuka, TAPI cycle lamanya TERNYATA belum tercatat tertutup di data operasional - keadaan yang seharusnya tidak terjadi (auto-resolve harusnya sudah menangani cycle yang benar-benar tertutup, lihat _auto_resolve_if_cycle_closed). Ditolak eksplisit alih-alih menempelkan inspection ke cycle yang sudah tidak aktif.

### `open_alerts_by_item` — Python, former lines 96-99

> Baca status alert OPEN saat ini, per item_id - dipakai `auto_resolve_closed_cycles()`/`resolve_by_item()` untuk mencari alert OPEN milik satu/beberapa item. TIDAK PERNAH membuka/menutup alert apa pun, murni baca.

### `open_alerts_by_item` — Python, former lines 110

> index 3 = item_id

### `evaluate_and_open` — Python, former lines 271-274

> terminal_serial_code di sini = serial code fisik terminal (frame["terminal_label"]), BUKAN ID internal terminal_inventory_item_id - sama seperti predictive/scoring.py::record_predictions(), lihat WHY di sana.

### `_auto_resolve_if_cycle_closed` — Python, former lines 143-154

> Dua jalan untuk mematikan alert (docs - klarifikasi user 2026-09-03): (1) inspection tercatat lewat API (resolve_with_inspection) - untuk perbaikan KECIL yang tidak pernah masuk data operasional (mis. cuma kencangkan baut), atau (2) OTOMATIS di sini - work order corrective/preventive yang berakhir dismantle SUDAH tercatat di data operasional (data_reader.get_cycles(), cycle_end_reason FAILURE/RETURNED/DISMANTLED, dibaca langsung tiap panggilan - docs/DECISIONS.md §30) - itu sendiri sudah bukti PART ditangani, tidak perlu laporan inspection terpisah lewat API. Return baris alert yang baru di-RESOLVE (kalau cycle-nya memang sudah tertutup), None kalau cycle masih aktif (tidak melakukan apa-apa).

### `auto_resolve_closed_cycles` — Python, former lines 178-185

> RESOLVE otomatis setiap alert OPEN yang cycle-nya ternyata sudah tertutup di data operasional (dismantle/failure/return sungguhan sudah tercatat, dibaca langsung tiap panggilan) - dipanggil di awal setiap evaluate_and_open(). Dipisah jadi fungsi sendiri (bukan inline di evaluate_and_open) supaya bisa juga dipanggil untuk SATU alert saja dari resolve_with_inspection saat mendeteksi cycle sudah berpindah.

### `_emergency_override` — Python, former lines 200-202

> docs §25 master prompt - lonjakan skor tajam atau skor sudah sangat tinggi membuka alert BARU walau masih dalam masa suppression. Nilai ambang: lihat WHY di core/config.py (belum divalidasi data nyata).

### `resolve_by_item` — Python, former lines 211-222

> Jalur MANUAL diidentifikasi lewat item (bukan alert_id) - dipakai endpoint `POST /api/v1/inspections`, body-nya cuma `host_serial_code` (diresolve ke `item_id` internal oleh caller lewat `core.data_reader.resolve_item_by_host_serial_code()` sebelum masuk sini - lihat docs/DECISIONS.md §28). Kalau item ini SEDANG punya alert OPEN, resolve alert itu - delegasi penuh ke `resolve_with_inspection()` (transaksi/suppression/cycle-mismatch-handling yang sama persis, tidak diduplikasi di sini). Kalau TIDAK ada alert OPEN, tetap catat inspection - satu POST tetap berarti ada perbaikan (docs/DECISIONS.md §25), hanya saja tidak ada alert yang perlu ditutup.

### `evaluate_and_open` — Python, former lines 234-254

> Satu siklus evaluasi alert - dipanggil SEKALI per scheduled scoring run (predictive/scoring.py::run_and_persist()), bukan per request live. Langkah 0 (docs - klarifikasi user 2026-09-03): auto-resolve dulu semua alert OPEN yang cycle-nya SUDAH tertutup di data operasional (corrective/preventive work order yang berakhir dismantle - lihat auto_resolve_closed_cycles()). Item yang baru dilepas TIDAK MUNCUL lagi di `frame` (sudah bukan PART aktif), jadi ini dicek terpisah dari seluruh alert OPEN, bukan dari isi `frame`. Untuk tiap PART yang gate_flagged di `frame`: sinkron cycle-nya, lewati kalau sudah ada alert OPEN untuk episode yang sama, lewati kalau masih dalam masa suppression (kecuali emergency override), lalu buka alert baru - `alert.prediction_id` ditautkan ke baris `item_prediction` yang memicunya (`frame["prediction_id"]`, diisi `scoring.py::run_and_persist()` - docs/DECISIONS.md §32). Satu prediction menghasilkan NOL atau SATU alert, tidak pernah lebih - ditegakkan `UNIQUE(prediction_id)` di `predictive.alert`. Return daftar alert_id yang baru dibuka pada run ini (TIDAK termasuk yang auto-resolved).

### `resolve_with_inspection` — Python, former lines 321-336

> Jalur MANUAL untuk mematikan alert - untuk perbaikan yang TIDAK tercatat di data operasional (mis. sekadar mengencangkan baut). Kalau perbaikannya sudah tercatat di data operasional (work order corrective/preventive yang berakhir dismantle), alert mati sendiri lewat jalur OTOMATIS (auto_resolve_closed_cycles(), dipanggil dari evaluate_and_open()) - endpoint ini tidak perlu dipanggil untuk kasus itu, dan kalau tetap dipanggil, akan melihat alert ini sudah RESOLVED. Transaksi tunggal (docs §22 master prompt): validasi alert -> validasi cycle -> insert inspection -> resolve alert -> set suppression -> commit. Gagal di tengah = ROLLBACK, alert tidak pernah tersisa RESOLVED tanpa inspection atau sebaliknya. Tidak idempotent (docs/DECISIONS.md §28) - tidak ada identifier eksternal untuk dideteksi ulang, dipanggil lewat `resolve_by_item()` yang sudah memastikan hanya alert OPEN yang diproses.

### `resolve_with_inspection` — Python, former lines 348-354

> Baca cycle aktif LANGSUNG dari data operasional (cycles.py::ensure_active_cycle(), tidak ada yang ditulis). Kalau item sudah pindah cycle sejak alert ini dibuka, itu berarti cycle LAMA sudah tertutup di data operasional (dismantle/failure/return) - auto-resolve alert ini dulu (jalur OTOMATIS, docs - klarifikasi user), baru laporkan ke pemanggil bahwa alert ini SUDAH selesai (bukan lewat inspection yang baru saja dikirim).

### `resolve_with_inspection` — Python, former lines 374-375

> Kunci ulang baris alert DI DALAM transaksi (defends terhadap race dengan resolve lain yang lolos pengecekan awal di atas).

## `src/partrisk/predictive/cycles.py`

### `module` — Python, former lines 1-7

> Info siklus fisik operasional (core.data_reader.get_cycles()) - dibaca LANGSUNG tiap dibutuhkan, TIDAK disalin ke tabel predictive.item_cycle (tabel itu dihapus, docs/DECISIONS.md §30) - volume operasi alert terlalu kecil (~1/bulan) untuk butuh cache lokal, dan concurrency saat menghitung inspection_seq dijaga lewat Postgres advisory lock (lock_item()) yang tidak butuh baris/tabel untuk dikunci sama sekali.

### `module` — Python, former lines 13-16 (`_STILL_ACTIVE_REASON`)

> RIGHT_CENSORED_AT_DATA_END = "belum ada event penutup sampai batas data terakhir" (bukan penutupan fisik sungguhan) - satu-satunya alasan cycle dianggap masih aktif. Semua reason lain (FAILURE/RETURNED/DISMANTLED) berarti cycle itu benar-benar sudah berakhir secara fisik.

### `ensure_active_cycle` — Python, former lines 30-33

> Cycle AKTIF item ini SAAT INI, dibaca langsung dari data operasional - tidak ada apa pun yang ditulis. Raise ItemNotInstalled kalau item tidak dikenal atau tidak sedang terpasang - inspection/alert tidak bisa dicatat untuk item yang tidak punya cycle aktif.

### `cycle_status` — Python, former lines 52-55

> Status cycle TERTENTU (dikenali lewat cycle_id) - dipakai auto-resolve (alerts.py) untuk cek apakah cycle yang tercatat di satu alert SUDAH tertutup di data operasional. Return None kalau cycle_id itu tidak ditemukan sama sekali di riwayat item ini.

### `lock_item` — Python, former lines 72-77

> Kunci transaksional per-item (Postgres advisory lock) - serialize dua penulis yang menghitung inspection_seq untuk item yang SAMA secara bersamaan, tanpa butuh baris/tabel untuk dikunci (schema operasional read-only, tidak bisa SELECT ... FOR UPDATE di sana - docs/DECISIONS.md §30). Lock otomatis lepas saat transaksi commit/rollback - HARUS dipanggil di dalam transaksi yang sama dengan penghitungan seq/insert.

## `src/partrisk/predictive/inspections.py`

### `module` — Python, former lines 1-14

> Pencatatan tindakan teknisi/aplikasi eksternal (predictive.inspection) - lihat docs/DATABASE.md dan docs §10/22/23 master prompt refactor.
>
> "inspection" (SEBELUMNYA "intervention" - rename istilah, arti TIDAK berubah, docs/DECISIONS.md §31): tidak ada klasifikasi jenis - satu POST berarti satu PERBAIKAN terjadi, apa pun bentuknya (keputusan user, docs/DECISIONS.md §25 update), BUKAN sekadar "diperiksa". Tidak ada outcome/remark juga - body POST /api/v1/inspections cuma host_serial_code (docs/DECISIONS.md §28) + external_event_id opsional untuk idempotency retry (docs/DECISIONS.md §37).
>
> Minor repair TIDAK menutup installation cycle - inspection_seq naik DALAM cycle aktif yang sama (predictive/cycles.py), bukan membuka cycle baru.

### `record_inspection` — Python, former lines 54-57

> Catat satu inspection (perbaikan) untuk `item_id`, DALAM cycle aktifnya saat ini. Tidak idempotent - tidak ada identifier eksternal untuk dideteksi ulang (docs/DECISIONS.md §28), setiap panggilan selalu membuat baris baru.

### `record_inspection` — Python, former lines 66-69

> Kunci item ini (advisory lock, docs/DECISIONS.md §30) supaya dua inspection untuk item yang SAMA tidak bisa menghitung inspection_seq berikutnya secara bersamaan (race condition) - writer kedua menunggu, bukan gagal karena UNIQUE(cycle_id, inspection_seq).

## `src/partrisk/predictive/scoring.py`

### `module` — Python, former lines 1-8

> Menyimpan hasil batch scoring failure (Q2) ke schema `predictive` - `model_run` + `item_prediction` (append-only). Dipanggil eksplisit (CLI `score-and-persist`, dipanggil scheduler eksternal berkala) - BUKAN otomatis di setiap `serving.batch.score_active_parts()`, supaya batch ad-hoc (API on-demand, CLI predict, test, golden-batch) tidak ikut menulis baris ke riwayat prediksi setiap kali dipanggil.

### `record_predictions` — Python, former lines 80-89

> Tulis satu baris `item_prediction` per PART di `frame` (hasil `serving.batch.score_active_parts().frame`). APPEND-ONLY - tidak pernah UPDATE/DELETE baris lama, prediction_id sebelumnya tetap ada. Kolom `terminal_serial_code` di sini diisi `frame["terminal_label"]` (serial code fisik terminal, docs/DECISIONS.md §28) - BUKAN `frame["terminal_id"]` (ID internal `terminal_inventory_item_id` yang dipakai jalur live/filtering di serving/batch.py, TIDAK berubah) - supaya aplikasi eksternal yang baca tabel ini bisa mengorelasikan terminal pakai kode yang sama dengan sistem mereka sendiri.

### `prediction_ids_for_run` — Python, former lines 126-131

> item_id -> prediction_id untuk satu run - dipakai run_and_persist() untuk menautkan alert.prediction_id ke baris item_prediction yang memicunya (docs/DECISIONS.md §32). Query terpisah (bukan RETURNING pada executemany di record_predictions()) supaya kontrak record_predictions() tidak berubah - satu item_id cuma muncul sekali per run, jadi lookup ini selalu unik.

### `run_and_persist` — Python, former lines 143-149

> Satu siklus scoring: skor SELURUH PART aktif (force refresh, tidak pakai cache lama), simpan sebagai model_run + item_prediction baru. Dipanggil scheduler eksternal secara berkala (mis. cron) - lihat CLI `score-and-persist`. Kegagalan DI TENGAH scoring dicatat sebagai model_run FAILED, bukan diam-diam hilang.

### `run_and_persist` — Python, former lines 169-172

> Tautkan tiap baris frame ke prediction_id yang baru ditulis, supaya alert yang dibuka evaluate_and_open() bisa menyimpan alert.prediction_id (docs/DECISIONS.md §32) - satu prediction menghasilkan NOL atau SATU alert, tidak pernah lebih (ditegakkan UNIQUE(prediction_id) di alert).

### `run_and_persist` — Python, former lines 176-178

> Evaluasi alert SETELAH model_run tercatat SUCCEEDED - kegagalan di sini tidak mengubah status run (prediksi sudah aman tersimpan), tapi tetap dilaporkan keras (raise), bukan ditelan diam-diam.

## `src/partrisk/serving/batch.py`

### `module` — Python, former lines 27

> ── QUERY_CACHE ──

### `module` — Python, former lines 89

> ── DATA_STATE ──

### `module` — Python, former lines 135

> ── BATCH_PREDICTOR ──

### `_fetch_batch_inputs` — Python, former lines 191-198

> WHY: keempat query ini SALING BEBAS (tidak ada yang butuh hasil yang
> lain) tapi sama-sama menjalankan pipeline CTE yang sama di atas
> journal.t_item_journey - diukur berurutan, sendiri-sendiri makan
> 14-20 detik dan totalnya ~70-90% dari seluruh waktu _compute()
> (~68 dari ~77 detik). Dijalankan PARALEL di sini murni memangkas
> waktu TUNGGU jadi kira-kira selambat query paling lambat, BUKAN
> mengubah query/hasil apa pun - lihat _TEXT_MAPS_LOCK di data_reader.py
> untuk race condition yang diantisipasi dari paralelisasi ini.

### `_score_failure` — Python, former lines 292-298

> WHY: gerbang presisi digerbang pada failure_probability_30d (skor
> TERKALIBRASI, sama yang ditampilkan ke user) - bukan tier_score (raw,
> cuma untuk urutan). Threshold dicari dari VALIDATION, diuji sekali di
> TEST saat training (docs/EXPERIMENTS.md E-46/E-47/E-48,
> train.py::compute_gate()) - menggantikan antrian isi-sampai-kapasitas
> lama (ADR docs/DECISIONS.md §9, SUPERSEDED §11). Kalau blok gate belum
> ada (artifact lama) atau infeasible, tidak ada PART yang lolos gerbang.

### `_score_failure` — Python, former lines 309-315

> WHY: alert dibuka di sini (bukan hanya dihitung ulang tiap request) -
> PART yang sudah punya alert OPEN tidak dibuka ulang, itu yang mencegah
> alert berulang untuk PART yang sama sebelum inspeksi/maintenance
> sebelumnya selesai (ditutup lewat serving.resolve_alert()). Antrian
> resmi = gate_flagged SEKARANG *atau* masih ada alert OPEN yang belum
> diselesaikan (skor bisa turun sedikit di bawah ambang sebelum sempat
> diperiksa - itu tidak menutup alertnya secara diam-diam).

### `module` — Python, former lines 435-440

> WHY: hanya status yang mengonfirmasi parent-nya SUNGGUH terminal yang ada
> di inventory (bukan sekadar tercatat, PARENT_NOT_TERMINAL/PARENT_TERMINAL_
> NOT_IN_INVENTORY/dll) dipakai untuk mengelompokkan PART ke Terminal fisik -
> lihat data_reader.py::get_terminal_context() untuk arti tiap status. PART
> yang tidak masuk kelompok mana pun TIDAK dipaksakan ke terminal manapun
> (terminal_id tetap NaN) - itu risiko data nyata, bukan dirapikan diam-diam.

### `_attach_terminal` — Python, former lines 451-454

> WHY: .last() setelah groupby - terminal_raw sudah terurut ascending
> (item_identifier_clean, installed_on, journey_id) dari data_reader.py,
> jadi ini mengambil relasi parent TERBARU per PART, sama seperti
> _attach_context() di atas untuk lokasi.

### `_attach_terminal` — Python, former lines 456-461

> WHY: dipaksa ke string EKSPLISIT di sini (lewat Int64 dulu supaya
> tidak berakhir "12345.0" - kolom PK inventory.t_item.item_id datang
> sebagai float64 begitu ada NaN campur) - terminal_id dipakai sebagai
> identitas untuk dikelompokkan/difilter/ditampilkan, bukan untuk
> dihitung, dan skema API (PriorityItem.terminal_id: str | None)
> mengharapkan string bersih, bukan pengubahan tipe diam-diam.

### `filter_scores` — Python, former lines 504-509

> WHY: default True - antrian RESMI (docs/DECISIONS.md §11, menggantikan
> §9) cuma menampilkan PART yang lolos gerbang presisi ATAU masih punya
> alert OPEN yang belum diselesaikan (skor sempat turun tipis sebelum
> sempat diperiksa - alertnya tidak menutup diam-diam), ukurannya
> dinamis dan boleh 0. False = daftar lengkap terurut tier_score (mode
> eksplorasi lama, dipertahankan untuk drill-down manual).

### `summary` — Python, former lines 537-540

> WHY: ukuran antrian resmi (gerbang presisi, docs/DECISIONS.md §11,
> ditambah alert OPEN yang belum diselesaikan) - dinamis, boleh 0.
> BUKAN hitungan risk_level (itu cuma label warna, tidak pernah
> menyaring antrian - §9 SUPERSEDED).

### `summary` — Python, former lines 550-554

> WHY: total nilai harapan (expected value) - jumlah probabilitas
> terkalibrasi seluruh PART aktif, BUKAN hitungan kerusakan sungguhan
> (itu baru diketahui belakangan). Dipakai dashboard untuk kurva
> tangkapan kumulatif ("~200 teratas menangkap ~43 dari ~128 kerusakan
> bulan depan") - lihat CLAUDE.md §9 Halaman 1.

### `terminal_summary` — Python, former lines 590-595

> WHY: agregasi MURNI dari prediction per-PART yang sudah ada (frame) -
> TIDAK ada model/skor baru khusus terminal, sesuai permintaan (hindari
> overengineering). PART tanpa terminal yang bisa dipercaya (lihat
> _attach_terminal) TIDAK ikut - dilaporkan terpisah lewat
> terminal_overview()["parts_without_terminal"], bukan disembunyikan
> atau dipaksa masuk kelompok manapun.

## `src/partrisk/serving/single.py`

### `module` — Python, former lines 25

> ── ERRORS ──

### `module` — Python, former lines 57

> ── SETTINGS ──

### `module` — Python, former lines 64

> ── HISTORY ──

### `module` — Python, former lines 107

> ── RECOMMENDATION ──

### `module` — Python, former lines 211

> ── EXPLANATION ──

### `module` — Python, former lines 364

> ── MODEL_LOADER ──

### `survival_metadata` — Python, former lines 389-395

> WHY: baca metadata.json langsung (JSON ringan), BUKAN
> predict_survival.load_model() - itu juga men-deserialize seluruh
> models.joblib (RSF + Cox, berat) yang tidak dibutuhkan hanya untuk
> menampilkan metrik training di halaman admin. None (bukan exception)
> kalau artifact belum pernah dilatih - beda dari failure/scrap yang
> WAJIB ada (ModelUnavailable), Q1 survival memang advisory/opsional
> (docs/DECISIONS.md §1).

### `describe` — Python, former lines 448-451

> WHY: validation_metrics DECISIVE sejak docs/DECISIONS.md
> §13 (gerbang promosi digerbang di VALIDATION, bukan TEST
> lagi) - test_metrics informasional saja, sama seperti Q2
> di atas.

### `module` — Python, former lines 469

> ── PREDICTOR ──

### `_survival_advisory_fields` — directive note, former lines 547

> advisory, tidak boleh menjalar ke assessment utama

## `tests/conftest.py`

### `module` — Python, former lines 40-42

> WHY: di CI, test yang di-skip diam-diam lebih berbahaya daripada test yang
> gagal - hasilnya terbaca "semua lulus" padahal tidak ada yang benar-benar
> diuji. REQUIRE_DATABASE=1 mengubah ketidaktersediaan jadi kegagalan keras.

## `tests/test_api.py`

### `test_model_info` — Python, former lines 36-39

> WHY: docs/DECISIONS.md §13 - survival block dibaca dari metadata.json
> artifact (kalau ada), TIDAK diwajibkan seperti failure/scrap karena
> Q1 murni advisory (§1). validation_metrics HARUS ada kalau blok
> survival ada - itu yang jadi dasar keputusan promosi sejak §13.

### `test_daftar_rekomendasi` — Python, former lines 111-114

> WHY: official_queue_only=false - tes ini menguji perilaku daftar
> penuh (rank berurutan, tiap baris punya recommended_action/priority),
> bukan antrian resmi yang digerbang (docs/DECISIONS.md §11) dan boleh
> sangat pendek/kosong - lihat test_antrian_resmi_* untuk gerbangnya.

### `test_cors_aktif_saat_origin_didaftarkan` — Python, former lines 186-193

> WHY: reload menjalankan ulang SELURUH modul api/app.py, termasuk baris
> `CORS_ALLOW_ORIGINS = [... os.getenv(...) ...]` - monkeypatch langsung
> ke atribut akan DITIMPA oleh reload itu sendiri. Ubah env var supaya
> perhitungan ulang saat reload mengambil nilai yang benar (dulu
> db_pool.py dan logging_config.py terpisah dan tidak ikut ter-reload
> saat main.py di-reload) - tanpa menyimpan _pool/_configured, connection
> pool lama jadi yatim (tidak pernah ditutup) dan root logger dapat
> handler duplikat.

### `module` — Python, former lines 211

> ── AUTH ──

### `test_require_api_key_health_tetap_terbuka_walau_dikonfigurasi` — Python, former lines 245-246

> WHY: /health dipakai health-checker/orkestrator tanpa kredensial dan
> tidak membocorkan data bisnis - lihat docs/DECISIONS.md §15.

### `test_overview` — Python, former lines 288-289

> WHY: docs/DECISIONS.md §11 - ukuran antrian resmi (dinamis, boleh 0),
> bukan hitungan risk_level (itu label warna, tidak menyaring apa pun).

### `test_antrian_resmi_default_digerbang_dan_boleh_kosong` — Python, former lines 304-306

> WHY: docs/DECISIONS.md §11 - default TIDAK LAGI mengisi sampai
> kapasitas; hanya PART yang lolos gerbang presisi. 0 hasil adalah
> keadaan SAH (model abstain), bukan error - respons tetap 200.

### `test_endpoint_terminals` — Python, former lines 323-324

> WHY: terurut menurun berdasarkan risk (docs/DECISIONS.md §14) -
> cek monoton di sini supaya urutan tidak diam-diam berubah.

### `test_antrian_resmi_subset_dari_mode_eksplorasi` — Python, former lines 343-345

> WHY: official_queue_only=false HARUS mereproduksi (superset dari)
> daftar lengkap lama - antrian resmi tidak pernah menandai PART yang
> tidak ada di mode eksplorasi.

### `test_resolve_alert_membuka_alert_baru_kalau_masih_berisiko` — Python, former lines 367-371

> WHY: resolve TIDAK menjamin PART hilang dari antrian - kalau
> kondisinya masih memenuhi gerbang risiko, batch berikutnya membuka
> alert BARU (opened_at baru), bukan mempertahankan alert lama. Itu
> persis perilaku yang diminta: "dipromosikan kembali hanya jika masih
> memenuhi aturan risiko", bukan "hilang selamanya setelah di-resolve".

### `module` — Python, former lines 411

> ── LOCATIONS ──

## `tests/test_gate.py`

### `test_select_precision_constrained_threshold_infeasible_tidak_substitusi_diam_diam` — Python, former lines 34

> tidak ada sinyal sama sekali

### `test_select_precision_constrained_threshold_maksimalkan_recall_bukan_presisi` — Python, former lines 53-55

> WHY: threshold yang benar adalah yang memaksimalkan RECALL di antara
> semua threshold yang presisinya >= target - bukan threshold dengan
> presisi tertinggi (yang biasanya recall-nya lebih kecil).

### `test_select_precision_constrained_threshold_maksimalkan_recall_bukan_presisi` — Python, former lines 59-60

> threshold lebih rendah dari target=0.85 (lebih permisif) harus
> menghasilkan alert (dan recall) setidaknya sebanyak target lebih tinggi

### `test_honest_test_evaluation_murni_mengukur_bukan_mencari_ulang` — Python, former lines 70-71

> threshold beku, dipakai di "data lain" (di sini data yang sama, cukup
> untuk membuktikan fungsi ini tidak mencari threshold baru)

### `module` — Python, former lines 97

> ── LIFECYCLE (first-alert, Fase 8 Langkah A) ──

### `_lifecycle_dataset` — Python, former lines 102-105

> cycle A: alert PERTAMA (hari 0) salah alarm; baris "yang benar"
> (hari 30, tepat sebelum rusak) tidak pernah dipakai - dedup meniru
> production (sekali alert terbuka, tidak flag ulang sebelum
> diselesaikan). Harus jadi FALSE POSITIVE *dan* FALSE NEGATIVE.

### `_lifecycle_dataset` — Python, former lines 110

> cycle B: alert pertama TEPAT menangkap kerusakan - TRUE POSITIVE.

### `_lifecycle_dataset` — Python, former lines 113-114

> cycle C: skor tidak pernah lolos threshold, tapi memang rusak -
> FALSE NEGATIVE murni (tidak pernah dipromosikan sama sekali).

### `_lifecycle_dataset` — Python, former lines 117-118

> cycle D: lolos threshold tapi memang tidak pernah rusak -
> FALSE POSITIVE murni, tidak ikut menghitung recall.

### `test_lifecycle_metrics_dedup_hanya_pakai_alert_pertama` — Python, former lines 131-135

> A, B, C
> A, B, D
> hanya B
> A (alert pertamanya salah), D
> A (baris benarnya tidak pernah dipakai), C

### `test_lifecycle_metrics_lead_time_dari_alert_pertama` — Python, former lines 143-144

> WHY: satu-satunya true positive (cycle B) - lead time = failure_onset_on
> dikurangi observation_on ALERT PERTAMA (2026-01-01), bukan tanggal lain.

### `test_select_lifecycle_threshold_infeasible_tidak_substitusi_diam_diam` — Python, former lines 199

> tidak ada sinyal sama sekali

## `tests/test_lifecycle.py`

### `module` — Python, former lines 18

> ── SURVIVAL_CURVES ──

### `module` — Python, former lines 84-89

> ── PARITY ──
>
> WHY: file ini digabung dari test_survival_curves.py (murni matematika,
> tanpa database/model) dan test_parity.py (butuh keduanya) - pytestmark
> module-level TIDAK dipakai di sini supaya tidak ikut men-skip test kurva
> murni di atas. Setiap test parity di bawah ditandai sendiri-sendiri.

## `tests/test_pipeline.py`

### `module` — Python, former lines 17

> ── FEATURE_INTEGRITY ──

### `test_training_observations_horizon_days_mempersempit_jendela_target` — Python, former lines 242-245

> WHY: docs/EXPERIMENTS.md E-46/E-47/E-48 - gerbang presisi dibangun di
> atas kemampuan menguji horizon selain 30 hari tanpa mengubah default.
> Kerusakan terjadi 15 hari setelah observasi PERTAMA: horizon 30 hari
> (default) harus menandainya positif, horizon 7 hari TIDAK.

### `test_current_observations_menyaring_part_yang_sudah_dilepas` — Python, former lines 321-324

> WHY (docs/EXPERIMENTS.md E-71): cycle_end_reason RIGHT_CENSORED_AT_
> DATA_END tidak melacak RETURNED/DISMANTLED tanpa install ulang -
> PART begini harus DIBUANG dari populasi aktif, bukan tetap disebut
> "masih berjalan".

### `module` — Python, former lines 393

> ── PROMOTION ──

### `module` — Python, former lines 611

> ── GOLDEN_BATCH ──

