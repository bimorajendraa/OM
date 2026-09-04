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

## config

`config.py` ada di `src/partrisk/core/config.py` - repo root (tempat
`models/` dan `.env` sungguhan tinggal, `models/` ARTIFACT bukan bagian
package) ada EMPAT tingkat di atasnya (`core/` -> `partrisk/` -> `src/` ->
root). `PACKAGE_DIR` default menghitung ini secara struktural, BUKAN
menebak - jadi benar otomatis selama layout `src/partrisk/core/`
dipertahankan, tanpa perlu env var apa pun di dev biasa. `PARTRISK_HOME`
override tetap ada untuk kasus di mana struktur relatif itu TIDAK berlaku
(mis. Docker image yang tidak menyalin `src/` apa adanya - lihat Dockerfile
`ENV PARTRISK_HOME`). `FAILURE_MODEL_DIR`: satu folder per model, berisi
`CURRENT` + `v1`, `v2`, ... supaya tidak ada dua "v1" yang artinya berbeda.

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
BUKAN dilatih ulang. Resample baris TEST (dukungan beku dari metadata,
sama metodologi dengan `baseline-comparison`/P0-6 di atas), `training_
failure.full_metrics()` dipanggil ulang 1000x pada tiap resample.

Field `bootstrap_ci_95` ditambahkan ke metadata.json model kerusakan
(TIDAK menimpa field yang sudah ada) - `risk_cutoffs`/`features`/
`part_model_support`/kalibrator TIDAK tersentuh, jadi golden_batch tetap
byte-identik setelah perintah ini dijalankan.

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

## serving

`BATCH_CACHE_TTL_SECONDS`: berapa lama hasil batch scoring dipakai ulang
sebelum dihitung ulang. Data sumber hanya bertambah beberapa kali sehari,
dan satu kali batch memakan waktu menit-menitan, jadi menghitungnya per
request tidak masuk akal.

`DATA_FRESHNESS_TTL_SECONDS`: seberapa sering batas waktu data diperiksa
ulang. Pemeriksaannya satu query ringan, tetapi dipanggil di setiap
request - jadi hasilnya ditahan sebentar. Ini juga yang menentukan seberapa
cepat data baru terlihat oleh aplikasi.
