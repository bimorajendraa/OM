# Code Notes

Komentar penjelasan yang sebelumnya berada di file Python dalam `src/`, `dashboard/`, dan `tests/` dipusatkan di sini. Nomor baris adalah lokasi sebelum pemindahan dan dipertahankan untuk audit historis; gunakan nama scope sebagai rujukan stabil ketika kode berubah. Docstring dan string deskripsi runtime bukan komentar sehingga tetap berada di kode. Directive teknis seperti `# noqa` dan `# type: ignore` juga tetap berada di kode.

## `dashboard/api_client.py`

### `module` — Python, former lines 10-13

> WHY: docker-compose sudah meneruskan .env lewat env_file: (jadi ini
> no-op di situ), tapi menjalankan `streamlit run dashboard/app.py`
> langsung (dev native) TIDAK otomatis membaca .env tanpa panggilan ini -
> beda dari api/app.py yang sudah load_dotenv() sejak awal.

### `module` — Python, former lines 17-20

> WHY: kalau API_KEY diisi (lihat src/partrisk/api/app.py::require_api_key,
> docs/DECISIONS.md §15), API menolak semua request tanpa header ini -
> dashboard baca nilai yang SAMA dari .env (docker-compose meneruskan
> env_file yang sama ke kedua container).

### `_get` — Python, former lines 47-49

> WHY: "detail" fallback - respons 401 dari require_api_key() pakai
> HTTPException bawaan FastAPI ({"detail": ...}), beda dari skema
> error kustom aplikasi ({"status", "message"}) untuk rute lain.

## `dashboard/ui.py`

### `require_login` — Python, former lines 90-94

> WHY: opt-in seperti API_KEY (api/app.py::require_api_key) - password
> kosong di .env berarti dashboard tetap terbuka seperti sebelumnya
> (dev lokal tanpa konfigurasi tambahan). st.session_state bertahan
> antar halaman dalam satu sesi browser, jadi login sekali berlaku
> untuk seluruh halaman multi-page ini.

### `estimated_failure_month` — Python, former lines 183-186

> WHY: HANYA dari median_days_to_failure (Q1/RSF) - itu satu-satunya
> output model yang benar-benar berupa perkiraan TANGGAL. Q2 hanya
> memberi peluang per horizon tetap (30/60/90/120 hari), bukan proyeksi
> tanggal - memaksakan tanggal dari situ berarti mengarang angka.

### `priority_table` — Python, former lines 215-218

> WHY: to_numeric dulu, BUKAN langsung .round() - kolom ini SERING
> seluruhnya None dalam satu halaman (median cuma tersedia untuk
> ~5% PART aktif), pandas menyimpannya sebagai dtype object saat
> itu terjadi, dan .round() menolak dtype object.

### `survival_advisory` — Python, former lines 306-310

> WHY: border=True per metric (bukan cuma kolom telanjang) - Streamlit
> 1.61 mengukur/skalakan ulang font nilai st.metric() berdasarkan lebar
> kontainer, dan tanpa kotak sendiri, nilai pendek ("2 hari") di kolom
> lebar bisa membesar tidak proporsional (overflow) - box eksplisit
> memberi Streamlit ukuran yang stabil untuk dihitung.

### `survival_advisory` — Python, former lines 327-334

> WHY: overflow-x:auto ditarget ke data-testid chart INI SAJA
> (bukan style global) - Altair width="container" TERBUKTI
> tidak menyusutkan sumbu-x sampai 400+ hari di dalam vconcat
> (dicoba, chart tetap melebar native dan meluber keluar
> kotak walau width="stretch" dipasang) - solusi yang benar-
> benar bekerja adalah biarkan chart di lebar aslinya TAPI
> dibungkus kotak yang bisa digeser horizontal, bukan
> dipaksa menyusut (yang bikin tick 20-harian berdesakan).

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

## `src/partrisk/api/services.py`

### `module` — Python, former lines 21

> ── GEOCODING_SERVICE ──

### `module` — Python, former lines 25-33

> WHY: dulu dibatasi ketat ke Jabodetabek (asumsi seluruh lokasi client ada
> di sana) - cache geocoding sungguhan menunjukkan itu salah: ada stasiun
> nyata di Sumatera Utara (BINJAI, BATANG KUIS, BANDARKHALIPAH, ARASKABU)
> dan Banten pesisir (CILEGON) yang selalu ditolak walau namanya jelas dan
> valid, cuma karena di luar kotak sempit itu - bukan kasus nama ambigu.
> Diperluas ke seluruh Indonesia (bukan dihapus sama sekali) supaya
> proteksi anti-salah-pin tetap ada untuk kasus realistis (nama stasiun
> yang sama kebetulan ada di negara lain), sementara lokasi asli client di
> luar Jabodetabek tidak lagi ditolak begitu saja.

### `module` — Python, former lines 165

> ── MONITORING_SERVICE ──

### `failure_monitoring` — Python, former lines 234-239

> WHY: precision/recall/false-positive/false-negative/lead-time DI
> TINGKAT LIFECYCLE (bukan snapshot) - objective baru "maximize
> recall @ precision>=target, dievaluasi per first-alert" (docs/
> EXPERIMENTS.md E-49/E-54). gate.threshold TETAP row-level (belum
> diubah production - lihat WHY di train.py::compute_gate()), blok
> gate.lifecycle di sini murni informasional untuk monitoring.

### `failure_monitoring` — Python, former lines 265-270

> WHY: ukuran antrian resmi + jumlah alert OPEN saat ini (docs/
> DECISIONS.md §11, alert lifecycle di serving/alerts.py) - dinamis,
> boleh 0, BUKAN kuota tetap. "alert" di sini = promosi yang SUDAH
> dibuka dan belum diselesaikan (resolve-alert), bukan seluruh baris
> yang lolos gerbang tiap siklus batch - PART yang sama tidak
> dihitung berulang selama alertnya belum ditutup.

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

## `src/partrisk/core/features_survival.py`

### `module` — Python, former lines 13

> ── CATEGORICAL_SUPPORT ──

### `module` — Python, former lines 53

> ── INSTALL_CONTEXT ──

### `module` — Python, former lines 74

> ── TERMINAL_CONTEXT ──

### `module` — Python, former lines 90

> ── DYNAMIC_HISTORY ──

### `cumulative_cycle_age` — Python, former lines 102-107

> WHY: cumsum() dulu, BARU shift PER GRUP - shift() polos di atas hasil
> cumsum global membocorkan total item SEBELUMNYA ke baris siklus
> PERTAMA item berikutnya (frame sudah diurutkan lintas item). Bug nyata
> yang sempat terjadi: mengenai 19.239/24.045 baris pada percobaan
> pertama. groupby(...).shift(1) memastikan shift berhenti di batas
> tiap item.

### `module` — Python, former lines 200

> ── LANDMARKS ──

### `module` — Python, former lines 289

> ── LIFECYCLE ──

### `assign_lifecycle_outcome` — Python, former lines 372-377

> WHY: dibulatkan ke hari bulat - RandomSurvivalForest menyimpan satu
> titik kurva survival PER waktu unik DI SETIAP leaf node. Tanpa
> pembulatan ini, presisi sub-hari dari ratusan ribu timestamp berbeda
> membengkakkan grid waktu unik sampai ribuan titik - terbukti membuat
> artifact model >4 GiB. np.maximum menjaga durasi tetap positif untuk
> lifecycle yang sangat pendek.

### `module` — Python, former lines 384

> ── PREVIOUS_CYCLE ──

### `module` — Python, former lines 427

> ── BUILDER ──

### `point_in_time_support` — Python, former lines 461-467

> WHY: point_in_time_support(), BUKAN feature_builder.cumulative_support()/
> cumulative_support() langsung pada frame landmark - keduanya me-rank
> tiap BARIS dalam frame yang diberikan, dan satu lifecycle di sini
> menghasilkan banyak baris (landmark); dipakai apa adanya, dukungan akan
> menghitung landmark yang sama berkali-kali seolah banyak instalasi baru
> terjadi. Ini menghitung dukungan yang BENAR: jumlah LIFECYCLE (bukan
> baris landmark) dengan installed_on <= observation_on landmark ini.

### `compute_features` — Python, former lines 503-510

> WHY: feature_builder.build_features() menghitung part_model_category
> pakai config.MIN_PART_MODEL_SUPPORT=300 (threshold classification,
> skala 251rb baris) - BUKAN threshold 200 tervalidasi untuk skala
> survival. Jebakan ini SUDAH PERNAH terjadi di model statis (lihat
> docs/METHODOLOGY.md features.survival.builder). part_model_category
> jadi dihitung SENDIRI di sini lewat apply_threshold + `support` milik
> fungsi ini (threshold=200) - full["part_model_category"] dari
> feature_builder TIDAK dipakai sama sekali.

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

## `src/partrisk/engines/failure/train_mtbf_candidate.py`

### `module` — Python, former lines 34

> ── SKEMA WINDOW & SPLIT ──

### `default_split_boundaries` — Python, former lines 47-52

> WHY: TEST/VALIDATION panjang TETAP (120/90 hari), bukan pecahan window
> total - supaya ukurannya cukup untuk gate search tidak degenerate DAN
> tetap "baru" (dekat data_end) tiap kali dijalankan ulang bulan
> berikutnya. TRAIN otomatis tumbuh (MTBF_COVERAGE_START sampai
> validation_start) - itulah mekanisme "jarak ke v4 mengecil seiring
> waktu" yang dicatat di E-66.

### `module` — Python, former lines 72

> ── FITUR MTBF (point-in-time safe) ──

### `module` — Python, former lines 142

> ── DATASET ──

### `module` — Python, former lines 204

> ── TRAIN + BANDINGKAN vs v4 ──

### `module` — Python, former lines 278

> ── SIMPAN ──

### `main` — Python, former lines 328-334

> WHY: TIDAK memanggil argparse.parse_args() di sini - modul ini
> dipanggil dari dua jalur: langsung (`python -m ...train_mtbf_candidate`,
> sys.argv sudah bersih) DAN dari partrisk.cli's dispatch (sys.argv MASIH
> mengandung "train-mtbf-candidate" yang sudah dikonsumsi parser cli.py
> sendiri - parse_args() kedua di sini akan gagal "unrecognized
> arguments"). Perintah ini tidak punya argumen sungguhan, jadi aman
> dilewati sepenuhnya.

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

## `src/partrisk/engines/scrap/train.py`

### `main` — Python, former lines 251-257

> WHY: masih digerbang di TEST (SCRAP_TEST_START tetap, diintip ulang
> setiap retrain) - risiko TEST-leakage yang sama seperti failure model
> (docs/DECISIONS.md §10/§13), TAPI BELUM diperbaiki di sini karena
> scrap tidak punya split VALIDATION terpisah (positif TEST sudah
> cuma puluhan, E-45 - memecahnya lagi berisiko membuat evaluasi makin
> tidak bisa dipercaya). Dicatat sebagai risiko terbuka, bukan diabaikan
> - lihat docs/DECISIONS.md §13.

## `src/partrisk/engines/survival/curve.py`

### `module` — Python, former lines 15

> ── CURVES ──

### `calibrate_curve` — Python, former lines 66-71

> WHY: setiap titik grid harus jatuh TEPAT SATU region setengah-terbuka
> (h_lo, h_hi] - grid harian s/d 120 hari hampir pasti memuat titik
> PERSIS sama dengan horizon terlatih (t=60, t=90). Region '<'/'>' ketat
> di kedua ujung pernah jadi bug nyata: titik itu tidak tercakup region
> manapun, diam-diam terisi NaN. Assert di bawah menjaga ini gagal
> keras, bukan silent no-op, kalau celah serupa muncul lagi.

### `module` — Python, former lines 99

> ── METRICS ──

### `module` — Python, former lines 191

> ── MODEL_FIT ──

## `src/partrisk/engines/survival/predict.py`

### `_current_artifacts_dir` — Python, former lines 21-26

> WHY: resolusi via CURRENT (sama pola dengan engines/predict.py model
> kerusakan) - versi survival TERBARU yang lolos gerbang R3
> (engines/survival/train.py::decide_survival_promotion()), bukan lagi
> satu artifact tunggal yang ditimpa di tempat. Kalau CURRENT belum ada
> (belum pernah dilatih), kembalikan jalur yang pasti tidak ada -
> load_model() di bawah sudah menangani FileNotFoundError.

### `load_model` — Python, former lines 51-52

> WHY: n_jobs=-1 ter-unpickle dari joblib.load() bikin
> predict_survival_function() hang tanpa error - paksa n_jobs=1.

### `predict` — Python, former lines 101-104

> WHY: positional (bukan keyword) - query_cache.py mencocokkan cache key
> persis dari (args, kwargs); predict.py juga memanggil get_cycles/
> get_events positional untuk PART yang sama, kalau di sini keyword maka
> cache tidak pernah nyambung (query DB berulang).

### `predict` — Python, former lines 126-131

> WHY: attach_fleet_snapshot (BUKAN attach_fleet + get_cycles() tanpa
> filter) - versi lama memanggil get_cycles() fleet-wide di SETIAP
> request satu-PART; lewat jaringan Docker (host.docker.internal) itu
> terukur >50 detik per assessment. predict_failure.fleet_snapshot()
> sudah dicache per-proses (diinvalidasi via data_state) - reuse cache
> itu, jangan query fleet-wide kedua kalinya.

## `src/partrisk/engines/survival/train.py`

### `module` — Python, former lines 20

> ── DATASETS ──

### `build` — Python, former lines 56-59

> WHY: transform_for_model() butuh KEDUA kolom audit (confirmed-failure-
> mean DAN last-confirmed) walau hanya confirmed-failure-mean yang
> dipakai di fitur FINAL - merge tanpa last_confirmed_failure_lifetime
> akan KeyError di dalam transform_for_model().

### `module` — Python, former lines 123

> ── LANDMARK_EVAL ──

### `module` — Python, former lines 170

> ── OPERATIONAL_EVAL ──

### `compute_risk_30d` — Python, former lines 194-198

> WHY: kurva S(t) dihitung SEKALI per lifecycle unik (bukan per baris
> snapshot) - banyak baris TEST classification (grid 30-harian)
> berasal dari lifecycle yang SAMA. Tanpa dedup ini,
> predict_survival_function() pada puluhan ribu baris sekaligus bisa
> mengalokasikan >1 GiB.

### `module` — Python, former lines 230

> ── FAILURE_SURVIVAL ──

### `module` — Python, former lines 239

> WHY: n_jobs=-1 ter-unpickle bikin predict_survival_function() hang tanpa error

### `decide_survival_promotion` — Python, former lines 276-279

> WHY: split_label cuma label untuk pesan - lihat WHY yang sama di
> main() soal kenapa VALIDATION, bukan TEST, jadi dasar keputusan sejak
> docs/DECISIONS.md §13 (pola TEST-leakage yang sama dengan failure
> model, §10).

### `main` — Python, former lines 326-328

> WHY: RSF di-fit dengan target dikasarkan (baris di atas), tapi EVALUASI
> di sini tetap pakai y_train ASLI (tidak dikasarkan) supaya angka
> C-index/IBS/Brier/AUC jujur dan sebanding dengan konfigurasi riset lama.

### `main` — Python, former lines 344-349

> WHY: versi + CURRENT pointer (pola SAMA dengan models/failure,
> models/scrap - training_failure.current_version()/next_version() di-
> reuse apa adanya, bukan diimplementasi ulang) - kandidat SELALU
> disimpan sebagai versi baru untuk dibandingkan, CURRENT cuma pindah
> kalau lolos gerbang. Sebelumnya (sebelum versi ini ada) artifact
> ditimpa DI TEMPAT tanpa jejak versi lama - tidak "ter-track".

### `main` — Python, former lines 360-364

> WHY: digerbang di VALIDATION, bukan TEST - pola TEST-leakage yang sama
> dengan failure model berlaku juga di sini (docs/DECISIONS.md §10/§13):
> TEST yang sama akan dipakai berulang kali lintas retrain kalau dia
> jadi dasar keputusan. TEST tetap dihitung/dicetak di atas untuk
> laporan, bukan lagi dasar keputusan.

## `src/partrisk/serving/alerts.py`

### `module` — Python, former lines 8-14

> ── ALERT_LIFECYCLE ──
>
> WHY: in-memory saja, TANPA file/DB baru - permintaan eksplisit user
> (tidak mau menambah penyimpanan apa pun). Konsekuensinya: status alert
> RESET tiap kali proses API restart/deploy - semua PART yang masih
> gate_flagged akan terbuka lagi sebagai alert baru setelah restart. Ini
> trade-off yang disadari dan diterima, bukan bug yang perlu diperbaiki.

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

## `tests/test_serving.py`

### `module` — Python, former lines 37

> ── RECOMMENDATION ──

### `module` — Python, former lines 94

> ── EXPLANATION ──

### `module` — Python, former lines 204

> ── FRESHNESS ──

### `module` — Python, former lines 342

> ── ALERTS ──

### `test_alert_tidak_dibuka_ulang_selama_masih_open` — Python, former lines 372-374

> WHY: skor berubah pada siklus batch berikutnya, tetapi PART masih
> gate_flagged dan alertnya masih OPEN - snapshot skor/opened_at
> TIDAK boleh diperbarui, itu intinya "tidak boleh alert berulang".

### `test_open_lead_times_days_positif_untuk_alert_baru_dibuka` — Python, former lines 427

> baru dibuka, umurnya jauh di bawah 1 hari

### `module` — Python, former lines 430

> ── MONITORING ──

### `test_jumlah_high_live_dan_expected_konsisten_secara_struktur` — Python, former lines 503-505

> WHY: high_count_ratio_vs_training DIBULATKAN 3 desimal oleh
> services.py (round(..., 3)) sebelum sampai ke sini - toleransi
> harus mengikuti presisi pembulatan itu, bukan lebih ketat.

### `module` — Python, former lines 538

> ── SURVIVAL_METADATA ──

### `module` — Python, former lines 550

> ── TERMINAL ──

### `module` — Python, former lines 659

> ── GEOCODING ──

### `isolated_cache` — Python, former lines 664-666

> WHY: bukan fixture tmp_path bawaan pytest - di mesin ini tmp_path
> gagal karena folder temp bersama pytest tidak bisa dibersihkan
> (izin Windows), tidak terkait test ini sama sekali.

### `test_hasil_di_luar_indonesia_ditolak` — Python, former lines 718-722

> WHY: Bangkok, BUKAN kota Asia Tenggara sembarang - kotak Indonesia
> melebar sampai mencakup sebagian semenanjung Malaysia/Singapura
> (berbatasan langsung dengan Sumatera), jadi kandidat "di luar" yang
> valid untuk tes ini harus jelas di luar rentang lintang/bujur
> Indonesia (13,75 > batas utara 6,05), bukan cuma "negara lain".

### `test_hasil_di_luar_jabodetabek_tapi_dalam_indonesia_diterima` — Python, former lines 744-747

> WHY: cache geocoding nyata menunjukkan lokasi client ADA di luar
> Jabodetabek (Sumatera Utara: BINJAI/BATANG KUIS, Banten pesisir:
> CILEGON) - kotak Jabodetabek yang sempit dulu menolak lokasi valid
> ini begitu saja. INDONESIA_BBOX menerimanya selama masih di Indonesia.

### `module` — Python, former lines 841

> ── MAP_MARKERS ──

### `module` — Python, former lines 876-883

> ── DASHBOARD ──
>
> WHY: pytestmark module-level dan fixture autouse dari test_dashboard.py
> TIDAK dipakai apa adanya di sini - keduanya akan ikut membebani/men-skip
> test lain di file gabungan ini (recommendation, explanation, map_markers,
> dst yang tidak butuh database/model/internet maupun TestClient routing).
> Ditandai eksplisit per fungsi lewat needs_database/needs_models/
> needs_internet + usefixtures, bukan pytestmark/autouse global.

### `test_halaman_bisa_dirender` — Python, former lines 940-943

> WHY: authenticated=True - kalau DASHBOARD_PASSWORD terisi di .env
> (ui.py::require_login), halaman berhenti di layar login sebelum
> render apa pun. Test ini menguji halaman untuk pengguna yang SUDAH
> login, bukan gerbang login-nya sendiri.

### `test_filter_lokasi_terisi_otomatis_dari_peta` — Python, former lines 1003-1005

> WHY: tab Peta menulis session_state di bagian akhir skrip - baru
> dikonsumsi sebagai default oleh tab Antrian di AWAL skrip pada
> rerun BERIKUTNYA, sama seperti relay detail_item_id antar halaman.

### `test_filter_lokasi_terisi_otomatis_dari_peta` — Python, former lines 1011-1014

> WHY: berbeda dari relay detail_item_id (satu kali pakai antar
> halaman), pemilih lokasi di tab Peta TETAP terpilih antar rerun
> (widget biasa, tanpa key) - tautannya bertahan selama lokasi masih
> dipilih di sana, dan lepas begitu pemilih dikembalikan ke "-".
