# Metodologi

Diindeks per NAMA SIMBOL (modul/konstanta/fungsi), bukan nomor baris -
`grep NAMA_SIMBOL docs/METHODOLOGY.md` langsung menemukan penjelasannya.
Isi di sini dipindahkan dari docstring/komentar kode saat Fase 3 konsolidasi
(2026-08-23) - kode sendiri tidak lagi menyimpan narasi ini, lihat
`CLAUDE.md` §6 untuk aturannya.

---

## `data_reader`

Membangun ulang rantai pembersihan data dari TABEL MENTAH
(`journal`/`inventory`/`master`) memakai query SELECT saja - `production_ml`
tidak bergantung sama sekali pada schema `analytics` hasil research dan
tidak pernah membuat object apa pun di database (lihat `docs/DECISIONS.md`
§2). Rantai: `journal.t_item_journey` -> event bersih (kode dinormalisasi,
client & lokasi dikanonikalisasi) -> event operasional (buang RECON
administratif dan tanggal tidak valid) -> failure event (DISMANTLED +
CORRECTIVE, atau PREVENTIVE yang berakhir BROKEN/UNREPAIRABLE) ->
installation cycle (satu baris per pemasangan). Tanggung jawab modul ini
berhenti di MEMBACA - observasi/agregat/fitur dikerjakan `partrisk.features`.
Kolom era-research yang tidak dipakai 18 fitur final classification
(relocation/preventive/repair-process counts, window tak terpakai,
last_status, last_place, hierarchy TERMINAL, kolom audit kualitas data)
sengaja tidak dibawa.

### `_recon_context()`

Satu-satunya definisi RECON di modul ini - dipakai rantai utama maupun
query batas tanggal (`get_dataset_max_event_on`), supaya keduanya tidak
mungkin berbeda aturan.

### `_valid_operational_date()`

Tanggal yang masuk akal dipakai: bukan kosong, bukan sebelum sistem
pencatatan ada (`>= 1971-01-01`), dan bukan tanggal masa depan (salah
input).

### `_inventory_lookup_cte()` / `_matches_inventory()`

Identitas PART menurut inventory, dipakai memastikan model PART konsisten.
Dipakai bersama oleh `get_cycles()` dan `get_failure_episodes()` - ditulis
sekali di sini supaya keduanya tidak mungkin memakai aturan identitas yang
berbeda.

### `_canonical_map()` / `_build_text_maps()`

Nilai mentah dipetakan ke nama master lewat tiga tahap berurutan: cocok
persis -> alias yang sudah disetujui -> fuzzy match yang sangat mirip.
Tahap fuzzy penting, bukan kosmetik: **31% baris journey menuliskan nama
client dengan typo** (mis. "KERETE COMMUTER INDONESIA"), dan tanpa tahap
itu fitur `client_category` baris-baris tersebut akan jatuh ke UNKNOWN.
Nilai sumber yang cocok persis dengan >1 nama master (ambigu) tidak boleh
dipetakan otomatis kecuali ada alias eksplisit. Nilai yang tetap tidak
terpetakan sengaja dibiarkan absen dari hasil (jadi NULL -> UNKNOWN),
bukan ditebak. `_build_text_maps()` dihitung sekali per proses (jumlah
nilai unik kecil - ~5 client, ~156 lokasi - murah dihitung penuh, hasilnya
dipakai berulang oleh beberapa query).

### `_chain_sql(with_failures=...)`

`with_failures=False` melewati penentuan failure onset untuk query yang
memang tidak memerlukannya (mis. `get_dataset_max_event_on` - mencari
tanggal data terbaru), karena tahap itu jauh paling mahal di seluruh
rantai.

### `get_terminal_context()`

PART -> TERMINAL parent link PERSIS pada event INSTALLED yang membuka
tiap siklus. **Fitur eksperimental** - TIDAK dipakai model
classification/survival statis manapun, hanya dibaca `survival_model/
event_based/`; fungsi terpisah, tidak menyentuh `get_cycles()`/
`get_events()`.

Dibangun ulang dari tabel MENTAH (`journal.t_item_request_out`,
`master.t_mtr_item`, `inventory.t_item`) - mekanisme IDENTIK dengan view
riset `analytics.eda_part_terminal_cycle_link` (diverifikasi lewat
`pg_get_viewdef`, bukan ditebak): setiap PART yang diminta keluar gudang
dicatat `parent_serial_code`-nya (device tempat PART itu akan dipasang) -
baris itu dicocokkan ke event INSTALLED lewat (`host_serial_code`,
`wo_code`) yang sama. `parent_serial_code` berformat
`MODEL-PAIRING-REPAIR_SEQ`; bagian MODEL dicocokkan ke `master.t_mtr_item`,
bagian PAIRING diverifikasi ada di `inventory.t_item` - relasi hanya valid
kalau KEDUANYA cocok DAN kategorinya 'TERMINAL'
(`parent_link_quality_status`).

**Point-in-time**: `parent_link_quality_status='VALID_POINT_IN_TIME_RELATION'`
HANYA kalau baris `t_item_request_out` tercatat PADA ATAU SEBELUM
instalasi. Kalau baru tercatat SETELAHNYA
(`VALID_RELATION_RECORDED_AFTER_INSTALLATION`, **~43% populasi** pada
audit awal), relasi itu TIDAK boleh dipakai sebagai fitur di
`observation_on=installed_on` (baru "diketahui" belakangan) - **pemanggil
wajib menyaring status ini** sebelum memakai `terminal_type_clean`/
`terminal_model_name_clean`, sengaja tidak disaring di fungsi ini supaya
kebijakan penyaringan tetap eksplisit di kode pemanggil.

### `get_cycles(item_id, dataset_max_event_on)`

`dataset_max_event_on` wajib diisi kalau `item_id` diisi: batas waktu data
bersifat global, tidak boleh dihitung ulang dari riwayat satu item saja.

Satu lifecycle PART dibuka oleh `INSTALLED` dan ditutup oleh event pertama
di antara:

- failure onset (`cycle_end_reason='FAILURE'`),
- `DISMANTLED` non-failure,
- `RETURNED`, termasuk representasi histori lama `OK` + `RECEPTION`,
- `INSTALLED` berikutnya (`REINSTALL_WITHOUT_RECORDED_FAILURE`), atau
- batas data jika tidak ada penutup (`RIGHT_CENSORED_AT_DATA_END`).

Jika satu `DISMANTLED` sekaligus merupakan failure onset, `FAILURE` menang
pada timestamp/journey yang sama. Event failure yang baru terjadi setelah
PART `RETURNED`/`DISMANTLED` tidak ditempelkan ke lifecycle lama. Instalasi
setelah penutup selalu membuka cycle baru. Untuk training klasifikasi,
pelepasan non-failure adalah censoring: label negatif hanya confirmable
sampai `cycle_end_on - horizon`. Untuk survival, durasi censoring berhenti
tepat di `cycle_end_on`, bukan diteruskan ke cutoff split.

### `get_failure_episodes()`

Beda dari `get_cycles()`: satu siklus pemasangan hanya mencatat kerusakan
PERTAMA yang mengakhirinya, sedangkan di sini SEMUA kerusakan ikut -
termasuk yang terjadi sebelum pemasangan pertama tercatat (jumlahnya tidak
sedikit).

---

## `serving_batch`

Skoring SELURUH PART aktif sekaligus - dashboard bertanya "PART mana yang
paling perlu diperhatikan", bukan skor satu PART, jadi memanggil
`predict()` belasan ribu kali (belasan ribu query + belasan ribu potret
armada) tidak masuk akal. Data dibaca sekali, fitur dibangun sekali sebagai
DataFrame, model dijalankan pada semua baris sekaligus. Fitur dan
matematikanya SAMA PERSIS dengan `predict.py`/`predict_scrap.py` (fitur
kerusakan dari `feature_builder.project_features()` yang sama, perantaian
hazard urutan sama, kelompok risiko dari `_risk_level()` model masing-
masing, fitur scrap dari `scrap_features.build_features()` yang sama) -
satu-satunya yang ditulis ulang adalah penyusunan kolom mentah scrap untuk
banyak PART sekaligus (`scrap_features.current_state()` hanya melayani
satu PART/panggilan). Kesamaan dijaga `tests/test_parity.py`.

### `BatchScores.is_stale()`

Basi kalau umurnya lewat TTL, ATAU kalau database sudah bertambah
(generation berbeda) - dua-duanya perlu: batas umur mencegah hasil dipakai
selamanya, penanda generasi membuat data baru langsung terlihat tanpa
menunggu TTL habis.

### `_score_failure()`

Urutan panggilan sengaja identik dengan `predict.predict()`:
`current_observations` -> `attach_history` -> `attach_degradation_history`
-> `attach_fleet_snapshot` -> `attach_item_type_density_snapshot` ->
`part_model_support` -> `project_features` per langkah 30 hari.

`tier_score` = skor mentah langkah pertama (bukan probabilitas
terkalibrasi) - dipakai mengurutkan daftar, resolusinya lebih halus
daripada probabilitas terkalibrasi sehingga PART yang skornya berdekatan
tetap bisa dibedakan; kelompok risiko tetap dari
`failure_probability_30d` terkalibrasi.

Mengembalikan TIGA hal: skor, nilai fitur mentah (dipakai halaman detail
menjelaskan faktor risiko - sudah dihitung di sini, jauh lebih murah
daripada membaca ulang database untuk satu PART), dan `full_snapshot`
(sebelum direduksi ke `SOURCE_COLUMNS`) yang dipakai ulang
`_score_survival_advisory()` supaya tidak perlu menghitung ulang
`current_observations`/`attach_history` untuk model survival. Kolom
`DEGRADATION_FEATURES`/`LOCAL_DENSITY_FEATURES` dibuang dari
`full_snapshot` sebelum diserahkan ke survival - kalau ikut terbawa,
`attach_dynamic_extra()` milik survival (nama kolom sama untuk konsep
sama, mis. `cumulative_prior_cycle_days`) bentrok lewat `pd.concat` -
persis bug yang ditemukan `docs/EXPERIMENTS.md` E-28.

`episodes` diteruskan dari `_compute()` (sudah diambil di sana) supaya
potret density item_type dihitung LANGSUNG dari cycles/events/episodes
yang sudah di tangan - BUKAN lewat cache in-proses milik `predict.py`
(dirancang untuk request satu-PART berulang). Batch scoring sengaja di
luar `query_cache` dan cuma jalan sekali per `data_end` - lewat cache
`predict.py` di sini berarti fetch cycles/events/episodes redundan
(terukur: **~172 detik vs ~66 detik** tanpa redundansi).

### `_score_survival_advisory()`

Field ADVISORY murni: TIDAK dipakai menentukan `risk_level`/`tier_score`/
`rank` (itu urusan `_score_failure` saja). Kalau model survival belum
pernah dilatih, kolomnya tetap ada tapi seluruhnya `None` - bukan
kegagalan batch. `predict_survival.load_model()` dibungkus try/except di
sini secara sengaja - satu-satunya tempat error model survival ditelan,
supaya kegagalan advisory-only tidak pernah menjatuhkan seluruh batch
scoring.

### `_scrap_states()`

Kondisi "seandainya rusak sekarang" untuk banyak PART sekaligus - kolom
sama persis dengan `scrap_features.current_state()`, dihitung sekali lewat
groupby alih-alih satu PART per panggilan. Setiap kolom adalah terjemahan
langsung dari fungsi itu; `tests/test_parity.py` membandingkan keduanya
baris per baris. Urutan event dari `data_reader` (item, created_on,
journey_id) membuat `GroupBy.last()` setara `dropna().iloc[-1]` per item -
kebenaran hasil bergantung pada urutan itu tetap terjaga.

---

## `predict_survival`

CLI + library prediksi survival satu PART aktif (`python -m
partrisk.predict_survival <item_id>`), model event-based. Beda MENDASAR
dari model classification (`predict.py`): fitur dihitung pada
`observation_on = SEKARANG` (bukan `installed_on`), lewat mekanisme
landmark yang SAMA dengan training - PART yang baru saja kena corrective
bulan lalu terlihat berbeda dari PART sejenis yang tidak pernah
bermasalah, walau usia instalasinya sama. Konsekuensi matematika: kurva
`S(t)` model ini SUDAH dari `t=0=observation_on=sekarang`, jadi
`risk_Nd = 1 - S(N)` LANGSUNG - tidak perlu rumus `1-S(age+N)/S(age)`
seperti model baseline-instalasi.

`ARTIFACTS_DIR` (`engines/survival/predict.py::_current_artifacts_dir()`)
menunjuk `models/survival/<CURRENT>/` (kandidat compact A2, ~66 MB) -
versi + `CURRENT` pointer, POLA SAMA dengan `models/failure/`/
`models/scrap/` (`docs/DECISIONS.md` §6/§7), bukan lagi satu artifact
tunggal yang ditimpa di tempat (lihat §5b/§7 untuk kapan skema versi ini
mulai berlaku). Tetap TIDAK cutover CatBoost (`docs/DECISIONS.md` §1) -
versi di sini murni supaya retrain field advisory bisa ditelusuri/di-
rollback, bukan tanda model ini jadi mesin keputusan utama.

### `load_model()`

Melempar `FileNotFoundError` (BUKAN `SystemExit`) supaya pemanggil library
(`serving_batch.py`) bisa menangkapnya dan melewati field
advisory dengan aman kalau model belum pernah dilatih - CLI satu PART
(`main()`) yang menampilkan pesan `SystemExit` ke pengguna.
`calibrators` SELALU dimuat kalau filenya ada - artifact yang dilatih
sejak Fase A3 selalu punya file ini (`docs/EXPERIMENTS.md` E-23), jadi
`None` di sini menandakan artifact versi sangat lama, bukan kondisi
normal.

### `_calibrate_risk()`

Kalibrasi tiap horizon SENDIRI-SENDIRI (isotonic terpisah per horizon),
lalu cummax lintas horizon 30->120 - tanggung jawab PEMANGGIL kalibrator
karena isotonic per horizon BISA saling silang walau S(t) mentahnya
monoton turun (`docs/EXPERIMENTS.md` E-23). Kalau calibrators `None`
(artifact lama) atau nilai raw `None` (beyond follow-up), kembalikan
`None` apa adanya - tidak mengarang angka. Terpisah dari `risk_Nd` mentah
supaya kejujuran "ini sudah dikalibrasi atau belum" tetap eksplisit lewat
nama field, bukan ditimpa diam-diam.

### `predict()` - urutan pembangunan fitur

Landmark TUNGGAL = SEKARANG (`observation_on=dataset_max_event_on`) - SAMA
persis mekanismenya dengan satu baris landmark saat training
(`features_survival.py`), hanya satu titik waktu ("sekarang")
bukan banyak. `attach_dynamic_extra`/`audit_previous_cycle_features` groupby
per `item_identifier_clean`, dipakaikan `cycles_for_item` (riwayat PART INI
saja) bukan seluruh armada - baris item lain tidak pernah mempengaruhi
hasil baris item ini. Dukungan historis (`support`/`item_type_support`/
`terminal_support`) DIBEKUKAN saat training, bukan dihitung ulang dari 1
baris ini sendiri (alasan sama dengan `predict.py`).

Kurva TERKALIBRASI (Fase upgrade RSF, Langkah B - `docs/EXPERIMENTS.md`
E-41) dipakai untuk SEMUA turunan waktu (median/p90/kurva ditampilkan)
supaya konsisten dengan `calibrated_risk_Nd`, bukan kurva mentah -
fallback ke kurva mentah hanya kalau `calibrators` `None` (artifact sangat
lama).

Median SERING `None` (kebanyakan PART aktif belum cukup lama untuk S(t)
turun sampai separuh dalam rentang follow-up training, `docs/EXPERIMENTS.md`
E-40) - ambang 90% jauh lebih sering tercapai dan tetap actionable. Ambang
`days_until_risk_medium/high` (Langkah C rencana upgrade RSF) SENGAJA
memakai skala risiko yang sudah dikenal user dari CatBoost
(`config.FAILURE_MEDIUM/HIGH_PROBABILITY_THRESHOLD`, 0,15/0,25) - "kapan
kira-kira risiko versi RSF ini masuk MEDIUM/HIGH", BUKAN cross-reference
ke `failure_probability_30d` CatBoost (model TERPISAH) - cuma ambangnya
yang disamakan supaya gampang dipahami.

### `score_batch()`

`median_days_to_failure` untuk BANYAK PART aktif sekaligus - field
advisory dipakai `serving_batch.py` (mode aditif, TIDAK
menentukan risk_level/urutan). `rows` = potret PART aktif
(`feature_builder.current_observations(cycles, events)` - sejak
docs/EXPERIMENTS.md E-71, juga menyaring PART yang status TERBARU-nya
bukan INSTALLED); fitur event-based
dibangun PADA `observation_on` tiap baris lewat `training_survival`
(mekanisme SAMA dengan `predict()`, cuma divektorkan). Kurva penuh SENGAJA
tidak disertakan (payload batch akan melipat puluhan kali untuk field yang
jarang dibutuhkan di daftar prioritas) - tetap eksklusif endpoint satu
PART lewat `predict()`. `calibrators` (opsional) - kalau ada, median/p90
dibaca dari kurva TERKALIBRASI, KONSISTEN dengan `predict()`.

---

## `features_survival`

Fitur event-based: SAMA seperti fitur final model statis (lineage-nya
sudah dihapus - event-based menang di semua metrik operasional, lihat
`docs/EXPERIMENTS.md` E-24) DITAMBAH umur pemasangan
(`log_days_since_installation`/`installation_age_band`). Model statis dulu
men-drop 2 fitur itu karena SELALU konstan (umur=0, sebab
`observation_on==installed_on` selalu di sana). Di sini `observation_on` =
landmark, BUKAN `installed_on` lagi - umur pemasangan jadi sinyal UTAMA,
jadi DIPERTAHANKAN.

Reuse SEPENUHNYA (tidak ada logic baru untuk hal yang sudah ada):
`feature_builder.attach_history`/`attach_fleet` (sudah generik terhadap
`observation_on`, dipanggil dengan observation_on=landmark bukan
installed_on); `install_context.attach_install_context` (konteks instalasi
KONSTAN per lifecycle, benar untuk di-merge apa adanya ke semua landmark
lifecycle yang sama); `previous_cycle` (previous-cycle KONSTAN per
lifecycle, sama alasannya); threshold kategori (`FINAL_CATEGORY_THRESHOLDS`,
part_model=200/item_type=300) = angka sama dengan hasil sweep VALIDATION
model statis - TIDAK di-sweep ulang khusus populasi landmark (populasi
dasarnya sama, hanya jumlah baris per lifecycle yang berbeda; re-sweep
kandidat penyempurnaan lanjutan, bukan blocker).

**`point_in_time_support()` jebakan historis** (lihat WHY comment di
kode): dukungan historis TIDAK boleh dihitung lewat
`cumulative_support()` langsung pada frame landmark - fungsi itu me-rank
tiap BARIS, sedangkan satu lifecycle di sini menghasilkan banyak baris
landmark; dipakai apa adanya, "dukungan" menghitung landmark yang sama
berkali-kali seolah banyak instalasi baru terjadi.

**`compute_features()` jebakan threshold** (lihat WHY comment di kode):
`feature_builder.build_features()` menghitung `part_model_category`
memakai `config.MIN_PART_MODEL_SUPPORT=300` (threshold classification,
dikalibrasi skala 251rb baris) - BUKAN threshold 200 yang tervalidasi
untuk skala survival (`docs/EXPERIMENTS.md` E-03). Jebakan yang SAMA
PERSIS sudah pernah terjadi di model statis (lineage lama, sudah dihapus)
- karena itu `part_model_category` di sini dihitung ULANG sendiri lewat
`point_in_time_support()` + threshold 200, `full["part_model_category"]`
dari `feature_builder` tidak dipakai sama sekali.

Fitur DINAMIS TAMBAHAN (hasil ablation, konfigurasi
"G_combined_without_device", VAL t0-only RSF 0,7849 -> 0,7954, lihat
`docs/EXPERIMENTS.md` E-08 dan E-12): degradation trend + cumulative
physical usage + jendela corrective 60/90 hari, dihitung di
`features_survival.py`, ditempel via `attach_dynamic_extra()`.
Jendela 7/14 hari DICOBA lalu DIBATALKAN (regresi pada retrain penuh
dengan database fresh - `docs/EXPERIMENTS.md` E-14).

Fitur DEVICE/TERMINAL (`terminal_type_grouped`, konfigurasi
"F_combined_all", VAL t0-only 0,8036) - AWALNYA diambil dari schema
`analytics` (riset lama, dilarang produksi - `docs/DECISIONS.md` §2).
SEKARANG direproduksi APA ADANYA sebagai query kanonikal
`data_reader.get_terminal_context()` (diverifikasi angkanya PERSIS sama
dengan schema `analytics`) - production TIDAK lagi bergantung ke schema
itu sama sekali.

`attach_terminal_extra()`: dukungan/grouping `terminal_type_context`
SENGAJA TIDAK dihitung di fungsi ini - pola SAMA dengan
`part_model_category`/`item_type_at_install_grouped`: dukungan HARUS
dibekukan saat training dan dipakai ulang saat prediction (bukan dihitung
ulang dari satu baris, yang akan selalu memberi dukungan=1).

---

## `features_survival`

Bangun observasi landmark ("event-based survival"): BANYAK titik observasi
per lifecycle, bukan satu (`installed_on`) seperti model statis lama.
Objective beda dari model statis: "dengan kondisi PART SAAT INI (umur A,
riwayat sampai titik ini), berapa lama lagi sampai failure?" - bukan "saat
pertama dipasang, berapa lama PART akan bertahan?".

### Desain landmark: titik mana yang jadi observasi

Audit data (`docs/EXPERIMENTS.md` E-13) menemukan **80,3% dari 23.927
lifecycle TIDAK punya event operasional sama sekali** di antara INSTALLED
dan akhir siklus - kebalikan dari asumsi "banyak event menandai perubahan
kondisi". Landmark di sini adalah GABUNGAN tiga sumber, bukan murni event
organik:

1. **L=installed_on** (age=0) - SELALU ada, ekuivalen dengan satu-satunya
   observasi model statis, supaya event-based tetap punya baseline yang
   bisa dibandingkan head-to-head.
2. **Event organik** - operational event APA PUN pada item yang sama,
   STRICTLY di antara installed_on dan endpoint lifecycle - kalau ada
   (~20% lifecycle).
3. **Anchor jarang** (90, 180, 365 hari, lalu +365 hari, dibatasi
   `MAX_ANCHORS_PER_LIFECYCLE`) - SENGAJA bukan grid 30-harian tetap:
   cycle yang bertahan bertahun-tahun (umum di TRAIN, installed_on bisa
   2014) akan menghasilkan puluhan snapshot redundan kalau anchornya
   rapat - persis pola classification grid yang ingin dihindari. Interval
   MELEBAR (bukan tetap) supaya densitas anchor tinggi di awal umur
   (informasi paling berharga) dan menurun untuk ekor lifecycle yang
   sangat panjang.

`MAX_ORGANIC_PER_LIFECYCLE=8`: jaring pengaman untuk kasus langka (maks
teramati 16 event organik dalam satu lifecycle, `docs/EXPERIMENTS.md`
E-13) - kalau melebihi batas, ambil yang PALING BARU (paling relevan
dengan kondisi mendekati endpoint), bukan yang paling awal.

### Split & cutoff: mengikuti LIFECYCLE, bukan L masing-masing

**Keputusan desain PALING PENTING di modul ini**: split (TRAIN/VALIDATION/
TEST) dan cutoff administrative censoring sebuah landmark row ditentukan
oleh lifecycle induknya (`installed_on`, SAMA PERSIS dengan model statis
lewat `lifecycle.assign_lifecycle_outcome()`), BUKAN dihitung ulang dari L
masing-masing.

Alternatif yang DITOLAK: assign split per-L (mis. cycle yang installed_on
2020 dan masih aktif bisa punya landmark early di TRAIN, landmark 2025 di
VALIDATION, landmark 2026 di TEST). Itu tidak menghasilkan leakage
temporal (tiap L tetap hanya memakai fitur/label sampai L), TAPI membuat
SATU lifecycle fisik muncul di TRAIN *dan* VALIDATION/TEST via landmark
berbeda - model bisa "mengenali" identitas item lewat kombinasi fitur
unik lintas landmark, bukan cuma belajar generalisasi. README model
statis sudah mendokumentasikan risiko serupa (7,5% item beririsan split
lewat previous-cycle feature) sebagai leakage non-temporal yang diterima -
landmark per-L akan MEMPERBESAR risiko itu drastis. Desain di sini (split
ikut lifecycle) menghilangkan risiko itu sepenuhnya: lifecycle TRAIN yang
`installed_on`-nya lama (mis. 2014) tetap boleh menghasilkan landmark
sampai `validation_start` (2025) - umurnya panjang tapi TIDAK menyeberang
ke VALIDATION/TEST.

### Reuse total logika censoring - TIDAK ada aturan baru

`event_observed`/`duration_days` (dari install) untuk sebuah lifecycle
SUDAH final dari `lifecycle.assign_lifecycle_outcome()` (diimpor APA
ADANYA). Landmark HANYA menggeser titik ASAL pengukuran durasi dari
`installed_on` ke L:

```
age_at_landmark    = L - installed_on
duration_landmark  = duration_days - age_at_landmark   (residual)
event_landmark     = event_observed                    (tidak berubah)
```

berlaku selama `age_at_landmark < duration_days` (L terjadi sebelum
endpoint lifecycle - kalau tidak, L bukan landmark valid, dibuang). Tidak
ada percabangan FAILURE/CENSORED/EXCLUDE baru ditulis di sini - itu
sepenuhnya keputusan `lifecycle.py` yang sudah diaudit.

### `build_landmarks()` - detail implementasi

Event tepat DI `installed_on`/endpoint STRICT tidak dianggap landmark baru
(sudah tercakup L=0 / adalah endpoint itu sendiri). Umur event organik
dibulatkan ke hari bulat (konsisten dengan `duration_days`) + dedup -
beberapa event operasional (REQUESTED/ISSUED/DELIVERY) sering terjadi di
hari yang sama. Dedup final lintas ketiga sumber (anchor bisa kebetulan
sama dengan hari event organik) mempertahankan urutan prioritas
INSTALL > ORGANIC_EVENT > ANCHOR. Age harus STRICT < duration (residual
harus positif, >=1 hari - konsisten dengan invarian `duration_days >= 1.0`
di `lifecycle.py`). `landmark_source` (INSTALL/ORGANIC_EVENT/ANCHOR)
murni diagnostik, TIDAK dipakai sebagai fitur model.

---

## `training_failure`

`python -m partrisk.engines.failure.train`. Alur: database ->
observasi + target -> fitur -> latih -> evaluasi -> simpan. Setiap
dijalankan, hasil disimpan sebagai versi BARU di `models/vN/`. Model
production hanya diganti kalau versi baru tidak lebih buruk pada data uji
(lihat `docs/DECISIONS.md` §5a) - kalau lebih buruk, versinya tetap
tersimpan lengkap dengan metriknya untuk dibandingkan, tapi production
tetap memakai model lama.

### `assign_split()`

Split berdasarkan WAKTU, bukan acak - model harus diuji pada periode yang
belum pernah dilihatnya. Tahun terakhir jadi TEST, setahun sebelumnya
VALIDATION, sisanya TRAIN. Di antara blok ada jeda (embargo) selebar
horizon target: snapshot yang jawabannya baru terungkap di periode
berikutnya dibuang, supaya jawaban periode uji tidak bocor ke data latih.

### `evaluate_incumbent()`

Menjalankan model CURRENT (bukan kandidat) pada test split yang PERSIS
SAMA seperti kandidat, supaya keduanya dibandingkan pada window evaluasi
yang identik. Sebelumnya promosi membandingkan skor kandidat dengan
metrik LAMA yang tersimpan di metadata model production - dihitung pada
test split model itu SENDIRI saat ia dilatih. Karena `test_start`
dihitung ulang dari tahun `data_end` setiap kali retrain
(`assign_split()`), window itu bergeser maju setiap tahun - kandidat dan
incumbent akhirnya dibandingkan pada dua periode berbeda. Fungsi ini
menutup celah itu: incumbent dijalankan ulang pada data BARU, dibatasi ke
baris test split yang sama dengan kandidat.

### `active_part_scores()`

Peluang kerusakan 30 hari (terkalibrasi) seluruh PART yang saat ini masih
terpasang - populasi produksi sesungguhnya yang dihadapi `predict.py`,
bukan grid observasi data latih yang sudah tersaring aturan kelayakan
label dan jumlahnya jauh lebih sedikit.

### `choose_cutoffs()`

Ambang kelompok risiko: nilai tetap
`FAILURE_HIGH`/`MEDIUM_PROBABILITY_THRESHOLD` (`config.py`) pada
probabilitas kerusakan 30-hari yang sudah dikalibrasi - angka yang sama
persis dengan yang dibaca pengguna di layar. Bukan hasil optimasi
statistik, melainkan diturunkan dari kapasitas kerja yang ditetapkan
bisnis.

---

## `config`

Hampir semua angka/ambang di sini berasal dari hasil research yang sudah
terbukti di repository lama (`db_om_preparation`). Pengecualian:
`FAILURE_HIGH/MEDIUM_PROBABILITY_THRESHOLD` adalah keputusan operasional
dipilih belakangan dari sebaran probabilitas armada aktif sungguhan, bukan
dari research.

### `FEATURE_COLUMNS` (32 fitur - `CATEGORICAL_FEATURES` + `NUMERIC_FEATURES` + `FLEET_FEATURES` + `DEGRADATION_FEATURES` + `LOCAL_DENSITY_FEATURES`)

- **15 fitur dasar** (`NUMERIC_FEATURES`) bicara tentang PART itu sendiri.
- **`FLEET_FEATURES`** (3, jendela `FLEET_WINDOW_DAYS=90`) melihat keadaan
  di sekeliling PART: seberapa sering model PART ini rusak belakangan,
  berapa unit sedang terpasang. Beda dari `part_model_category` (identitas
  statis): laju armada tahu KONDISI TERKINI - menangkap cacat satu batch
  produksi, kohort yang menua bersama, atau masalah musiman. Terbukti
  menambah daya tebak: ROC-AUC 0,7947->0,8211, lift 6,05->6,86, 95% CI
  selisih PR-AUC [+0,0129, +0,0255] (seluruhnya di atas nol). Pada
  kapasitas 200 PART/bulan: 79 kerusakan tertangkap vs 66 sebelumnya.
- **`DEGRADATION_FEATURES`** (7, dibawa dari model event-based,
  `features.py` `attach_degradation_history`): umur fisik
  kumulatif siklus SEBELUMNYA (bukan cuma siklus berjalan), tren jarak
  antar-kerusakan (memburuk/membaik), jendela corrective 60/90 hari
  (melengkapi 30 hari yang sudah ada). Terbukti menaikkan ROC-AUC
  0,8211->0,8244, PR-AUC 0,1610->0,1884, DAN Recall/Presisi@kapasitas
  SEKALIGUS (jarang - biasanya trade-off) - promosi v3 (commit `30da7f8`).
- **`LOCAL_DENSITY_FEATURES`** (4, `features.py`
  `attach_item_type_density`): generalisasi `FLEET_FEATURES` (per
  `item_model_code_clean`) ke kategori lebih luas (`item_type_at_install`).
  Terbukti menaikkan ROC-AUC 0,8244->0,8319, PR-AUC 0,1884->0,1961, Brier
  turun, Recall/Presisi@kapasitas tidak turun - `docs/EXPERIMENTS.md`
  E-27. Dimensi client/place DICOBA dan DITOLAK di eksperimen yang sama
  (client murni merugikan, kombinasi client+place gagal gerbang PR-AUC) -
  jangan ditambahkan tanpa bukti baru.

### `MIN_OBSERVATION_DATE` / split waktu

Batas latih/validasi/uji TIDAK ditulis sebagai tanggal tetap:
`assign_split()` menghitungnya dari tahun terakhir yang ada di data
(dengan embargo selebar horizon target), supaya training ulang tahun
depan tetap menguji pada periode terbaru.

### `MIN_PART_MODEL_SUPPORT`

Tipe PART dengan riwayat < 300 observasi dikelompokkan jadi satu kategori
(`LOW_SUPPORT_LABEL`) supaya model tidak menghafal pola dari sampel yang
terlalu kecil. **Skala classification (251rb baris)** - JANGAN dipakai
untuk skala survival (~15rb lifecycle, lihat `docs/EXPERIMENTS.md` E-03,
threshold 200/300 terpisah), jebakan yang sudah pernah terjadi (lihat WHY
comment di `features_survival.py::compute_features`).

### `AGE_BAND_THRESHOLDS`

Umur bersifat pecahan, jadi ambang ditulis sebagai batas "lebih kecil
dari" persis seperti definisi SQL yang membuat data training: <91, <181,
<366, <731, <1461.

### `PREDICTION_HORIZON_DAYS`

Semua horizon kelipatan 30 hari supaya setiap titik adalah hasil hazard
chaining langsung, tanpa interpolasi.

### `FAILURE_HIGH_PROBABILITY_THRESHOLD` = 0.25 / `FAILURE_MEDIUM_PROBABILITY_THRESHOLD` = 0.15

Ambang tetap pada probabilitas kerusakan 30-hari YANG SUDAH DIKALIBRASI -
angka yang sama persis dengan yang dibaca pengguna di layar (bukan skor
mentah). **BUKAN dari research** - keputusan operasional diambil setelah
memeriksa sebaran probabilitas armada aktif sungguhan (~16.900 PART): PART
paling berisiko sekalipun jarang melewati ~27% pada horizon 30 hari, jadi
ambang 25%/15% dipilih sadar konsekuensinya - jumlah PART ter-flag
HIGH/MEDIUM akan JAUH di bawah kapasitas kerja tim (~200/bulan) dan bisa
naik-turun signifikan bulan ke bulan mengikuti kondisi armada, tidak lagi
tetap sejumlah kapasitas seperti sistem lama. Ubah SATU angka ini kalau
ambangnya perlu digeser, lalu retrain.

**Catatan**: kapasitas kerja 200/bulan jauh di atas jumlah PART yang
ter-flag HIGH/MEDIUM pada ambang ini (27+57=84 - FASE 7 P0-5, sudah
diaudit), jadi ambang ini TIDAK mengisi kapasitas. Pengurutan antrian
memakai `tier_score`, bukan `risk_level` - lihat `docs/DECISIONS.md` §9.

### `FAILURE_CAPACITY_PER_MONTH` = 200

BUKAN dasar kelompok risiko di atas (itu ambang tetap) - dipakai
`training_failure.py` menghitung Recall/Precision@kapasitas, metrik
yang membandingkan model kandidat vs production saat retrain (lihat
`docs/DECISIONS.md` §5a). Berapa PART per bulan yang sanggup diprioritaskan
tim, diukur pada data uji 2026 (~5.500 pemeriksaan PART/bulan):

| kapasitas/bln | ambang | presisi | tertangkap | berapa kali lebih tepat |
|---:|---:|---:|---|---:|
| 50 | 0,1365 | 29,4% | 145 dari 902 | 12,5x |
| 100 | 0,0994 | 20,3% | 267 dari 902 | 8,6x |
| **200** | 0,0882 | 16,6% | 329 dari 902 | 7,1x |
| 400 | 0,0450 | 7,4% | 496 dari 902 | 3,2x |
| 800 | 0,0372 | 7,4% | 633 dari 902 | 3,2x |

Default 200/bulan dipilih karena SETARA dengan aturan lama yang sudah
tervalidasi di research (>=3x base rate validasi: presisi 16,6%, recall
36,6%).

**Sejak `docs/DECISIONS.md` §11 (2026-08-25)**: konstanta ini HANYA
dipakai `decide_promotion()`/`capacity_metrics()` (dual-gate PR-AUC/
Recall@kapasitas lama, tetap dihitung untuk kontinuitas historis) -
BUKAN LAGI dasar `/api/v1/recommendations`. Antrian resmi sekarang
digerbang `FAILURE_GATE_TARGET_PRECISION`, lihat di bawah.

### `FAILURE_GATE_TARGET_PRECISION` = 0,40

Target presisi minimum untuk antrian resmi (`docs/DECISIONS.md` §11) -
BUKAN 0,85 yang diminta di awal, karena 0,85 terbukti tidak genuinely
generalize di TEST untuk model/horizon/data apa pun yang diuji
(`docs/EXPERIMENTS.md` E-46/E-47/E-48 - threshold presisi tinggi selalu
jatuh ke <10 baris VALIDATION paling ekstrem, kolaps ke 0 alert di TEST).
0,40 dipilih user secara eksplisit dari sweep threshold yang genuinely
generalize (E-47: presisi diutamakan di atas volume - "selama presisinya
tinggi tidak apa 2 bulan sekali"). Threshold aktual (bukan target) dicari
ulang setiap retrain dari VALIDATION (`train.py::compute_gate()`), diuji
sekali di TEST, disimpan di `metadata["gate"]["threshold"]` - BUKAN nilai
tetap. Model `v4` per 2026-08-25: threshold 0,3750, TEST presisi 0,625,
recall 0,0055, 8 alert.

---

## `training_survival`

`python -m partrisk.engines.survival.train`. Reuse TOTAL logic
fitting/evaluasi native dari `survival` (tidak diubah/disalin) -
satu-satunya beda dari classification adalah sumber data
(`training_survival`, banyak baris/lifecycle) dan
encoder/fitur (`features_survival`).

`COMPACT_RSF_PARAMS` = kandidat COMPACT pemenang Fase A2
(`docs/EXPERIMENTS.md` E-22/E-24), BUKAN default riset penuh di
`survival`. Target `duration_days` yang dilihat `RSF.fit()`
DIKASARKAN (resolusi harian s/d 120 hari - horizon kontrak API, kelipatan
60 hari di atasnya) - evaluasi (C-index/IBS/Brier/AUC) TETAP pakai
`duration_days` ASLI, HANYA yang dilihat `.fit()` yang dikasarkan.
Diverifikasi A2: artifact 5,26 GB -> 66,2 MB (79x) dengan C-index
VALIDATION yang JUSTRU lebih baik (grid lebih kasar bertindak sebagai
regularisasi). `n_estimators=50, min_samples_leaf=100` (vs default
100/30) lewat parameter `params` di `model_fit.fit_models()`, BUKAN
mengubah `DEFAULT_RSF_PARAMS` (tetap dipakai skrip riset lama/model lain
yang belum eksplisit override).

Kalibrasi 4 isotonic regressor (horizon 30/60/90/120, populasi
VALIDATION) + cummax lintas horizon disimpan sebagai `calibrators.joblib`
(Fase A3, `docs/EXPERIMENTS.md` E-23), dipakai `predict_survival.py`
untuk SELURUH kurva (bukan cuma 4 titik horizon diskrit -
`docs/EXPERIMENTS.md` E-41/Langkah B).

Mode ADITIF (`docs/DECISIONS.md` §1) - artifact disimpan versi
(`models/survival/vN/` + `CURRENT`, sejak `docs/DECISIONS.md` §7 update),
POLA SAMA dengan `models/failure/`/`models/scrap/`: retrain SELALU
disimpan sebagai versi baru, `CURRENT` cuma pindah kalau lolos gerbang R3
(`decide_survival_promotion()` di bawah) - beda dari sebelumnya (artifact
tunggal ditimpa di tempat, tidak ada jejak versi gagal/lama).

Kebijakan retrain (Fase R3, `docs/EXPERIMENTS.md` E-39): model ADVISORY -
tidak mengatur ranking/urutan inspeksi (tetap CatBoost), jadi TIDAK perlu
retrain tiap minggu. Retrain wajar: bulanan, atau kapan pun `data_end`
sudah bergeser jauh (>60 hari) dari training terakhir - retrain lebih
sering tidak salah, hanya tidak perlu.

Gate promosi (`decide_survival_promotion()`, `docs/DECISIONS.md` §5b):
RINGAN - bukan dual-gate PR-AUC/Recall seperti CatBoost, karena model ini
tidak dipakai untuk ranking. Kandidat menggantikan artifact production
HANYA kalau Brier@30d DAN Brier@90d VALIDATION tidak memburuk (dipindah
dari TEST - lihat `docs/DECISIONS.md` §13). Kalau gagal, artifact LAMA
tetap dipakai - training tidak menimpa file diam-diam.

---

## `serving`

Prediksi untuk SATU PART, lapisan HTTP. Membungkus `predict.py`/
`predict_scrap.py` apa adanya - tidak ada fitur dihitung ulang, tidak ada
ambang ditentukan di sini. `_translate()`: ML core melempar SATU jenis
error (`ItemNotScorable`) baik untuk "PART tidak ada" maupun "PART ada
tapi tidak bisa dinilai", HTTP status keduanya harus beda - `_exists()`
membedakannya lewat query terpisah.

`_survival_advisory_fields()`: kegagalan APA PUN pada model survival
(belum dilatih, PART tidak scorable) menghasilkan field kosong dengan
alasan, bukan exception yang menjalar ke `get_part_assessment()` - field
ini murni advisory (`docs/DECISIONS.md` §1).

`_feature_row()`/`_active_snapshot()`: diambil dari hasil batch scoring
kalau ada dan masih segar (fitur SELURUH PART aktif sudah dihitung di
sana, dijamin pada batas waktu data yang berlaku sekarang) - kalau batch
belum pernah jalan, snapshot dibangun untuk satu PART saja (jauh lebih
murah daripada memaksa seluruh armada diskor).

---

## `predict`

Hazard chaining: model 30-hari yang sama dipakai berulang, fitur waktu
dimajukan 30 hari tiap langkah, peluang bertahan dikalikan berantai:
`P(rusak dalam 30k hari) = 1 - hasil kali (1 - hazard tiap langkah)`. Ini
menjamin risiko 30<=60<=90<=120 hari secara matematis, dan pada pengujian
research terbukti lebih akurat daripada melatih model terpisah per
horizon.

`_fleet_snapshot()`: perlu riwayat kerusakan SELURUH model PART (bukan
hanya PART yang ditanyakan) - membangunnya dari nol makan waktu ~45 detik,
jadi potret hasil training dipakai ulang SELAMA data belum bertambah;
begitu ada kejadian baru, dihitung ulang (lihat `clear_fleet_cache()`,
dipanggil dari `data_state.py` saat data terbukti bertambah -
`_item_type_density_snapshot()` ikut dibuang bersamaan, sumber datanya
sama).

---

## `api_services`

Koordinat lokasi lewat OpenStreetMap Nominatim, dengan penyaringan ketat.
Nama lokasi di database bukan alamat lengkap - hanya nama singkat
("STASIUN JUANDA", "GUDANG NI") - dan geocoding otomatis polos TERBUKTI
berbahaya: dicoba langsung, "SERVICE CENTER" memang ketemu, tapi nyangkut
ke bangunan retail di Semarang, bukan gudang servis di Jakarta. Pin salah
tempat lebih menyesatkan daripada tidak ada pin sama sekali untuk
keputusan operasional.

Penyaringan: (1) `_looks_like_public_station()` - nama yang tidak berpola
"STASIUN ..."/"...(KA BANDARA)" TIDAK PERNAH dikirim ke Nominatim sama
sekali (bukan cuma disaring setelah hasil kembali), supaya tidak ada
peluang kebetulan ketemu tempat yang salah. Diperiksa terhadap SELURUH 153
lokasi di data: 142 berpola "STASIUN ...", 6 "...(KA BANDARA)" (stasiun
kereta publik, datanya ada di OSM); sisa 5 nama ("GUDANG NI", "SERVICE
CENTER", "DIPO DEPOK", "IT KCI JUANDA", "SRASIUN RAWA BUAYA" - typo)
adalah fasilitas internal/typo. (2) `_within_jabodetabek()` - koordinat
harus jatuh di `JABODETABEK_BBOX`, diturunkan dari cakupan operasi client
tercatat (KCI, LRT Jabodebek, Railink bandara), bukan angka dikarang.
Batas barat sengaja dipepetkan ke 106.23 (bukan dibulatkan lebar): cukup
mencakup Rangkasbitung (106.2516, ujung jalur KRL Commuter Line - batas
lama 106.30 salah membuang stasiun ini), tapi tetap membuang stasiun jalur
Merak (bukan Commuter Line) yang nama tempatnya juga muncul di data dan
geografis berdekatan (Walantaka 106.2188 paling dekat, lalu Serang/
Karangantu/Cilegon/Krenceng/Merak lebih barat lagi) - batas diverifikasi
terhadap koordinat asli tiap nama tempat, bukan ditaksir dari peta.
Lokasi yang gagal lolos saringan TIDAK ditampilkan sebagai pin -
dilaporkan terpisah sebagai "belum punya koordinat".

Cache disk (`.cache/geocode.json`) - nama lokasi tidak berubah dari hari
ke hari. Mematuhi kebijakan Nominatim: User-Agent deskriptif, maksimum 1
req/detik (`MIN_SECONDS_BETWEEN_REQUESTS`), hanya untuk lokasi belum ada
di cache. `_resolve_one()`: kegagalan jaringan (bukan bukti lokasi tidak
ada) TIDAK ditandai `checked_at`, supaya dicoba lagi nanti - beda dari
kegagalan penyaringan (permanen, `retry: False`).

---

## `survival`

Kurva survival: dari objek `StepFunction` scikit-survival ke array yang
mudah dievaluasi pada usia berapa pun. `survival_curve_arrays()` mengambil
grid manual (bukan lewat `StepFunction.__call__`) supaya bisa
mengekstrapolasi rata di luar rentang training, alih-alih melempar
`ValueError`. `eval_survival_at()`: t sebelum grid pertama -> 1.0
(S(0)=1 by definition); t melewati grid terakhir -> nilai terakhir yang
diketahui (ekstrapolasi RATA, bukan ditebak turun/naik - keterbatasan
diketahui, bukan presisi palsu). `step_eval_matrix()` step function (nilai
konstan di antara event), BUKAN interpolasi linear - S(t) memang turun
tangga.

`conditional_risk()`: `P(failure<=age+horizon | selamat sampai age) =
1 - S(age+horizon)/S(age)` - cara standar memakai kurva survival (dilatih
dari t=0=installed_on) untuk subjek yang SUDAH berjalan sebagian, dipakai
model baseline-instalasi (bukan event-based, yang kurvanya sudah
t=0=sekarang).

`calibrate_curve()` (Langkah B, `docs/EXPERIMENTS.md` E-41): S(t)
TERKALIBRASI di SELURUH grid, bukan cuma 4 titik horizon
(`predict_survival.py::_calibrate_risk()`) - dibutuhkan karena
`median_survival_time()`/`survival_time_at_threshold()` dipanggil pada
SELURUH kurva. raw_risk(t)=1-S(t) dipetakan lewat isotonic per horizon
terlatih (tidak dilatih ulang), interpolasi LINEAR antara dua horizon
terdekat, flat-extrapolation di ujung, cummax WAJIB di SELURUH grid
(kalibrasi per-titik tidak menjamin hasil interpolasi tetap monoton walau
tiap calibrator sendiri monoton).

`survival_time_at_threshold()`: tidak diekstrapolasi kalau kurva belum
turun sampai `threshold` dalam rentang follow-up training - lebih baik
tidak menjawab daripada menebak. Ambang tinggi (0,9) tercapai jauh lebih
sering daripada ambang rendah (0,5="median", `median_survival_time()`) -
`days_until_survival_90pct` karena itu field yang jauh lebih sering
terisi dan tetap actionable ("berapa hari lagi sampai risikonya mulai
naik", bukan "kapan separuh populasi ini gagal").

---

## `features_survival`

Fitur dynamic TAMBAHAN event-based: degradation trend + cumulative
physical usage lintas siklus + jendela corrective 60/90 hari. Semua
dihitung point-in-time terhadap `observation_on` tiap landmark, mekanisme
SAMA (searchsorted/cumsum per item) dengan `feature_builder.attach_history`
- tidak ada logic leakage baru, hanya jendela/statistik tambahan.

`cumulative_cycle_age()`: `cumulative_prior_cycle_days` = total hari FISIK
SEMUA siklus SEBELUMNYA item yang sama (durasi asli `cycle_end_on -
installed_on`, APA PUN cara berakhirnya) - beda dengan
`previous_cycle_confirmed_failure_lifetime_mean` yang SENGAJA hanya
menghitung siklus FAILURE: di sini pertanyaannya "berapa lama fisik part
ini sudah dipakai", bukan "berapa lama part sejenis biasanya bertahan
sampai gagal" - end-reason TIDAK relevan, part yang direinstall tanpa
failure tercatat TETAP benar-benar sudah aus fisik selama siklus itu.
Dihitung dari populasi PENUH `data_reader.get_cycles()`, BUKAN dibatasi
cohort eligible.

`corrective_degradation_trend()`: `failure_interval_trend_ratio` =
last/mean jarak antar kejadian `is_failure_onset` sebelum
`observation_on` - **di bawah 1 berarti jarak terbaru LEBIH PENDEK dari
rata-rata historis (memburuk/makin sering rusak)**, di atas 1 = membaik.
Butuh >=3 kejadian (>=2 interval) untuk dihitung; kurang dari itu diisi
0/False, BUKAN diasumsikan stabil.

`windowed_corrective_extra()`: jumlah corrective N hari terakhir sebelum
`observation_on`, melengkapi `prior_corrective_30d` yang sudah ada.
Jendela 7/14 hari SEMPAT dicoba (`docs/EXPERIMENTS.md` E-14) - ablation di
atas CACHE lama terlihat menang, TAPI retrain penuh dengan database FRESH
menunjukkan REGRESI di semua metrik - DIBATALKAN. Fitur jendela SANGAT
pendek butuh validasi ulang pada snapshot data yang SAMA PERSIS dengan
retrain akhir, tidak cukup divalidasi sekali di cache yang bisa basi.

---

## `config`

Model kedua: "kalau sudah rusak, apakah PART itu masih bisa diperbaiki" -
terpisah dari model failure ("kapan PART akan rusak"), tidak saling
menggantikan.

### `SCRAP_ERA_START` = "2025-04-01"

Status `UNREPAIRABLE` baru dipakai sejak tanggal ini bersama proses repair
detail. Sebelum itu PART yang dibuang tidak bisa dibedakan dari PART yang
sekadar hilang dari catatan, jadi tidak boleh ikut dilatih.

### `SCRAP_EMBARGO_DAYS` = 30

Bukti "dibuang" muncul cepat (median 2,9 hari), bukti "diperbaiki" lewat
pemasangan ulang jauh lebih lambat (p80 = 30 hari). Tanpa embargo, periode
terbaru akan tampak penuh kerusakan fatal semata-mata karena bukti
selamatnya belum sempat muncul.

### `SCRAP_CAPACITY_PER_MONTH` = 3

**Angka keputusan bisnis, bukan hasil hitungan statistik.** Ambang risiko
diturunkan dari sini: model mengurutkan seluruh kerusakan, sebanyak
kapasitas ini yang ditandai HIGH. Kenapa kapasitas, bukan balanced
accuracy: balanced accuracy diam-diam menganggap satu scrap yang kelewat
sama ruginya dengan satu salah alarm - di lapangan tidak begitu, yang
membatasi adalah berapa banyak PART yang sanggup disiapkan penggantinya
lebih awal.

Diukur pada data uji 2026 (~106 kerusakan masuk bengkel/bulan):

| kapasitas/bln | ambang | presisi | tertangkap |
|---:|---:|---:|---|
| **3** | 0,68 | 42,1% | 8 dari 21 |
| 5 | 0,64 | 30,8% | 8 dari 21 |
| 10 | 0,58 | 18,2% | 8 dari 21 |
| 15 | 0,52 | 16,7% | 10 dari 21 |
| 30 | 0,47 | 12,0% | 14 dari 21 |

Kapasitas 3-10 TIDAK menambah tangkapan sama sekali (tetap 8 dari 21),
hanya menurunkan presisi - jadi hanya dua titik yang masuk akal: 3/bulan
(daftar pendek, tajam) atau 30/bulan (mengejar tangkapan sebanyak
mungkin). Default 3/bulan: daftarnya pendek, hampir separuhnya benar-benar
dibuang (42,1% vs 6,5% kalau menebak acak), dan realistis dikerjakan.

### `SCRAP_ROLLING_CUTOFFS`

Titik potong untuk MEMERIKSA (bukan memilih) model. Semuanya wajib lebih
awal dari `SCRAP_TEST_START` - kalau ada yang menyentuh periode uji, angka
akhirnya tidak lagi jujur.

### `SCRAP_MODEL_NAME` = "Gabungan LogReg + RF"

Model DITETAPKAN DI MUKA, tidak dipilih dari data. Fold pemeriksaan hanya
berisi 7 dan 2 kejadian "dibuang" - PR-AUC pada sampel sekecil itu nyaris
acak, sehingga "memilih model terbaik" darinya sama saja memilih dari
derau (terbukti saat dicoba: pemenang fold justru yang paling buruk di
data uji). Yang dipakai adalah rata-rata regresi logistik + random forest
- dasarnya prinsip (keduanya salah dengan cara berbeda, merata-ratakan
model yang salahnya tidak searah menurunkan ragam tanpa perlu bukti dari
sampel kecil), bukan angka. Tabel perbandingan tetap dicetak
`train_scrap.py` sebagai pemeriksaan, supaya kalau ada kandidat unggul
jauh melampaui derau, itu terlihat.

---

## `features_survival`

Satu baris per lifecycle (installation cycle), duration/event dihitung
terhadap batas administrative censoring MASING-MASING split (bukan satu
cutoff global). Sumber `data_reader.get_cycles()` - modul ini TIDAK
membangun ulang lifecycle dari event mentah, hanya menurunkan label
survival dari tabel yang sudah ada.

**Kenapa cutoff per-split**: kalau lifecycle TRAIN yang dipasang lama
tetap disensor di tanggal data TERBARU, labelnya diam-diam membawa
informasi tentang apa yang terjadi sepanjang periode VALIDATION/TEST -
versi survival dari alasan model classification butuh embargo. Solusi:
tiap baris disensor pada batas SPLIT-nya sendiri (`validation_start`
untuk TRAIN, `test_start` untuk VALIDATION, `data_end` untuk TEST),
dihitung ulang dari fakta yang sudah ada di `cycle_end_on`/
`failure_onset_on` - bukan exclude berbasis embargo seperti
classification, karena durasi survival tidak punya window tetap.

`assign_lifecycle_outcome()` - aturan (sama untuk ketiga split; untuk
TEST, cutoff=data_end otomatis identik dengan cutoff global lama):

- `failure_onset_on` ADA dan `<= cutoff`: `event=1`,
  `duration = failure_onset_on - installed_on` (fakta historis).
- `cycle_end_on > cutoff`: `event=0`, `duration = cutoff - installed_on`
  (bukti langsung: masih berjalan tepat di cutoff, apa pun nasib
  akhirnya - REINSTALL atau RECON ambigu setelah cutoff jadi tidak
  relevan).
- Selain itu (cycle sudah berakhir pada/sebelum cutoff, tanpa failure):
  `RETURNED`/`DISMANTLED` adalah censoring teramati, jadi `event=0` dan
  `duration = cycle_end_on - installed_on`. `RIGHT_CENSORED_AT_DATA_END`
  juga dipakai bila `is_recon_verified_negative_eligible`; akhir
  `REINSTALL_WITHOUT_RECORDED_FAILURE` tetap EXCLUDE karena status pada
  cutoff tidak bisa dipastikan.

`lifecycle_split_bounds()`/`assign_lifecycle_split()`: formula SAMA dengan
`train.assign_split()` classification, supaya kedua model diuji pada
window kalender yang sama persis - split berdasar `installed_on` (awal
lifecycle), bukan `observation_on`, karena unit datanya sudah
lifecycle-level (tanpa embargo bergaya classification).

`cohort_cycles()`: filter (identitas model PART konsisten + durasi
positif) SAMA dengan cohort model classification
(`feature_builder.training_observations`), supaya `part_model_category`
berarti hal yang sama di kedua model.

---

## `training_scrap`

`python -m partrisk.engines.scrap.train`. Kandidat sengaja dibatasi model
sederhana + diregularisasi (kejadian scrap sedikit, kerumitan terbukti
menurunkan performa sesungguhnya walau angka validasi naik). Pemilihan
model final memakai PR-AUC rolling-origin (`config.SCRAP_ROLLING_CUTOFFS`)
- lihat `docs/DECISIONS.md`/`config::SCRAP_MODEL_NAME` untuk kenapa
model ditetapkan di muka, bukan dipilih dari tabel perbandingan.

`fit_calibrator()`: model dilatih dengan bobot kelas diseimbangkan, jadi
keluarannya berkisar 0,3-0,7 padahal kenyataannya hanya ~3% kerusakan
berakhir dibuang. Regresi logistik satu-variabel (Platt scaling)
memetakannya ke skala wajar: rata-rata keluaran turun dari 41,2% ke 2,5%,
Brier membaik 3x. Sengaja BUKAN isotonic seperti model kerusakan - dengan
kejadian sesedikit ini isotonic hanya menghasilkan 8 nilai berbeda dan
merusak urutannya (ROC-AUC 0,762 -> 0,699); sigmoid monoton jadi urutan
dijamin utuh.

`choose_cutoffs()`: ambang dari kapasitas kerja bisnis (sama prinsip
dengan `config::SCRAP_CAPACITY_PER_MONTH`), dihitung dari prediksi
out-of-fold data LATIH - data uji tidak boleh ikut memilih ambang.

`evaluate_incumbent()`: pola sama dengan
`training_failure::evaluate_incumbent()` - metrik lama
tersimpan dari window uji `SCRAP_TEST_START` saat model lama dilatih,
sementara data terus bertambah, jadi incumbent dievaluasi ulang pada data
BARU untuk perbandingan adil dengan kandidat.

---

## `scrap_features`

Label DIPUTUSKAN dari dua sumber bukti, bukan hanya vonis bengkel: DIBUANG
(vonis `UNREPAIRABLE`/`BROKEN`); DIPERBAIKI (vonis `REPAIRED`, ATAU PART
yang sama terbukti dipasang kembali - bukti langsung PART kembali dipakai);
TIDAK BISA DILABELI (bukan keduanya - bisa dibuang tanpa dicatat, bisa
masih di bengkel, tidak ada cara membedakan). Memakai vonis bengkel saja
akan membuang ratusan episode yang sebenarnya sudah terbukti selamat.

`resolve_outcomes()`: vonis hanya berlaku sampai episode berikutnya
dimulai (pemasangan ulang atau kerusakan berikutnya menutup episode ini) -
vonis boleh tercatat pada detik yang sama dengan kerusakannya.
`_first_after()`: perbandingan memakai pasangan (waktu, journey_id) bukan
waktu saja - beberapa event bisa tercatat pada detik yang sama, journey_id
yang menentukan urutannya. Embargo (`SCRAP_EMBARGO_DAYS`): dekat ujung
data, vonis "dibuang" sudah terlihat sementara bukti "diperbaiki" lewat
pemasangan ulang belum tentu sempat muncul.

`current_state()`: kondisi PART hari ini dalam bentuk yang sama seperti
episode - dibaca sebagai "seandainya PART ini rusak sekarang", kolom
sengaja sama persis dengan training supaya `build_features()` tidak perlu
tahu bedanya.

`build_features()`: `log_age_total` = umur TOTAL PART, bukan umur siklus
ini saja - PART tua yang baru pertama kali rusak justru yang paling sering
langsung dibuang. `log_prior_repaired_count`: PART yang pernah berhasil
diperbaiki terbukti masih bisa diperbaiki lagi.

---

## `predict`

Dua cara pakai, hanya yang pertama sudah teruji kuat: (1) saat PART baru
saja rusak - pemakaian utama, ketahuan perlu siapkan pengganti tanpa
menunggu vonis bengkel (~3 hari); teruji ROC-AUC 0,76, 3,9x lebih baik
dari menebak. (2) untuk PART yang masih sehat, dibaca "seandainya rusak
besok", digabung model 30-hari lewat `predict_death_risk()` - sudah
dibacktest pada 74.412 observasi dan terbukti lebih baik daripada model
30-hari sendirian (PR-AUC naik, 100% dari 500 resampling memihak
gabungan), TETAPI kejadiannya sangat jarang (~2-3 PART mati/bulan dari
belasan ribu aktif) - cocok sebagai daftar pantau perencanaan stok, BUKAN
pemicu tindakan per PART.

`predict_scrap()`: `scrap_probability` sudah dikalibrasi (boleh dibaca
sebagai persentase) tapi PERKIRAAN, bukan angka pasti - kalibrator
dipasang pada data latih dengan tingkat scrap 2,3%, sementara belakangan
naik ke 6,5%, jadi angka ini cenderung MERENDAHKAN risiko sesungguhnya;
urutannya tetap yang paling bisa dipercaya. `item_type_known_to_model`:
jenis PART belum dikenal masuk kelompok "jarang" yang cenderung diberi
risiko tinggi - ditandai supaya tidak dibaca seolah model tahu sesuatu
tentang jenis itu.

---

## `survival`

Metrik evaluasi survival NATIVE (C-index, Integrated Brier Score, Brier
per horizon, time-dependent AUC) lewat scikit-survival langsung, tidak ada
rumus custom. Horizon yang melebihi window follow-up TRAIN/eval TIDAK
dipaksakan - kalau sksurv menolak (ValueError, khas untuk horizon yang
melebihi rentang data), horizon itu dilaporkan sebagai "tidak dapat
dihitung", bukan diam-diam dilewati atau membuat evaluasi gagal total.

`native_metrics()`: Harrell C-index bisa bias optimis kalau censoring
TIDAK acak terhadap fitur (plausibel di sini: lifecycle yang
`installed_on`-nya belakangan otomatis lebih sering censored). Uno/IPCW
C-index menimbang ulang lewat model censoring, kurang bias oleh pola itu -
dilaporkan berdampingan, bukan menggantikan Harrell.

`bootstrap_c_index()`: mengukur SEBERAPA LEBAR ketidakpastian C-index -
VALIDATION hanya punya 385 event, jadi kandidat model/fitur baru hanya
dianggap "menang" kalau naiknya di luar rentang bootstrap ini, bukan
menang tipis 0,001 yang bisa jadi murni noise resampling (dipakai
`docs/EXPERIMENTS.md` E-16/E-18). Risk score dihitung SEKALI di luar loop
resampling (predict() tidak berubah antar resample) supaya 200 resample
tidak memanggil ulang `model.predict()` 200 kali.

---

## `training_survival`

Skor model survival event-based pada kondisi SEKARANG (`observation_on`
tiap baris), bukan dibekukan di `installed_on` - scorer promosi permanen,
hasil Fase A1 (`docs/EXPERIMENTS.md` E-24). Mengevaluasi model survival
dengan fitur dibekukan di `installed_on` (t0-only) menghukumnya justru
pada sumbu yang jadi alasannya dibangun (fitur dinamis yang di-refresh
seiring waktu) - `predict_survival` menskor pada KONDISI SEKARANG,
evaluasi representatif harus melakukan hal yang sama. Untuk SETIAP baris
populasi yang dinilai, fitur event-based dibangun PERSIS pada
`observation_on` baris itu - diperlakukan sebagai satu landmark tunggal,
mekanisme SAMA PERSIS dengan `features_survival.py`, hanya
titik waktunya beda.

**Tiga jebakan yang masing-masing bisa memalsukan hasil:**

1. `risk_30d = 1 - S(30)` LANGSUNG, BUKAN `curves.conditional_risk()` -
   kurva event-based sudah bermula di t=0=`observation_on`;
   `conditional_risk()` (rumus `1-S(age+30)/S(age)`) untuk model yang
   kurvanya dari t=0=`installed_on` salah dua kali kalau dipakai di sini.
2. Support totals (part_model/item_type/terminal) WAJIB dari dict BEKU
   hasil training (`metadata.json`), dipetakan persis seperti
   `predict_survival.py` - BUKAN dihitung ulang dari frame yang dinilai
   (menghitung ulang = leakage).
3. Memori: banyak baris x `predict_survival_function` pada RSF besar bisa
   OOM - harus di-chunk, kurva dibuang tiap chunk.

---

## `survival`

`DEFAULT_RSF_PARAMS`: titik awal (dipakai apa adanya kalau tidak ada
override) - pembulatan `duration_days` ke hari bulat + `min_samples_leaf`
ini menjaga artifact model tetap <1 GiB tanpa kehilangan C-index.

`fit_models()`: bawaan (`model_names=None`) TETAP RSF + Cox PH
berdampingan - Cox TIDAK PERNAH dibuang begitu saja di eksperimen mana
pun; kalau ia menyamai/mengalahkan model lain pada suatu kombinasi fitur,
itu temuan penting (bottleneck ada di fitur, bukan kompleksitas model),
bukan sekadar baseline formalitas.

`MODEL_REGISTRY["*"]["risk_sign"]`: dikalikan ke `model.predict()`
sebelum masuk `concordance_index_censored`/`ipcw` &
`cumulative_dynamic_auc` - SEMUA model harus dibandingkan dengan konvensi
sama "skor lebih tinggi = lebih berisiko/lebih cepat gagal". RSF
(cumulative hazard) dan Cox PH (log hazard ratio) SUDAH mengikuti konvensi
itu (`risk_sign=1`) - kalau suatu saat model AFT-style ditambahkan lagi,
`risk_sign` adalah tempatnya, bukan pembalikan ad-hoc di
`survival.py`.

---

## `api`

Connection pooling untuk `data_reader.connect()`, TANPA mengubah
`data_reader.py` - menambal `data_reader.connect` SEKALI saat API start
(pola sama dengan `query_cache.py`) supaya seluruh pemanggilan `with
data_reader.connect() as conn:` yang sudah ada transparan memakai koneksi
dari pool. Hanya API yang memasangnya (lewat `install()` di `main.py`
lifespan) - dipanggil dari terminal (predict.py/train.py),
`data_reader.connect` tetap `psycopg.connect()` langsung apa adanya.

`install()` idempoten (BUKAN "tutup lalu buat ulang"): test suite membuat
beberapa `TestClient` terpisah dalam satu proses, dan "tutup lalu buat
ulang" pada tiap start aplikasi berarti `TestClient` kedua menutup pool
yang masih dipakai yang pertama. Production sesungguhnya hanya punya satu
siklus hidup aplikasi per proses, jadi idempoten ini juga yang benar di
sana. `MIN_SIZE=1`/`MAX_SIZE=8` kecil dengan sengaja: aplikasi ini
melayani dashboard internal, bukan lalu lintas publik - batch scoring dan
satu assessment masing-masing memakai paling banyak 1 koneksi pada satu
waktu.

---

## `cli::evaluate-survival` / `training_survival::score_operational`

`numeric_columns` HARUS diisi eksplisit saat memanggil
`score_operational()` untuk model event-based: defaultnya memakai daftar
kolom numerik milik model classification (14 kolom - TIDAK sama dengan
kolom event-based, mis. `log_days_since_installation` ada di event-based
tapi tidak di classification) - tanpa override ini akan salah kolom/
KeyError. Evaluasi Lapis 2 (t0-only vs classification) di script ini
CENDERUNG menguntungkan model survival secara tidak sengaja (fitur
dibekukan di `installed_on`, bukan dihitung ulang pada `observation_on`
tiap baris TEST seperti `training_survival` - lihat
`docs/EXPERIMENTS.md` E-24 untuk perbandingan yang benar-benar dipakai
keputusan gerbang) - dipertahankan sebagai diagnostik cepat saja, bukan
dasar keputusan promosi.

---

## `features_survival`

Konteks device/terminal - PART -> TERMINAL parent link, KONSTAN per
lifecycle. Data dari `data_reader.get_terminal_context()`, direproduksi
dari definisi VIEW riset `analytics.eda_part_terminal_cycle_link` lewat
`pg_get_viewdef`, diverifikasi angkanya PERSIS sama: **24.008/24.045
valid link, 10.313 baris "recorded after installation"**. Point-in-time
safety: HANYA baris `parent_link_quality_status ==
'VALID_POINT_IN_TIME_RELATION'` dipakai sebagai terminal_type/model -
selain itu diberi `UNKNOWN_LABEL`, BUKAN diam-diam dipakai seolah sudah
diketahui sejak awal (lihat `docs/METHODOLOGY.md` `data_reader::get_terminal_context`).

---

## `features_survival::attach_install_context`

Join by (item_identifier_clean, installed_on) ke event INSTALLED yang
membuka siklus. Diverifikasi bersih pada data produksi: **24.045 cycles ->
24.045 baris hasil join** (tidak ada penggandaan baris), sesudah
`drop_duplicates` menangani 8 baris event ber-timestamp kembar (4 pasang,
diselesaikan ambil yang pertama). `device_type`/`device_model` SENGAJA
tidak dibuat: `item_category` pada cohort survival selalu 'PART' -
mengekstraknya butuh JOIN PART->TERMINAL baru (mapping baru), di luar
cakupan "reuse kolom canonical yang sudah ada" - keterbatasan data yang
didokumentasikan, bukan dipaksakan.

---

## `predict`

`death_probability()` = `P(rusak dalam horizon) x P(dibuang | rusak)`.
Sudah dibacktest pada 74.412 observasi: gabungan ini lebih baik daripada
model failure sendirian (PR-AUC naik, 100% dari 500 resampling memihak
gabungan) - tapi kejadiannya sangat jarang, pakai sebagai daftar pantau
perencanaan stok, bukan pemicu tindakan per PART. Diletakkan di
`predict/` (bukan `serving/`) karena lapisan ML inti (predict.py,
predict.py, predict.py - dipakai berdiri sendiri lewat CLI
masing-masing) TIDAK boleh bergantung pada lapisan serving DI ATASnya,
walau `serving/` juga memakainya - arah dependensi sama semangatnya dengan
`api -> serving` (`docs/DECISIONS.md` §4).

---

## `tests.test_freshness::test_assessment_tidak_membaca_hal_yang_sama_berulang`

Ambang 7 koneksi per assessment (bukan angka arbitrer): batas data,
siklus+event PART ini, siklus SELURUH armada + episode + terminal context
(dipakai model survival), event SELURUH armada untuk local density
item_type - 7 pembacaan BERBEDA minimal yang WAJIB tersatukan lewat
`query_cache`, tanpanya satu assessment membuka belasan koneksi. Ambang
naik dari 4 ke 6 gara-gara bug cache-key posisional/keyword (lihat WHY
comment di `predict_survival.py::predict()`); naik lagi ke 7 saat local
density ditambahkan (`get_events()` fleet-wide punya cache key
`('get_events', (), ())`, BEDA dari `get_events(item_id)`
`('get_events', (item_id,), ())` - genuinely data baru, bukan bug).
**Kalau angka ini naik lagi tanpa penambahan fitur yang jelas butuh
pembacaan baru, curigai bug cache-key seperti sebelumnya.**

---

## `api_services`

Fondasi monitoring - metrik untuk diamati, BUKAN retraining otomatis.
Sengaja berhenti di "menyediakan angka": tidak ada alert, tidak ada
trigger retraining (lihat `docs/DECISIONS.md` §7/roadmap P2-12 - alarm
belum ada).

Dua jenis metrik dicampur, diberi label jelas: **OFFLINE** (dari training
- PR-AUC/ROC-AUC/Precision-Recall@kapasitas/Brier, dibaca APA ADANYA dari
`metadata.json` model production, tidak dihitung ulang) berguna sebagai
konteks dan pengaman (kalau `CURRENT` tertukar manual ke model lebih
buruk, angka ini langsung menunjukkannya); **LIVE** (dari populasi PART
aktif SEKARANG - sebaran skor, jumlah HIGH/MEDIUM, pangsa kategori tipe
PART tidak dikenal, ringkasan fitur numerik, probabilitas scrap, dari
hasil `batch_predictor` yang sudah ada, TANPA query tambahan). **Tidak ada
label ground-truth untuk PART aktif** (belum diketahui apakah nanti benar
rusak) - PR-AUC/ROC-AUC LIVE secara matematis TIDAK BISA dihitung di sini,
karena itu dua kelompok metrik dipisah tegas, bukan dicampur jadi satu
angka menyesatkan.

`_unknown_category_share()`: naik drastis berarti armada mulai didominasi
tipe PART yang jarang dilihat model saat training - sinyal awal perlu
retrain sebelum akurasi ikut turun. `high_count_ratio_vs_training`: jumlah
HIGH SEHARUSNYA dekat kapasitas kerja yang dipakai menyetel ambang (lihat
`choose_cutoffs()`) kalau populasi PART aktif belum banyak berubah sejak
training - menjauh jauh dari expected berarti populasi sudah bergeser.
`predicted_scrap_probability_mean`: BUKAN scrap rate historis sungguhan
(itu perlu event kerusakan nyata) - rata-rata PREDIKSI model untuk PART
aktif, dilabeli jelas supaya tidak tertukar dengan tingkat scrap yang
benar-benar terjadi.

### `build_landmarks()` - detail implementasi

Event tepat DI `installed_on`/endpoint STRICT tidak dianggap landmark baru
(sudah tercakup L=0 / adalah endpoint itu sendiri). Umur event organik
dibulatkan ke hari bulat (konsisten dengan `duration_days`) + dedup -
beberapa event operasional (REQUESTED/ISSUED/DELIVERY) sering terjadi di
hari yang sama. Dedup final lintas ketiga sumber (anchor bisa kebetulan
sama dengan hari event organik) mempertahankan urutan prioritas
INSTALL > ORGANIC_EVENT > ANCHOR. Age harus STRICT < duration (residual
harus positif, >=1 hari - konsisten dengan invarian `duration_days >= 1.0`
di `lifecycle.py`). `landmark_source` (INSTALL/ORGANIC_EVENT/ANCHOR)
murni diagnostik, TIDAK dipakai sebagai fitur model.

## serving

Kesalahan lapisan serving dipisah dari service supaya route dan dashboard
bisa membedakan "PART tidak ada" (`PartNotFound`) dari "PART ada tapi tidak
bisa diskor" (`PartNotScorable`) tanpa membaca teks pesan. Alasan
`PartNotScorable` dianggap bukan kesalahan sistem: PART yang sedang tidak
terpasang memang tidak punya risiko kerusakan yang perlu diperkirakan;
alasannya (`reason`) dibawa apa adanya dari ML core, bukan dikarang ulang
di lapisan serving.

## dashboard.api_client

Satu-satunya pintu dashboard ke data: dashboard TIDAK pernah menyentuh
database atau memuat model, semua angka datang lewat HTTP dari FastAPI,
sehingga aturan bisnis, ambang risiko, dan kredensial database hanya ada di
satu tempat. Hasil di-cache sebentar (`CACHE_TTL_SECONDS`) supaya berpindah
halaman tidak memicu permintaan baru untuk data yang sama.
`REQUEST_TIMEOUT = (5, 180)`: batch scoring di sisi API bisa memakan waktu
puluhan detik saat cache-nya dingin.

## dashboard.pages.1_Detail_PART

Timeline riwayat (`ui.survival_timeline_chart`) dan kurva survival S(t)
dipasang di sumbu-x yang sama ("hari relatif terhadap sekarang", 0 =
`as_of`) supaya kaitan visual antara kejadian corrective dan penurunan
kurva langsung terlihat. Area di luar `curve_horizon_days` diberi arsiran
abu-abu, BUKAN dipotong dari chart - model tidak mengekstrapolasi di sana,
dan chart menunjukkan penolakannya, bukan menyembunyikannya dengan memotong
sumbu. Event timeline hanya menyertakan kejadian pada siklus pemasangan
SAAT INI (`date >= installed_on`) - kejadian dari siklus pemasangan
sebelumnya bukan bagian dari cerita kurva survival yang sedang ditampilkan.

Basis bukti panel rusak total (`{{scrap}} kejadian dari {{total}}
kerusakan`) diambil dari `training_rows` di `serving.describe()["scrap"]`
(`/api/v1/model`) - field yang sama yang dipakai proses training, bukan
angka yang dihitung ulang di dashboard.

## dashboard.pages.3_Kesehatan_Model

Halaman admin. Metrik ROC-AUC/PR-AUC/Brier yang ditampilkan berasal dari
SATU split TEST tanpa interval kepercayaan (FASE 7 P0-1/P0-2 belum
dikerjakan) - caption di halaman menyatakan ini eksplisit supaya tidak
dibaca sebagai angka pasti. Jumlah PART `NOT_SCORABLE` per sebab BELUM
tersedia sebagai metrik agregat (butuh perubahan `serving_batch`/
`api_services` yang belum dikerjakan) - halaman menyatakan ini apa adanya
daripada membangun logic baru yang berat untuk FASE 6. Kinerja terealisasi
(prediksi vs kejadian nyata) butuh prediction store (FASE 7 P0-4), juga
belum ada.

## config

`config.py` ada di `src/partrisk/config.py` - repo root (tempat
`models/` dan `.env` sungguhan tinggal, `models/` ARTIFACT bukan bagian
package) ada EMPAT tingkat di atasnya (`paths.py` -> `config/` -> `partrisk/`
-> `src/` -> root). `PACKAGE_DIR` default menghitung ini secara struktural,
BUKAN menebak - jadi benar otomatis selama layout `src/partrisk/config/`
dipertahankan, tanpa perlu env var apa pun di dev biasa. `PARTRISK_HOME`
override tetap ada untuk kasus di mana struktur relatif itu TIDAK berlaku
(mis. Docker image yang tidak menyalin `src/` apa adanya - lihat Dockerfile
`ENV PARTRISK_HOME`). `FAILURE_MODEL_DIR`/`SCRAP_MODEL_DIR`: satu folder per
model, masing-masing berisi `CURRENT` + `v1`, `v2`, ... supaya tidak ada dua
"v1" yang artinya berbeda.

## dashboard.app (tab Peta lokasi)

Database hanya punya NAMA lokasi ("STASIUN JUANDA"), bukan koordinat GPS.
Titik di peta datang dari OpenStreetMap (dicari otomatis di sisi API, lihat
`api_services.py`), disaring ketat: hanya nama berpola
stasiun publik yang dicoba, dan hasilnya harus jatuh di dalam kotak
Jabodetabek. Lokasi yang tidak lolos TIDAK dipasang pin - supaya peta tidak
pernah menunjukkan tempat yang salah - tetapi tetap dilaporkan di tabel di
bawah peta supaya PART berisiko tinggi di lokasi itu tidak hilang dari
pandangan hanya karena belum ada titiknya. Peta tidak dimuat otomatis
(tombol "Muat peta") - anggaran geocoding 60-90 detik terlalu mahal untuk
setiap kali halaman dibuka; tabel agregat per lokasi selalu tersedia tanpa
menunggu itu (`resolve=False` di request pertama).

## features

Diekstrak dari `feature_builder.py` (Fase B2 restrukturisasi) supaya
`observations.py`/`fleet.py`/`failure.py` tidak perlu saling impor untuk dua
fungsi kecil ini.

`_log1p()`: LN(1+x) dengan nilai kosong diperlakukan sebagai 0. Kosong di
sini berarti "belum pernah terjadi" (mis. belum pernah ada corrective),
bukan data hilang - itulah kenapa dipasangkan dengan kolom penanda `has_*`
supaya model bisa membedakan keduanya.

## api

Modul-modul di `api/` sudah memanggil `logging.getLogger(__name__)` dan
mencatat kejadian penting (model dimuat, batch scoring selesai, potret
armada dibuang), tapi tanpa logging dikonfigurasi, pesan level INFO hilang
diam-diam - Python hanya punya handler darurat untuk WARNING ke atas. Tanpa
modul ini, startup production terlihat sukses tanpa jejak apakah model
benar-benar dimuat. `setup()` aman dipanggil berulang (idempotent).

## cli::baseline-performance

Ukur baseline performa CatBoost SEBELUM restrukturisasi (plan
restrukturisasi survival_model, Fase 0.3). Angka ini jadi ambang G5/G6 di
Fase A (gerbang validasi) - model survival dibandingkan terhadap performa
NYATA model yang akan digantikan, bukan angka yang dikarang. Satu panggilan
`predict()` pemanasan dilakukan sebelum diukur karena fleet snapshot
pertama kali selalu lebih lambat.

## cli::baseline-comparison

BEDA dari `baseline-performance` di atas - ini soal akurasi (precision@
kapasitas), bukan RSS/latency. Nama keduanya sengaja dipisah biar tidak
tertukar. FASE 7 P0-6: bandingkan model production dengan kebijakan urutan
kerja yang bisa jalan TANPA model - lihat `docs/EXPERIMENTS.md` E-43 untuk
hasil dan angkanya. Model diskor ulang dengan `metadata["part_model_support"]`
(dukungan BEKU), BUKAN support dinamis hasil `build_dataset()` yang baru -
kalau tertukar, hasilnya tidak sebanding dengan cara `predict.py` sungguh
melayani production (lihat WHY di `training_failure.py::main()` soal
kenapa `candidate_support` dihitung ulang khusus, bukan dipakai langsung
dari `build_dataset()`). `log_prior_corrective_90d` dipakai APA ADANYA
(sudah log1p) untuk ranking baseline "corrective terbanyak 90 hari" -
`argsort` pada transform monoton menghasilkan urutan identik dengan count
mentahnya, jadi tidak perlu dihitung ulang.

## cli::rolling-backtest

FASE 7 P0-1. ⚠️ Hasilnya BUKAN sekadar angka rutin - lihat
`docs/DECISIONS.md` §10 (TERBUKA) dan `docs/EXPERIMENTS.md` E-44 sebelum
menjalankan ulang atau menafsirkan output baru dari perintah ini.

`_assign_rolling_split()` SENGAJA tidak memanggil `training_failure.
assign_split()` - fold di sini butuh `test_start`/`test_end` per-fold
(window bergerak), sedangkan `assign_split()` production menetapkan
`test_start` tetap "1 Jan tahun data_end" dan `TEST` tidak berujung
(sampai `data_end`). Metodologi split (embargo `resolved < validation_
start`, dst) SAMA PERSIS - hanya titik waktunya yang diparameterkan. Model
yang dilatih di jalur ini TIDAK PERNAH disimpan ke `models/failure/vN/` -
murni evaluasi in-memory, dibuang setelah proses selesai.

`features` (frame LENGKAP, bukan per-fold) dihitung SEKALI dari `training_
failure.build_dataset()` dan dipakai ulang untuk seluruh 6 fold x 2
varian fitur - subset kolom (`v3_metadata["features"]` vs `v4_metadata[
"features"]`) dan filter baris (mask TRAIN/VAL/TEST per fold) yang
berubah, bukan fiturnya sendiri. Ini valid karena kolom fitur v3/v4 tidak
tergantung waktu evaluasi - dihitung point-in-time per baris observasi,
sama seperti training production. Menghitung ulang fitur per fold akan
6x lebih mahal untuk hasil yang identik.

Perbandingan pakai selisih BERPASANGAN per-fold (v4 fold i - v3 fold i),
bukan mean(v4) - mean(v3) independen - lebih tepat secara statistik karena
kedua varian selalu diuji di fold yang SAMA PERSIS (populasi TEST identik,
hanya kolom fitur beda), jadi variasi ANTAR-fold (yang besar - lihat sd
per-metrik) tidak ikut mengaburkan selisih ANTAR-varian yang sesungguhnya
ingin diukur.

## cli::bootstrap-ci

FASE 7 P0-2. Model/calibrator yang SUDAH ADA diskor ulang APA ADANYA -
BUKAN dilatih ulang. Tiga model diperlakukan beda karena bentuk evaluasi
aslinya beda:
- **failure**: resample baris TEST (dukungan beku dari metadata, sama
  metodologi dengan `baseline-comparison`/P0-6 di atas), `training_
  failure.full_metrics()` dipanggil ulang 1000x pada tiap resample.
- **scrap**: sama, tapi via `training_scrap.evaluate_incumbent()` yang
  sudah ADA (awalnya untuk membandingkan kandidat vs incumbent saat
  training, di sini dipakai untuk re-skor v1 terhadap dirinya sendiri).
  Base rate SANGAT kecil (21 positif/323 baris TEST) - resample yang
  kebetulan tidak punya kelas positif SAMA SEKALI dibuang (`ValueError`
  dari `roc_auc_score`), bukan dipaksa jadi 0 - itulah kenapa CI recall
  DIPERKIRAKAN lebar, bukan bug.
- **survival**: pakai `survival.bootstrap_c_index()` yang sudah ada dan
  BELUM PERNAH dipanggil sejak FASE 1 (lihat CLAUDE.md §4.1C) - beda dari
  dua di atas karena ini resample event/time/risk array langsung, bukan
  lewat `full_metrics()` (C-index bukan metrik klasifikasi).

Field `bootstrap_ci_95` ditambahkan ke metadata.json masing-masing model
(TIDAK menimpa field yang sudah ada) - `risk_cutoffs`/`features`/
`part_model_support`/kalibrator TIDAK tersentuh, jadi golden_batch tetap
byte-identik setelah perintah ini dijalankan.

## config (`__init__.py`)

Sebelumnya satu file `config.py` (308 baris, Fase B3 restrukturisasi
memecahnya jadi `paths.py`/`failure.py`/`scrap.py`/`text.py`/`database.py`
per subjek - logic dan nilai TIDAK diubah). Modul `config/__init__.py`
mengimpor-ulang SEMUA nama yang dulu diekspor supaya pemanggil yang menulis
`from partrisk import config` lalu memakai `config.X` tidak perlu berubah
sama sekali.

## tests.test_map_markers

Warna dan ukuran titik peta ditarik keluar dari halaman Peta Risiko
(`dashboard/ui.py`) supaya bisa diuji tanpa harus mensimulasikan klik
sungguhan pada peta (AppTest tidak bisa melakukan itu - sama seperti
keterbatasannya pada seleksi baris dataframe).

## config

Mapping kanonikalisasi teks (client/lokasi) yang sudah disetujui reviewer
pada fase research. Disimpan sebagai konstanta supaya production tidak
bergantung pada tabel di schema `analytics`. `FUZZY_MIN_SCORE`/
`FUZZY_MIN_MARGIN`: kandidat fuzzy diterima otomatis hanya kalau sangat
mirip DAN jauh lebih mirip dibanding kandidat kedua.

## api (`__init__.py`)

Paket `api` TIDAK memuat logic machine learning apa pun. Seluruh
perhitungan fitur dan prediksi tetap dikerjakan modul lain di package
`partrisk` (predict, feature builders, data reader); paket `api` hanya
membungkusnya supaya bisa dipanggil lewat HTTP.

## config

`db_settings()`: kredensial database dari `.env`/environment. Production
hanya membaca (read-only) - lihat DECISIONS.md §2.

## `_env`

Helper baca environment variable kecil, dipakai `api.py` dan
`serving.py` (Fase B4 restrukturisasi - dedup, sebelumnya masing-
masing punya salinan sendiri yang identik).

## dashboard.app

Halaman default dashboard (`streamlit run dashboard/app.py`), disusun
menurut Q2 (PART mana dirawat duluan) - dua tab: "Antrian kerja" dan "Peta
lokasi". Dashboard hanya bicara ke FastAPI (lihat `dashboard/api_client.py`)
- tidak pernah ke database dan tidak pernah memuat model sendiri.

Daftar antrian diisi sampai kapasitas menurut urutan `tier_score` dari API
(`/api/v1/recommendations`), TIDAK disaring menurut `risk_level` - kalau
disaring HIGH, user hanya melihat puluhan baris padahal kapasitasnya
ratusan, dan akan menyimpulkan modelnya rusak. Lihat DECISIONS.md.

Kurva tangkapan kumulatif (`ui.capture_curve_chart`) dihitung dari
`failure_probability_Nd` yang SUDAH dikalibrasi isotonic - karena itu
`cumsum()`-nya adalah estimator valid untuk "ekspektasi kerusakan
tertangkap", bukan cuma skor relatif. Totalnya (`expected_failures_by_horizon`
di `serving_batch.summary()`) dan garis acak pembanding (rate = total harapan
/ populasi aktif) dihitung dari data yang sudah ada di batch scoring - nol
query baru. Kurva hanya akurat sampai N=500 (`MAX_RECOMMENDATION_LIMIT`,
batas API) - kapasitas kerja realistis jauh di bawah itu.

`default_location` di tab Antrian diisi otomatis lewat
`st.session_state["map_location_filter"]` kalau datang dari tab Peta
(pilih lokasi -> filter Lokasi terisi). Sekali pakai (`pop`), sama seperti
`detail_item_id` di halaman Detail PART.

## dashboard.pages.2_Perencanaan_Penggantian

Halaman ini TIDAK menyatakan bahwa sebuah PART akan dibuang. Yang
ditampilkan adalah PART yang risiko rusaknya tinggi DAN - seandainya rusak -
kecil kemungkinannya bisa diperbaiki. Kombinasi itulah yang membuat
menyiapkan pengganti lebih awal masuk akal.

## serving

`BATCH_CACHE_TTL_SECONDS`: berapa lama hasil batch scoring dipakai ulang
sebelum dihitung ulang. Data sumber hanya bertambah beberapa kali sehari,
dan satu kali batch memakan waktu menit-menitan, jadi menghitungnya per
request tidak masuk akal.

`DATA_FRESHNESS_TTL_SECONDS`: seberapa sering batas waktu data diperiksa
ulang. Pemeriksaannya satu query ringan, tetapi dipanggil di setiap
request - jadi hasilnya ditahan sebentar. Ini juga yang menentukan seberapa
cepat data baru terlihat oleh aplikasi.

## api

Koordinat dari OpenStreetMap Nominatim (lihat `geocoding_service.py`), bukan
dari database - database ini hanya punya NAMA lokasi, bukan GPS. Hasilnya
disaring ketat: lokasi yang tidak lolos disiplin geografis Jabodetabek TIDAK
ditampilkan sebagai pin, melainkan dilaporkan terpisah di `unresolved`
supaya petanya jujur tentang apa yang tidak diketahuinya.

## api

Endpoint monitoring memisahkan metrik offline (training) dan live (populasi
aktif) untuk tiap model - lihat `api_services.py` untuk
definisi lengkap keduanya dan kenapa dipisah tegas.

## api

Semua endpoint di sini membaca satu hasil batch yang sama (lihat
`serving_batch.py`). Batch dihitung sekali lalu dipakai ulang
selama masih segar, jadi permintaan filter/paging tidak pernah memicu
skoring ulang seluruh armada. `_INTERNAL_COLUMNS = ["tier_score"]`: kolom
internal yang tidak perlu keluar ke client.

## api_schemas

`_CONFIG`: `model_` adalah prefix yang dilindungi pydantic; kolom kita
memang bernama `model_version`, jadi perlindungan itu dimatikan
(`protected_namespaces=()`) di seluruh skema.

`FailurePrediction`: field `median_days_to_failure_basis` dst adalah
advisory dari model survival TERPISAH (`partrisk.predict_survival`) - tidak
pernah ikut menentukan `failure_probability_*`/`risk_level` di atas.
`days_until_risk_medium`/`days_until_risk_high` (Langkah C, rencana upgrade
RSF - median 50% sering None/kasar): hari sampai risiko kumulatif versi RSF
(kurva terkalibrasi) mencapai ambang yang SAMA dengan MEDIUM/HIGH CatBoost
(`config.FAILURE_MEDIUM/HIGH_PROBABILITY_THRESHOLD`, 0,15/0,25) - bukan
cross-reference ke `failure_probability_30d` (model TERPISAH), cuma
skalanya disamakan supaya gampang dipahami dibanding median 50% yang sering
kosong. `survival_risk_30d..120d`: peluang kerusakan per horizon dari model
survival (BEDA dari `failure_probability_*` di atas - model TERPISAH),
dikalibrasi isotonic per horizon + cummax (Fase R1 upgrade RSF).
`survival_risk_is_calibrated` menandai apakah field ini benar-benar terisi
kalibrasi (False kalau model survival tidak scorable/tidak tersedia - lihat
`median_days_to_failure_basis`).

`Explanation.notes`: keterangan cara membaca angkanya - mis. bahwa
kerusakan di riwayat tidak berarti PART berhenti dipakai.

`AssessmentResponse.scrap`: boleh kosong - PART yang riwayatnya belum
cukup tetap dapat penilaian kerusakan, hanya tanpa sumbu scrap.

`PriorityItem.median_days_to_failure` dst: advisory (model survival
TERPISAH, mode aditif - lihat `FailurePrediction`). Kurva PENUH sengaja
TIDAK di sini - lihat `serving_batch.py`
`_score_survival_advisory()` soal ukuran payload daftar.

`UnresolvedLocation.checked`: False = belum pernah dicoba sama sekali
(kehabisan anggaran waktu). True = sudah dicoba, tidak ada hasil yang lolos
penyaringan Jabodetabek.
