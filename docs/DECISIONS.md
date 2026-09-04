# Keputusan (ADR)

Keputusan yang MENGIKAT desain/arsitektur/kebijakan operasional - bukan
hasil eksperimen (itu di `docs/EXPERIMENTS.md`). Satu section per keputusan.
Tambahkan section baru untuk keputusan baru; jangan menulis ulang yang lama
kecuali keputusannya benar-benar berubah (tandai "SUPERSEDED oleh §N" kalau
begitu, jangan hapus riwayatnya).

---

## 1 · Survival event-based TIDAK cutover CatBoost (Gate A5)

**Status**: berlaku. Tanggal: 2026-08-21. Detail eksperimen lengkap: lihat
`EXPERIMENTS.md` E-20 s/d E-24.

Model survival Random Survival Forest (event-based) TIDAK menggantikan
CatBoost sebagai mesin keputusan utama (Q2: "PART mana yang dirawat
duluan?"). Sesuai aturan gerbang: G1 lulus (PR-AUC survival 0,1643 vs
CatBoost 0,1444) tapi **G2 dan G3 gagal secara nyata** - Recall@kapasitas
survival 0,2816 vs CatBoost 0,3348, Precision@kapasitas 0,1805 vs 0,2146.
Aturan gerbang: "G1 atau G2 gagal -> JANGAN cutover".

**Konsekuensi mengikat**:

- CatBoost tetap satu-satunya sumber `failure_probability_*`, `risk_level`,
  `tier_score`, `rank` - field yang mengatur keputusan/urutan inspeksi.
- Survival masuk mode **aditif**: menyuplai `median_days_to_failure`,
  `days_until_survival_90pct`, `survival_curve` sebagai field advisory
  (`advisory: true`) di kontrak API. Field ini TIDAK PERNAH memengaruhi
  `risk_level`/`tier_score`/`rank` CatBoost - lihat invariant §2 CLAUDE.md.
- Restrukturisasi `src/partrisk` (pemisahan modul) berjalan terlepas dari
  keputusan gerbang ini.

**Kalau mau dibuka lagi**: butuh perbaikan nyata di Recall@kapasitas DAN
Precision@kapasitas survival (bukan cuma PR-AUC), diukur pada populasi
production-realistic yang sama (E-20), bukan cuma metrik native survival
(C-index/IBS).

---

## 2 · Larangan schema `analytics`

**Status**: berlaku sejak awal proyek, ditegakkan ulang di ablation
event-based.

Kode production (`src/partrisk/`) TIDAK PERNAH bergantung pada tabel di
schema `analytics` - hanya tabel operasional yang sudah dipetakan
`data_reader.py`. Alasan: schema `analytics` adalah hasil riset lama, bukan
sumber data live yang terjamin kesegarannya/skema-nya untuk production.

Kasus uji nyata: `E_plus_device_terminal`/`F_combined_all`
(`EXPERIMENTS.md` E-08) memakai schema `analytics` untuk device/terminal
context dan menang C-index paling tinggi (VAL t0-only RSF 0,8036) - TAPI
TIDAK dipakai produksi karena dependency ini. Varian tanpa dependency
(`G_combined_without_device`, E-12, VAL t0-only 0,7954) yang jadi basis
fitur produksi, meski C-index-nya lebih rendah.

**Verifikasi**: `grep -rn "schema.*analytics" src/` hanya boleh muncul di
komentar yang MENJELASKAN larangan ini (`data_reader.py`, `config/text.py`,
`features/survival/builder.py`), tidak pernah di kode yang benar-benar query
`analytics.*`.

---

## 3 · `dashboard/` tidak pernah menyentuh database atau memuat model

**Status**: berlaku, invariant keras (lihat CLAUDE.md §2).

`dashboard/` (Streamlit) mendapat SEMUA angka lewat HTTP ke `partrisk.api`.
Tidak ada `import partrisk` (selain util HTTP client sendiri jika ada),
tidak ada koneksi database langsung, tidak ada `joblib.load()` model apa
pun di kode dashboard.

**Alasan**: satu sumber kebenaran (API) untuk semua consumer, mencegah
dashboard dan API menghitung metrik yang sama dengan cara berbeda dan
diam-diam berbeda hasil. Juga mencegah dashboard butuh kredensial database/
akses filesystem model.

**Verifikasi**: `grep -rn "^import partrisk\|^from partrisk" dashboard/`
harus kosong (dicek ulang 2026-08-23, kosong).

---

## 4 · Arah dependensi `api -> serving`, tidak pernah sebaliknya

**Status**: berlaku, invariant keras.

`src/partrisk/serving/` (predictor, batch_predictor, model_loader, dst)
TIDAK PERNAH meng-import apa pun dari `src/partrisk/api/`. Arah dependensi
hanya satu arah: `api` (routes, schemas) memanggil `serving` untuk mendapat
prediksi/rekomendasi, bukan sebaliknya.

**Alasan**: `serving/` harus bisa dipakai lepas dari HTTP (mis. dari CLI/
skrip batch) tanpa menyeret FastAPI/routing. Kalau `serving` bergantung ke
`api`, itu terbalik dan menciptakan siklus dependensi begitu `api` juga
butuh sesuatu dari `serving`.

**Verifikasi**: `grep -rn "^import partrisk.api\|^from partrisk.api\|^from partrisk import api" src/partrisk/serving/` harus kosong (dicek ulang
2026-08-23, kosong).

---

## 5 · Aturan promosi model

**Status**: berlaku, dua kebijakan TERPISAH untuk dua model. **Update
2026-08-27 (§13)**: untuk failure dan survival, kedua gerbang di bawah
sekarang dievaluasi di **VALIDATION**, bukan TEST (TEST tetap
dihitung/disimpan, murni laporan) - lihat §13 untuk alasan dan detail.
Scrap MASIH digerbang di TEST (belum diperbaiki, lihat §13).

### 5a. CatBoost (failure/scrap) - `engines/failure/train.py::decide_promotion()`
(fungsi sama, dipakai ulang oleh `engines/scrap/train.py`)

Dua syarat harus **SAMA-SAMA tidak memburuk** dibanding incumbent (bukan
skor tunggal, bukan ROC-AUC sendirian):

- `PR-AUC` kandidat >= incumbent.
- `Recall@kapasitas` kandidat >= incumbent.

Kriteria di atas SAMA untuk failure dan scrap. Yang BEDA sejak §13
hanyalah split yang dipakai untuk menghitung kedua angka ini: failure
di VALIDATION, scrap masih di TEST (lihat §13 kenapa scrap belum ikut
dipindah).

ROC-AUC dan Brier dilaporkan tapi TIDAK menggerbang. Kalau belum ada
incumbent (training pertama), lolos otomatis. `--force-promote` bisa
memaksa promosi walau gagal gerbang (dicatat eksplisit di alasan). Model
yang gagal gerbang TETAP disimpan sebagai versi baru (`vN`) untuk
dibandingkan, tapi `CURRENT` tidak dipindah ke situ.

**Alasan dual-gate**: base rate kecil (~1,5-2,3% positif) membuat ROC-AUC
sendirian bisa terlihat bagus walau presisi pada kapasitas kerja nyata
(200/bulan) memburuk - dibuktikan empiris di `EXPERIMENTS.md` E-25 s/d
E-35 (banyak kandidat naik ROC-AUC/Recall@kapasitas tapi turun PR-AUC,
digagalkan gerbang dengan benar).

### 5b. RSF survival - `training.failure_survival.decide_survival_promotion()`

Satu syarat: **Brier@30d DAN Brier@90d** kandidat harus `<=` incumbent,
keduanya. Kalau belum ada artifact (training pertama): lolos otomatis.
Kalau gagal: artifact TIDAK ditimpa, exit code 1 (training gagal eksplisit).

**Sengaja BUKAN dual-gate PR-AUC/Recall@kapasitas** seperti CatBoost - RSF
tidak dipakai untuk ranking/urutan inspeksi (§1 gate A5), jadi metrik
ranking tidak relevan untuk gerbangnya. Brier per horizon (kualitas
probabilitas mentah) relevan untuk field advisory `risk_Nd`/median/90pct.
Detail: `EXPERIMENTS.md` E-39.

---

## 6 · Prosedur rollback lewat `CURRENT`

**Status**: berlaku, mekanisme sudah ada di kode
(`training/versioning.py::current_version`).

Setiap direktori model (`models/failure/`, `models/scrap/`, dan sejak
2026-08-30 juga `models/survival/` - lihat §7) punya file teks `CURRENT`
berisi nama versi aktif (mis. `v4`). Serving (`serving/
model_loader.py` dkk, `engines/survival/predict.py::_current_artifacts_dir()`
untuk survival) selalu membaca `CURRENT` untuk menentukan artifact mana
yang dimuat - tidak pernah hardcode nomor versi.

**Rollback** = mengubah isi `CURRENT` ke versi lama (mis. `v3`) SELAMA
direktori versi itu (`models/failure/v3/`) masih ada di disk. Tidak perlu
retrain, tidak perlu deploy ulang kode - restart proses serving (atau tunggu
refresh cache berikutnya) sudah cukup.

**Prasyarat**: versi yang mau di-rollback-ke HARUS belum dihapus - lihat §7
untuk aturan retensi.

---

## 7 · Retensi versi model - kapan versi lama boleh dihapus

**Status**: berlaku.

- **`models/failure/v1/`, `v2/`**: DIHAPUS (Fase 1 konsolidasi,
  2026-08-23). Bukan model production sejak lama (`CURRENT` sudah v4
  sebelum penghapusan), `v2`'s `evaluation_metrics` identik dengan `v1`
  (cuma `cutoff_basis` berubah, bukan model baru secara substansial).
  `test_promotion.py` sudah punya skip-guard eksplisit untuk kasus `v1`
  tidak ada.
- **`models/failure/v3/`**: TIDAK dihapus. Ini mekanisme rollback untuk
  `v4` (`CURRENT` saat ini). **Aturan**: hapus `v3` HANYA setelah `v4`
  melewati **satu siklus retrain produksi penuh** tanpa insiden yang
  memerlukan rollback ke `v3`. Sampai saat itu, `v3` tetap ada di disk
  walau bukan `CURRENT`.
- **Model survival**: SUPERSEDED 2026-08-30 (lihat update di bawah) - bullet
  ini dipertahankan apa adanya sesuai konvensi append-only dokumen ini,
  BUKAN lagi kebijakan yang berlaku.

**Pola umum**: jangan hapus versi model yang masih bisa jadi target
rollback aktif. Hapus hanya versi yang sudah dilewati DUA generasi (`v1`/
`v2` dihapus setelah `v4` jadi `CURRENT`, `v3` masih bertahan sebagai satu
generasi di belakang).

**Update 2026-08-30 - model survival PINDAH ke skema versi `models/survival/vN/`
+ `CURRENT`, POLA SAMA dengan failure/scrap**: sebelumnya artifact tunggal
di `survival_model/event_based/artifacts/` (di luar `models/`, TIDAK
dilacak git - `.gitignore`) ditimpa di tempat setiap retrain lolos gerbang
R3, tanpa jejak versi lama/gagal. Diminta user eksplisit ("masukkan ke
folder models dan jadikan ada versi juga agar ter-tracking") - artifact
production yang ada dipindah APA ADANYA jadi `models/survival/v1/`
(`CURRENT`="v1"), `engines/survival/train.py::main()` sekarang reuse
`training_failure.current_version()`/`next_version()` (fungsi yang SAMA
dipakai failure model, tidak diimplementasi ulang): setiap retrain SELALU
tersimpan sebagai versi baru, `CURRENT` cuma pindah kalau lolos
`decide_survival_promotion()` (§5b, tidak berubah - Brier@30d/90d
VALIDATION tidak boleh memburuk). Rollback survival sekarang BISA lewat
`CURRENT` (sama seperti failure/scrap, §6) - tidak perlu lagi retrain
ulang dari commit sebelumnya. `.gitignore` diubah dari mem-blok seluruh
`survival_model/event_based/` jadi hanya `models/survival/v*/*.joblib`
(binary besar, ~65MB/versi TETAP tidak di-commit) - `CURRENT` dan
`metadata.json` tiap versi TETAP dilacak git (kecil, berguna untuk audit
riwayat retrain), pola sama dengan `models/failure/`/`models/scrap/`.
Retensi versi survival BELUM ada aturan eksplisit (beda dari `v3`/`v4`
failure di atas) - jumlah retrain yang menghasilkan versi TIDAK dipromosikan
masih sedikit, revisit kalau `models/survival/` mulai menumpuk banyak versi
gagal.

---

## 8 · Prasyarat deployment yang belum terpenuhi (dicatat, belum dieksekusi)

**Status**: TERBUKA - dicatat di sini supaya tidak terlupa, bukan
keputusan yang sudah dieksekusi.

- **API tanpa autentikasi.** DB read-only, tidak ada kredensial di image,
  CORS default tertutup - semua benar untuk localhost. Tapi endpoint tidak
  punya API key/rate limit. **Sebelum API dipasang di jaringan kantor
  (bukan lagi localhost-only), wajib tambahkan minimal API key + rate
  limit** - jangan deploy "sebentar saja" tanpa ini.
- **Repo ini publik.** Tidak ada kredensial/nama client (`APPROVED_CLIENT_
  ALIAS` kosong, `fleet_snapshot.csv` hanya kode agregat) - tapi laju
  kerusakan armada, kode model part, dan volume operasional klien nyata
  bisa dibaca siapa pun yang mengakses repo. **Sebelum membagikan link repo
  lebih luas** (portofolio/skripsi publik dll.), perlu persetujuan tertulis
  pemilik data ATAU repo dijadikan privat dengan versi publik yang
  dianonimkan terpisah.

---

## 9 · `tier_score` memerintah antrian kerja, `risk_level` hanya label warna (FASE 7 P0-5)

**Status**: SUPERSEDED oleh §11 (2026-08-25) untuk ANTRIAN RESMI - lihat §11
untuk kebijakan yang berlaku sekarang (`gate_flagged`/gerbang presisi
menggantikan isi-sampai-kapasitas sebagai default `/api/v1/recommendations`).
Bagian di bawah ini TETAP benar untuk mode eksplorasi
(`official_queue_only=false`) - `tier_score` masih dipakai mengurutkan
tampilan eksplorasi itu, `risk_level` masih tidak pernah menyaringnya.
Tidak dihapus/ditulis ulang, sesuai konvensi append-only dokumen ini.

**Status (historis, sebelum §11)**: berlaku, sudah diimplementasikan sejak awal (`serving_batch.py`
mengurutkan `frame` menurut `tier_score`, bukan menyaring `risk_level`) -
bagian ini FORMALISASI kebijakan yang sudah ada di kode, plus audit angka
yang membuktikan kenapa kebijakan itu satu-satunya yang masuk akal.

**Audit (data s/d 2026-08-03, populasi 16.877 PART aktif, sama dengan
snapshot training v4)**:

| Ambang | Jumlah PART |
|---|---:|
| HIGH (`>= 0.25`) | 27 |
| MEDIUM (`>= 0.15`) | 57 |
| HIGH + MEDIUM | 84 |
| Kapasitas kerja tipikal | 200/bulan |

`HIGH + MEDIUM` = 84, jauh di bawah kapasitas 200. Kalau antrian kerja
DISARING menurut `risk_level` (mis. "hanya tampilkan HIGH", atau bahkan
"HIGH + MEDIUM"), tim hanya akan melihat 27-84 baris walau sanggup
menindaklanjuti 200 - dan akan menyimpulkan modelnya kurang berguna,
padahal ambangnya memang tidak dirancang untuk mengisi kapasitas (lihat
juga §1 - `FAILURE_HIGH_PROBABILITY_THRESHOLD`/`_MEDIUM_...` di
`docs/METHODOLOGY.md`, dipilih dari sebaran skor, bukan dari kapasitas
kerja tim).

**Keputusan**: `tier_score` (skor mentah 30-hari, `serving_batch.py`)
SELALU yang mengisi dan mengurutkan antrian kerja sampai kapasitas yang
dipilih user - `risk_level` (LOW/MEDIUM/HIGH) HANYA dipakai sebagai label
warna per PART di tabel/detail, TIDAK PERNAH untuk menyaring atau membatasi
baris yang ditampilkan. Dashboard (`dashboard/app.py`, Halaman Antrian
Kerja) menyatakan ini eksplisit di caption halaman.

**Alternatif yang ditolak**: menggeser ambang `risk_cutoffs` supaya
HIGH+MEDIUM mendekati 200. Ditolak - melanggar invariant §2 "ambang risiko
tetap dibekukan saat training, layer rekomendasi tidak punya ambang
sendiri". Ambang itu dipilih dari sebaran skor populasi (representasi
"seberapa tidak biasa" suatu PART), bukan dari kapasitas kerja tim yang
bisa berubah-ubah - menggesernya supaya pas dengan satu angka kapasitas
membuatnya berhenti berarti apa-apa di luar konteks kapasitas itu.

**Verifikasi**: `frame.sort_values("tier_score", ascending=False)` di
`serving_batch.py::_compute()` (tanpa filter `risk_level` di jalur ini);
`/api/v1/recommendations` tidak punya parameter yang menyaring berdasar
`risk_level` untuk pengisian kapasitas (`risk` query param ADA tapi
opsional, dipakai user untuk eksplorasi, bukan dipaksakan oleh dashboard
Antrian Kerja).

---

## 10 · v3 mungkin lebih baik dari v4 di PR-AUC (FASE 7 P0-1) - mekanisme promosi diperbaiki (§13), rollback v3-vs-v4 masih belum diputuskan

**Status**: SEBAGIAN DITUTUP 2026-08-27, lihat §13. P0-3 (pindahkan
gerbang promosi ke VALIDATION) yang jadi prasyarat opsi 1 di bawah SUDAH
dikerjakan untuk failure dan survival. Pertanyaan asli (apakah v3 lebih
baik dari v4) BELUM dijawab ulang lewat mekanisme yang sudah diperbaiki -
itu butuh retrain baru dan keputusan manusia terpisah, bukan otomatis
dari perbaikan mekanisme saja.

**Temuan** (detail lengkap: `docs/EXPERIMENTS.md` E-44). Backtest 6-fold
temporal bergulir (metodologi split identik production, hanya window per
fold yang berbeda) menunjukkan **v3 (28 fitur) punya PR-AUC lebih tinggi
dari v4 (32 fitur, CURRENT production) secara konsisten** - selisih
berpasangan per-fold -0,0078 ± 0,0062, melebihi 1 sd (kriteria yang
ditetapkan sendiri untuk klaim "A lebih baik dari B"). v3 menang PR-AUC di
5 dari 6 fold. Empat metrik lain (ROC-AUC, Brier, precision@cap,
recall@cap) TIDAK beda signifikan.

Ini persis kekhawatiran yang memotivasi FASE 7 P0-1: saat v4 dipromosikan
(§5a), VALIDATION PR-AUC TURUN (0,1174->0,1116) sementara TEST NAIK
(0,1884->0,1961) - pola klasik model beradaptasi ke SATU TEST split yang
sudah dipakai memutuskan promosi berkali-kali, bukan perbaikan yang
generalize.

**Kenapa belum diputuskan di sini**: mengembalikan `CURRENT` failure model
ke v3 (rollback lewat mekanisme §6) adalah keputusan produksi yang
mengubah apa yang dilihat user - di luar wewenang satu sesi audit FASE 7
P0-1 (yang scope-nya "tanpa model baru", murni evaluasi). Rollback butuh
pertimbangan tambahan yang TIDAK dijawab backtest ini sendirian:
- Apakah 6 fold x 60 hari representasi yang cukup, atau perlu lebih
  banyak fold / window lebih panjang untuk lebih yakin?
- P0-3 (pindahkan keputusan promosi ke VALIDATION + fold bergulir, TEST
  jadi touch-once) BELUM dikerjakan - argumen kuat untuk dikerjakan
  SEBELUM keputusan rollback, supaya keputusan berikutnya (rollback atau
  tidak) sudah pakai metodologi yang benar dari awal, bukan backtest
  ad-hoc yang terpisah dari jalur promosi resmi.
- v4 tetap unggul (walau tidak signifikan) di precision@kapasitas dan
  recall@kapasitas - dua metrik yang PALING dekat dengan dampak
  operasional nyata (siapa yang benar-benar diperiksa). PR-AUC adalah
  metrik ranking keseluruhan, bukan metrik pada titik operasi (kapasitas)
  yang sebenarnya dipakai.

**Opsi yang tersedia, belum dipilih**:
1. Kerjakan P0-3 dulu (pindahkan gerbang promosi ke VALIDATION+fold
   bergulir), lalu jalankan ulang keputusan promosi v3-vs-v4 lewat jalur
   RESMI dengan metodologi yang sudah diperbaiki.
2. Rollback manual ke v3 sekarang lewat mekanisme `CURRENT` (§6), sambil
   P0-3 dikerjakan terpisah.
3. Perluas backtest (lebih banyak fold/window lebih panjang) dulu sebelum
   memutuskan apa pun - satu backtest 6-fold belum tentu cukup meyakinkan
   untuk keputusan produksi.
4. Tidak lakukan apa-apa untuk saat ini, catat sebagai known-issue dan
   revisit di retrain siklus berikutnya.

**Tidak ada perubahan pada `CURRENT` atau model production dari temuan
ini.** Model yang dilatih selama backtest tidak disimpan.

---

## 11 · Antrian resmi digerbang presisi, bukan lagi diisi sampai kapasitas tetap (menggantikan §9)

**Status**: berlaku, 2026-08-25. Menggantikan kebijakan §9 UNTUK ANTRIAN
RESMI. Detail eksperimen: `docs/EXPERIMENTS.md` E-46, E-47, E-48.

**Keputusan** `/api/v1/recommendations` (`official_queue_only=true`,
DEFAULT) hanya mengembalikan PART dengan `failure_probability_30d >=`
`metadata["gate"]["threshold"]` model failure `CURRENT` - bukan lagi
seluruh armada terurut `tier_score` dipotong di angka kapasitas pilihan
user. Ukuran antrian jadi **dinamis** (bergantung berapa PART yang
benar-benar melewati ambang), dan **boleh nol** (`{"total": 0, "items":
[]}`) kalau memang tidak ada PART yang cukup meyakinkan - model
"abstain", bukan dipaksa merekomendasikan sejumlah tertentu.
`official_queue_only=false` mengembalikan perilaku lama (§9, mode
eksplorasi, dipertahankan penuh untuk drill-down manual/dashboard capture
curve).

**Threshold** dicari HANYA dari VALIDATION (`gate.select_precision_
constrained_threshold()`, `sklearn.precision_recall_curve`, recall
dimaksimalkan dengan syarat presisi >= `config.FAILURE_GATE_TARGET_
PRECISION`), lalu diuji SEKALI di TEST (`gate.honest_test_evaluation()`) -
tidak pernah dicari ulang di TEST. Dihitung otomatis setiap retrain
(`train.py::main()` -> `compute_gate()` -> blok `gate` di `metadata.json`,
aditif, key lama `risk_cutoffs`/`features`/`part_model_support`/kalibrator
TIDAK tersentuh). Untuk artifact yang dilatih sebelum fitur ini ada
(v4): `python -m partrisk.cli attach-gate` (sekali saja, dijalankan
2026-08-25).

**Kenapa target presisi 0,40, BUKAN 0,85**: 0,85 diminta di awal, tapi
TERBUKTI tidak genuinely generalize di TEST untuk model/horizon/data
apa pun yang diuji (E-46 horizon 7/14/30 hari semua gagal; E-47
mendiagnosis penyebab strukturalnya - ~38% kerusakan TEST dari populasi
yang modelnya buta total, dan ekor skor terlalu tipis untuk presisi
ekstrem yang stabil; E-48 satu-satunya data baru yang plausibel
(`journal.t_mtbf`) terbukti tidak bisa diuji dengan skema training
historis yang ada - cakupan TRAIN 0,0000%). 0,40 dipilih user secara
eksplisit (presisi lebih diutamakan daripada volume - "selama presisinya
tinggi tidak apa 2 bulan sekali") dari sweep threshold yang genuinely
generalize (lihat E-47 untuk tabel lengkap 0,30/0,35/0,40/0,45+).
`v4` per 2026-08-25: threshold 0,3750, TEST presisi 0,625, recall 0,0055,
8 alert (~1/bulan pada window TEST ~7-8 bulan).

**Field baru** (aditif, tidak mengubah kontrak lama): `gate_flagged`
(bool, per PART, `serving_batch.py`), `official_queue_size` (int, di
`summary()`/`/api/v1/overview`). `tier_score` tetap ada dan tetap
mengurutkan mode eksplorasi - lihat §9 (bagian yang masih berlaku).

**Alternatif yang ditolak**: mengubah `risk_cutoffs`/`FAILURE_HIGH_
PROBABILITY_THRESHOLD` supaya jadi gerbang - ditolak, itu ambang label
warna yang dipilih dari sebaran skor (lihat §9/`docs/METHODOLOGY.md`),
tercampur dengan gerbang presisi akan melanggar invariant "ambang risiko
dibekukan saat training, layer rekomendasi tidak punya ambang sendiri"
DAN mencampur dua tujuan berbeda (label deskriptif vs keputusan gerbang).
Karena itu `gate.threshold` disimpan TERPISAH di blok `gate`, bukan
menimpa `risk_cutoffs`.

**Verifikasi**: `serving_batch.py::filter_scores(..., official_queue_only=True)`
menyaring `gate_flagged`; `tests/test_api.py` menguji jalur antrian kosong
(`{"total": 0, "items": []}`, tanpa error) dan `official_queue_only=false`
mereproduksi daftar lengkap lama.

---

## 12 · Target presisi 85% TIDAK dikejar lagi - ceiling ~0,30-0,45 diterima, pencarian fitur/model struktural ditutup (FASE 8)

**Status**: berlaku, 2026-08-26. Menutup arahan awal FASE 8 ("maximize
recall dengan syarat presisi >= 85%") sebagai target yang TIDAK tercapai
dengan data yang tersedia sekarang - bukan pembatalan gerbang presisi itu
sendiri (§11 tetap berlaku, `FAILURE_GATE_TARGET_PRECISION=0,40` tetap
jadi target operasional).

**Keputusan** Setelah 13 eksperimen struktural (fitur baru, teknik
training, arsitektur model - `docs/EXPERIMENTS.md` E-49 s/d E-62, metodologi
lifecycle/first-alert dan rolling backtest 6-fold disiplin di semua),
TIDAK SATU PUN menghasilkan perbaikan recall yang tervalidasi dan
material. User memutuskan: **terima ceiling presisi genuinely-generalize
~0,30-0,45 (E-47) sebagai titik operasi**, hentikan pencarian fitur/model
tambahan, alihkan effort ke pengerasan production (monitoring, stabilitas,
dokumentasi) - BUKAN terus mencari terobosan yang 13 percobaan berturut-
turut tidak menemukan.

**Dasar keputusan** (ringkasan `docs/EXPERIMENTS.md` E-62): 99,4% kerusakan
TEST terlewat model production, skor median untuk yang terlewat cuma 0,05
(JAUH di bawah threshold manapun - bukan soal kalibrasi/threshold). Dua
populasi blind-spot besar dengan penyebab BERBEDA: first-failure/tanpa-
histori-corrective (37,3% FN) dan late-life/histori-tenang (40,1% dari
SEMUA kerusakan TEST, recall 0%, ditemukan E-58) - keduanya TIDAK
terjawab oleh fitur histori-PART, fitur fleet-level, hard-negative mining,
lifecycle weighting, maupun percobaan discrete-time hazard model. Data
device-monitoring yang plausibel jadi proxy intensitas pemakaian terbukti
kosong total di database ini (E-48).

**Yang TETAP dipertahankan sebagai infrastruktur permanen** (aditif,
tidak mengubah gerbang/model production yang berjalan):
- `gate.py::lifecycle_metrics()`/`select_lifecycle_threshold()` (E-49) -
  metodologi evaluasi resmi untuk kandidat MANA PUN ke depan.
- `cli.py::rolling-lifecycle-backtest` (baru) - WAJIB dijalankan untuk
  kandidat baru sebelum diklaim sebagai perbaikan; satu TEST split
  terbukti tidak cukup pada skala alert serendah ini (E-55).
- `train.py::compute_gate()` blok `gate.lifecycle` (informasional,
  tidak mengubah threshold yang dipakai `gate_flagged`).
- `serving/alerts.py` (alert lifecycle, dedup, `resolve-alert`) dan
  perluasan `/api/v1/monitoring/metrics/failure` (precision/recall/FP/FN/
  lead-time offline, alert count + umur alert live) - lihat commit sesi
  ini.

**Kapan revisit**: kalau ada sinyal BARU yang genuinely belum diuji (data
eksternal di luar database ini, atau QC/MTBF pada window 2025+ terbatas -
E-48, meski E-58 menunjukkan itu tidak akan menjawab populasi FN
terbesar). Jangan re-buka pencarian fitur/model berbasis data yang SUDAH
ada di database ini tanpa sinyal baru yang belum dicoba - `docs/
EXPERIMENTS.md` E-62 berisi daftar lengkap yang sudah gagal supaya tidak
diulang.

**Update 2026-08-26 (lanjutan sesi) - MTBF pada window 2025+ TERBATAS
menang lintas 3 fold (E-66)**: jalur yang disebut di atas SEBAGAI OPSI
ternyata BERHASIL ketika benar-benar dicoba - ROC-AUC/PR-AUC naik
konsisten di 3 fold berbeda (bukan artefak satu split, lihat E-66 untuk
metodologi lengkap). **Status "pencarian struktural ditutup" TIDAK LAGI
berlaku utuh** - MTBF window-terbatas adalah kandidat produksi nyata,
BELUM dipromosikan karena butuh keputusan arsitektur (model TERPISAH
untuk window 2025+ vs model penuh-histori v4 yang ada, trade-off volume
TRAIN vs sinyal MTBF - lihat catatan penting di E-66). Keputusan
promosi dikembalikan ke user. Tooling pemantauan berkala:
`python -m partrisk.cli train-mtbf-candidate` (E-68).

**Update 2026-08-26 (lanjutan sesi, Langkah L1) - two-stage cascade (E-67)
DITOLAK setelah rolling backtest**: kandidat kedua yang sempat terlihat
kompetitif di satu split ternyata artefak SATU fold juga (pola sama
dengan E-55/E-59) - kalah di 4 dari 5 fold lain. **MTBF (E-66/E-68) TETAP
SATU-SATUNYA kandidat Fase 8 yang lolos validasi multi-fold.** Pencarian
arsitektur/teknik-training tambahan (di luar memantau MTBF berkala) TIDAK
direkomendasikan dibuka lagi tanpa sinyal genuinely baru.

---

## 13 · Gerbang promosi model (failure & survival) dipindah dari TEST ke VALIDATION - P0-3 dikerjakan

**Status**: berlaku, 2026-08-27. Menutup sebagian §10 (opsi 1: "kerjakan
P0-3 dulu, baru evaluasi rollback v3-vs-v4 lewat jalur resmi").

**Masalah**: audit repo menyeluruh (diminta user, cakupan Q1/Q2/Q3 +
serving/API/DB + production-readiness) menemukan ulang persis apa yang
sudah dicatat §10/E-44 - `decide_promotion()` di
`engines/failure/train.py` dan `decide_survival_promotion()` di
`engines/survival/train.py` membandingkan kandidat vs incumbent di TEST
pada **SETIAP retrain**, bukan sekali. Ini pola textbook TEST-adaptation:
E-44 membuktikan efeknya nyata (VALIDATION PR-AUC turun 0,1174->0,1116
sementara TEST naik 0,1884->0,1961 lintas 4 promosi v4). Temuan ini
sempat dicatat sebagai flag terbuka di §10 tapi mekanismenya sendiri
tidak pernah diperbaiki sampai sekarang.

**Perbaikan** (`src/partrisk/engines/failure/train.py`,
`src/partrisk/engines/survival/train.py`):
- `decide_promotion()` dan `decide_survival_promotion()` dapat parameter
  `split_label` (default tetap `"TEST"` untuk kompatibilitas mundur -
  lihat scrap di bawah) murni untuk label pesan/metadata, TIDAK mengubah
  cara metrik dihitung.
- `main()` failure: candidate & incumbent SEKARANG dievaluasi juga di
  VALIDATION (`evaluate_incumbent(..., split=VALIDATION)` - parameter ini
  sudah ada sebelumnya, tinggal dipakai), dan hasil VALIDATION itulah yang
  dikirim ke `decide_promotion(..., split_label="VALIDATION")` sebagai
  dasar keputusan. Metrik TEST TETAP dihitung, dicetak, dan disimpan penuh
  di `metadata.json` (`comparison["test_informational"]`) - murni untuk
  laporan/audit, TIDAK lagi memengaruhi keputusan promosi.
- `main()` survival: `metrics["random_survival_forest"]["validation"]`
  (sudah selalu dihitung sebelumnya, cuma belum dipakai untuk gerbang)
  sekarang jadi dasar `decide_survival_promotion()`. TEST tetap
  dicetak untuk laporan.
- Gerbang threshold presisi (`gate.py`, §11) dan gerbang promosi versi
  model (di atas) sekarang KONSISTEN - keduanya VALIDATION-driven,
  TEST-touch-informational.

**Scrap TIDAK ikut diperbaiki di pass ini** (`engines/scrap/train.py`):
scrap tidak punya split VALIDATION terpisah (hanya TRAIN/TEST via
`SCRAP_TEST_START` tetap), dan positif TEST-nya sudah sangat sedikit
(~21, E-45) - memecah lagi berisiko membuat evaluasi makin tidak bisa
dipercaya, bukan lebih baik. Risiko TEST-leakage yang sama SECARA
STRUKTURAL tetap ada di sana (dicatat lewat komentar WHY di kode) tapi
BELUM diperbaiki - butuh keputusan terpisah (mis. pakai rata-rata
rolling-cutoff backtest `compare_models()` yang sudah ada sebagai basis
promosi, bukan TEST tunggal) sebelum dikerjakan, supaya tidak
memperkecil data uji yang sudah tipis tanpa manfaat jelas.

**Belum dikerjakan** (di luar scope pass ini, dicatat supaya tidak
hilang):
- Menjawab ulang pertanyaan asli §10 (v3 vs v4) lewat mekanisme yang
  sudah diperbaiki - butuh retrain baru + keputusan manusia.
- Menyambungkan gerbang promosi survival ke metrik MAE-median/kalibrasi
  (saat ini masih Brier@30d/90d saja, walau sudah VALIDATION-based).
- Perbaikan serupa untuk scrap (lihat di atas).

**Tidak ada perubahan pada model `CURRENT` production dari perubahan
mekanisme ini sendiri** - retrain berikutnya yang akan memakai jalur
baru ini.

---

## 14 · Hierarki Terminal → PART - agregasi prediction yang sudah ada, BUKAN model baru

**Status**: berlaku, 2026-08-27.

**Masalah**: mekanik secara operasional melihat Terminal dulu, baru PART
di dalamnya - dashboard sebelumnya hanya menampilkan daftar PART datar.
Diminta user: kelompokkan PART per Terminal FISIK dari data yang benar-
benar ada di database, tanpa model/skor baru khusus Terminal, dan tanpa
memaksakan PART yang relasi Terminal-nya tidak bisa dipastikan masuk ke
kelompok manapun.

**Temuan penting soal data**: `data_reader.get_terminal_context()` sudah
ada (dipakai fitur survival, `features_survival.attach_terminal_extra()`)
tapi cuma pernah dipakai untuk TIPE Terminal (`terminal_type_clean`),
BUKAN identitas Terminal fisik. `terminal_inventory_item_id` (FK ke
`inventory.t_item.item_id`, hasil join `parent_serial_code` -> pairing
code inventory) sudah dihitung di CTE-nya sejak awal tapi TIDAK PERNAH
di-SELECT keluar - ditambahkan sekarang (lihat WHY di kode) karena baru
sekarang ada konsumen yang butuh identitas fisik, bukan cuma tipe.

Relasi parent-Terminal punya `parent_link_quality_status` (6 nilai,
lihat docstring `get_terminal_context()`) - hanya
`VALID_POINT_IN_TIME_RELATION` dan `VALID_RELATION_RECORDED_AFTER_
INSTALLATION` dipakai untuk mengelompokkan (PART dengan status lain -
UNMATCHED_INSTALLATION_REQUEST/MISSING_PARENT_SERIAL/PARENT_NOT_
TERMINAL/PARENT_TERMINAL_NOT_IN_INVENTORY - relasi parent-nya tidak bisa
dipastikan). PART tanpa relasi yang bisa dipercaya TIDAK masuk kelompok
manapun (`terminal_id` tetap kosong) - dilaporkan apa adanya lewat
`parts_without_terminal` di `/api/v1/terminals`, bukan disembunyikan atau
dipaksa masuk satu kelompok "lainnya".

**Implementasi** (`src/partrisk/core/data_reader.py`,
`src/partrisk/serving/batch.py`, `src/partrisk/api/schemas.py`,
`src/partrisk/api/app.py`, `dashboard/`):
- `get_terminal_context()`: SELECT ditambah `terminal_inventory_item_id`
  dan `terminal_serial_code_clean` (label fisik, dari `parent_serial_
  code` yang sudah dibersihkan) - tidak ada query/join baru, kolomnya
  sudah lama dihitung di CTE.
- `batch.py::_attach_terminal()`: menempelkan `terminal_id`/
  `terminal_label`/`terminal_model_name` ke frame prediction per-PART
  yang SUDAH ADA (pola sama dengan `_attach_context()` untuk lokasi) -
  murni join, bukan komputasi baru.
- `batch.py::terminal_summary()`/`terminal_overview()`: AGREGASI dari
  prediction per-PART yang sudah dihitung (`failure_risk_level`,
  `tier_score`, `median_days_to_failure`) - PART paling berisiko dan
  perkiraan kerusakan terdekat per Terminal diambil langsung dari nilai
  itu, tidak ada skor/model baru untuk Terminal.
- `filter_scores()` dapat parameter `terminal_id` (pola sama dengan
  `location`) - endpoint `/api/v1/recommendations` yang sudah ada
  langsung bisa dipakai untuk "lihat semua PART di satu Terminal", tidak
  perlu endpoint detail terpisah.
- Endpoint baru `GET /api/v1/terminals` - ringkasan per Terminal
  (jumlah PART, sebaran risiko, PART paling berisiko, perkiraan
  kerusakan terdekat), dipakai sebagai daftar navigasi.
- `PriorityItem` dapat field `terminal_id`/`terminal_label`/
  `terminal_model_name` - tampil di SETIAP PART di mana pun (bukan cuma
  endpoint Terminal), sesuai permintaan mekanik ingin lihat Terminal
  langsung dari daftar PART.
- Halaman dashboard baru `pages/4_Terminal.py`: daftar Terminal
  (terurut risiko) + pilih satu Terminal untuk lihat seluruh PART di
  dalamnya (reuse `ui.priority_table`, tabel PART yang sama dengan
  halaman lain, bukan komponen baru).

**Yang SENGAJA tidak dikerjakan** (hindari overengineering,
`docs/EXPERIMENTS.md` sesi ini): tidak ada model/skor risiko khusus
Terminal - `high_risk_parts`/`medium_risk_parts`/dst murni hitungan dari
`failure_risk_level` per PART yang sudah divalidasi lewat model Q2 yang
ada. Tidak ada koordinat peta untuk Terminal (beda dari `/locations/map`)
- di luar scope permintaan, bisa ditambah nanti kalau dibutuhkan lewat
`location` yang sudah ikut disertakan di ringkasan Terminal.

---

## 15 · Autentikasi API - API key opsional (opt-in lewat env var), pola sama dengan CORS

**Status**: berlaku, 2026-08-27.

**Masalah**: audit repo menyeluruh menemukan `api/app.py` sama sekali
tidak punya autentikasi - seluruh rute terbuka untuk siapa pun yang bisa
menjangkau port-nya. Proteksi yang ada murni infrastruktur
(`docker-compose.yml` bind `127.0.0.1` saja), bukan aplikasi - begitu ada
reverse proxy/expose ke jaringan lebih luas (disebut sebagai rencana
di komentar `docker-compose.yml`), API jadi benar-benar terbuka.

**Keputusan**: `API_KEY` (env var) - **opt-in**, pola PERSIS sama dengan
`CORS_ALLOW_ORIGINS` yang sudah ada. Kalau kosong (default, termasuk dev
lokal dan CI tanpa konfigurasi tambahan): semua endpoint tetap terbuka
seperti sebelumnya, TIDAK ADA perubahan perilaku. Kalau diisi: setiap
request ke `/api/v1/*` WAJIB header `X-API-Key` yang cocok, kecuali
`/health` (dipakai health-checker/orkestrator tanpa kredensial, tidak
membocorkan data bisnis).

**Implementasi** (`src/partrisk/api/app.py`, `dashboard/api_client.py`,
`.env.example`): `require_api_key()` sebagai FastAPI dependency,
dipasang lewat `app.include_router(..., dependencies=[Depends(
require_api_key)])` per router (bukan per-route atau lewat middleware) -
supaya `/health` gampang dikecualikan tanpa if/else tersebar. Dashboard
(`api_client.py`) membaca `API_KEY` yang SAMA dari `.env` (diteruskan ke
kedua container lewat `env_file:` yang sama di `docker-compose.yml`) dan
mengirimkannya sebagai header di setiap panggilan.

**Kenapa opt-in, bukan wajib**: mengunci endpoint secara default akan
memutus dev lokal dan CI yang belum (dan tidak perlu) mengonfigurasi
kredensial - shared-secret sederhana ini levelnya "jangan biarkan
port terbuka polos begitu saja", bukan sistem user/role. Kalau nanti
butuh multi-user/permission granular, itu keputusan terpisah yang lebih
besar (di luar scope perbaikan ini).

**Belum dikerjakan** (dicatat, bukan diabaikan): tidak ada rate-limiting,
tidak ada rotasi key, tidak ada audit-log siapa yang memanggil apa -
sengaja tidak ditambah sekarang (hindari overengineering untuk kebutuhan
yang belum ada bukti nyatanya).

---

## 16 · CI (GitHub Actions) - DIBUAT lalu DIHAPUS, belum diinginkan sekarang

**Status**: TIDAK berlaku, 2026-08-27. `.github/workflows/tests.yml`
sempat ditambahkan (workflow `pytest` di `ubuntu-latest`, subset test
pure-logic karena tidak ada database di CI, lint `ruff` non-blocking
karena ~260 pelanggaran lama) tapi diminta dihapus lagi sebelum sempat
di-commit. Dicatat di sini supaya tidak diusulkan ulang tanpa konteks -
audit masih menandai "tidak ada CI" sebagai gap production-readiness,
tapi user belum mau menambahkannya sekarang. Revisit kalau diminta lagi.

---

## 17 · Prediction-outcome logging - SENGAJA TIDAK dikerjakan, gap yang diterima sadar

**Status**: berlaku, 2026-08-27. Keputusan TIDAK bertindak, bukan lupa.

**Masalah**: audit repo menyeluruh menemukan tidak ada cara membandingkan
prediksi dengan kejadian nyata belakangan (prediksi tidak pernah dicatat
dengan ID untuk direkonsiliasi) - akurasi real-world hanya bisa diketahui
lewat backtest offline, tidak pernah dari kinerja production sungguhan.

**Kenapa tidak dikerjakan**: solusi apa pun untuk ini butuh MENYIMPAN
sesuatu (log prediksi, minimal timestamp+item_id+skor) di luar database
operasional read-only. Ini berlawanan langsung dengan batasan eksplisit
user di awal sesi ini untuk mekanisme alert (§ alert lifecycle,
`serving/alerts.py`): "no persistence at all, even a file". Ditanyakan
ulang secara eksplisit untuk kasus prediction-logging ini - user memilih
tetap TIDAK menambah penyimpanan apa pun, konsisten dengan batasan awal.

**Implikasi yang diterima sadar**: kinerja model di production TETAP
hanya bisa diverifikasi lewat backtest temporal offline (`rolling-
backtest`, `rolling-lifecycle-backtest` di `cli.py`) - tidak akan pernah
ada laporan "dari 100 alert HIGH bulan lalu, berapa yang benar rusak".
Kalau prioritas berubah nanti, jalur paling ringan (belum diimplementasi)
adalah tabel append-only kecil di database yang SAMA (bukan file lokal
terpisah) - tapi itu juga menyentuh batasan "database read-only" yang
berlaku di seluruh proyek, jadi tetap butuh keputusan arsitektur
terpisah, bukan default.

---

## 18 · `current_observations()` menyaring PART yang sudah dilepas (RETURNED/DISMANTLED) dari populasi aktif

**Status**: berlaku, 2026-08-27. Perbaikan korektnes dari temuan audit
E-71 (`docs/EXPERIMENTS.md`).

**Masalah**: `cycle_end_reason='RIGHT_CENSORED_AT_DATA_END'` (dipakai
`current_observations()` untuk menentukan populasi "aktif" yang di-skor
SEKARANG) cuma berarti "belum ada event INSTALLED berikutnya tercatat" -
BUKAN "masih benar-benar terpasang". PART yang sudah RETURNED/DISMANTLED
tapi belum sempat dipasang ulang tetap lolos filter ini. Diverifikasi:
~18,4% populasi yang dianggap aktif statusnya TERBARU bukan INSTALLED.

**Perbaikan** (`src/partrisk/core/features.py`): `current_observations()`
sekarang menerima `events` (selain `cycles`) dan menyaring lewat
`_still_installed()` - status event TERBARU tiap PART (per
`item_identifier_clean`, diurutkan `created_on`+`journey_id`) harus
`INSTALLED` supaya tetap dianggap aktif. 5 titik pemanggilan diupdate
(`cli.py`, `serving/batch.py`, `serving/single.py`, `engines/predict.py`,
`engines/failure/train.py::active_part_scores`) - SEMUA jalur yang
memakai populasi aktif (serving live, training-time cutoff calibration,
CLI pipeline report) ikut konsisten, bukan diperbaiki sebagian.

**Dampak terukur**: populasi aktif turun dari 16.877 ke 13.775 PART
(-18,4%) pada snapshot data saat perbaikan ini dibuat.

**Konsekuensi pada test lama**: `test_populasi_batch_sama_dengan_yang_
dipakai_menyetel_ambang` (`tests/test_lifecycle.py`) akan GAGAL sampai
model `v4` yang sedang production di-retrain - test itu membandingkan
populasi live SEKARANG dengan `cutoff_basis["active_parts_scored"]`
yang tersimpan di metadata v4, yang dihitung SEBELUM perbaikan ini ada
(16.877, bukan 13.775). Ini SAH/diharapkan, bukan regresi - metadata v4
memang jadi usang relatif terhadap definisi populasi yang baru, sampai
retrain berikutnya menghitung ulang `active_part_scores()` (yang sudah
otomatis memakai `current_observations()` yang sudah diperbaiki).
Retrain v4 BELUM dilakukan di sesi ini - itu keputusan produksi terpisah
(bisa menggeser `risk_cutoffs`), dikembalikan ke user.

**Update 2026-08-31**: gap `get_cycles()` yang dicatat di sini sudah
ditutup oleh keputusan §20. Filter `_still_installed()` tetap dipakai
sebagai defense-in-depth untuk status baru yang belum dikenali sebagai
terminator lifecycle.

---

## 19 · Login dashboard - password tunggal opsional, pola sama dengan §15 (API key)

**Status**: berlaku, 2026-08-27. `dashboard/ui.py::require_login()`.

**Keputusan**: `DASHBOARD_PASSWORD` (env var) - **opt-in**, pola PERSIS
sama dengan `API_KEY` (§15). Kosong (default) = dashboard tetap terbuka
seperti sebelumnya. Diisi = setiap halaman menampilkan layar login
(`st.text_input(type="password")` dalam `st.form`) sebelum konten apa
pun dirender - `st.session_state["authenticated"]` bertahan antar
halaman dalam satu sesi browser, jadi login sekali berlaku untuk semua
halaman multi-page.

**Implementasi**: dipasang di `page_setup()` (dipanggil di AWAL setiap
halaman) - otomatis melindungi seluruh halaman tanpa menyentuh tiap file
satu-satu. `dashboard/api_client.py` sekarang panggil `load_dotenv()`
(sebelumnya TIDAK - hanya mengandalkan `env_file:` docker-compose atau
env shell; tanpa ini `DASHBOARD_PASSWORD`/`API_KEY` tidak terbaca sama
sekali saat dashboard dijalankan native `streamlit run`, di luar Docker).

**Konsekuensi pada test**: 4 test AppTest-based di `tests/test_serving.py`
(`test_halaman_bisa_dirender`, `test_detail_part_menampilkan_angka`,
`test_filter_lokasi_terisi_otomatis_dari_peta`,
`test_detail_part_menjelaskan_yang_tidak_bisa_diskor`) sekarang set
`app.session_state["authenticated"] = True` SEBELUM `.run()` pertama -
menguji halaman untuk pengguna yang SUDAH login (skenario nyata), bukan
gerbang login itu sendiri.

**Catatan**: shared-secret sederhana (satu password untuk semua orang),
bukan sistem user/role - level "jangan biarkan dashboard terbuka polos",
sama seperti alasan opt-in di §15.

---

## 20 · Lifecycle dataset ditutup saat PART dilepas

**Status**: berlaku, 2026-08-31. Menutup gap P0 yang dicatat E-71 dan
§18 untuk TRAIN/VALIDATION/TEST, bukan perubahan yang dimotivasi oleh
kenaikan metrik model.

**Keputusan**: `get_cycles()` sekarang menutup cycle pada event pertama
setelah `INSTALLED`: `FAILURE`, `DISMANTLED`, `RETURNED`, instalasi
berikutnya, atau batas data. Histori lama menyimpan banyak pengembalian
PART sebagai `status=OK, activity=RECEPTION`; pola itu dinormalisasi
menjadi `cycle_end_reason='RETURNED'`. Jika satu `DISMANTLED` sekaligus
failure onset, `FAILURE` menang pada timestamp/journey yang sama. Event
failure setelah pelepasan tidak lagi ditempelkan ke cycle lama, dan
`INSTALLED` setelah pelepasan membuka cycle baru.

Pelepasan non-failure diperlakukan sebagai censoring, bukan otomatis
sebagai negatif sepanjang horizon: observasi klasifikasi hanya eligible
sampai `cycle_end_on - horizon`; outcome survival dicensor tepat pada
`cycle_end_on`. `_still_installed()` tetap menjadi defense-in-depth pada
jalur serving.

**Dampak snapshot data**: 24.291 cycle terdiri dari 13.857 right-censored
di batas data, 5.937 failure, 3.455 returned, 483 dismantled non-failure,
dan 559 reinstall tanpa failure tercatat. Cohort aktif menjadi 13.767
PART dan 100% punya status event terbaru `INSTALLED`. Ada 168 onset yang
sebelumnya dipasang ke cycle lama walaupun terjadi setelah pelepasan;
onset itu sekarang tidak lagi menjadi failure cycle tersebut.

Dataset dibangun ulang in-memory oleh `rolling-lifecycle-backtest`:
366.965 observasi eligible, 5.919 failure. Audit invariant menghasilkan
0 observasi pada/setelah waktu pelepasan dan 0 label negatif eligible
melewati batas confirmable. Hasil rolling lengkap dicatat di E-81.
Tidak ada artifact model production yang dilatih, dipromosikan, atau
diubah oleh keputusan korektnes ini.

---

## 21 · Survival (Q1) dan Scrap (Q3) DIHAPUS dari scope production - SUPERSEDES §1

**Status**: berlaku, 2026-09-03. **MENGGANTIKAN §1** ("Survival event-based
TIDAK cutover CatBoost") untuk keputusan scope - §1 sebelumnya mempertahankan
Survival sebagai model advisory permanen; keputusan itu sekarang dibalik
sepenuhnya atas instruksi eksplisit user (bukan temuan teknis baru) untuk
menyederhanakan sistem menjadi murni failure-prediction, terintegrasi dengan
aplikasi eksternal untuk keputusan lifecycle/intervention/alert. §1
DIPERTAHANKAN APA ADANYA sesuai konvensi append-only dokumen ini (riwayat
kenapa Survival awalnya TIDAK menggantikan CatBoost tetap valid secara
historis), tapi implikasinya ("Survival masuk mode aditif...") tidak lagi
berlaku - Survival sudah tidak ada di kode sama sekali.

**Yang dihapus** (Milestone 1 dari refactor Terminal->Part->Item):
- `src/partrisk/engines/survival/` (train.py, predict.py, curve.py) dan
  `src/partrisk/core/features_survival.py` - model RSF/Cox Q1 beserta
  seluruh feature builder landmark-nya.
- `src/partrisk/engines/scrap/` - model LogReg+RF Q3.
- `src/partrisk/engines/failure/train_mtbf_candidate.py` dan
  `models/failure_mtbf_2025plus/` - kandidat Q2 eksperimental (E-66/E-68)
  yang belum pernah dipromosikan ke `CURRENT`; dihapus bersamaan karena
  tooling yang memproduksinya (`train-mtbf-candidate` CLI) tidak lagi punya
  tempat setelah cleanup, bukan karena kandidatnya sendiri gagal.
- `models/survival/`, `models/scrap/` - seluruh artifact versi.
- Field API: `scrap_probability`, `scrap_risk_level`, `scrap_risk_basis`,
  `death_probability_30d`, `median_days_to_failure`,
  `days_until_survival_90pct`, `days_until_risk_medium`,
  `days_until_risk_high`, `survival_curve`, `survival_risk_Nd`,
  `replacement_candidate` (dan filter `replacement_candidates_only`),
  prioritas `CRITICAL` (kombinasi kerusakan+scrap yang sekarang tidak
  mungkin terjadi).
- Endpoint `GET /api/v1/parts/{item_id}/scrap`,
  `GET /api/v1/monitoring/metrics/scrap`.
- CLI: `evaluate-survival`, `audit-scrap-outcomes`, `train-mtbf-candidate`,
  dan blok scrap/survival di `bootstrap-ci`.
- Dashboard: halaman `4_Perencanaan_Penggantian.py` (murni stock-planning
  berbasis scrap - eksplisit di luar scope baru, lihat alasan di bawah),
  bagian "MODEL KONDISI PASCAKERUSAKAN" di `6_Sistem.py`, kolom
  waktu-tersisa (`estimasi_bulan_rusak`, `days_until_survival_90pct`) di
  semua tabel prioritas.

**Yang TETAP** (tidak tersentuh oleh cleanup ini - lihat §2, §4 untuk
invariant terkait): model kerusakan (Q2, CatBoost) dengan seluruh
metodologi gerbang presisinya (§11), alert lifecycle in-memory
(`serving/alerts.py`, §17 - BELUM diganti persistent, itu Milestone 5
terpisah), agregasi Terminal->PART (§14).

**Kenapa** (bukan temuan model baru, murni keputusan scope): permintaan
eksplisit user untuk fokus ulang sistem menjadi failure-prediction murni
yang terintegrasi dengan aplikasi eksternal (lifecycle installation
cycle/intervention/alert persistent) - lihat master-prompt refactor
Terminal->Part->Item. Kebutuhan stok/penggantian spare part ditentukan
sepenuhnya oleh teknisi/aplikasi eksternal, bukan lagi oleh model di
sini - eksplisit di luar scope, bukan berarti pertanyaannya tidak penting.

**Dampak pada §1**: kalimat "Kalau mau dibuka lagi: butuh perbaikan nyata
di Recall@kapasitas DAN Precision@kapasitas survival..." di §1 tidak lagi
relevan sebagai jalur reopen - membuka Survival lagi sekarang berarti
membangun ulang dari nol (kode sudah dihapus), bukan sekadar mengubah
keputusan cutover. `docs/EXPERIMENTS.md` (E-01 dst) TETAP dipertahankan
sebagai riwayat penelitian, sesuai konvensi append-only - tidak dihapus
meski kode yang dideskripsikannya sudah tidak ada.

**Verifikasi**: `grep -rn "survival\|scrap" src/ --include=*.py -i` hanya
boleh muncul sebagai (a) variabel lokal hazard-chaining di
`engines/predict.py`/`serving/batch.py` (`survival = 1.0 - hazard`, istilah
matematika umum, BUKAN model Q1), atau (b) komentar/docstring yang
menjelaskan riwayat penghapusan ini.

---

## 22 · Predictive DB satu server dengan schema baru, BUKAN server Postgres kedua

**Status**: berlaku, 2026-09-03. Milestone 2 dari refactor Terminal->Part->Item.

Rencana awal (dipilih user via pertanyaan eksplisit sebelum Milestone 1
dimulai) adalah menambah **server Postgres kedua** (docker-compose) khusus
untuk data predictive, terpisah total dari database operasional. Dibatalkan
setelah user mengklarifikasi: akan ada scheduler EKSTERNAL (di luar repo
ini, dikelola tim/infra lain) yang pull/duplicate data dari database
production ke database yang SAMA yang dipakai proyek ini (`OMNEW` di `.env`
lokal saat ini) - jadi database yang sudah dikonfigurasi BUKAN production
langsung, melainkan salinan yang sudah diisolasi. Server Postgres kedua jadi
tidak perlu; cukup schema baru (`predictive`) di database yang sama.

**Implementasi** (`migrations/predictive/0001_init.sql`,
`src/partrisk/predictive/db.py`, `src/partrisk/predictive/scoring.py`):
- `core/data_reader.py` TIDAK berubah - tetap baca schema operasional,
  tetap `default_transaction_read_only=on`. Repo ini TIDAK bertanggung
  jawab atas proses duplikasi data production->OMNEW itu sendiri (di luar
  scope, dikerjakan tim lain).
- `predictive/db.py::connect()` - koneksi TERPISAH, `search_path=
  predictive,public`, TANPA read-only. Kredensial (`config.db_settings()`)
  sama persis dengan `data_reader.py` untuk sekarang (satu set env var) -
  dibedakan murni lewat schema, bukan lewat host/kredensial berbeda.
- Tabel Milestone 2 saja: `predictive.model_run`, `predictive.item_prediction`
  (append-only). `item_cycle`/`intervention`/`alert`/`alert_event` MENYUSUL
  di migrasi Milestone 4/5, sengaja belum dibuat sekarang (hindari
  merancang skema sebelum kebutuhan konkretnya jelas).
- Penulisan prediksi dipisah dari cache batch in-memory yang dipakai
  serving (`serving/batch.py`, tidak diubah) - `predictive/scoring.py::
  run_and_persist()` dipanggil eksplisit lewat `python -m partrisk.cli
  score-and-persist`, dimaksudkan dipanggil scheduler eksternal berkala.
  Kalau dikaitkan otomatis ke tiap `score_active_parts()`, setiap
  request API/test/CLI ad-hoc akan ikut menulis baris riwayat prediksi -
  tidak diinginkan (lihat WHY di scoring.py).

**Kalau nanti perlu dipisah lagi** (skala, izin akses berbeda per tim):
`pg_dump --schema=predictive` ke server baru + ubah `.env` - tidak
menyentuh kode, karena `predictive/db.py` sudah terisolasi dari
`data_reader.py` sejak awal.

**Verifikasi**: `grep -rn "predictive" src/partrisk/core/data_reader.py`
harus kosong - `data_reader.py` tidak boleh tahu apa pun soal schema
predictive. Sebaliknya, tidak ada `INSERT/UPDATE/DELETE` di
`core/data_reader.py` (masih dipaksa DB-level lewat
`default_transaction_read_only`).

---

## 23 · Geocoding/peta lokasi dihapus - di luar scope failure-prediction

**Status**: berlaku, 2026-09-03. Permintaan eksplisit user: fitur peta
(geocoding OpenStreetMap/Nominatim, pin lokasi, `GET /api/v1/locations/map`)
tidak berhubungan dengan objektif failure-prediction sistem ini - dihapus.

**Yang dihapus**: `api/services.py` (seluruh fungsi geocoding -
`_query_nominatim`, `resolve_missing`, `known_coordinates`, dst -
`_score_distribution`/`_unknown_category_share`/`_feature_summary`/
`failure_monitoring`/`summary` TETAP, itu monitoring model bukan geocoding),
endpoint `GET /api/v1/locations/map`, schema `ResolvedLocation`/
`UnresolvedLocation`/`LocationMapResponse`, `serving/batch.py::
location_summary()` (satu-satunya konsumennya endpoint di atas), tab "Peta
Persebaran" di `dashboard/pages/3_Inspeksi.py` beserta `ui.py::
risk_marker_color/radius`, `api_client.py::locations_map()`, dan alur
`map_location_filter` (session-state relay dari tab peta ke filter Lokasi
di `1_Parts.py` - producer-nya hilang, jadi consumer-nya ikut disederhanakan).
Fixture `needs_internet` (`tests/conftest.py`) juga dihapus - satu-satunya
pemakainya geocoding dan test AppTest dashboard yang dulu ikut terkena
(halaman tidak lagi memanggil jaringan eksternal apa pun).

**Yang TETAP**: field `location` (nama lokasi tekstual dari data
operasional, mis. "GUDANG NI") dan filternya di `/recommendations` -
itu data operasional biasa, independen dari fitur peta/koordinat yang
dihapus.

**Kenapa bisa dihapus bersih tanpa mengubah perilaku Q2**: fitur peta murni
lapisan presentasi read-only di atas prediction yang sudah ada (seperti
Terminal §14) - tidak pernah memengaruhi `failure_probability_*`/
`risk_level`/`tier_score`.

---

## 24 · Monitoring drift/live dihapus - di luar scope untuk sekarang

**Status**: berlaku, 2026-09-03. Permintaan eksplisit user, sesi yang sama
dengan §23. `src/partrisk/api/services.py` HABIS isinya cuma monitoring
(geocoding sudah dihapus di §23) - file dihapus total, bukan dikosongkan.

**Yang dihapus**: endpoint `GET /api/v1/monitoring/metrics` dan
`/monitoring/metrics/failure`, `api/services.py` (seluruh isi: score
distribution, unknown-category-share, feature drift summary,
`failure_monitoring()`/`summary()`), `api_client.py::monitoring_metrics()`,
bagian "live" (PART aktif dinilai, distribusi risiko saat ini) di
`dashboard/pages/6_Sistem.py` - halaman TETAP ADA, disederhanakan jadi
murni info versi/metrik uji model dari `GET /api/v1/model` (metadata
training statis, BUKAN monitoring live).

**Yang TETAP**: `serving.describe()` (`GET /api/v1/model`) - field `gate`
ditambahkan ke situ (sebelumnya cuma muncul lewat monitoring) supaya
halaman Sistem masih bisa menampilkan status gerbang presisi tanpa endpoint
monitoring. `serving/alerts.py::open_count()`/`open_lead_times_days()`
SENGAJA TIDAK dihapus walau konsumen satu-satunya (`failure_monitoring()`)
sudah hilang - itu bagian API alert lifecycle sendiri (bukan kode
monitoring), sudah punya test dedicated, dan kemungkinan besar dibutuhkan
lagi utuh di Milestone 5 (persistent alert).

**Observability production (master-prompt §32)** dicatat sebagai gap
TERBUKA, bukan ditutup - kalau/ketika dibutuhkan lagi, revisit sebagai
bagian Milestone 7 (Production Hardening), bukan dibangun ulang sekarang.

**Update 2026-09-03 (lanjutan sesi)**: setelah §24 di atas menyisakan
halaman "Sistem" cuma menampilkan versi model (isi monitoring-nya sudah
kosong), user minta dihapus juga - `dashboard/pages/6_Sistem.py` dihapus
total. `GET /api/v1/model` (`serving.describe()`) TETAP ADA sebagai
endpoint API publik (bukan "monitoring", murni metadata versi model - field
`gate` ditambahkan langsung ke situ, lihat WHY di `serving/single.py::
describe()`), tapi `api_client.py::model_info()` dihapus karena tidak ada
lagi halaman dashboard yang memanggilnya.

---

## 25 · Item -> Installation Cycle -> Intervention (Milestone 4)

**Status**: berlaku, 2026-09-03. `migrations/predictive/0002_lifecycle.sql`.

**Desain inti**: `predictive.item_cycle` BUKAN sumber kebenaran siklus fisik
sendiri - itu tetap `core.data_reader.get_cycles()` (data operasional,
sudah lama dipakai untuk fitur training/serving Q2). `item_cycle` murni
CERMIN dari situ, disinkron on-demand per item (`predictive/cycles.py::
sync_item_cycles()`), supaya `intervention` (dan `alert` di Milestone 5)
punya foreign key stabil untuk ditempel tanpa perlu query silang ke data
operasional tiap kali. `cycle_id` REUSE `installation_cycle_id` operasional
apa adanya (format `"<item_id>:<urutan>"`) - bukan ID baru yang diciptakan
di predictive DB, menghindari kelas bug "dua sumber kebenaran untuk hal
yang sama" (persis alasan `docs/METHODOLOGY.md` `data_reader._recon_context()`
disebut di `cli.py` sebagai pelajaran masa lalu).

**Kenapa on-demand per item, bukan sinkron massal**: sistem ini murni
reaktif terhadap tindakan teknisi/aplikasi eksternal (intervention) - tidak
ada kebutuhan riil untuk tahu status cycle SEMUA item setiap saat sebelum
ada yang benar-benar menyentuhnya. Sinkron massal berkala bisa ditambah
nanti (Milestone 7) kalau ada konsumen nyata yang butuh (mis. dashboard
Terminal->Part->Item versi lifecycle) - belum dibangun sekarang, hindari
overengineering.

**Minor repair tidak membuka cycle baru** (docs §10 master prompt):
`predictive/interventions.py::record_intervention()` SELALU menempel ke
cycle AKTIF item saat dipanggil (`ensure_active_cycle()`) - intervention_seq
naik DALAM cycle itu. Cycle baru HANYA bisa terjadi lewat perubahan data
operasional (install ulang tercatat di sana), tidak pernah lewat klaim
`type=DISMANTLE` dari intervention - `type` di situ murni LABEL tindakan
yang dilaporkan, tidak (dan tidak boleh) memicu efek samping ke `item_cycle`.
Ini sengaja: kalau ternyata sebuah DISMANTLE tercatat di intervention tapi
BELUM tercatat di data operasional, `item_cycle` tidak boleh "mengaku"
cycle sudah berakhir padahal sumber kebenarannya belum bilang begitu.

**Idempotency & concurrency**: `UNIQUE(external_system, external_event_id)`
(partial, hanya kalau keduanya terisi) - lihat docs §23. Race dua
intervention untuk cycle yang sama dihindari dengan `SELECT ... FOR UPDATE`
pada baris `item_cycle`-nya sebelum menghitung `intervention_seq`
berikutnya (serialize lewat row lock, bukan retry-on-conflict) - throughput
rendah (satu teknisi, satu waktu, per PART) jadi trade-off blocking singkat
ini lebih sederhana daripada retry logic.

**Jenis intervention** (`config.INTERVENTION_TYPES`) divalidasi di
Python, BUKAN `CHECK` constraint database - menambah jenis baru cukup ubah
satu konstanta, tidak perlu migrasi (docs §11 master prompt: "jangan
hardcode... kalau domain table/enum lebih tepat", dipilih app-level list
di sini karena extensibility lebih penting daripada validasi DB-level untuk
kolom yang murni label operasional, bukan foreign key). **SUPERSEDED
sesaat kemudian, lihat update di bawah** - dipertahankan apa adanya sesuai
konvensi append-only dokumen ini.

**Update 2026-09-03 (lanjutan sesi) - klasifikasi jenis intervention
DIHAPUS**: permintaan eksplisit user - "tidak usah ada intervention type,
intinya kalau ngepost artinya ada perbaikan". Kolom `type` dan
`config.INTERVENTION_TYPES` dihapus total (migrasi `0002_lifecycle.sql`
diedit langsung, BUKAN migrasi baru yang membatalkan sebagian - tabel masih
kosong/belum pernah dipakai produksi saat perubahan ini terjadi, jadi tidak
melanggar aturan "jangan edit migrasi lama" di `docs/DATABASE.md`, yang
berlaku untuk migrasi yang SUDAH berjalan di production). `outcome`/
`action_code`/`remark` TETAP ADA sebagai detail bebas isi - yang dihapus
murni kategori terkontrolnya, bukan kemampuan mencatat detail. Satu baris
`predictive.intervention` sekarang cukup berarti "satu perbaikan terjadi",
tanpa perlu memilih kategori.

**Belum dikerjakan** (Milestone 5, di luar scope pass ini): `alert_id` di
`intervention` masih nullable tanpa FK (tabel `alert` belum ada); endpoint
`POST /api/v1/alerts/{alert_id}/interventions` BELUM dibuat - `predictive/
interventions.py::record_intervention()` baru lapisan Python/DB, belum
diekspos lewat API. Lihat catatan user: endpoint publik yang dibutuhkan
nanti HANYA POST (aplikasi eksternal baca terminal/part/item/alert langsung
dari database, bukan lewat GET API) - jadi endpoint intervention akan
dibangun di Milestone 5 sekali jalan bersama alert, bukan endpoint terpisah
sekarang yang harus diubah lagi nanti.

**Verifikasi**: test end-to-end nyata terhadap database - item dengan 11
cycle historis tersinkron benar (10 non-aktif dengan `end_reason` asli dari
data operasional, 1 aktif), dua intervention berturut-turut pada item yang
sama menghasilkan `intervention_seq` 0 lalu 1 DALAM `cycle_id` yang sama,
retry dengan `external_event_id` yang sama mengembalikan baris pertama
(bukan baris baru) - lihat `tests/test_predictive.py`.

---

## 26 · Alert persisten - Prediction -> Alert -> Resolve -> Suppression -> Re-alert (Milestone 5)

**Status**: berlaku, 2026-09-03. `migrations/predictive/0003_alerts.sql`,
`src/partrisk/predictive/alerts.py`. Menggantikan `serving/alerts.py`
(in-memory, §17 - SUPERSEDED sepenuhnya oleh milestone ini, dipertahankan
apa adanya di dokumen sesuai konvensi append-only) - status alert sekarang
selamat dari restart proses, sesuai definition-of-done master prompt §38.

**Tiga fungsi, tiga tanggung jawab terpisah tegas** (docs §2 master prompt -
"jangan mencampur ketiganya"):
- `evaluate_and_open()` - satu-satunya yang MEMBUKA alert, dipanggil SEKALI
  per siklus `predictive/scoring.py::run_and_persist()` (scheduled scoring,
  `score-and-persist`). TIDAK PERNAH dipanggil dari jalur baca live.
- `open_alerts_by_item()` - MURNI BACA, dipakai `serving/batch.py` untuk
  menandai `alert_id`/`alert_status`/`alert_score_at_open` di response
  API/dashboard. `serving/batch.py::_score_failure()` yang SEBELUMNYA
  memanggil `alert_store.register_flagged()` (MENULIS) di setiap
  `score_active_parts()` sekarang HANYA membaca - konsekuensi langsung dari
  pemisahan scheduled-write vs live-read yang sudah diputuskan Milestone 2
  (`docs/DECISIONS.md` §22, alasan yang sama: batch ad-hoc/test/API
  on-demand tidak boleh ikut menulis state).
- `resolve_with_intervention()` - satu-satunya yang MERESOLVE, SELALU
  lewat intervention tercatat (endpoint `POST /api/v1/alerts/{alert_id}/
  interventions`, satu-satunya endpoint publik yang dibutuhkan - sesuai
  klarifikasi user bahwa GET tidak perlu karena aplikasi eksternal baca
  database langsung).

**Resolve TIDAK PERNAH mengubah `item_prediction` historis** (docs §19
master prompt, invariant paling ditekankan di seluruh refactor ini) -
`resolve_with_intervention()` HANYA mengubah `alert.status`/`resolved_at`/
`resolution_reason`/`suppression_until`. Skor 0,72 yang memicu alert tetap
0,72 selamanya di `item_prediction`, walau alertnya sudah RESOLVED.

**Identitas alert** = `(item_id, cycle_id, intervention_seq)` (docs §16),
bukan cuma `item_id`. `alert.intervention_seq` disetel ke seq yang AKAN
didapat intervention yang menyelesaikannya (dihitung sama seperti
`interventions.py::_next_intervention_seq()` saat alert dibuka) - invariant
ini membuat unique index `(item_id, cycle_id, intervention_seq) WHERE
status='OPEN'` otomatis mencegah duplikat DAN otomatis membuat re-alert
jadi baris/ID baru (episode intervention_seq lebih tinggi), tanpa logic
tambahan untuk "apakah ini alert baru atau lama" (docs §24 - "alert
berikutnya adalah alert baru, jangan reopen record lama").

**Suppression & emergency override** (docs §24/§25): `ALERT_SUPPRESSION_
DAYS=14`, `ALERT_EMERGENCY_SCORE_JUMP=0.30`, `ALERT_EMERGENCY_SCORE_
ABSOLUTE=0.80` di `core/config.py` - **PLACEHOLDER eksplisit**, BEDA dari
`FAILURE_GATE_TARGET_PRECISION` (lewat 13+ eksperimen terdokumentasi,
`docs/EXPERIMENTS.md`) - belum ada riwayat resolve/re-alert produksi sama
sekali untuk divalidasi sebelum milestone ini berjalan. Emergency override
membandingkan skor sekarang terhadap `opened_score` alert TERAKHIR yang
di-resolve untuk item+cycle yang sama (bukan terhadap ambang gerbang
presisi `FAILURE_GATE_TARGET_PRECISION`, dan bukan terhadap
`suppression_until`). **Wajib direvisit begitu ada data resolve nyata.**

**Transaksi & idempotency** (docs §22/§23): `resolve_with_intervention()`
satu `with db.connect()` - validasi alert (lock `FOR UPDATE`) -> validasi
cycle masih sama (`AlertCycleMismatch` kalau item sudah pindah cycle sejak
alert dibuka - lihat WHY di kode) -> insert intervention -> update alert ->
insert 2 alert_event (INTERVENTION_RECORDED, RESOLVED) -> commit. Gagal di
tengah = ROLLBACK penuh (properti transaksi Postgres, tidak ada
try/except manual). Idempotent lewat `(external_system, external_event_id)`
- retry mengembalikan hasil yang SAMA (intervention + alert saat itu),
bukan mencoba insert lagi.

**API - hanya POST, sesuai klarifikasi user**: `GET /api/v1/alerts` atau
`GET /api/v1/alerts/{alert_id}` **SENGAJA TIDAK dibuat** - aplikasi
eksternal akan baca terminal/part/item/alert langsung dari database
(schema `predictive` perlu grant read-only untuk itu, dicatat sebagai
pekerjaan Milestone 6/7, BELUM dikerjakan). `PriorityItem` (`/recommendations`,
dipakai dashboard) dapat field `alert_id` baru; `alert_threshold_at_open`/
`alert_model_version` (field lama dari in-memory alert) DIHAPUS - tidak ada
kolom setara di `predictive.alert`, dan tidak ada consumer yang
memakainya (dashboard tidak pernah menampilkannya).

**Belum dikerjakan** (di luar scope pass ini, dicatat supaya tidak
hilang): `ACKNOWLEDGED`/`SUPPRESSED`/`NEW_ALERT_CREATED` sebagai
`alert_event.event_type` disediakan di CHECK constraint status tapi belum
ada jalur kode yang menulisnya (tidak ada endpoint "acknowledge" terpisah -
belum ada kebutuhan konkret). Grant read-only schema `predictive` untuk
aplikasi eksternal (Milestone 6/7). `alert.prediction_id` selalu NULL -
`predictive/scoring.py::record_predictions()` pakai `executemany` tanpa
`RETURNING` per baris, jadi prediction_id per item tidak pernah diambil
balik untuk ditautkan; field ini nullable dan informasional saja, tidak
menghalangi apa pun yang sudah berjalan.

---

## 27 · Auto-resolve alert saat cycle operasional tertutup - dua jalur resolve, bukan satu

**Status**: berlaku, 2026-09-03. `src/partrisk/predictive/alerts.py`.
Melengkapi §26 (Milestone 5) - bukan mengganti `resolve_with_intervention()`,
menambah jalur kedua.

**Masalah**: `resolve_with_intervention()` (§26) mengasumsikan SETIAP
perbaikan yang menutup alert akan tercatat lewat intervention (endpoint
`POST /api/v1/alerts/{alert_id}/interventions`). User mengoreksi asumsi
ini: ada dua skenario nyata di lapangan, bukan satu -
1. Teknisi melakukan worktype corrective/preventive yang berujung status
   dismantle (perbaikan besar/swap) - ini SUDAH tercatat di data
   operasional (`journal`/`installation_cycle` lewat event
   FAILURE/RETURNED/DISMANTLED, ditutup sebagai `cycle_end_reason` oleh
   §20). Memaksa teknisi mem-POST intervention manual untuk kejadian yang
   sudah tercatat sistem adalah kerja ganda.
2. Perbaikan kecil yang TIDAK PERNAH tercatat operasional (mis. mengencang-
   kan baut) - item tetap di cycle yang sama, tidak ada event apa pun di
   data operasional. Satu-satunya sinyal bahwa alert harus mati adalah
   intervention manual - jalur §26 yang sudah ada.

Sebelum perbaikan ini, alert pada skenario 1 tidak pernah mati otomatis -
`resolve_with_intervention()` yang dipanggil belakangan (mis. karena
teknisi lain mencoba mem-POST) hanya menghasilkan `AlertCycleMismatch`
mentah (item sudah pindah cycle sejak alert dibuka), tanpa menjelaskan
KENAPA - padahal itu justru tanda alert seharusnya sudah selesai.

**Keputusan - dua jalur resolve, tanggung jawab tetap terpisah tegas
(docs §2 master prompt, sama seperti §26)**:
- **Otomatis** (`auto_resolve_closed_cycles()`, fungsi baru) - untuk
  skenario 1. Membaca `predictive.item_cycle.is_active`/`end_reason`
  (hasil sinkronisasi `cycles.py::sync_item_cycles()` dari data
  operasional, §25) untuk cycle alert yang masih OPEN; kalau cycle sudah
  tertutup (`is_active=false`), alert langsung di-`UPDATE` ke RESOLVED
  dengan `resolution_reason=f"OPERATIONAL_CYCLE_CLOSED:{end_reason}"`
  (mis. `OPERATIONAL_CYCLE_CLOSED:FAILURE`) - TANPA intervention row, TANPA
  panggilan API apa pun. Dipanggil di dua tempat: (a) langkah pertama
  `evaluate_and_open()` (proaktif, menyapu semua alert OPEN sebelum
  membuka alert baru, supaya tidak menumpuk alert basi), (b) reaktif di
  dalam `resolve_with_intervention()` saat terdeteksi cycle mismatch -
  kalau ternyata cycle-nya memang sudah tertutup operasional, alert
  di-auto-resolve dulu lalu fungsi melempar `AlertNotOpen` (bukan lagi
  `AlertCycleMismatch` mentah) supaya caller tahu alert SUDAH selesai,
  bukan error yang tidak jelas maknanya.
- **Manual** (`resolve_with_intervention()`, §26, TIDAK berubah
  perilakunya untuk skenario 2) - tetap satu-satunya jalur untuk
  perbaikan yang tidak pernah muncul di data operasional. `AlertCycleMismatch`
  masih bisa terjadi untuk kasus lain yang genuinely tidak terjelaskan
  (item pindah cycle karena alasan yang bukan FAILURE/RETURNED/DISMANTLED
  yang dikenali `end_reason`) - kini kasusnya jauh lebih sempit karena
  penyebab paling umum (cycle ditutup event operasional biasa) sudah
  ditangkap otomatis lebih dulu.

**Kenapa TIDAK ada endpoint/tabel baru**: skenario 1 murni derivasi dari
data operasional yang sudah dibaca `item_cycle` (§25) - tidak ada
informasi baru yang perlu disimpan, jadi tidak ada migrasi/kolom baru.
`resolution_reason` yang membedakan asal-usul resolve (`OPERATIONAL_
CYCLE_CLOSED:*` vs `INTERVENTION_RECORDED`) cukup di kolom teks yang
sudah ada di `predictive.alert` sejak §26.

**Verifikasi**: `tests/test_predictive.py::
test_auto_resolve_closed_cycles_menutup_alert_pada_cycle_yang_sudah_berakhir`
dan `::test_resolve_with_intervention_auto_resolve_alert_pada_cycle_lama`
- keduanya memakai cycle historis SUNGGUHAN dari database (fixture
`closed_cycle`, dicari lewat `data_reader.get_cycles()` untuk baris dengan
`cycle_end_reason != RIGHT_CENSORED_AT_DATA_END`) dengan alert sintetis
yang di-insert manual lalu dibersihkan lewat fixture `cleanup_alert_ids` -
bukan mengarang data operasional, murni membuktikan alert engine bereaksi
benar terhadap cycle yang SUDAH tertutup nyata.

---

## 28 · Endpoint intervention diidentifikasi lewat host_serial_code, bukan alert_id - tabel alert_event dan kolom intervention yang mati dibuang

**Status**: berlaku, 2026-09-03. Hasil klarifikasi user setelah rapat tim.
Menggantikan sebagian §26 (bentuk endpoint `POST /api/v1/alerts/{alert_id}/
interventions`) dan §21 master prompt (body request).

**Masalah**: endpoint intervention sebelumnya mengharuskan `alert_id`
internal di path URL. Tapi §26 sudah menetapkan TIDAK ADA `GET /alerts` -
aplikasi eksternal baca database langsung untuk kebutuhan GET. Kalau
aplikasi eksternal memang tidak dirancang membaca schema `predictive`
untuk kasus ini, mereka tidak akan pernah tahu `alert_id` yang harus
dikirim - endpoint jadi tidak bisa dipakai dari sisi mereka. User
mengklarifikasi: body-nya cukup `host_serial_code` - label fisik PART
(format MODEL-PAIRINGCODE-REPAIRSEQ, kolom `journal.t_item_journey.
host_serial_code`, dibaca teknisi langsung dari kode PART) yang aplikasi
eksternal SUDAH tahu tanpa perlu query tambahan ke schema `predictive`.
Field lain (outcome/action_code/remark/external_system/dst) SENGAJA
dihapus juga - "body betul-betul cuma serial code" (keputusan eksplisit,
trade-off: tidak ada lagi idempotency key eksternal, retry POST akan
membuat baris intervention baru, bukan dikembalikan hasil yang sama).

**Implementasi**:
- `core/data_reader.py::resolve_item_by_host_serial_code(host_serial_code)`
  - lookup baru, cari `item_id` internal (item_identifier_clean) dari
  `journal.t_item_journey` lewat `host_serial_code`, ambil catatan
  TERBARU yang cocok (host_serial_code menyertakan repair_seq yang bisa
  berubah tiap perbaikan besar - PART fisik yang sama bisa punya beberapa
  host_serial_code berbeda sepanjang riwayatnya, jadi harus match yang
  paling baru, bukan yang pertama ditemukan).
- `predictive/alerts.py::resolve_by_item(item_id, performed_at)` - fungsi
  baru, titik masuk endpoint. Kalau item ini SEDANG punya alert OPEN,
  delegasi penuh ke `resolve_with_intervention()` (logic sama persis,
  tidak diduplikasi). Kalau TIDAK ada alert OPEN, tetap catat intervention
  langsung (satu POST tetap berarti ada perbaikan, §25) - `alert: null` di
  respons, tidak ada yang perlu ditutup.
- Endpoint pindah dari `POST /api/v1/alerts/{alert_id}/interventions` ke
  `POST /api/v1/interventions` (top-level, bukan lagi sub-resource
  alert) - body `{"host_serial_code": "..."}`, `performed_at` diambil dari
  waktu server menerima request (tidak dikirim caller). 404 kalau
  `host_serial_code` tidak ditemukan (`PartNotFound`, pola yang sama
  dengan endpoint lain); 404 juga kalau item ditemukan tapi tidak sedang
  punya installation cycle aktif sama sekali (`ItemNotInstalled`,
  exception handler baru - sebelumnya tidak terdaftar karena jalur ini
  belum pernah tercapai dari HTTP).

**Konsekuensi pada skema `predictive.intervention`**: kolom `outcome`,
`action_code`, `remark`, `external_system`, `external_work_order_id`,
`external_inspection_id`, `external_event_id` DIBUANG (`migrations/
predictive/0002_lifecycle.sql` diedit langsung, bukan migrasi ALTER baru -
tabel ini belum pernah menampung data production, sama seperti alasan
penghapusan kolom `type` di §25) - field itu tidak akan pernah terisi lagi
karena body endpoint sudah tidak mengirimkannya. Sisa kolom: `intervention_id`,
`item_id`, `cycle_id`, `intervention_seq`, `alert_id`, `performed_at`,
`created_at`. `interventions.py::record_intervention()` disederhanakan jadi
`(item_id, performed_at, alert_id=None) -> dict` (bukan lagi
`tuple[dict, bool]` - tidak ada lagi status "created vs replay idempotent"
untuk dilaporkan). `find_by_external_event()` dihapus (tidak ada lagi
kolom untuk dicari).

**Tabel `predictive.alert_event` DIHAPUS SEPENUHNYA** (bukan cuma
dikosongkan) - ditemukan saat diskusi ulang desain: tidak ada satu pun
kode yang PERNAH membaca tabel ini (murni ditulis di OPENED/
INTERVENTION_RECORDED/RESOLVED), dan seluruh informasi yang dicatatnya
sudah tersedia langsung di kolom `alert.opened_at`/`resolved_at`/
`resolution_reason` - tabel terpisah cuma menduplikasi data tanpa
consumer nyata. `alerts.py` tidak lagi meng-import `json` sama sekali
(satu-satunya pemakaiannya adalah `metadata` JSONB tabel ini).
`evaluate_and_open()` juga tidak lagi butuh parameter `run_id` (dulu
hanya dipakai untuk metadata alert_event) - signature jadi
`evaluate_and_open(frame, scored_at)`.

**Kenapa alert TIDAK digabung jadi kolom di `item_prediction`** (dibahas
ulang di sesi ini, ditolak): `item_prediction` APPEND-ONLY (tidak pernah
di-UPDATE, itu jaminan §19), sementara alert PERLU berubah status
(OPEN->RESOLVED) - kalau digabung, resolve alert berarti meng-UPDATE baris
prediksi historis yang seharusnya beku selamanya. Umur satu alert juga
bisa melintasi BEBERAPA baris `item_prediction` (dibuka di satu siklus
scoring bulanan, baru resolve beberapa siklus kemudian) - tidak ada
pemetaan 1:1 yang bersih ke satu baris prediksi. Dan ritme tulisnya beda
total: `item_prediction` ditulis sekali sebulan lewat batch, alert bisa
berubah kapan saja lewat API - mencampur keduanya berarti tabel
append-only jadi punya kolom yang sering di-UPDATE, bertentangan dengan
tujuan tabel itu sendiri.

**Kenapa `item_prediction.terminal_id` (dan `alert.terminal_id`) sekarang
diisi serial code, bukan ID internal**: `predictive/scoring.py::
record_predictions()` dan `predictive/alerts.py::evaluate_and_open()`
sebelumnya menulis `frame["terminal_id"]` (ID internal
`terminal_inventory_item_id`, dari `serving/batch.py::_attach_terminal()`)
ke kolom `terminal_id` di kedua tabel. Diubah jadi menulis
`frame["terminal_label"]` (serial code fisik terminal, `terminal_serial_
code_clean` dari §14 - SUDAH dihitung di frame yang sama, tidak ada query
baru) - supaya aplikasi eksternal yang baca tabel `predictive` langsung
bisa mengorelasikan terminal pakai kode yang sama dengan sistem mereka
sendiri, bukan ID yang cuma berarti di database ini. **Hanya memengaruhi
apa yang DITULIS ke `item_prediction`/`alert`** - `serving/batch.py`'s
frame (`frame["terminal_id"]`, dipakai live filtering/grouping di
`filter_scores()`/`terminal_summary()`/API `PriorityItem.terminal_id`)
TIDAK berubah sama sekali, jadi tidak ada dampak ke dashboard/API live.
Kolom database TETAP bernama `terminal_id` (tidak di-rename) - isinya saja
yang beda makna sekarang, dicatat di sini supaya tidak membingungkan
pembaca tabel di masa depan.

**Belum dikerjakan** (di luar scope pass ini): kolom `alert.item_id`/
`item_prediction.item_id` TETAP ID internal (`item_identifier_clean`,
dipakai FK ke `item_cycle` di seluruh schema `predictive`) - TIDAK diganti
`host_serial_code`, karena `host_serial_code` bisa berubah antar perbaikan
(menyertakan repair_seq) sementara `item_id` harus stabil sepanjang umur
PART untuk keperluan join. Kalau aplikasi eksternal butuh korelasi PART
lewat serial code juga (bukan cuma terminal), itu keputusan terpisah
(kemungkinan kolom baru `item_serial_code`, informasional saja, tidak
menggantikan `item_id`) - belum diminta user, belum dikerjakan.

---

## 29 · Dashboard dan seluruh endpoint GET dihapus - API cuma /health + POST /api/v1/interventions

**Status**: berlaku, 2026-09-04. SUPERSEDED sebagian dari §14/§15/§19/§26/§28
(bagian yang menyebut dashboard/endpoint GET, `PriorityItem`, `filter_scores()`,
`terminal_summary()`, dst - dipertahankan apa adanya di dokumen sesuai
konvensi append-only, tidak berlaku lagi untuk bagian yang dihapus di sini).

**Keputusan user**: aplikasi eksternal lain akan jadi consumer utama yang
membaca schema `predictive` langsung dari database (§26/§28) - dashboard
Streamlit ini pada dasarnya melakukan hal yang SAMA (menampilkan hasil
prediksi ke manusia lewat HTTP ke API), jadi mempertahankan keduanya berarti
dua kali kerja maintenance untuk satu fungsi yang tumpang tindih. Diputuskan:
**hapus dashboard, dan API GET yang HANYA ada untuk melayani dashboard itu**.

**Dihapus sepenuhnya**:
- `dashboard/` (seluruh direktori - Streamlit `app.py`, `pages/`, `ui.py`,
  `api_client.py`, `.streamlit/`).
- Router API: `model_info_router` (`GET /api/v1/model` - sudah jadi dead
  code lebih dulu sejak `6_Sistem.py` dihapus, §24), `prediction_router`
  (`GET /api/v1/parts/{item_id}/failure|history|assessment`),
  `recommendations_router` (`GET /api/v1/recommendations|overview|filters|
  terminals|terminals/{id}/parts|terminals/{id}/parts/{part_type}`).
- `serving/single.py`: seluruh fungsi single-item prediction/explanation
  (`predict_failure`, `get_part_assessment`, `explain`, `risk_factors`,
  `caveats`, `item_history`, `failure_history`, `location_history`,
  `_active_snapshot`, `_feature_row`, `_guard`, `_exists`, `_translate`,
  `describe`) dan kelas `PartNotScorable` - SEMUA murni pendukung endpoint
  yang baru dihapus. Modul ini sekarang HANYA berisi exception bersama
  (`PartNotFound`, `ModelUnavailable`, `DataSourceUnavailable`) dan metadata
  model (`failure_metadata`/`versions`/`warmup`) yang masih dipakai
  `/health` dan scoring internal - nama file dipertahankan `single.py`
  walau isinya sudah bukan lagi "prediksi satu PART" (rename dianggap
  cosmetic, di luar scope pass ini).
- `serving/batch.py`: `filter_scores()`, `summary()`, `terminal_overview()`,
  `terminal_summary()`, `terminal_part_summary()`, `facets()` - SEMUA murni
  dipakai endpoint yang dihapus, TIDAK dipakai `predictive/scoring.py`
  ataupun `cli.py`. Juga blok enrichment status alert di `_score_failure()`
  (`open_alerts_by_item()`, kolom `alert_id`/`alert_status`/`alert_opened_at`/
  `alert_score_at_open`/`in_official_queue`) - murni untuk tampilan live
  API/dashboard, TIDAK ditulis ke `item_prediction` (`record_predictions()`)
  ataupun dipakai `evaluate_and_open()` (yang baca status alert dari
  `predictive.alert` langsung, bukan dari frame). Import
  `predictive.alerts` di `batch.py` ikut dibuang (satu-satunya pemakainya
  blok ini).
- `api/schemas.py`: `FailurePrediction`, `Recommendation`, `RiskFactor`,
  `Explanation`, `FailureResponse`, `AssessmentResponse`, `PriorityItem`,
  `ScoredAt`, `RecommendationListResponse`, `OverviewResponse`,
  `FiltersResponse`, `FailureHistoryItem`, `LocationHistoryItem`,
  `HistoryResponse`, `TerminalSummaryItem`, `TerminalListResponse`,
  `TerminalPartSummaryItem`, `TerminalPartListResponse`, dan type alias
  `RiskLevel`/`Priority`/`ScoringStatus` yang cuma dipakai kelas-kelas itu.
- `docker-compose.yml`: service `dashboard` dibuang. `requirements-serving.txt`:
  `streamlit`/`requests` dibuang (`requests` khusus dipakai
  `dashboard/api_client.py`, tidak ada pemakai lain).
- Test: `tests/test_serving.py` dihapus (AppTest Streamlit + unit test
  fungsi yang ikut dihapus). Diganti `tests/test_batch.py` (baru, ringkas) -
  cuma menyimpan test yang MASIH relevan: `recommend()` (§ di bawah) dan
  `_attach_terminal()` (masih dipakai internal). `tests/test_api.py`
  dipangkas drastis - sisa test health/CORS/API-key/training-endpoint-tidak-
  ada/intervention saja, test API-key direwrite pakai `POST /api/v1/
  interventions` (satu-satunya router berdependency `require_api_key` yang
  tersisa, sebelumnya pakai `GET /api/v1/model`).
- `tests/conftest.py`: fixture `not_scorable_item` dibuang (tidak ada test
  yang masih memakainya setelah endpoint assessment/dashboard hilang).

**SENGAJA DIPERTAHANKAN meski awalnya terlihat seperti kandidat hapus** -
audit ulang sebelum eksekusi menemukan dependency nyata dari `cli.py`
(tooling standalone, TIDAK terkait dashboard/API sama sekali) terhadap
sebagian fungsi `batch.py` yang HAMPIR ikut terhapus:
- `serving.recommend()`/`RISK_LEVELS`/`_RECOMMENDATION_TABLE` (single.py) -
  dipakai `batch.py::_attach_recommendation()` yang mengisi kolom
  `priority`/`recommended_action` yang DICETAK `cli.py predict` (`_predict_main`).
- `batch.py::_attach_context()` - mengisi kolom `item_type` yang juga
  dicetak `cli.py predict`.
- `batch.py::_attach_recommendation()` sendiri.
- `BatchScores.snapshot` (dan `_score_failure()` mengembalikan tuple
  `(result, features_by_item)`, bukan cuma `result`) - dipakai
  `cli.py golden-batch generate/compare` (oracle regresi, `_SOURCE_COLUMNS`
  dipindah dari `single.py` ke `batch.py` karena satu-satunya pemakainya
  sekarang di sana). Tanpa audit ini, `golden-batch` dan `predict --top`
  akan pecah - kesalahan yang sempat dibuat (fungsi-fungsi ini sempat
  terhapus) dan diperbaiki dalam pass yang sama setelah `cli.py` dibaca
  penuh dan dependency-nya ketahuan.

**Yang TIDAK berubah sama sekali**: `predictive/scoring.py`,
`predictive/alerts.py`, `predictive/cycles.py`, `predictive/interventions.py`,
seluruh `predictive` schema, `POST /api/v1/interventions` (§28), dan
`cli.py score-and-persist`/`predict`/`golden-batch`/eksperimen lain - siklus
scoring bulanan dan alert engine berjalan PERSIS sama seperti sebelumnya,
sama sekali tidak tersentuh oleh penghapusan ini.

**Verifikasi**: `pytest -q` penuh lolos (89 lulus, 1 skip) setelah
penghapusan; smoke test manual `python -m partrisk.cli predict --top 3` dan
`python -m partrisk.cli golden-batch generate --out FILE` dijalankan
langsung terhadap database nyata untuk membuktikan jalur yang dipertahankan
benar-benar masih berfungsi, bukan cuma lolos test unit.

---
