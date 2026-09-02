# Log Eksperimen

Satu section per eksperimen, urut kronologis (commit pertama file laporan
asalnya). Dikonsolidasikan dari 42 laporan lama (`reports/` root + 
`survival_model/reports/` + `survival_model/event_based/reports/`) yang
dihapus setelah dipindahkan ke sini - isi aslinya tetap tersedia lewat
`git show archive/pre-consolidation-2026-08:<path>` kalau perlu diverifikasi
ulang.

**Aturan permanen**: eksperimen baru menambah section di file ini, TIDAK
PERNAH menambah file laporan baru. Kalau file ini tembus ~2.000 baris, pecah
per tahun (`EXPERIMENTS-2027.md`), bukan per eksperimen.

---

## E-01 · Validasi dataset survival

2026-08-20 · asal: `survival_model/reports/data_validation.md`

**Pertanyaan** Apakah dataset survival (lifecycle PART, event=kerusakan)
bersih dan konsisten sebelum dipakai training?

**Metode** Audit integritas pada populasi cohort (`is_initial_model_cohort`,
durasi positif) dan populasi eligible (lolos aturan censoring per-split).

**Hasil**

- Cohort: 23.927 lifecycle. Eligible untuk survival: 20.116 (84,1% dari
  cohort).
- Lifecycle di-exclude per split & alasan:

  | Split | Alasan | Jumlah |
  |---|---|---:|
  | EXCLUDED_TOO_OLD | FAILURE | 915 |
  | EXCLUDED_TOO_OLD | REINSTALL_WITHOUT_RECORDED_FAILURE | 348 |
  | EXCLUDED_TOO_OLD | RIGHT_CENSORED_AT_DATA_END | 2.146 |
  | TEST | REINSTALL_WITHOUT_RECORDED_FAILURE | 28 |
  | TRAIN | REINSTALL_WITHOUT_RECORDED_FAILURE | 342 |
  | VALIDATION | REINSTALL_WITHOUT_RECORDED_FAILURE | 32 |

- Event vs censored per split: TEST censored=2.450 event=370; TRAIN
  censored=11.939 event=3.041; VALIDATION censored=1.931 event=385.
- Distribusi `duration_days`: min=1,0 p25=198,0 median=492,0 p75=1.898,0
  p99=3.402,0 max=4.011,0.
- Cek integritas (semua 0): `duration_days<=0`=0, `installation_cycle_id`
  duplikat=0, `failure_onset_on < installed_on` (event=1)=0, `installed_on`
  di masa depan=0.
- Tipe PART yang hanya muncul di VALIDATION (tidak pernah di TRAIN): 20 dari
  58 tipe. Yang hanya muncul di TEST: 12 dari 56 tipe (keduanya diredam oleh
  `part_model_category` low-support grouping + `handle_unknown='ignore'`
  pada encoder).
- 1.234 item (7,5% item unik) punya >1 lifecycle yang jatuh di split
  berbeda - bukan leakage temporal, tapi potensi model "mengenali" identitas
  item lintas split lewat fitur riwayat siklus sebelumnya. Didokumentasikan
  sebagai keterbatasan, tidak diperbaiki dengan grouped split.
- Base rate event per split: TEST=0,131206 TRAIN=0,203004
  VALIDATION=0,166235.

**Keputusan** Dataset dinyatakan layak dipakai untuk training survival;
keterbatasan (item lintas split, base rate bergeser antar split) dicatat,
tidak diperbaiki.

---

## E-02 · Evaluasi survival model statis (lineage lama, baseline-instalasi)

2026-08-20 · asal: `survival_model/reports/evaluation_report.md`

**Pertanyaan** Seberapa baik RSF/Cox PH (fitur dibekukan di `installed_on`,
lineage statis - sudah digantikan event-based, dihapus Fase 1) memprediksi
survival, dan bagaimana perbandingannya dengan model classification?

**Hasil**

Lapis 1 - native (t=0=installed_on):

| Split | Model | rows | events | C-index (Harrell) | C-index (Uno/IPCW) | IBS |
|---|---|---:|---:|---:|---:|---:|
| VALIDATION | random_survival_forest | 2.316 | 385 | 0,8114 | 0,8117 | 0,07636 |
| VALIDATION | cox_ph | 2.316 | 385 | 0,7819 | 0,7821 | 0,08588 |
| TEST | random_survival_forest | 2.820 | 370 | 0,8082 | 0,8083 | 0,08112 |
| TEST | cox_ph | 2.820 | 370 | 0,7722 | 0,7724 | 0,09497 |

Brier per horizon (VAL / TEST, RSF): 30d 0,0627/0,0807 · 60d 0,0755/0,0827 ·
90d 0,0805/0,0808 · 120d 0,0836/0,0790. AUC waktu-bergantung (VAL / TEST,
RSF): 30d 0,7991/0,8424 · 60d 0,8261/0,8690 · 90d 0,8450/0,8827 · 120d
0,8546/0,9051. (Cox PH: lihat laporan asli di arsip git untuk angka penuh.)

Lapis 2 - perbandingan adil vs classification (37.923/38.451 baris TEST
classification cocok dengan lifecycle survival, window 211 hari, kapasitas
200/bulan):

| Model | PR-AUC | ROC-AUC | Recall@cap | Precision@cap | Brier |
|---|---:|---:|---:|---:|---:|
| random_survival_forest | 0,1633 | 0,6871 | 0,3108 | 0,1962 | 0,0214 |
| cox_ph | 0,0939 | 0,6893 | 0,1869 | 0,1180 | 0,0224 |
| classification (v2) | 0,1607 | 0,8206 | 0,3359 | 0,2154 | 0,0215 |

**Catatan** Skor survival di sini pakai fitur baseline INSTALASI (bukan
fitur di-refresh ke tanggal snapshot seperti classification) - classification
punya keuntungan struktural (fitur lebih segar). Ini alasan langsung
dibangunnya lineage event-based berikutnya.

**Keputusan** Baseline diterima sebagai titik awal; lineage ini kemudian
digantikan sepenuhnya oleh event-based (lihat E-07 dst).

---

## E-03 · Threshold kategori khusus survival

2026-08-20 · asal: `survival_model/reports/category_threshold.md`

**Pertanyaan** Threshold low-support kategori berapa yang optimal untuk
kolom survival (`item_model_code_clean`, `item_type_at_install`,
`place_at_install`), terpisah dari `config.MIN_PART_MODEL_SUPPORT=300`
milik classification (dikalibrasi untuk skala 251.568 baris, bukan ~15rb
lifecycle survival)?

**Metode** Sweep threshold, dipilih dari VAL C-index (RSF ringan, 50 pohon).
TEST tidak dipakai memilih, hanya dilaporkan di tahap final.

**Hasil**

| Kolom | Threshold | Kategori asli | Digabung LOW_SUPPORT | Unseen VAL | Unseen TEST | VAL C-index |
|---|---:|---:|---:|---|---|---:|
| item_model_code_clean | 20 | 46 | 3 | 353/2316 | 369/2820 | 0,8040 |
| item_model_code_clean | 50 | 46 | 9 | 458/2316 | 639/2820 | 0,8081 |
| item_model_code_clean | 100 | 46 | 13 | 593/2316 | 693/2820 | 0,8114 |
| **item_model_code_clean** | **200** | 46 | 22 | 919/2316 | 852/2820 | **0,8116 (dipilih)** |
| item_model_code_clean | 300 | 46 | 28 | 1060/2316 | 1016/2820 | 0,8084 |
| item_type_at_install | 20 | 18 | 0 | 0/2316 | 0/2820 | 0,8102 |
| item_type_at_install | 50 | 18 | 1 | 6/2316 | 3/2820 | 0,8095 |
| item_type_at_install | 100 | 18 | 1 | 6/2316 | 3/2820 | 0,8093 |
| item_type_at_install | 200 | 18 | 4 | 66/2316 | 71/2820 | 0,8096 |
| **item_type_at_install** | **300** | 18 | 6 | 100/2316 | 100/2820 | **0,8147 (dipilih)** |
| place_at_install | 20 | 137 | 24 | 852/2316 | 239/2820 | 0,8056 |
| **place_at_install** | **50** | 137 | 50 | 1013/2316 | 330/2820 | **0,8099 (dipilih)** |
| place_at_install | 100 | 137 | 78 | 1322/2316 | 874/2820 | 0,8055 |
| place_at_install | 200 | 137 | 121 | 1842/2316 | 1604/2820 | 0,8044 |
| place_at_install | 300 | 137 | 130 | 2107/2316 | 2520/2820 | 0,8081 |

**Keputusan** Threshold terpilih: `item_model_code_clean=200`,
`item_type_at_install=300`, `place_at_install=50`. Catatan: `place_at_install`
kemudian dihapus total dari fitur (dihitung tapi tidak pernah dipakai
`FEATURE_COLUMNS`) - dibersihkan di Fase 1 konsolidasi 2026-08-23 karena
zero call-site produksi. Threshold `item_model_code_clean`/
`item_type_at_install` di atas tetap dipakai lineage event-based.

---

## E-04 · Feature ablation: fitur warisan vs konteks instalasi vs gabungan

2026-08-20 · asal: `survival_model/reports/feature_ablation.md`

**Pertanyaan** Apakah menambahkan `item_type_at_install`/`place_at_install`
ke 19 fitur warisan classification (A) meningkatkan C-index survival?

**Metode** A = 19 fitur warisan classification. B = HANYA konteks instalasi
(tanpa riwayat/armada/lifecycle). C = A + item_type_at_install +
place_at_install. A_plus_* mengisolasi kontribusi 1 fitur baru.

**Hasil**

| Experiment | Model | VAL C-index | TEST C-index | Uno C | AUC30 | AUC90 | IBS |
|---|---|---:|---:|---:|---:|---:|---:|
| A_current | random_survival_forest | 0,8078 | 0,8051 | 0,8052 | 0,8377 | 0,8820 | 0,0801 |
| A_current | cox_ph | 0,7706 | 0,8149 | 0,8150 | 0,8564 | 0,8929 | 0,0868 |
| B_context_only | random_survival_forest | 0,6547 | 0,6227 | 0,6226 | 0,6408 | 0,6415 | 0,1044 |
| B_context_only | cox_ph | 0,6678 | 0,6245 | 0,6246 | 0,6372 | 0,6454 | 0,1064 |
| A_plus_item_type | random_survival_forest | 0,8118 | 0,8034 | 0,8035 | 0,8378 | 0,8815 | 0,0811 |
| A_plus_item_type | cox_ph | 0,7809 | 0,7965 | 0,7966 | 0,8365 | 0,8778 | 0,0940 |
| A_plus_place | random_survival_forest | 0,8089 | 0,8091 | 0,8092 | 0,8436 | 0,8846 | 0,0814 |
| A_plus_place | cox_ph | 0,7625 | 0,7984 | 0,7984 | 0,8394 | 0,8743 | 0,0908 |
| C_combined | random_survival_forest | 0,8074 | 0,8036 | 0,8037 | 0,8365 | 0,8775 | 0,0826 |
| C_combined | cox_ph | 0,7716 | 0,7826 | 0,7827 | 0,8221 | 0,8606 | 0,0962 |

**Keputusan** (tidak dinyatakan eksplisit di laporan asli - konteks-only (B)
jauh lebih buruk sendirian, konfirmasi riwayat/armada/lifecycle adalah
sumber sinyal utama; A_plus_item_type dipakai jadi basis E-06.)

---

## E-05 · RSF tuning & perbandingan model final (lineage statis)

2026-08-20 · asal: `survival_model/reports/model_comparison.md`

**Pertanyaan** Hyperparameter RSF mana yang optimal di sekitar titik
current, dan bagaimana RSF final dibanding Cox PH final?

**Metode** Pencarian coordinate-wise KECIL (bukan grid penuh) di sekitar
titik current - satu sumbu diubah per langkah, dipertahankan hanya kalau
menaikkan VAL C-index. TEST hanya untuk pelaporan akhir.

**Hasil**

Pencarian tuning (RSF, kolom operasional sengaja kosong - lihat laporan
asli/E-02 untuk alasan):

| Konfigurasi | VAL C-index | TEST C-index | AUC30 | AUC90 | IBS |
|---|---:|---:|---:|---:|---:|
| current (baseline) | 0,8120 | 0,8096 | 0,8467 | 0,8859 | 0,0809 |
| n_estimators=200 | 0,8095 | 0,8084 | 0,8450 | 0,8836 | 0,0807 |
| n_estimators=400 | 0,8100 | 0,8096 | 0,8456 | 0,8840 | 0,0804 |
| min_samples_leaf=10 | 0,8100 | 0,8082 | 0,8454 | 0,8844 | 0,0798 |
| min_samples_leaf=20 | 0,8102 | 0,8074 | 0,8441 | 0,8831 | 0,0803 |
| min_samples_leaf=50 | 0,8069 | 0,8083 | 0,8456 | 0,8844 | 0,0811 |
| max_features=0,5 | 0,8069 | 0,7945 | 0,8286 | 0,8697 | 0,0813 |
| max_features=1,0 | 0,8087 | 0,7805 | 0,8097 | 0,8537 | 0,0839 |
| max_depth=8 | 0,8100 | 0,8091 | 0,8454 | 0,8828 | 0,0810 |
| max_depth=12 | 0,8058 | 0,8072 | 0,8447 | 0,8840 | 0,0807 |

Model final: RSF VAL 0,8120 TEST 0,8096 AUC30 0,8467 AUC90 0,8859 IBS
0,0809; Cox PH VAL 0,7858 TEST 0,7963 AUC30 0,8342 AUC90 0,8755 IBS 0,0942.

**Keputusan** current (baseline) tetap konfigurasi terpilih - tidak ada
kandidat tuning yang mengalahkannya secara jelas di VAL C-index.

---

## E-06 · Audit `previous_cycle_lifetime_mean`

2026-08-20 · asal: `survival_model/reports/previous_cycle_audit.md`

**Pertanyaan** `previous_cycle_lifetime_mean` mencampur SEMUA cara siklus
sebelumnya berakhir (FAILURE, RIGHT_CENSORED, REINSTALL) - apakah varian
yang hanya menghitung siklus CONFIRMED FAILURE lebih baik?

**Metode** Diuji di atas konfigurasi A_plus_item_type (E-04).

**Hasil**

| Varian | VAL C-index | TEST C-index | AUC30 | AUC90 | IBS |
|---|---:|---:|---:|---:|---:|
| existing (campuran) | 0,8109 | 0,8015 | 0,8358 | 0,8781 | 0,0809 |
| **confirmed_failure_only** | **0,8120** | 0,8096 | 0,8467 | 0,8859 | 0,0809 |
| last_confirmed_failure | 0,8093 | 0,8106 | 0,8479 | 0,8863 | 0,0804 |
| confirmed_failure_only+end_reason | 0,8113 | 0,8108 | 0,8474 | 0,8857 | 0,0810 |

**Keputusan** `confirmed_failure_only` dipilih (VAL C-index 0,8120 vs
existing 0,8109). `previous_cycle_end_reason` TIDAK dipertahankan (VAL
0,8113 <= 0,8120).

---

## E-07 · Concept drift: jendela tahun TRAIN (event-based)

2026-08-21 · asal: `survival_model/event_based/reports/concept_drift.md`

**Pertanyaan** Jendela tahun TRAIN mana (dipangkas berdasar `installed_on`
lifecycle) yang optimal untuk model event-based?

**Hasil**

| Jendela TRAIN | Lifecycle | VAL C-index (full) | VAL C-index (t0-only) | VAL t0 IBS |
|---|---:|---:|---:|---:|
| 2014-2024 (penuh) | 14.980 | 0,7963 | 0,7963 | 0,0777 |
| 2018-2024 | 10.712 | 0,8065 | 0,8065 | 0,0780 |
| 2020-2024 | 6.806 | 0,7961 | 0,7961 | 0,0843 |
| 2022-2024 | 5.285 | 0,8067 | 0,8067 | 0,0830 |

**Keputusan** (tidak dinyatakan eksplisit - jendela penuh 2014-2024 tetap
dipakai produksi berdasarkan laporan-laporan berikutnya.)

---

## E-08 · Ablation lanjutan event-based: dynamic history + device/terminal

2026-08-21 · asal: `survival_model/event_based/reports/dynamic_ablation.md`

**Pertanyaan** Apakah menambah degradation trend, cumulative history,
windowed corrective, dan device/terminal context (schema `analytics`, riset
lama) di atas baseline event-based (A_t0_baseline) meningkatkan C-index?

**Metode** Keputusan dari VAL t0-only (sebanding dengan C-index model
statis) - VAL full TIDAK dipakai memilih (repeated measures).
E_plus_device_terminal memakai schema `analytics` dengan filter
`parent_link_quality_status=='VALID_POINT_IN_TIME_RELATION'`.

**Hasil**

| Experiment | Model | VAL C-index (full) | VAL C-index (t0-only) | VAL t0 IBS |
|---|---|---:|---:|---:|
| A_t0_baseline | random_survival_forest | 0,8173 | 0,7849 | 0,0774 |
| A_t0_baseline | cox_ph | 0,7905 | 0,7612 | 0,0885 |
| B_plus_degradation_trend | random_survival_forest | 0,8199 | 0,7890 | 0,0775 |
| B_plus_degradation_trend | cox_ph | 0,7890 | 0,7593 | 0,0895 |
| C_plus_cumulative_history | random_survival_forest | 0,8242 | 0,7910 | 0,0783 |
| C_plus_cumulative_history | cox_ph | 0,7974 | 0,7702 | 0,0889 |
| D_plus_windowed_corrective | random_survival_forest | 0,8224 | 0,7929 | 0,0772 |
| D_plus_windowed_corrective | cox_ph | 0,7900 | 0,7607 | 0,0897 |
| E_plus_device_terminal | random_survival_forest | 0,8206 | 0,7884 | 0,0776 |
| E_plus_device_terminal | cox_ph | 0,7327 | 0,7119 | 0,0916 |
| F_combined_all | random_survival_forest | 0,8313 | 0,8036 | 0,0783 |
| F_combined_all | cox_ph | 0,7391 | 0,7207 | 0,0939 |

**Keputusan** F_combined_all (RSF) menang telak di VAL t0-only (0,8036),
tapi memakai schema `analytics` (dilarang produksi) lewat
E_plus_device_terminal - lihat E-12 (G_combined_without_device) untuk
varian tanpa dependency itu, yang jadi basis produksi berikutnya.

---

## E-09 · Ensemble operasional: model statis + event-based

2026-08-21 · asal: `survival_model/event_based/reports/ensemble_operational.md`

**Pertanyaan** Apakah menggabungkan skor model statis dan event-based
(rata-rata/rank/max) mengalahkan salah satu model sendirian?

**Metode** Populasi irisan (37.923 baris, window 211 hari, kapasitas
200/bulan) - `static_only`/`event_based_only` dihitung ULANG pada populasi
irisan (bukan angka lama dari laporan masing-masing) supaya adil.

**Hasil**

| Kandidat | PR-AUC | ROC-AUC | Recall@cap | Precision@cap | Brier |
|---|---:|---:|---:|---:|---:|
| static_only (irisan) | 0,1633 | 0,6871 | 0,3108 | 0,1962 | 0,0214 |
| **event_based_only (irisan)** | **0,1824** | 0,6961 | **0,3401** | **0,2146** | 0,0212 |
| ensemble_avg_raw | 0,1756 | 0,6882 | 0,3311 | 0,2090 | 0,0213 |
| ensemble_avg_rank | 0,1756 | 0,6913 | 0,3322 | 0,2097 | 0,3192 |
| ensemble_max | 0,1751 | 0,6860 | 0,3255 | 0,2054 | 0,0212 |

**Keputusan** event_based_only sendirian mengalahkan seluruh varian
ensemble - tidak ada nilai tambah dari menggabungkan dengan model statis.
Ini titik balik yang mengarahkan seluruh evaluasi berikutnya ke
event_based sebagai kandidat tunggal (E-10 dst).

---

## E-10 · Evaluasi event-based survival (Tahap 6-9)

2026-08-21 · asal: `survival_model/event_based/reports/evaluation_report.md`

**Pertanyaan** Bagaimana performa event-based secara menyeluruh (3 lapis:
native semua baris, native t0-only sebanding-statis, operasional vs
classification)?

**Hasil**

Lapis 1 (semua baris landmark): VALIDATION RSF rows=5.540 events=534
C-index=0,8290 IBS=0,04747; Cox PH C-index=0,7915 IBS=0,05363. TEST RSF
rows=4.890 events=412 C-index=0,8477 IBS=0,05089; Cox PH C-index=0,7618
IBS=0,06501.

Lapis 1b (t0-only, sebanding model statis): VALIDATION RSF C-index=0,7985
IBS=0,07807; Cox PH C-index=0,7651 IBS=0,09073. TEST RSF C-index=0,8105
IBS=0,07871; Cox PH C-index=0,7405 IBS=0,10281.

Lapis 2 (window 211 hari, kapasitas 200/bulan, t0-only): RSF PR-AUC=0,1824
ROC-AUC=0,6961 Recall@cap=0,3401 Precision@cap=0,2146 Brier=0,0212; Cox PH
PR-AUC=0,0590 ROC-AUC=0,6832 Recall@cap=0,1160 Precision@cap=0,0732
Brier=0,0233.

**Keputusan** RSF event-based (t0-only VAL 0,7985) dijadikan baseline
produksi baru untuk seluruh ablation berikutnya (E-11 dst).

---

## E-11 · Fleet hierarchy: laju kerusakan level item_type

2026-08-21 · asal: `survival_model/event_based/reports/fleet_hierarchy.md`

**Pertanyaan** Apakah mengelompokkan fleet failure rate per
`item_type_at_install` (bukan per `item_model_code_clean` persis) menaikkan
C-index TANPA turun, dan AUC-30d/90d naik (proxy murah untuk
Recall@kapasitas)?

**Metode** C-index TIDAK dikejar lagi (sudah mentok ~0,80) - baris
`0_baseline_production` adalah pagar (floor); kandidat hanya layak diadopsi
kalau C-index tidak turun DAN AUC-30d/90d naik.

**Hasil**

| Experiment | Model | VAL C-index (full) | VAL C-index (t0-only) | VAL t0 IBS | AUC-30d | AUC-90d |
|---|---|---:|---:|---:|---:|---:|
| 0_baseline_production | random_survival_forest | 0,8290 | 0,7985 | 0,0781 | 0,7862 | 0,8265 |
| 0_baseline_production | cox_ph | 0,7915 | 0,7651 | 0,0907 | 0,7453 | 0,7947 |
| H_plus_fleet_hierarchy | random_survival_forest | 0,8300 | 0,7994 | 0,0796 | 0,7865 | 0,8285 |
| H_plus_fleet_hierarchy | cox_ph | 0,7771 | 0,7527 | 0,0919 | 0,7326 | 0,7805 |

**Keputusan** DITOLAK - tidak pernah di-wire ke `features/survival/`
produksi (fungsi `fleet_hierarchy_features` dihapus sebagai kode mati di
Fase 1 konsolidasi 2026-08-23, zero call-site). IBS memburuk (0,0796 vs
0,0781) walau C-index/AUC naik tipis - kenaikannya dianggap tidak cukup
meyakinkan untuk kompleksitas tambahan.

---

## E-12 · G_combined_without_device (tanpa dependency schema `analytics`)

2026-08-21 · asal: `survival_model/event_based/reports/g_without_device.md`

**Pertanyaan** Bandingkan dengan F_combined_all (E-08, VAL t0-only RSF
0,8036) - apakah menghapus komponen device/terminal (yang butuh schema
`analytics`, dilarang produksi) menjaga sebagian besar kenaikan?

**Hasil**

| Experiment | Model | VAL C-index (full) | VAL C-index (t0-only) | VAL t0 IBS |
|---|---|---:|---:|---:|
| G_combined_without_device | random_survival_forest | 0,8270 | 0,7954 | 0,0777 |
| G_combined_without_device | cox_ph | 0,7961 | 0,7684 | 0,0908 |

**Keputusan** G_combined_without_device (VAL t0-only 0,7954) menjaga
sebagian besar kenaikan F_combined_all (0,8036) tanpa dependency
`analytics` - jadi kandidat fitur produksi final event-based, konsisten
dengan larangan schema `analytics` di kode produksi.

---

## E-13 · Audit event density dalam siklus + ketersediaan device/usage context

2026-08-21 · asal: `survival_model/event_based/reports/intra_cycle_event_audit.md`

**Pertanyaan** (1) Kalau landmark dibuat "hanya saat event organik", berapa
titik observasi realistis dihasilkan per lifecycle? (2) Apakah
device_type/device_model/usage intensity benar-benar tidak tersedia di
database (diverifikasi ulang, bukan dipercaya dari kesimpulan lama)?

**Metode** (1) Untuk 23.927 lifecycle cohort, hitung event operasional yang
timestamp-nya strictly di antara `installed_on` dan `cycle_end_on`. (2)
Sweep skema database penuh (`information_schema.columns ILIKE '%device%'`).

**Hasil**

(1) 0 event di tengah siklus: 19.208 lifecycle (80,3%). >=1 event: 4.719
(19,7%) - rata-rata 2,03 event, median 1, maksimum 16. Pola dominan:
REPAIRED -> CORRECTIVE REQUESTED -> ISSUED -> DELIVERY (logistik repair
siklus sebelumnya).

**Kesimpulan (1)**: skema "observation hanya saat event organik" sendirian
tidak cukup - 80% lifecycle hanya akan punya SATU observasi (install), sama
seperti model statis. Anchor jarang (90/180/365 hari, lalu +365) jadi
sumber UTAMA landmark tambahan, bukan pelengkap opsional.

(2) Ditemukan `journal.replacement_history` (12.695 baris, TIDAK
direferensikan `data_reader.py`/`feature_builder.py`/`survival_model/`
manapun). `device_type` (4 nilai): GATE 10.680, CVIM 1.431, BALANCE READER
331, POS 253. `install_time` rentang 2025-01-01 s/d 2026-08-03 (0 baris
sebelum 2025). `total_hours` min=-2020 max=14293 mean=10005 (ADA nilai
negatif). Ditemukan baris `install_time > failure_time` (anomali urutan).
`spare_part_serial_code` campuran format pairing-code dan host-code pendek.

**Keputusan (2)**: DITOLAK secara definitif untuk TRAIN split (mencakup
2014-2024) - 0% coverage karena baris tabel paling awal 2025-01-01, bukan
sesuatu yang bisa diperbaiki lewat rekayasa fitur. Anomali kualitas data
(total_hours negatif, timestamp terbalik) dan skema identifier campuran
adalah alasan sekunder. Dicatat sebagai kandidat audit ulang di masa depan
kalau tabel di-backfill historis dan dibersihkan - bukan ditutup permanen.

---

## E-14 · Jendela corrective sangat dekat (7/14 hari) - DIBATALKAN

2026-08-21 · asal: `survival_model/event_based/reports/short_window.md`

**Pertanyaan** Apakah jendela corrective sangat pendek (7/14 hari, di atas
baseline 0_baseline_production VAL t0=0,7985 AUC30=0,7862) meningkatkan
akurasi?

**Hasil (awal, dari cache lama)**

| Experiment | Model | VAL C-index (full) | VAL C-index (t0-only) | VAL t0 IBS | AUC-30d | AUC-90d |
|---|---|---:|---:|---:|---:|---:|
| I_plus_short_window_7_14d | random_survival_forest | 0,8352 | 0,8058 | 0,0778 | 0,7948 | 0,8351 |
| I_plus_short_window_7_14d | cox_ph | 0,7933 | 0,7639 | 0,0933 | 0,7437 | 0,7947 |

**TINDAK LANJUT: DIBATALKAN.** Tabel di atas dihitung dari cache lama
(`SURVIVAL_BUILD_CACHE=1`). Setelah di-wire ke produksi dan retrain PENUH
dengan cache dihapus (DB fresh), hasil menunjukkan REGRESI di semua metrik
dibanding baseline: VAL t0-only turun ke 0,7974, TEST turun ke 0,8065,
Recall@kapasitas turun ke 0,3345, PR-AUC turun ke 0,1817 - melanggar syarat
"tidak boleh lebih buruk dari sebelumnya".

**Keputusan** DIBATALKAN, dikembalikan ke jendela 60/90 hari yang stabil
(`windowed_corrective_extra()` default `(60, 90)`). Fitur produksi final
tetap di VAL t0-only 0,7985 / TEST 0,8105 / Recall@kapasitas 0,3401 (lihat
E-10). **Pelajaran metodologis**: fitur berbasis jendela waktu sangat
sempit, diuji lewat cache yang bisa basi, tidak cukup divalidasi - butuh
retrain penuh pada snapshot data yang SAMA PERSIS dengan produksi sebelum
benar-benar diadopsi.

---

## E-15 · XGBoost AFT pada fitur produksi final

2026-08-21 · asal: `survival_model/event_based/reports/xgboost_aft.md`

**Pertanyaan** Apakah XGBoost AFT mengalahkan RSF/Cox PH produksi pada
fitur yang sama?

**Hasil** Fit time 3,1 detik (n_estimators=200, max_depth=4).

| Model | VAL t0-only C-index |
|---|---:|
| XGBoost AFT (normal, scale=1,2) | 0,7971 |
| RSF (produksi saat ini) | 0,7985 |
| Cox PH (produksi saat ini) | 0,7651 |

**Keputusan** Tidak mengalahkan RSF produksi - tidak diadopsi.

---

## E-16 · Fitur hazard baru: prior survival empiris per grup

2026-08-21 · asal: `survival_model/reports/hazard_ablation.md`

**Pertanyaan** Apakah menambahkan prior survival empiris per grup (part
model/item type/client - dihitung dari lifecycle lain yang sudah berakhir
sebelum `installed_on` baris ini, point-in-time) meningkatkan C-index di
luar rentang ketidakpastian baseline (lihat E-18)?

**Hasil**

| Experiment | Model | VAL C-index | TEST C-index | AUC30 | AUC90 | IBS |
|---|---|---:|---:|---:|---:|---:|
| A_final (baseline) | random_survival_forest | 0,8114 | 0,8082 | 0,8424 | 0,8827 | 0,0811 |
| A_final (baseline) | cox_ph | 0,7819 | 0,7722 | 0,8027 | 0,8449 | 0,0950 |
| A_plus_partmodel_prior | random_survival_forest | 0,8135 | 0,8056 | 0,8387 | 0,8774 | 0,0834 |
| A_plus_partmodel_prior | cox_ph | 0,7743 | 0,7600 | 0,7911 | 0,8225 | 0,0930 |
| A_plus_itemtype_prior | random_survival_forest | 0,8136 | 0,7975 | 0,8332 | 0,8752 | 0,0887 |
| A_plus_itemtype_prior | cox_ph | 0,7806 | 0,7508 | 0,7769 | 0,8218 | 0,1067 |
| A_plus_client_prior | random_survival_forest | 0,8108 | 0,8099 | 0,8473 | 0,8882 | 0,0852 |
| A_plus_client_prior | cox_ph | 0,6318 | 0,7546 | 0,7792 | 0,8195 | 0,1104 |
| A_plus_all_priors | random_survival_forest | 0,8124 | 0,8044 | 0,8435 | 0,8793 | 0,0881 |
| A_plus_all_priors | cox_ph | 0,6267 | 0,7754 | 0,8002 | 0,8356 | 0,1079 |

**Keputusan** Semua kandidat naik VAL C-index tipis (0,8108-0,8136 vs
baseline 0,8114) - dalam rentang bootstrap CI baseline (E-18: [0,7926,
0,8289]), jadi tidak dianggap menang bersih. Tidak ada yang di-wire.

---

## E-17 · Keluarga model: RSF vs Cox PH vs ExtraSurvivalTrees vs GBSA vs ComponentwiseGBSA

2026-08-21 · asal: `survival_model/reports/model_family.md`

**Pertanyaan** Apakah keluarga model lain (belum pernah dicoba) mengalahkan
RSF/Cox PH pada fitur produksi yang sama?

**Metode** Semua model dilatih pada fitur final produksi yang sama,
hyperparameter DEFAULT per keluarga (bukan hasil tuning).
`GradientBoostingSurvivalAnalysis(loss='ipcwls'/'squared')` diuji lewat
smoke test dan DIBUANG dari registry: loss selain 'coxph' tidak punya
baseline hazard model, `predict_survival_function()` melempar ValueError -
tidak kompatibel dengan pipeline yang butuh kurva S(t) penuh.

**Hasil**

| Experiment | VAL C-index | TEST C-index | AUC30 | AUC90 | IBS |
|---|---:|---:|---:|---:|---:|
| random_survival_forest | 0,8114 | 0,8082 | 0,8424 | 0,8827 | 0,0811 |
| cox_ph | 0,7819 | 0,7722 | 0,8027 | 0,8449 | 0,0950 |
| extra_survival_trees | 0,8080 | 0,8111 | 0,8474 | 0,8848 | 0,0833 |
| gbsa_coxph | 0,8072 | 0,8256 | 0,8692 | 0,9038 | 0,0885 |
| componentwise_gbsa | 0,7937 | 0,8314 | 0,8698 | 0,9049 | 0,0874 |

**Keputusan** Meski gbsa_coxph/componentwise_gbsa unggul di TEST C-index,
`DEFAULT_MODEL_NAMES` tetap RSF+Cox - ketiga keluarga tambahan (
extra_survival_trees, gbsa_coxph, componentwise_gbsa) TIDAK PERNAH dipakai
pemanggil produksi mana pun. Entri registry-nya (beserta parameter dan
import sksurv terkait) dihapus sebagai kode mati di Fase 1 konsolidasi
2026-08-23 - `MODEL_REGISTRY` sekarang hanya berisi RSF+Cox.

---

## E-18 · Ketidakpastian baseline C-index

2026-08-21 · asal: `survival_model/reports/uncertainty_baseline.md`

**Pertanyaan** Seberapa besar noise C-index VALIDATION (2.316 baris, 385
event - kecil) sebelum eksperimen model-family/fitur baru dievaluasi?

**Metode** Dua sumber ketidakpastian diukur terpisah: (1) bootstrap
resampling baris VAL pada model produksi saat ini, (2) variasi antar
`random_state` RSF.

**Hasil**

Bootstrap CI (200 resample):

| Model | Point estimate | 95% CI lower | 95% CI upper | Std |
|---|---:|---:|---:|---:|
| random_survival_forest | 0,8114 | 0,7926 | 0,8289 | 0,0096 |
| cox_ph | 0,7819 | 0,7603 | 0,8011 | 0,0103 |

Variasi antar seed RSF (5 seed): seed 0=0,8115 · 1=0,8086 · 2=0,8111 ·
3=0,8120 · 4=0,8106. Rentang [0,8086, 0,8120], std=0,0012.

**Keputusan** Kandidat model/fitur baru hanya dianggap menang kalau VAL
C-index-nya DI LUAR rentang [0,7926, 0,8289] (bootstrap CI), bukan menang
tipis di dalam noise ini. Ini kriteria yang dipakai E-16/E-17. Fungsi yang
menghasilkan bootstrap CI ini (`bootstrap_c_index`) dipertahankan di
`survival/metrics.py` meski belum ada pemanggil lain - dibutuhkan FASE 7
P0-2 (bootstrap CI di metadata production).

---

## E-19 · Baseline performa CatBoost (v2) sebelum restrukturisasi

2026-08-21 · asal: `reports/baseline_performance_catboost.md`

**Pertanyaan** Berapa baseline latency/ukuran CatBoost v2 sebelum
restrukturisasi, untuk menetapkan ambang gerbang G5/G6 (Fase A)?

**Hasil** Diukur 2026-08-21 14:49:41.

| Metrik | Nilai |
|---|---:|
| model_version | v2 |
| Ukuran artifact model failure (semua file) | 0,157 MB |
| Cold model load | 0,862 s |
| RSS setelah load model | 171,5 MB |
| Single predict() p50 (20 PART) | 2311,8 ms |
| Single predict() p90 (20 PART) | 2453,1 ms |
| Batch penuh (16.877 PART) | 47,0 s |
| RSS naik setelah batch penuh | 98,2 MB |

**Keputusan** Ambang turunan gerbang Fase A: **G5** ukuran artifact <=100 MB
(keras, bukan skala dari baseline). **G6** cold load <=5 s; single predict
p50 <=3.467,7 ms (1,5x baseline); batch penuh <=94,0 s (2x baseline).

---

## E-20 · Fase A1: evaluasi production-realistic event-based vs CatBoost

2026-08-21 · asal: `survival_model/event_based/reports/gate_a1_landmark_operational.md`

**Pertanyaan** Bagaimana performa event-based dibanding CatBoost v2 kalau
fitur event-based dihitung PADA `observation_on` tiap baris (kondisi PART
saat snapshot diambil, cara `predict.py` sebenarnya dipakai) - bukan
dibekukan di `installed_on` seperti E-09?

**Hasil** Populasi: 38.451 baris TEST classification (window 211 hari,
kapasitas 200/bulan), identik untuk kedua model.

| Model | PR-AUC | ROC-AUC | Recall@cap | Precision@cap | Brier |
|---|---:|---:|---:|---:|---:|
| event-based (observation_on) | 0,1643 | 0,7437 | 0,2816 | 0,1805 | 0,0214 |
| catboost v2 (incumbent) | 0,1444 | 0,8165 | 0,3348 | 0,2146 | 0,0215 |

**Keputusan** Menjadi dasar keputusan gerbang A5 (E-24): event-based menang
PR-AUC/Brier, kalah telak Recall/Precision@kapasitas.

---

## E-21 · Fase A2: latency kandidat compact

2026-08-21 · asal: `survival_model/event_based/reports/gate_a2_compact_latency.md`

**Pertanyaan** Apakah kandidat compact (n_estimators=50,
min_samples_leaf=100, grid dikasarkan) lulus ambang G6?

**Hasil** Diukur pada `x_val` (fitur VALIDATION sudah di-encode).

| Metrik | Nilai | Ambang G6 | Status |
|---|---:|---|---|
| Ukuran artifact | 66,2 MB | <=100 MB | LULUS |
| Cold load | 0,174 s | <=5 s | LULUS |
| Single predict p50 | 2,7 ms | <=3.467,7 ms | LULUS |
| Batch (ekstrapolasi 16.877 PART) | 2,7 s | <=94,0 s | LULUS |

Batch di atas EKSTRAPOLASI linier dari chunk 2.000 baris (0,160 ms/baris) -
bukan pengukuran end-to-end lewat pipeline database.

**Keputusan** LULUS G6.

---

## E-22 · Fase A2: pencarian model compact (event-based RSF)

2026-08-21 · asal: `survival_model/event_based/reports/gate_a2_compact_model.md`

**Pertanyaan** Bisakah artifact RSF (5,26 GB) dikecilkan drastis tanpa
kehilangan C-index VALIDATION signifikan?

**Hasil**

| Konfigurasi | Ukuran (MB) | VAL C-index | VAL IBS | VAL Brier@30 | VAL AUC@30 |
|---|---:|---:|---:|---:|---:|
| baseline (production) | 5.262,3 | 0,8290 | 0,0475 | 0,0357 | 0,8357 |
| compact (kandidat A2) | 66,2 | **0,8417** | 0,0482 | 0,0357 | **0,8509** |

Fit compact: 67,2 detik. Lever: perkasar target `duration_days` yang
dilihat `.fit()` (resolusi harian s/d 120 hari, kelipatan 30 hari di
atasnya - evaluasi tetap pakai `duration_days` asli), n_estimators
100->60, min_samples_leaf 30->80, min_samples_split 40->110.

**Keputusan** LULUS - artifact <=100 MB, C-index VALIDATION justru lebih
tinggi dari baseline (grid kasar bertindak sebagai regularisasi). Jadi
kandidat compact untuk Fase C.

---

## E-23 · Fase A3: studi kalibrasi (kandidat compact)

2026-08-21 · asal: `survival_model/event_based/reports/gate_a3_calibration_study.md`

**Pertanyaan** Bagaimana kualitas kalibrasi isotonic per-horizon pada
kandidat compact, dan dampaknya ke ambang risk_level HIGH/MEDIUM?

**Hasil**

Brier per horizon, mentah vs terkalibrasi:

| Horizon | Baris | Kejadian | Brier mentah | Brier terkalibrasi |
|---|---:|---:|---:|---:|
| 30d | 5.147 | 227 | 0,0374 | 0,0367 |
| 60d | 4.154 | 330 | 0,0614 | 0,0594 |
| 90d | 3.763 | 400 | 0,0769 | 0,0728 |
| 120d | 3.374 | 441 | 0,0880 | 0,0829 |

Monotonisitas lintas horizon: pelanggaran SEBELUM cummax 527/5.540 baris
(diharapkan, isotonic per horizon dikalibrasi terpisah); SESUDAH cummax: 0
(diverifikasi assert).

Dampak ke risk_cutoffs (HIGH>=0,25, MEDIUM>=0,15) pada risiko 30 hari,
populasi VALIDATION landmark (5.540 baris, bukan populasi PART aktif
produksi):

| | HIGH | MEDIUM |
|---|---:|---:|
| Skor mentah (1-S(30)) | 141 | 245 |
| Skor terkalibrasi | 245 | 163 |

**Keputusan** cummax lintas horizon wajib (konsisten dengan
`test_parity.py` untuk CatBoost hazard-chaining). Kalibrasi bergeser
populasi HIGH/MEDIUM cukup besar - dicatat sebagai arah/besaran pergeseran,
bukan keputusan gerbang tersendiri (lihat E-24 untuk keputusan akhir).

---

## E-24 · Fase A5: Keputusan Gerbang - Survival Event-Based vs CatBoost v2

2026-08-21 · asal: `survival_model/event_based/reports/gate_decision.md`

**Pertanyaan** Apakah survival event-based menggantikan CatBoost v2 sebagai
mesin keputusan utama (cutover), berdasarkan seluruh gerbang G1-G8?

**Hasil**

G1-G4 (akurasi operasional, populasi TEST classification identik N=38.451,
lihat E-20):

| # | Kriteria | Ambang | Event-based | CatBoost v2 | Status |
|---|---|---|---:|---:|---|
| G1 | PR-AUC | survival >= catboost | 0,1643 | 0,1444 | LULUS |
| G2 | Recall@kapasitas 200/bln | survival >= catboost | 0,2816 | 0,3348 | **GAGAL** |
| G3 | Precision@kapasitas | >= 0,95x catboost (0,2039) | 0,1805 | 0,2146 | **GAGAL** |
| G4 | Brier (kalibrasi 30d) | <=1,10x catboost (0,02365) | 0,0214 | 0,0215 | LULUS |

ROC-AUC (dilaporkan, tidak menggerbang): event-based 0,7437 vs catboost
0,8165, di bawah ambang pertimbangan 0,78.

G5-G6 (ukuran artifact + latency, lihat E-21/E-22): keduanya LULUS. Bonus:
C-index VALIDATION kandidat compact lebih tinggi dari baseline produksi
(0,8417 vs 0,8290) dan AUC@30 lebih tinggi (0,8509 vs 0,8357).

G7 (instalasi scikit-survival di image production): LULUS dengan mitigasi
tercatat - `pip install scikit-survival==0.28.0` polos gagal di
`python:3.13-slim` (dependensi `ecos` tidak punya wheel py3.13, `gcc` tidak
ada di image). Diperbaiki dengan install dependensi riil secara eksplisit
lalu `pip install --no-deps`. `scikit-learn==1.9.0` (lock file) kompatibel
tanpa upgrade.

G8 (reproduksibilitas lintas snapshot DB): LULUS - ditutup 2026-08-22.
`python -m partrisk.training.failure_survival` (tanpa cache, DB fresh)
dijalankan dua kali berturut-turut: `data_end` sama (2026-08-03 11:07:22),
`rows_by_split` identik (TRAIN=92.298 VALIDATION=5.540 TEST=4.890), RSF
C-index VAL/TEST = 0,841674/0,862468 kedua run **BIT-IDENTIK** (diff=0),
seluruh IBS/Brier/AUC per horizon BIT-IDENTIK, Cox PH sama. Membuktikan
pipeline deterministik untuk state DB yang sama (bukan bukti stabilitas
lintas snapshot DB berbeda).

Konfigurasi pemenang (Fase C):
```
RandomSurvivalForest(
    n_estimators=50, min_samples_split=140, min_samples_leaf=100,
    max_features="sqrt", n_jobs=1, random_state=42,
)
# target .fit() dikasarkan: harian s/d 120 hari, kelipatan 60 hari di atasnya
# (evaluasi/serving TETAP pakai duration_days asli, tidak dikasarkan)
# + 4 IsotonicRegression per horizon [30,60,90,120] + cummax lintas horizon
```

**Keputusan TIDAK CUTOVER.** G1 lulus tapi G2 dan G3 gagal secara nyata
(bukan marginal) - sesuai aturan "G1 atau G2 gagal -> jangan cutover".
Survival event-based TIDAK menggantikan CatBoost v2 sebagai mesin
keputusan utama. Restrukturisasi `src/partrisk` tetap jalan (keputusan
rencana, tidak bersyarat gerbang). Survival masuk mode **aditif**: CatBoost
tetap memiliki `failure_probability_*`/`risk_level`/`tier_score`; survival
menyuplai `median_days_to_failure` + `survival_curve` sebagai field
advisory (`advisory: true`) - kontrak API diperluas, tidak menggantikan
jalur keputusan. `models/failure/CURRENT` tetap v2 CatBoost, dashboard/API
tidak disentuh oleh keputusan ini sendiri.

---

## E-25 · Eksperimen: rekayasa keseimbangan kelas (CatBoost)

2026-08-22 · asal: `reports/class_imbalance_experiment.md`

**Pertanyaan** Target 30-hari CatBoost sangat timpang (~1,5% positif TRAIN,
~2,3% TEST), ditangani lewat `auto_class_weights="Balanced"`. Apakah
merekayasa datanya sendiri (undersample/oversample) lebih baik dari
pembobotan loss?

**Metode** VALIDATION/TEST tidak pernah direkayasa (selalu distribusi
natural). Hanya TRAIN diubah. Hyperparameter CatBoost sama persis di semua
varian kecuali `auto_class_weights`. Kalibrator isotonic dilatih ulang di
VALIDATION natural untuk tiap varian. Baseline direproduksi ulang, cocok
sampai 4 desimal dengan `models/failure/v2/metadata.json`.

**Hasil (11 varian, diurutkan Recall@kapasitas menurun)**

| Varian | Pos rate TRAIN | n TRAIN | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Baseline (auto_class_weights, natural)** | 1,53% | 251.568 | 0,8211 | 0,1610 | 0,0215 | **0,3359** | **0,2154** |
| Tanpa class_weight, tanpa resample | 1,53% | 251.568 | 0,8175 | 0,1655 | 0,0213 | 0,3304 | 0,2118 |
| Undersample 25% (sendiri) | 25% | 15.408 | 0,8242 | 0,1497 | 0,0216 | 0,3315 | 0,2125 |
| Undersample 10% (sendiri) | 10% | 38.520 | 0,8268 | 0,1743 | 0,0212 | 0,3193 | 0,2047 |
| Oversample 10% | 10% | 275.240 | 0,8278 | 0,1559 | 0,0214 | 0,3115 | 0,1997 |
| Undersample 10% + class_weight | 10% | 38.520 | 0,8256 | 0,1612 | 0,0214 | 0,3071 | 0,1969 |
| Undersample 5% (sendiri) | 5% | 77.040 | 0,8287 | 0,1782 | 0,0212 | 0,3226 | 0,2068 |
| Undersample 50%/1:1 (sendiri) | 50% | 7.704 | 0,8142 | 0,1492 | 0,0217 | 0,2871 | 0,1841 |
| Undersample 25% + class_weight | 25% | 15.408 | 0,8126 | 0,1331 | 0,0218 | 0,2849 | 0,1827 |
| Oversample 25% | 25% | 330.288 | 0,8122 | 0,1277 | 0,0217 | 0,2639 | 0,1692 |
| Oversample 50%/1:1 | 50% | 495.432 | 0,8011 | 0,1000 | 0,0221 | 0,1973 | 0,1265 |

**Temuan**

1. Tidak satu pun dari 11 varian mengalahkan baseline di Recall@kapasitas
   maupun Presisi@kapasitas - dua metrik yang benar-benar menggerbang
   promosi.
2. ROC-AUC/PR-AUC dan Recall/Presisi@kapasitas bergerak BERLAWANAN arah
   pada undersample ringan (5-10%) - bukti empiris kenapa gerbang produksi
   sengaja tidak memakai ROC-AUC sendirian.
3. Kombinasi undersample + class_weight konsisten LEBIH BURUK daripada
   masing-masing sendirian - dobel-kompensasi membuat model overcorrect.
4. Oversample (duplikasi) tidak menghindari masalah undersample, malah
   lebih buruk pada rasio yang sama, dan memburuk drastis pada rasio tinggi
   (oversample 50% = varian TERBURUK dari 11).

**Keputusan** `auto_class_weights="Balanced"` pada data natural tetap yang
terbaik. TIDAK direkomendasikan mengubah pendekatan imbalance. (SMOTE,
class_weights manual/scale_pos_weight custom, rasio di antara titik yang
diuji - tidak dicoba, di luar cakupan.)

---

## E-26 · Eksperimen: terminal/device context di CatBoost (DITOLAK)

2026-08-22 · asal: `reports/terminal_context_classification_experiment.md`

**Pertanyaan** `terminal_type_grouped` terbukti +0,007-0,019 C-index di
survival event-based (E-11/E-12) - apakah sinyal yang sama berguna untuk
CatBoost (klasifikasi biner 30-hari)?

**Metode** Baseline = v3 production sungguhan (28 fitur), direproduksi
cocok dengan `models/failure/v3/metadata.json`. Threshold grouping=100.

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline v3 (28 fitur) | 0,8244 | 0,1884 | 0,0211 | 0,3392 | 0,2175 |
| + terminal_type_grouped (29) | 0,8221 | 0,1707 | 0,0213 | 0,3271 | 0,2097 |

Kalah di kelima metrik sekaligus. Distribusi `terminal_type_grouped`
(356.100 baris): UNKNOWN 142.783 (40,1%), GATE 101.187, POS 68.751, CVIM
29.947, VENDING MACHINE 6.783, BALANCE READER 6.451, LOW_SUPPORT 198.

**Analisis** (1) Cakupan cuma 59,9% (40% UNKNOWN, relasi device baru
diketahui setelah instalasi). (2) Beda horizon yang dijawab - survival
diukur ranking jangka panjang, classification menjawab jendela sempit
30-hari; device tempat PART dipasang lebih relevan untuk degradasi jangka
panjang. (3) CatBoost kemungkinan "membayar" kompleksitas pohon untuk fitur
berinformasi rendah di jendela sempit.

**Keputusan** TIDAK di-wire. Sinyal terbukti berguna untuk survival, TIDAK
terbukti berguna untuk classification 30-hari - keberhasilan fitur di satu
model tidak otomatis transfer ke model lain, selalu perlu ablation
terpisah per model dengan metrik yang benar-benar dipakai.

---

## E-27 · Eksperimen: local failure density (item_type/client/place) di CatBoost

2026-08-22 · asal: `reports/local_density_experiment.md`

**Pertanyaan** Apakah generalisasi `attach_fleet()` ke laju kerusakan
point-in-time per item_type/client/place (jendela 90/180 hari) meningkatkan
akurasi CatBoost?

**Metode** Baseline = v3 production sungguhan (28 fitur), cocok 4 desimal
dengan `models/failure/v3/metadata.json`. `item_type_at_install`: cakupan
100% (tersedia langsung tanpa join baru). client/place: perlu range-join ke
cycle asalnya, match rate 5.897/6.715 (87,8%) - 818 kejadian tidak match,
tidak dipaksakan.

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline v3 (28 fitur) | 0,8244 | 0,1884 | 0,0211 | 0,3392 | 0,2175 |
| **+ item_type density (32)** | **0,8319** | **0,1961** | **0,0210** | **0,3392** | **0,2175** |
| + item_type + client (36) | 0,8316 | 0,1705 | 0,0213 | 0,3370 | 0,2161 |
| + item_type + client + place (40) | 0,8322 | 0,1860 | 0,0212 | 0,3459 | 0,2217 |

**Keputusan per dimensi**

- **item_type: MENANG BERSIH** - naik di kelima metrik, tanpa trade-off.
  **Di-wire ke production** (v4, 32 fitur - model produksi saat ini).
- **client: MERUGIKAN** - PR-AUC/Brier/Recall/Presisi semua turun.
  Kemungkinan karena cakupan join 87,8%. **TIDAK di-wire.**
- **place**: menambal sebagian kerusakan client (Recall/Presisi jadi
  TERTINGGI dari semua varian), tapi PR-AUC gabungan (0,1860) tetap di
  bawah baseline v3 murni (0,1884) - gagal gerbang `decide_promotion`
  (PR-AUC dan Recall@kapasitas harus sama-sama tidak turun). **TIDAK
  di-wire dalam bentuk sekarang.** (place SENDIRIAN belum diuji terpisah;
  perbaikan join client/place >87,8% belum dicoba.)

---

## E-28 · Eksperimen: `survival_risk_30d` (RSF) sebagai fitur CatBoost (DITOLAK)

2026-08-22 · asal: `reports/survival_as_feature_experiment.md`

**Pertanyaan** RSF dan CatBoost "salah di tempat berbeda" (E-24: RSF menang
PR-AUC, kalah Recall/Presisi@kapasitas) - apakah memasukkan skor RSF
(`survival_risk_30d = 1-S(30)`, dihitung pada `observation_on`) sebagai
fitur tambahan CatBoost mengambil kelebihan keduanya?

**Metode** Baseline = v4 production sungguhan (32 fitur). Reuse
`training.landmark_eval.build_landmark_features_at_observation()` +
`score_risk_30d_chunked()` (Fase A1, sudah menangani anti-leakage/chunking
memori - fungsi ini kemudian dihapus sebagai kode mati di Fase 1
konsolidasi 2026-08-23 karena tidak ada pemanggil produksi lain). Bug
ditemukan saat wiring: kolom turunan CatBoost (`DEGRADATION_FEATURES` dkk)
bentrok nama dengan hasil `attach_dynamic_extra()` - diperbaiki dengan
membuang ketiga daftar kolom itu sebelum diserahkan ke fungsi landmark.
`log1p(survival_risk_30d)` diuji juga - identik hasilnya dengan versi
mentah (diharapkan, model tree-based tidak peka transformasi monoton).

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline v4 (32 fitur) | 0,8319 | 0,1961 | 0,0210 | 0,3392 | 0,2175 |
| + survival_risk_30d (34) | 0,8195 | 0,1910 | 0,0211 | 0,3437 | 0,2203 |
| + log1p(survival_risk_30d) (34) | 0,8195 | 0,1910 | 0,0211 | 0,3437 | 0,2203 |

**Analisis** Trade-off yang bersih tapi gagal gerbang: ROC-AUC/PR-AUC turun
jelas, Recall/Presisi@kapasitas naik. `decide_promotion` mensyaratkan
PR-AUC DAN Recall@kapasitas sama-sama tidak boleh turun - PR-AUC turun
(0,1961->0,1910) berarti gagal gerbang. CatBoost sepertinya sebagian
mengganti sinyalnya sendiri yang lebih tajam dengan sinyal RSF yang lebih
kasar.

**Keputusan** TIDAK di-wire sebagai fitur tunggal. (Soft-voting/stacking
skor akhir, horizon RSF lain 60/90/120 hari - belum dicoba.)

---

## E-29 · Eksperimen: interaksi eksplisit di CatBoost (DITOLAK)

2026-08-22 · asal: `reports/interaction_features_experiment.md`

**Pertanyaan** Apakah interaksi eksplisit (`age x prior_failures`,
`trend_ratio x age`, `item_type_failure_rate_90d x age`, dibangun dari
kolom fitur v4 yang sudah ada) membantu CatBoost di data jarang-positif
(~1,5% base rate)?

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline v4 | 0,8319 | 0,1961 | 0,0210 | 0,3392 | 0,2175 |
| + age x prior_failures (33) | 0,8343 | 0,1871 | 0,0211 | 0,3404 | 0,2182 |
| + trend x age (34) | 0,8291 | 0,1892 | 0,0211 | 0,3415 | 0,2189 |
| + item_type_rate x age (35) | 0,8346 | 0,1908 | 0,0211 | 0,3426 | 0,2196 |

Ketiganya gagal gerbang PR-AUC (turun terus, tidak pernah pulih ke
baseline), walau ROC-AUC dan Recall/Presisi@kapasitas konsisten naik.

**Pola berulang** Eksperimen KETIGA berturut-turut (setelah local density
client+place di E-27, survival_risk_30d di E-28) dengan pola identik:
ROC-AUC & Recall/Presisi@kapasitas naik, PR-AUC turun - kemungkinan ruang
sinyal "mudah" untuk PR-AUC sudah mendekati jenuh dengan v4.

**Keputusan** TIDAK di-wire.

---

## E-30 · Eksperimen: hari sejak kerusakan terakhir per item_type (DITOLAK)

2026-08-22 · asal: `reports/time_since_last_failure_experiment.md`

**Pertanyaan** Beda dari density rate 90/180d (jumlah kejadian), apakah
sinyal recency ("hari sejak kejadian TERAKHIR di item_type yang sama")
menangkap pola cluster/burst kerusakan?

**Metode** `time_since_last_group_failure()`: hari sejak `failure_onset_on`
terakhir pada `item_type_at_install` yang sama, strict sebelum
`observation_on` (anti-leakage). NaN -> 0 + flag. Cakupan sinyal 92%.

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline v4 (32 fitur) | 0,8319 | 0,1961 | 0,0210 | 0,3392 | 0,2175 |
| + time_since_last_item_type_failure (34) | 0,8331 | 0,1656 | 0,0213 | 0,3392 | 0,2175 |

PR-AUC turun TAJAM (-0,03, jauh lebih besar dari eksperimen fitur
sebelumnya yang turun -0,005 s/d -0,009). Recall/Presisi@kapasitas kali ini
TIDAK ikut naik (persis sama dengan baseline) - murni kalah, tanpa
kompensasi.

**Keputusan** TIDAK di-wire. Bukan masalah cakupan (92% terisi) - sinyal
recency per kategori LUAS (item_type, cuma 5-6 kategori) terlalu
kasar/noisy. (Grouping lebih sempit, `item_model_code_clean` - belum
dicoba; median recency 8,5 hari sangat pendek, wajar untuk grup sebesar
item_type.)

---

## E-31 · Eksperimen: repair-quality proxy (confirmed-failure previous cycle) DITOLAK

2026-08-22 · asal: `reports/repair_quality_proxy_experiment.md`

**Pertanyaan** Versi CONFIRMED-failure dari `previous_cycle_lifetime_mean`
(lihat E-06, ditemukan mencampur SEMUA cara siklus berakhir) - apakah
mengukur "seberapa cepat part serupa gagal lagi setelah repair" lebih jujur
membantu CatBoost?

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline v4 (32 fitur) | 0,8319 | 0,1961 | 0,0210 | 0,3392 | 0,2175 |
| + confirmed_failure_lifetime_mean (34) | 0,8323 | 0,1951 | 0,0211 | 0,3392 | 0,2175 |
| + last_confirmed_failure_lifetime (36) | 0,8316 | 0,1871 | 0,0211 | 0,3381 | 0,2168 |

Cakupan sinyal cuma 9,3% (mayoritas PART belum pernah punya siklus
sebelumnya yang CONFIRMED berakhir kerusakan). Varian rata-rata: praktis
netral (PR-AUC turun 0,001, level noise). Varian "terakhir": lebih buruk di
semua metrik.

**Keputusan** TIDAK di-wire. Cakupan terlalu sparse (9,3%) untuk mengubah
model secara meaningful - sesuai aturan `decide_promotion` (PR-AUC tidak
boleh turun sama sekali), varian mean pun gagal gerbang secara ketat walau
bedanya sangat kecil.

---

## E-32 · Eksperimen: completeness/data-quality score DITOLAK

2026-08-22 · asal: `reports/completeness_score_experiment.md`

**Pertanyaan** Data lama (~2013-2017) diduga kurang lengkap - apakah
menandai era/kelengkapan riwayat sebagai FITUR (bukan sample weight)
membantu model "kurang yakin" pada baris era itu?

**Metode** Dua varian: `is_early_era` (observation_on tahun <2018) dan
`completeness_score` (has_previous_cycle + log_total_prior_events
dinormalkan).

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline v4 (32 fitur) | 0,8319 | 0,1961 | 0,0210 | 0,3392 | 0,2175 |
| + is_early_era (33) | 0,8327 | 0,1767 | 0,0212 | 0,3337 | 0,2139 |
| + completeness_score (34) | 0,8356 | 0,1870 | 0,0211 | 0,3370 | 0,2161 |

Kedua varian kalah di SEMUA metrik operasional (PR-AUC, Recall@kapasitas,
Presisi@kapasitas) - cuma ROC-AUC yang naik. Berbeda dari eksperimen lain
di seri ini (yang setidaknya Recall@kapasitas ikut naik) - di sini murni
kalah.

**Keputusan** TIDAK di-wire. Distribusi tahun observasi (2013: 7.771 baris
s/d 2026: 38.451 baris) menunjukkan data memang tidak seimbang antar era,
tapi menandai era secara eksplisit sebagai fitur membuat model
"membedakan berdasarkan waktu" alih-alih "membedakan berdasarkan kondisi
PART" - kontraproduktif untuk generalisasi.

---

## E-33 · Eksperimen: sample weight by completeness DITOLAK

2026-08-22 · asal: `reports/sample_weight_completeness_experiment.md`

**Pertanyaan** Beda dari era-weighting (waktu, sudah ditolak) - apakah
memberi bobot lebih besar pada baris dengan riwayat lebih KAYA (bukan lebih
baru) lewat `completeness_score` sebagai `sample_weight` CatBoost membantu?

**Hasil**

| Varian | ROC-AUC | PR-AUC | Brier | Recall@kap | Presisi@kap |
|---|---:|---:|---:|---:|---:|
| Baseline (tanpa weight) | 0,8319 | 0,1961 | 0,0210 | 0,3392 | 0,2175 |
| Weight linear (1,0-2,0) | 0,8352 | 0,1878 | 0,0211 | 0,3437 | 0,2203 |
| Weight halus (0,7-1,3) | 0,8338 | 0,1915 | 0,0211 | 0,3404 | 0,2182 |

Kedua rentang bobot gagal gerbang PR-AUC, walau Recall/Presisi@kapasitas
naik di keduanya (rentang lebih halus = trade-off lebih kecil, tapi tetap
gagal).

**Pola berulang** Kali KEENAM pola identik muncul di seri eksperimen
roadmap ini (E-27 client+place, E-28, E-29 x3, sekarang): ROC-AUC &
Recall/Presisi@kapasitas naik, PR-AUC turun - properti struktural
populasi/metrik v4 saat ini. Pengecualian satu-satunya yang lolos gerbang:
local density item_type (E-27) - kemungkinan karena sinyal genuinely baru,
bukan reweighting/kombinasi dari yang sudah ada.

**Keputusan** TIDAK di-wire.

---

## E-34 · Eksperimen: grid hyperparameter CatBoost

2026-08-22 · asal: `reports/hyperparameter_grid_experiment.md`

**Pertanyaan** Apakah konfigurasi CatBoost sekarang (depth=4,
iterations=200, lr=0,03, l2=10) masih optimal, atau tree lebih dalam/lebih
banyak iterasi lebih baik?

**Metode** 5 kandidat (baseline + 4 varian), seleksi via VAL PR-AUC (bukan
TEST).

**Hasil**

| Config | VAL PR-AUC | VAL ROC-AUC |
|---|---:|---:|
| **Baseline (depth=4, iterations=200, lr=0,03, l2=10)** | **0,1116** | 0,8170 |
| depth=5, iterations=300, l2=12 | 0,1098 | 0,8124 |
| depth=6, iterations=400, l2=15 | 0,1065 | 0,8089 |
| depth=5, iterations=300, lr=0,025, l2=12 | 0,1098 | 0,8173 |
| depth=4, iterations=300, l2=15 | 0,1087 | 0,8155 |

Baseline menang VAL PR-AUC melawan semua 4 kandidat - tidak ada yang
dievaluasi di TEST karena baseline sendiri terpilih.

**Keputusan** Konfigurasi sekarang sudah dekat optimal untuk grid yang
dicoba. Tree lebih dalam/lebih banyak iterasi konsisten LEBIH BURUK di VAL
(base rate positif ~1,5% - kompleksitas tambahan kemungkinan overfit).
Tidak ada perubahan hyperparameter direkomendasikan. (Grid ke arah
sebaliknya - lebih dangkal/regularisasi lebih kuat - belum dicoba.)

---

## E-35 · Error analysis + eksperimen terarah: age_history_base_rate (DITOLAK)

2026-08-22 · asal: `reports/error_analysis_and_age_history_rate.md`

**Pertanyaan** Di mana model v4 sungguhan (bukan refit) gagal menangkap
kerusakan di TEST, dan apakah fitur terarah bisa memperbaiki blind spot
itu?

**Hasil - Error analysis** 902 kerusakan nyata di TEST, model v4 menangkap
306 (Recall@kapasitas 0,339) dari 1.407 slot.

| Slice | Recall |
|---|---:|
| Umur pasang 0-90 hari | 0,812 |
| Umur pasang 91-180 hari | 0,075 |
| Umur pasang 181-365 hari | 0,000 |
| Umur pasang 366-730 hari | 0,000 |
| Umur pasang 731-1460 hari | 0,000 |
| Umur pasang 1461+ hari | 0,000 |
| Tanpa riwayat corrective sebelumnya | 0,000 (347 kerusakan, 0 tertangkap) |
| Ada riwayat corrective sebelumnya | 0,551 |
| First failure | 0,197 |
| Repeat failure | 0,527 |
| Client KAI LRT Jabodebek | 0,037 |
| Client KCI (commuter) | 0,369 |

**Temuan struktural** Model nyaris buta terhadap PART berumur >90 hari
TANPA riwayat corrective sebelumnya (38% dari total positif TEST) - hampir
semua fitur utama model (count-based) bernilai NOL untuk populasi ini.

**Eksperimen terarah** `age_history_base_rate`: smoothed target encoding
(shrinkage `SMOOTH_STRENGTH=50`) base rate 30-hari historis per kombinasi
(item_type_at_install, installation_age_band, has_prior_corrective), dari
TRAIN, dibekukan.

| Metrik | Baseline v4 | + age_history_base_rate |
|---|---:|---:|
| ROC-AUC | 0,8319 | 0,8109 |
| PR-AUC | 0,1961 | 0,1788 |
| Brier | 0,0210 | 0,0212 |
| Recall@kapasitas | 0,3392 | 0,3082 |
| Presisi@kapasitas | 0,2175 | 0,1976 |

Kalah di SEMUA metrik, tanpa trade-off. Cek blind spot: umur 181-365
membaik 0,000->0,015, umur 366+ tetap 0,000, tanpa riwayat corrective TETAP
0,000, umur 0-90 (tadinya bagus) malah TURUN 0,812->0,734. Gagal total
mencapai tujuannya.

**Analisis** 180 kombinasi bucket dari ~250rb baris TRAIN dengan base rate
1,5% berarti rata-rata ~21 kejadian positif per bucket - terlalu sedikit
untuk target-encoding stabil.

**Keputusan** TIDAK di-wire. Bukti kuat blind spot ini kemungkinan besar
keterbatasan data (intensitas pemakaian, kondisi lingkungan, kualitas
batch produksi), bukan fitur yang hilang. Populasi "no prior corrective,
umur lama" (38% dari kerusakan TEST) kemungkinan besar tidak bisa
diprediksi lebih baik dengan data yang ada - opsi realistis: data baru
(di luar cakupan proyek) atau kebijakan operasional terpisah (jadwal
inspeksi preventif berbasis umur murni).

---

## E-36 · Fase R1: evaluasi RSF dengan metrik yang lebih mudah dipahami

2026-08-22 · asal: `reports/rsf_r1_evaluation.md`

**Pertanyaan** (bukan mengejar C-index lebih tinggi) (1) Seberapa bisa
dipercaya "sisa umur" (median/days-to-90%)? (2) Seberapa terkalibrasi
`risk_30d`...`risk_120d` setelah isotonic+cummax? (3) Kapan
`median_days_to_failure` kosong, dan apakah UI menangani itu jujur?

**Metode** TEST landmark dibangun ulang fresh (4.890 baris, 412
event_observed). Error median hanya pada baris event_observed=True dan
median terisi. Kalibrasi diukur reliability table 5 bucket, TEST (bukan
VAL) untuk ukuran generalisasi jujur.

**Hasil**

(1) Brier & AUC per horizon (TEST landmark): 30d Brier=0,0496 AUC=0,8949 ·
60d Brier=0,0519 AUC=0,9127 · 90d Brier=0,0529 AUC=0,8828 · 120d
Brier=0,0510 AUC=0,9055. C-index (Harrell)=0,8625, C-index (Uno/IPCW)=
0,8623, IBS=0,0517.

(2) Median sisa umur - jarang terisi:

| Populasi | % median = null |
|---|---:|
| TEST landmark (4.890 baris) | 79,3% |
| **Armada aktif sekarang (16.877 PART)** | **94,7%** |

Untuk 257 baris TEST event_observed=True dan median terisi (257/412=62%):
median error 439 hari, mean 751,9 hari, P25/P75 250/1.078 hari.
`days_until_survival_90pct` jauh lebih sering terisi: 32,9% null di armada
aktif (vs 94,7% untuk median).

(3) Kalibrasi risk_30d/risk_90d - reliability table TEST, 5 bucket:

risk_30d (n_label=4.382): bucket 1 n=877 pred_mentah=0,0024
pred_terkalibrasi=0,0000 aktual=0,0000 · bucket 2 n=877 0,0037/0,0000/
0,0034 · bucket 3 n=876 0,0077/0,0021/0,0023 · bucket 4 n=876 0,0483/0,0396/
0,0696 · **bucket 5 (tertinggi) n=876 0,1578/0,1699/0,2842** (underestimate).

risk_90d (n_label=2.430): bucket 1 n=486 0,0048/0,0000/0,0041 · bucket 2
n=486 0,0041/0,0000/0,0041 · bucket 3 n=486 0,0305/0,0160/0,0391 · bucket 4
n=486 0,1028/0,1266/0,2366 · **bucket 5 n=486 0,2460/0,3466/0,4877**
(underestimate).

(4) `dashboard/ui.py::survival_advisory()` (baris 257-275) sudah
menampilkan `days_until_survival_90pct` berdampingan dengan median, plus
caption saat median kosong - cocok persis dengan estimasi "~5%" di
docstring lama (aktual 5,3%). Tidak ada perubahan kode diperlukan untuk
item (c).

**Keputusan** Jangan jadikan median sebagai metrik sukses R2 -
`days_until_survival_90pct` adalah field yang lebih realistis. Kalibrasi di
bucket risiko tertinggi (underestimate, risk_30d 0,17 vs aktual 0,28;
risk_90d 0,35 vs aktual 0,49) adalah temuan baru layak ditindaklanjuti -
lihat E-37. Brier/AUC/C-index agregat sehat, tidak ada regresi mengejutkan.

---

## E-37 · Kalibrasi RSF via 5-fold CV untuk perbaiki underestimate bucket tertinggi (DITOLAK)

2026-08-22 · asal: `reports/rsf_calibration_cv_experiment.md`

**Pertanyaan** Apakah calibrator yang dilatih pada populasi jauh lebih
besar (5-fold CV out-of-fold, TRAIN+VALIDATION, bukan cuma VAL 534 event)
memperbaiki underestimate di bucket risiko tertinggi (temuan E-36)?

**Metode** 5-fold CV di level LIFECYCLE pada TRAIN+VALIDATION (97.838
baris, 17.296 lifecycle, 11.741 event) - RSF compact params production tiap
fold, encoder production dipakai apa adanya. Model yang di-deploy TIDAK
diganti - hanya `calibrators.joblib` yang disoal. TEST tetap 100% holdout.

**Hasil**

risk_30d, TEST (n_label=4.382): bucket 5 (tertinggi) aktual=0,2808
pred_lama=0,1699 gap_lama=0,1109 pred_CV=0,2267 **gap_CV=0,0541
(membaik)**.

risk_90d, TEST (n_label=2.430): bucket 4 aktual=0,2428 pred_lama=0,1266
gap_lama=0,1162 pred_CV=0,0968 **gap_CV=0,1460 (MEMBURUK)**; bucket 5
aktual=0,4877 pred_lama=0,3466 gap_lama=0,1411 pred_CV=0,3242 **gap_CV=
0,1635 (MEMBURUK)**.

**Interpretasi** Campuran, bukan kemenangan bersih - risk_30d membaik
hampir separuh gap di bucket tertinggi, tapi risk_90d justru MEMBURUK di
dua bucket teratas (berlawanan hipotesis). Dugaan penyebab: OOF raw_risk
dari 5 model RSF sementara (tiap fold beda), bukan dari model production
sesungguhnya - pemetaan isotonic tidak otomatis pas ke raw_risk model
production yang sebenarnya.

**Keputusan** DITOLAK sebagai pengganti penuh `calibrators.joblib`.
`calibrators.joblib` production TIDAK diubah. Underestimate di bucket
risiko tertinggi risk_30d/90d TETAP ADA, didokumentasikan sebagai
keterbatasan diketahui, bukan disembunyikan. (Kalibrasi per-horizon dengan
CV bersarang dari model final - belum dicoba, di luar cakupan "cepat, ROI
tinggi" R1.)

---

## E-38 · Fase R2: ablation LOCAL_DENSITY_FEATURES (item_type) di RSF (DITOLAK)

2026-08-22 · asal: `reports/rsf_r2_item_type_density_ablation.md`

**Pertanyaan** `LOCAL_DENSITY_FEATURES` menang di semua 5 metrik CatBoost
v4 (E-27) - apakah porting fitur yang sama ke RSF (kolom sudah tertempel
di pipeline untuk kebutuhan `feature_builder.build_features()`, tapi belum
masuk `FEATURE_COLUMNS` RSF) meningkatkan akurasi RSF?

**Metode** BASELINE = `features.compute_features()` apa adanya (production,
TANPA density). CANDIDATE = + 4 kolom `LOCAL_DENSITY_FEATURES`. Encoder
kategorikal sama persis. RSF compact params production. Gate R3: promote
hanya kalau Brier@30d DAN Brier@90d TEST tidak memburuk.

**Hasil (TEST)**

| Metrik | Baseline | Candidate (+density) | Selisih |
|---|---:|---:|---:|
| C-index | 0,8625 | 0,8550 | -0,0075 |
| IBS | 0,0517 | 0,0518 | +0,0001 |
| Brier@30d | 0,0496 | 0,0499 | **+0,0003 (memburuk)** |
| Brier@60d | 0,0519 | 0,0523 | +0,0004 (memburuk) |
| Brier@90d | 0,0529 | 0,0530 | **+0,0001 (memburuk)** |
| Brier@120d | 0,0510 | 0,0506 | -0,0004 (membaik) |
| AUC@30d | 0,8949 | 0,8928 | -0,0021 |
| AUC@60d | 0,9127 | 0,9110 | -0,0017 |
| AUC@90d | 0,8828 | 0,8806 | -0,0022 |
| AUC@120d | 0,9055 | 0,9027 | -0,0028 |

Kalibrasi bucket tertinggi relatif tidak berubah: risk_30d bucket 5
pred 0,1699->0,1645 (aktual 0,2842->0,2911); risk_90d bucket 5 pred
0,3466->0,3399 (aktual 0,4877->0,4938) - underestimate sama besarnya.

**Keputusan** DITOLAK. Gate R3 gagal (Brier@30d DAN Brier@90d keduanya
memburuk, walau tipis). C-index/AUC konsisten memburuk di semua horizon
kecuali Brier@120d. Perbedaan kecil (~0,0001-0,0075) tapi KONSISTEN arahnya
dan reproducible (random_state=42 tetap) - bukan noise run-to-run. Fitur
TIDAK diwire ke `features/survival/builder.py` - kolom density tetap ada
di `landmarks`/observations sebagai artefak pipeline (dibutuhkan
`feature_builder.build_features()`), tapi tidak dipakai `FEATURE_COLUMNS`
RSF. Konsisten dengan temuan CatBoost: item_type density genuinely
membantu classification, tapi RSF sudah punya cara berbeda melihat
"tekanan lokal" - menambahkan sinyal sama lewat jalur berbeda tidak
otomatis membantu model dengan arsitektur berbeda.

---

## E-39 · Fase R3: stabilitas & retrain policy RSF

2026-08-22 · asal: `reports/rsf_r3_retrain_policy.md`

**Pertanyaan** Tiga poin rencana upgrade RSF: (1) retrain lebih jarang, (2)
compact artifact + `n_jobs=1` saat serve, (3) gate ringan promote hanya
kalau Brier@30/90 tidak memburuk.

**Hasil / Keputusan**

1. RSF **advisory** - tidak menentukan ranking/urutan inspeksi (tetap
   CatBoost, E-24) - tidak perlu retrain semingguan seperti CatBoost.
   Kebijakan tertulis eksplisit di docstring `training/failure_survival.py`:
   retrain wajar bulanan, atau kapan pun `data_end` bergeser >60 hari dari
   training terakhir - operasional (manual/cron di luar repo).
2. Sudah ada, diverifikasi tetap ada, TIDAK diubah: `COMPACT_RSF_PARAMS`
   (n_estimators=50, min_samples_leaf=100, n_jobs=1, artifact ~66 MB vs
   5,26 GB lama). `predict/survival.py::load_model()` eksplisit set
   `model.n_jobs = 1` setelah `joblib.load()` - mencegah RSF ter-unpickle
   dengan n_jobs=-1 hang tanpa error saat `predict_survival_function()`.
3. **Diimplementasikan sebagai kode**: `decide_survival_promotion()`
   (`training/failure_survival.py`), dipanggil `main()` sebelum
   `joblib.dump()`. Kalau belum ada artifact: lolos otomatis. Kalau sudah
   ada: Brier@30d DAN Brier@90d kandidat harus <= incumbent, keduanya.
   Kalau gagal: TIDAK menimpa artifact, exit code 1. Sengaja BUKAN dual-gate
   PR-AUC/Recall@kapasitas ala `decide_promotion` CatBoost - model advisory
   tidak dipakai ranking. Normalisasi kunci horizon int vs string metadata
   ditangani eksplisit. 6 test baru di `tests/test_promotion.py`.

**Perbaikan tambahan** `metadata.json`'s
`calibration.applied_to_advisory_fields` masih `false` sejak Fase R1(a)
mengaktifkan kalibrasi (commit `be83a03`, tidak retrain ulang) - diperbaiki
di metadata.json existing (factual correction) dan sumber di
`training/failure_survival.py`.

**Status** Selesai. Murni kebijakan + satu gate kode kecil, sesuai sifatnya
yang ditandai opsional user.

---

## E-40 · Langkah A: baseline error sisa umur & kalibrasi kurva (RSF, sebelum perbaikan)

2026-08-22 · asal: `reports/rsf_median_curve_baseline.md`

**Pertanyaan** Membingkai ulang tujuan RSF: bukan ranking (PR-AUC/
Recall@kapasitas), tapi seberapa dekat median/kurva S(t) dengan kejadian
nyata. Baseline WAJIB sebelum eksperimen apa pun.

**Metode** Populasi: TEST landmark (4.890 baris, 412 event_observed),
dipecah (a) seluruh TEST dan (b) subset `landmark_source=="ANCHOR"` (2.041
baris, mirip populasi serving). MAE/bias median hanya baris
event_observed=True dan median terisi. Bias SIGNED (+ berarti model
terlalu optimis).

**Hasil**

Median: bias OPTIMIS masif dan sistematis.

| Subset | n usable | % null | MAE median | Bias (signed) | % over-predict |
|---|---:|---:|---:|---:|---:|
| Seluruh TEST | 257 | 79,3% | 751,9 hari | **+751,9 hari** | **99,6%** |
| ANCHOR (mirip serving) | 18 | 87,8% | 1.303,8 hari | **+1.303,8 hari** | **100,0%** |

Satu-satunya item_type dengan cukup event untuk dilaporkan (MODULE READER,
n=10, subset ANCHOR): bias +1.773,7 hari, MAE 1.794 hari - arah dan besaran
sama dengan agregat.

Kalibrasi kurva mentah: S(t) turun TERLALU LAMBAT.

| Horizon | mean S(d) prediksi (mentah) | proporsi empiris masih hidup | gap |
|---|---:|---:|---:|
| 30d | 0,9560 | 0,9281 | 0,028 |
| 60d | 0,9504 | 0,9104 | 0,040 |
| 90d | 0,9224 | 0,8457 | 0,077 |
| 120d | 0,9084 | 0,8140 | 0,094 |
| 180d | 0,7496 | 0,2862 | 0,464 (follow-up window terbatas, lihat catatan asli) |
| 365d | 0,6386 | 0,0000 | (artefak follow-up, bukan bukti kuantitatif) |

Gap tumbuh monoton dari 30d ke 120d (0,028->0,094) - model konsisten
memprediksi survival lebih tinggi dari kenyataan, menjelaskan bias median.

**Keputusan** RSF mentah menghasilkan median hampir selalu jauh lebih
optimis dari kenyataan - bukan sekadar "kadang null", tapi sistematis salah
arah saat terisi. `calibrators.joblib` sudah dipakai untuk
`calibrated_risk_Nd` (4 titik diskrit) TAPI median/days_until_survival_90pct
/kurva yang ditampilkan masih dari kurva MENTAH - inkonsistensi yang
diperbaiki di E-41.

---

## E-41 · Langkah B: kurva S(t) terkalibrasi konsisten, diwire ke production

2026-08-22 · asal: `reports/rsf_median_curve_calibration_result.md`

**Pertanyaan** Setelah kalibrasi horizon (E-40), bangun ulang cara baca
median dari kurva yang sudah disesuaikan skalanya secara konsisten (bukan
median dari kurva mentah + risk dari kurva kalibrasi - inkonsisten).

**Bug ditemukan** Prototipe pertama `calibrated_survival_matrix()` memakai
interval TERBUKA di kedua ujung untuk interpolasi antar horizon - titik
grid PERSIS sama dengan horizon terlatih (t=60, t=90) tidak masuk region
manapun, terisi `np.empty_like()` (memori tidak diinisialisasi), ikut
ter-cummax. Diperbaiki: interval setengah-terbuka `(h_lo, h_hi]` konsisten
+ `np.full(..., np.nan)` + `assert not np.isnan(...)` eksplisit. Dua run
(sebelum/sesudah fix) hampir identik (garbage kebetulan ter-overwrite),
tapi bug tetap diperbaiki. Regression test ditambahkan
(`tests/test_survival_curves.py`, 5 test).

**Metode** `curves.calibrate_curve()` (baru): raw_risk(t)=1-S(t) dipetakan
lewat isotonic per horizon terlatih, interpolasi linear antar horizon
terdekat, flat-extrapolation di luar rentang, cummax wajib di seluruh
grid. Dipakai konsisten oleh `predict()` dan `score_batch()`.
`calibrated_risk_Nd` (4 titik diskrit, Fase R1a) TIDAK diubah.

**Hasil (TEST landmark, populasi/definisi sama dengan E-40)**

| Metrik | RAW (baseline) | TERKALIBRASI | Perubahan |
|---|---:|---:|---:|
| Seluruh TEST — % median null | 79,3% | 60,4% | -18,9pp |
| — n usable | 257 | 386 | +50% |
| — MAE median | 751,9 hari | 450,0 hari | **-40,1%** |
| — Bias median (signed) | +751,9 hari | +448,5 hari | **-40,3%** |
| — % over-predict | 99,6% | 98,7% | ~tidak berubah |
| ANCHOR — % median null | 87,8% | 75,0% | -12,8pp |
| — n usable | 18 | 27 | +50% |
| — MAE median | 1.303,8 hari | 609,7 hari | **-53,2%** |
| — Bias median (signed) | +1.303,8 hari | +609,7 hari | **-53,2%** |
| — % over-predict | 100,0% | 100,0% | tidak berubah |

Gap kalibrasi kurva: 30d 0,028->0,030 (~sama) · 60d 0,040->0,032 (-20%) ·
90d 0,077->0,054 (-30%) · 120d 0,094->0,067 (-29%) · 180d 0,463->0,370
(-20%). Sanity: kurva tetap monoton non-increasing di semua baris.

**Keputusan DITERIMA, diwire ke production.** Perbaikan nyata dan konsisten
di hampir semua metrik (MAE turun 40-53%, gap kalibrasi turun 20-30% di
60-180d), TANPA retrain model apa pun. Bias TIDAK hilang sepenuhnya (median
masih optimis rata-rata +448 s/d +609 hari, over-predict rate nyaris tidak
bergerak) - sesuai ekspektasi: kalibrator hanya terlatih 30-120 hari,
sementara median jatuh di ratusan hari, jauh di luar rentang yang benar-
benar dikalibrasi. Memperbaiki lebih lanjut butuh kalibrator di horizon
lebih jauh (180/365d) - tidak dikerjakan di sini, didokumentasikan sebagai
keterbatasan diketahui.

**Perubahan kode**: `survival/curves.py` (fungsi baru `calibrate_curve()`),
`predict/survival.py` (`predict()` pakai kurva terkalibrasi, field baru
`curve_is_calibrated`; `score_batch()` parameter baru `calibrators=None`),
`serving/batch_predictor.py::_score_survival_advisory()` (meneruskan
`calibrators`), `serving/predictor.py::_survival_advisory_fields()`
(`curve_is_calibrated` dibaca dari `predict()`, dulu hardcode False). Test
baru: `tests/test_survival_curves.py` (5 test) +
`tests/test_parity.py::test_survival_kurva_terkalibrasi_monoton_turun_dan_flag_benar`.

---

## E-42 · Langkah D: ablation item_type density di RSF, digate MAE median + kalibrasi (DITOLAK)

2026-08-22 · asal: `reports/rsf_langkah_d_density_mae_ablation.md`

**Pertanyaan** E-38 (Fase R2) sudah menolak fitur ini dengan kriteria
Brier/C-index. Rencana user meminta kriteria BERBEDA: promote fitur hanya
jika MAE median/kalibrasi membaik di holdout, bukan kalau C-index naik -
apakah kesimpulannya berubah dengan kriteria ini?

**Metode** Sama persis dengan E-38: baseline vs baseline+4 kolom
`LOCAL_DENSITY_FEATURES`, encoder identik, RSF compact params production.
BEDA: kedua model di sini JUGA dikalibrasi penuh (`curves.calibrate_curve()`,
E-41) dan dievaluasi dengan MAE median + gap kalibrasi 30d/90d - metodologi
identik dengan E-40/E-41.

**Hasil**

| Metrik | Baseline | Candidate (+density) | Verdict |
|---|---:|---:|---|
| MAE median, seluruh TEST (n=386/380) | 450,0 hari | 480,0 hari | **MEMBURUK (+6,7%)** |
| MAE median, ANCHOR (n=27/25) | 609,7 hari | 446,5 hari | membaik (n kecil, lihat catatan) |
| % null, seluruh TEST | 60,4% | 61,6% | ~tidak berubah |
| % null, ANCHOR | 75,0% | 73,9% | ~tidak berubah |
| Gap kalibrasi 30d (n=4.382) | 0,0296 | 0,0312 | **MEMBURUK** |
| Gap kalibrasi 90d (n=2.430) | 0,0539 | 0,0547 | **MEMBURUK** |

Catatan: n_usable ANCHOR cuma 25-27 baris - jauh lebih rawan noise
dibanding seluruh TEST (n≈380-386). Perbaikan 609,7->446,5 hari mungkin
cuma pergeseran beberapa PART spesifik, tidak dijadikan dasar keputusan
sendirian.

**Keputusan DITOLAK** (konsisten dengan E-38, kriteria berbeda - kesimpulan
sama). 3 dari 4 metrik yang lebih bisa dipercaya (MAE seluruh TEST + gap
kalibrasi 30d + gap kalibrasi 90d, sampel jauh lebih besar) MEMBURUK.
Satu-satunya perbaikan (ANCHOR MAE) bertumpu pada sampel terlalu kecil.
Dua metodologi evaluasi BERBEDA (R2: Brier/C-index; sini: MAE median/
kalibrasi) menghasilkan kesimpulan SAMA - item_type density tidak membantu
RSF, baik untuk ranking maupun ketepatan waktu. Tidak ada perubahan kode
production.

**Langkah E (cek, bukan eksperimen)**: grid waktu RSF resolusi harian
sampai 120 hari, lalu 60-harian setelahnya (`coarsen_duration_days()`, hasil
Fase A2). `CURVE_STEP_DAYS=30` (sampling titik GRAFIK) tidak membatasi
perhitungan median/p90/ambang - `median_survival_time()` dkk selalu membaca
grid asli. **Temuan**: zona 120-180 hari hanya punya SATU titik tambahan
(t=180) - resolusi kasar, padahal mayoritas median (saat terisi) jatuh di
ratusan hari. Memperhalus resolusi 120-365 hari butuh retrain dengan skema
coarsening berbeda - trade-off langsung dengan ukuran artifact (5,26 GB ->
66,2 MB di Fase A2). Keputusan arsitektur yang sengaja TIDAK diambil
sepihak di sini - dilaporkan sebagai temuan untuk dipertimbangkan.

---

## E-43 · FASE 7 P0-6: precision@kapasitas model vs kebijakan urutan kerja tanpa model

2026-08-24 · `cli.py::baseline-comparison` (baru)

**Pertanyaan** Semua metrik model sejauh ini dibandingkan dengan tebakan
ACAK (lift 9,27x atas base rate). Yang ditanya manajemen bukan itu -
yang ditanya adalah "lebih baik dari cara kerja tim SEKARANG (tanpa
model)?". Tiga kebijakan urutan kerja yang bisa jalan tanpa model apa pun:
PART tertua dulu, PART dengan corrective terbanyak 90 hari terakhir dulu,
dan urutan aktual tim (kalau terekam - lihat §11.2, TIDAK terekam, jadi
tidak bisa dihitung).

**Metode** Populasi TEST identik dengan yang dipakai training v4 (38.451
baris, 902 kerusakan, window 211 hari, kapasitas evaluasi 1.407 baris
menurut `capacity_metrics()` yang sudah ada di `training_failure.py`).
Model diskor ULANG dengan dukungan BEKU dari `metadata.json` (metodologi
sama persis dengan `predict.py` production, BUKAN training-time dynamic
support) - hasilnya dicocokkan terhadap `promotion_comparison.candidate`
tersimpan sebagai sanity check (COCOK, selisih < 1e-6). Baseline "PART
tertua" diranking dari `days_since_installation`; baseline "corrective
terbanyak 90 hari" diranking dari `log_prior_corrective_90d` (transform
monoton dari hitungan mentah - urutan argsort identik, jadi aman dipakai
langsung tanpa hitung ulang count mentah).

**Hasil**

| Kebijakan | precision@1.407 | recall@1.407 | lift vs acak |
|---|---:|---:|---:|
| Model production (v4) | 0,2175 | 0,3392 | 9,27x |
| PART tertua dulu | 0,0739 | 0,1153 | 3,15x |
| Corrective terbanyak 90 hari dulu | 0,2139 | 0,3337 | 9,12x |
| Urutan aktual tim | - | - | tidak terekam |

**Keputusan/temuan** Model MENANG telak atas "PART tertua dulu" (2,9x
lipat precision) - umur pasang murni bukan proksi risiko yang baik,
konsisten dengan kenapa fitur ini bukan satu-satunya sinyal di model.

Temuan yang JAUH lebih menarik: "corrective terbanyak 90 hari dulu" -
heuristik SATU KOLOM, tanpa model, tanpa training - mencapai 98,3% dari
precision model dan 98,4% dari recall model (0,2139 vs 0,2175; 0,3337 vs
0,3392). Model MASIH menang, tapi tipis. Ini BUKAN alasan membatalkan
model (32 fitur menangkap pola yang tidak direduksi jadi satu angka -
selisihnya nyata dan konsisten arahnya), tapi ini bahan pembahasan yang
jujur: mayoritas nilai prediktif model saat ini bisa didekati satu sinyal
operasional sederhana yang sudah tersedia tanpa ML sama sekali. Relevan
untuk framing kontribusi model (§11.7) - "32 fitur menambah ~1,7%
precision di atas satu heuristik" adalah klaim yang defensible, "model
kami jauh lebih baik dari cara kerja manual" TIDAK didukung angka ini.

Tidak ada perubahan kode production. `Urutan aktual tim` tidak bisa
dihitung - dicatat sebagai alasan lain kenapa §11.2 (umpan balik teknisi/
urutan kerja aktual) perlu mulai dikumpulkan: tanpanya, baseline paling
relevan secara bisnis (apa yang SEBENARNYA dikerjakan tim sebelum ada
model) tidak pernah bisa diukur.

---

## E-44 · FASE 7 P0-1: backtest temporal bergulir v3 vs v4 (⚠️ TEMUAN PENTING, lihat DECISIONS.md)

2026-08-24 · `cli.py::rolling-backtest` (baru)

**Pertanyaan** Motivasi P0-1: saat v4 dipromosikan menggantikan v3, VAL
PR-AUC TURUN (0,1174 -> 0,1116) sementara TEST NAIK (0,1884 -> 0,1961) -
TEST split yang sama sudah dipakai memutuskan promosi 4 kali. Pola "VAL
turun / TEST naik" adalah tanda klasik model beradaptasi ke satu TEST
split, bukan sungguh membaik. Apakah v4 (28 fitur v3 + 4 fitur density
`LOCAL_DENSITY_FEATURES`) benar-benar lebih baik dari v3 kalau diuji di
LEBIH dari satu split?

**Metode** 6 fold temporal bergulir, window 60 hari tiap fold (non-
overlap, dari 2025-08-08 s/d 2026-08-03 - total ~1 tahun terakhir),
validasi 365 hari sebelum tiap fold, embargo 30 hari (`TARGET_HORIZON_
DAYS`) - metodologi split SAMA PERSIS dengan `training_failure.assign_
split()`, hanya `test_start`/`test_end` per-fold BUKAN tetap "1 Jan tahun
data_end". Tiap fold: 2 model dilatih dari NOL dengan hyperparameter
IDENTIK (`config.CATBOOST_PARAMS`, sama utk v3 & v4 - hanya kolom fitur
yang beda, `v3_metadata["features"]` 28 kolom vs `v4_metadata["features"]`
32 kolom, keduanya subset dari `features` yang sudah dihitung sekali untuk
seluruh dataset). Model yang dilatih di sini TIDAK disimpan/dipromosikan -
murni evaluasi. Perbandingan pakai selisih BERPASANGAN per-fold (v4-v3),
bukan selisih rata-rata independen - lebih tepat karena kedua varian diuji
di fold yang SAMA PERSIS tiap kalinya.

**Hasil**

| Metrik | v3 mean±sd | v4 mean±sd | selisih (v4-v3) berpasangan | Verdict (>1sd?) |
|---|---:|---:|---:|---|
| ROC-AUC | 0,8155±0,0378 | 0,8194±0,0316 | +0,0039±0,0094 | tidak signifikan |
| **PR-AUC** | **0,1478±0,1071** | **0,1401±0,1063** | **-0,0078±0,0062** | **v3 > v4** |
| Brier terkalibrasi | 0,0225±0,0158 | 0,0225±0,0157 | +0,0001±0,0001 | tidak signifikan |
| Precision@kapasitas | 0,1613±0,1252 | 0,1638±0,1321 | +0,0025±0,0091 | tidak signifikan |
| Recall@kapasitas | 0,2617±0,0828 | 0,2631±0,0858 | +0,0015±0,0144 | tidak signifikan |

Per-fold (lihat log lengkap `cli.py::_rolling_backtest_main`): v3 menang
PR-AUC di 5 dari 6 fold (kalah tipis hanya di fold 3 dan 6).

**Temuan** Kekhawatiran motivasi P0-1 **TERKONFIRMASI**: pada backtest yang
lebih robust (6 fold vs 1 split), **v3 (28 fitur) punya PR-AUC LEBIH
TINGGI dari v4 (32 fitur) secara konsisten, dan selisihnya melebihi 1 sd**
- kriteria yang ditetapkan sendiri di brief FASE 7 P0-1 untuk klaim "A >
B". Empat metrik lain (ROC-AUC, Brier, precision@cap, recall@cap) TIDAK
menunjukkan beda signifikan - jadi ini bukan "v3 menang di semua lini",
tapi spesifik di metrik yang jadi salah satu dari dua gerbang promosi
(`decide_promotion()`, DECISIONS.md §5a: PR-AUC DAN Recall@kapasitas
kandidat harus >= incumbent).

4 fitur density (`log_item_type_failures_90d/180d`, `item_type_failure_
rate_90d/180d`) yang membedakan v4 dari v3 tampaknya menambah VARIANS
tanpa menambah SINYAL rata-rata pada PR-AUC - konsisten dengan pola
"adaptasi ke satu TEST split" yang dicurigai di motivasi P0-1, BUKAN
perbaikan model yang genuinely generalize.

**Keputusan: TIDAK DIAMBIL DI SINI.** Membalik promosi v4->v3 adalah
keputusan produksi (mengubah `CURRENT`), di luar wewenang audit FASE 7
P0-1 sendirian - lihat DECISIONS.md §10 untuk status TERBUKA dan opsi yang
tersedia. Dicatat di sini APA ADANYA termasuk saat hasilnya TIDAK
menguntungkan model yang sedang production (§11.7: "menolak model sendiri
adalah bahan pembahasan yang jauh lebih kuat daripada 'model kami
menang'"). Tidak ada perubahan kode/model production dari eksperimen ini
sendiri.

---

## E-45 · FASE 7 P0-2: bootstrap CI 1000-resample untuk metrik headline ketiga model

2026-08-24 · `cli.py::bootstrap-ci` (baru)

**Pertanyaan** Semua metrik headline sejauh ini dilaporkan sebagai titik
tunggal, tanpa ukuran ketidakpastian. Paling mendesak untuk scrap - 21
positif di TEST berarti metrik seperti recall bisa berubah drastis dari
satu-dua kejadian saja. Berapa lebar sebenarnya rentang ketidakpastiannya?

**Metode** 1000 resample bootstrap (with-replacement) per model, seed=42,
CI persentil 2,5/97,5. Failure & scrap: resample baris TEST, hitung ulang
`training_failure.full_metrics()` (skor model TIDAK dihitung ulang tiap
resample, cuma di-resample - model/kalibrator dipakai APA ADANYA, sama
seperti sudah dilatih). Survival: `survival.bootstrap_c_index()` (FASE 1,
belum pernah dipanggil sebelum ini) pada RSF dan Cox PH, VALIDATION+TEST.

**Hasil**

| Model | Metrik | Titik | CI95 |
|---|---|---:|---:|
| Failure v4 (n=38.451, 902 positif) | PR-AUC | 0,1961 | [0,169 ; 0,225] |
| | Precision@kapasitas | 0,2175 | [0,1947 ; 0,2388] |
| | Recall@kapasitas | 0,3392 | [0,3091 ; 0,3675] |
| Scrap v1 (n=323, **21 positif**) | PR-AUC | 0,2546 | [0,1216 ; 0,4692] |
| | Precision@kapasitas | - | [0,1111 ; 0,7778] |
| | Recall@kapasitas | 0,3810 | [0,0476 ; 0,3529] |
| Survival RSF | C-index VAL | 0,8417 | [0,8291 ; 0,8525] |
| | C-index TEST | 0,8625 | [0,8498 ; 0,8748] |
| Survival Cox PH | C-index VAL | 0,7918 | [0,7726 ; 0,8099] |
| | C-index TEST | 0,7609 | [0,7373 ; 0,7826] |

**Temuan** Failure v4: CI cukup sempit (n besar) - metrik headline yang
selama ini dikutip (mis. precision@200 0,2175) BISA dipercaya sebagai
estimasi yang wajar, bukan angka yang kebetulan. Scrap v1: CI SANGAT lebar
seperti diperkirakan - precision@kapasitas rentangnya [0,11 ; 0,78],
hampir mencakup seluruh skala 0-1. **Setiap perbandingan/klaim "scrap
model A lebih baik dari B" di masa depan HARUS memeriksa CI ini dulu -
dengan rentang selebar itu, hampir semua perbandingan titik-tunggal tidak
bermakna.** Survival: CI RSF vs Cox TIDAK overlap di VALIDATION maupun
TEST (RSF lower-bound ~0,83 vs Cox upper-bound ~0,81) - keunggulan RSF
atas Cox robust, bukan kebetulan sampling.

**Keputusan** Field `bootstrap_ci_95` ditulis ke `metadata.json` masing-
masing model (failure v4, scrap v1, survival RSF+Cox) - aditif, field yang
dipakai scoring (risk_cutoffs/features/part_model_support/kalibrator)
tidak tersentuh. Diverifikasi: golden_batch tetap IDENTIK, pytest penuh
hijau setelah perubahan. Belum diekspos di dashboard Kesehatan Model
(FASE 6 sudah menyatakan "metrik TANPA interval kepercayaan" secara
eksplisit di halaman itu - sekarang sudah tersedia untuk ditambahkan,
tapi menambahkannya ke UI di luar cakupan sesi P0-2 ini).

---

## E-46 · Langkah 1: kelayakan gerbang presisi>=85% - baseline v4 vs horizon 7/14/30 hari

2026-08-25 · `engines/failure/gate.py` (baru), `cli.py::precision-gate-experiment` (baru)

**Pertanyaan** Q2 sekarang dievaluasi/dipromosikan dengan kuota rank-based
tetap (`capacity_metrics()`, top-200/bulan lepas dari probabilitasnya).
Tujuan barunya: PART hanya direkomendasikan inspeksi kalau model punya
keyakinan tinggi (presisi >= 85%) bahwa PART itu benar-benar sedang OTW
rusak - antrian jadi dinamis, boleh kosong. Threshold dicari HANYA dari
VALIDATION, diuji SEKALI secara jujur di TEST. Apakah target presisi 85%
ini bisa dicapai model v4 (30 hari, production sekarang) tanpa retrain,
dan apakah horizon lebih pendek (7/14 hari) membantu?

**Metode** `gate.select_precision_constrained_threshold()` -
`sklearn.precision_recall_curve` pada skor terkalibrasi VALIDATION, cari
threshold yang MEMAKSIMALKAN recall dengan syarat presisi >= 0,85 (kalau
tidak ada yang memenuhi: `feasible=False`, tidak diam-diam pakai threshold
terdekat). Threshold yang lolos diuji SEKALI (`honest_test_evaluation()`)
di TEST - tidak pernah dicari ulang di TEST. Empat kandidat: baseline v4
production (30 hari, model+kalibrator APA ADANYA, tidak dilatih ulang) dan
tiga retrain baru dengan `training_observations(horizon_days=h)`/
`assign_split(horizon_days=h)` untuk h di {7, 14, 30} (fitur, hyperparameter
CatBoost, dan kalibrasi isotonic identik dengan `train.py` produksi - hanya
lebar jendela target yang berubah). `OBSERVATION_STEP_DAYS` (spasi landmark
30 hari) TIDAK diubah. Split TRAIN/VALIDATION/TEST temporal sama seperti
production (`assign_split()`).

**Hasil**

| Kandidat | Baris layak (positif) | Threshold | Presisi/Recall/Alert VALIDATION | Presisi/Recall/Alert TEST (beku) |
|---|---:|---:|---|---|
| baseline v4 (30 hari, tanpa retrain) | 356.100 (5.876) | 1,0000 | 1,0000 / 0,0021 / 2 | **0,0000 / 0,0000 / 0** |
| horizon 7 hari (retrain) | 362.018 (1.770) | - | INFEASIBLE (presisi maks 0,1875) | - |
| horizon 14 hari (retrain) | 361.501 (3.167) | - | INFEASIBLE (presisi maks 0,5000) | - |
| horizon 30 hari (retrain baru) | 356.100 (5.876) | 1,0000 | 1,0000 / 0,0021 / 2 | **0,0000 / 0,0000 / 0** |

**Temuan** Tidak ada satu pun kandidat yang menghasilkan gerbang presisi
>= 85% yang genuinely berguna:

- **7 hari dan 14 hari: infeasible murni** - presisi tertinggi yang bisa
  dicapai DI THRESHOLD MANA PUN pada VALIDATION cuma 18,75% (7 hari) dan
  50% (14 hari), jauh dari target. Base rate makin kecil (1.770 dan 3.167
  kerusakan vs 5.876 di 30 hari) membuat model makin tidak yakin, bukan
  makin yakin - horizon pendek TIDAK membantu presisi di sini.
- **30 hari (baseline v4 maupun retrain baru): feasible di VALIDATION
  tapi DEGENERATE, bukan gerbang yang bisa dipakai.** Threshold yang lolos
  presisi>=85% ternyata threshold=1,0 (skor tertinggi yang pernah
  dikeluarkan kalibrator), disokong cuma 2 baris VALIDATION - klasik
  overfitting ekor distribusi skor bersampel kecil, bukan sinyal asli.
  Terbukti dari TEST: threshold yang sama itu men-flag **NOL PART**,
  presisi/recall runtuh ke 0. Ini persis skenario yang mau dicegah aturan
  "threshold dari VALIDATION, uji sekali jujur di TEST" - dan metodologi
  itu berhasil menangkapnya, bukan menyembunyikannya.
- Ini konsisten dengan angka production yang sudah diketahui (README):
  v4 PR-AUC 0,1961, precision@200/bln cuma 21,75% dari base rate 2,35%.
  Melompat dari ~22% (di titik operasi kapasitas kerja) ke presisi 85%
  yang stabil butuh pemisahan kelas yang jauh lebih tajam daripada yang
  dimiliki fitur/model sekarang di ekor distribusi skor manapun - bukan
  soal horizon prediksi, tapi soal daya pisah model itu sendiri di ujung
  skala probabilitas.

**Keputusan** **Berhenti di Langkah 1** sesuai aturan go/no-go rencana:
tidak ada kandidat yang menang secara jujur di TEST, jadi TIDAK lanjut ke
Langkah 2 (retrain resmi) atau Langkah 3 (ubah serving). Tidak ada model
baru disimpan ke `models/failure/`, tidak ada perubahan pada `CURRENT`,
`decide_promotion()`/`capacity_metrics()`/serving/API tidak tersentuh.
Perubahan kode yang TETAP ada dari sesi ini (aman, aditif, tidak mengubah
perilaku lama - lihat `tests/test_pipeline.py` regression check):
`gate.py` (modul baru), parameter `horizon_days`/`split` berdefault nilai
lama di `features.py::training_observations()`,
`train.py::assign_split()`/`build_dataset()`/`evaluate_incumbent()`, dan
`data_reader.py::get_cycles()`. Dicatat APA ADANYA termasuk hasil negatif,
sesuai budaya repo ini (lihat E-45 dan seterusnya - "menolak model sendiri
adalah bahan pembahasan yang jauh lebih kuat daripada 'model kami menang'").
Target presisi 85% pada gerbang klasifikasi butuh perbaikan signal/fitur/
arsitektur yang di luar cakupan sesi ini kalau mau dikejar lagi - lihat
opsi di laporan ke user.

---

## E-47 · Diagnosis: kenapa v4 mentok jauh di bawah presisi 85% di gerbang manapun

2026-08-25 · lanjutan E-46, diagnostik ad-hoc (tidak menambah kode permanen)

**Pertanyaan** E-46 menunjukkan gerbang presisi>=85% selalu degenerate
(threshold=1,0, 2 alert VALIDATION, 0 alert TEST). Apakah ini kebetulan
sampel, atau memang model v4 tidak punya daya pisah sebesar itu di ekor
manapun distribusi skornya?

**Metode** Tiga diagnostik pada model v4 (skor terkalibrasi VALIDATION/TEST
yang sama seperti E-46, tanpa retrain):
1. Presisi top-K (rank by skor mentah) K={5,10,20,50,100,200,500},
   VALIDATION vs TEST, untuk lihat bentuk kurva presisi di ekor.
2. Ulangi pencarian threshold (`select_precision_constrained_threshold`)
   dibatasi ke segmen yang SUDAH TERBUKTI paling dikenali model di error
   analysis sebelumnya (E-35): `installation_age_band == 000_090_DAYS`
   (recall 0,812 di E-35) dan `has_prior_corrective` (recall 0,551).
3. Sweep target presisi lebih rendah (0,30 s/d 0,60) di populasi penuh
   untuk cari di mana persisnya ambang "generalize" berubah jadi "noise
   ekor 1-2 baris".

**Hasil**

Top-K (rank mentah), VALIDATION vs TEST:

| K | TP·presisi VALIDATION | TP·presisi TEST |
|---:|---|---|
| 5 | 3 · 0,600 | 4 · 0,800 |
| 10 | 4 · 0,400 | 5 · 0,500 |
| 20 | 9 · 0,450 | 12 · 0,600 |
| 50 | 19 · 0,380 | 29 · 0,580 |
| 100 | 35 · 0,350 | 53 · 0,530 |
| 200 | 65 · 0,325 | 98 · 0,490 |
| 500 | 110 · 0,220 | 173 · 0,346 |

Presisi di K yang sama TIDAK sejalan antara VALIDATION dan TEST (mis. K=5:
0,60 vs 0,80; K=200: 0,325 vs 0,490) - ciri khas noise sampel kecil di
ekor, bukan sinyal stabil. Kalau ini sinyal asli, kedua split semestinya
lebih dekat pada K yang sama.

Segmen paling dikenali model (E-35) - hasilnya SAMA degenerate:

| Segmen | Baris·positif VALIDATION | Baris·positif TEST | Threshold VALIDATION | TEST (beku) |
|---|---|---|---|---|
| `installation_age_band` 0-90 hari | 7.652 · 314 | 8.682 · 372 | 1,0000 (2 alert, presisi 1,00) | **0 alert, presisi 0,00** |
| `has_prior_corrective` | 8.227 · 548 | 8.571 · 555 | 1,0000 (2 alert, presisi 1,00) | **0 alert, presisi 0,00** |

Membatasi ke populasi yang model paling kenal TIDAK memperbaiki masalah -
ekornya tetap cuma 2 baris VALIDATION, dan tetap tidak bertahan di TEST.

Sweep target presisi lebih rendah (populasi penuh):

| Target presisi | VALIDATION (presisi/recall/alert) | TEST beku (presisi/recall/alert) |
|---:|---|---|
| 0,30 | 0,301 / 0,078 / 246 | 0,480 / 0,119 / 223 |
| 0,35 | 0,381 / 0,025 / 63 | 0,659 / 0,030 / 41 |
| 0,40 | 0,423 / 0,012 / 26 | 0,625 / 0,006 / 8 |
| 0,45 | 1,000 / 0,002 / 2 | **0,000 / 0,000 / 0** |
| 0,50 - 0,60 | 1,000 / 0,002 / 2 (identik) | **0,000 / 0,000 / 0** |

**Temuan** Ada batas tajam di sekitar **target presisi ~0,40-0,45**: di
bawahnya ambang genuinely generalize (VALIDATION dan TEST sama-sama punya
puluhan-ratusan alert, presisi TEST malah cenderung LEBIH baik dari
VALIDATION - tanda sehat, bukan overfit). Di atasnya, pencarian threshold
selalu jatuh ke 2 baris VALIDATION paling ekstrem (produk sampingan
kalibrator isotonic yang menyaturasi ke skor 1,0 untuk segelintir baris
bersampel sangat kecil) yang TIDAK generalize sama sekali ke TEST.

Dua penyebab yang bertumpuk, KEDUANYA sudah pernah diselidiki terpisah di
riwayat proyek, bukan temuan baru yang mengejutkan:
1. **Batas data terkonfirmasi (E-35)**: ~38% kerusakan TEST berasal dari
   populasi (umur >90 hari, tanpa riwayat corrective) di mana model BUTA
   TOTAL (recall 0,000) - fitur utamanya nol untuk populasi ini, dan
   fitur terarah untuk memperbaikinya (`age_history_base_rate`) sudah
   dicoba dan gagal total (E-35, kalah di semua metrik). Ini realistis
   batas ketersediaan data (intensitas pemakaian, kondisi lingkungan
   operasional yang tidak tercatat), bukan fitur yang belum ditemukan.
2. **Ekor distribusi skor terlalu tipis untuk presisi ekstrem** (temuan
   baru sesi ini): bahkan di dalam segmen yang PALING dikenali model
   (recall 0,55-0,81 di E-35), jumlah baris yang genuinely percaya diri
   terlalu sedikit (~300-550 positif per segmen di TEST) untuk kalibrator
   isotonic menghasilkan skor ekstrem yang stabil - base rate ~1,5-2,3%
   dan PR-AUC ~0,20 model v4 (angka production yang sudah diketahui,
   README) tidak cukup tajam untuk memusatkan probabilitas di ekor
   99-persentil manapun.

**Kesimpulan** Presisi>=85% BUKAN tercapai oleh pilihan horizon atau
kebijakan threshold apa pun dengan model/fitur yang ada sekarang - ini
batas struktural yang sudah dikonfirmasi dari dua sisi (blind-spot
populasi by E-35, dan ekor skor terlalu tipis by sesi ini). Memperbaikinya
butuh data baru (fitur intensitas pemakaian/kondisi lingkungan yang tidak
tersedia di database ini) atau perombakan arsitektur model - keduanya di
luar cakupan sesi ini. **Presisi ~35-40% adalah titik operasi tertinggi
yang genuinely generalize (TEST-verified) dengan model v4 sekarang**,
dengan puluhan alert dinamis (bukan degenerate) sebagai gerbang.

**Keputusan** Tidak ada perubahan production dari diagnostik ini (hanya
analisis, tidak ada kode permanen ditambah di luar E-46). Opsi lanjutan
dikembalikan ke user - lihat laporan sesi ini.

---

## E-48 · Ablasi `journal.t_mtbf` (satu-satunya data belum terpakai yang plausibel) DITOLAK - bukan karena tidak ada sinyal, tapi TIDAK BISA DIUJI

2026-08-25 · ablasi ad-hoc (skrip sementara, tidak masuk repo), lanjutan E-47

**Pertanyaan** E-47 menyimpulkan ceiling presisi adalah batas struktural
data. Apakah ada sumber data BENAR-BENAR belum terpakai di database yang
bisa menaikkan ceiling itu (bukan rekayasa fitur dari data yang sudah
dipakai - itu sudah ditolak di E-25..E-35)?

**Metode** Audit langsung skema database (`information_schema.tables`)
untuk cari tabel operasional yang tidak pernah muncul di `data_reader.py`:
- `log.t_log_device_monitoring`/`t_log_edc_rekon`/`t_log_terminal_last_transaction`/
  `t_log_terminal_startup` (kandidat kuat untuk "intensitas pemakaian") -
  **KOSONG (0 baris)** di database ini. Bukan opsi, lepas dari skema.
- `journal.t_item_quality_control`/`t_item_test_quality_control`
  (damage_report/damage_analysis) - ADA isinya, tapi ini data HASIL repair
  (baru tercatat setelah PART sudah rusak masuk bengkel) - dipakai sebagai
  fitur prediksi berarti kebocoran data (model menyontek jawaban).
  Tidak layak dicoba.
- `journal.t_mtbf` (belum pernah dipakai kode manapun) - `time_operation`
  (menit, tervalidasi 100% oleh `analytics.mtbf_clean` milik tim riset
  sebelumnya, `is_time_operation_valid=True` di semua 42.696 baris) per
  `sn_ref`, nilai naik-turun (bukan kumulatif - kemungkinan "waktu sejak
  reset/kejadian terakhir"). Satu-satunya kandidat yang lolos due diligence
  awal - diuji lebih lanjut.

Dibangun: pemetaan `sn_ref` -> `item_identifier_clean` lewat `inventory.t_item`
(pakai ulang pola `_clean()`/`inventory_lookup` yang sudah ada di
`data_reader.py`, bukan reimplementasi baru), lalu point-in-time join
(`pd.merge_asof`, `direction="backward"`) - HANYA baca baris `t_mtbf` dengan
`created_on <= observation_on` per item (leakage-safe). Tiga fitur baru:
`has_mtbf_reading`, `log_days_since_mtbf_reading`, `log_last_time_operation_minutes`.
Retrain v4-setara (fitur asli + 3 fitur baru) vs baseline v4 (fitur asli
saja), evaluasi sama seperti E-46/E-47.

**Hasil** ROC-AUC/PR-AUC TEST **identik sampai 4 desimal** antara baseline
dan +mtbf (0,8319/0,1961 keduanya), dan seluruh sweep gerbang presisi
(target 0,30/0,40/0,85) **identik persis** di kedua model - tanda model
sama sekali tidak belajar apa pun dari 3 fitur baru itu. Diselidiki
kenapa: cakupan `has_mtbf_reading` per split -

| Split | Cakupan mtbf |
|---|---:|
| TRAIN | **0,0000%** |
| VALIDATION | 14,78% |
| TEST | 20,61% |

**Temuan** `journal.t_mtbf` baru mulai terisi 2025-01-15 - persis berhimpit
dengan batas VALIDATION (mulai ~awal 2025). TRAIN (2014 s/d akhir 2024)
punya **NOL BARIS** dengan bacaan mtbf - CatBoost tidak pernah melihat satu
pun contoh yang menghubungkan fitur ini dengan label kerusakan selama
training, jadi mustahil secara matematis fitur itu punya pengaruh apa pun,
lepas dari seberapa bagus sinyalnya sungguhan. Ini BUKAN bukti "mtbf tidak
berguna" - ini bukti bahwa skema training historis 2014-2026 yang ada
secara struktural tidak bisa menguji sinyal manapun yang cuma tersedia 19
bulan terakhir.

**Keputusan** Fitur ini **DITOLAK untuk sesi ini** - bukan gagal di
evaluasi, tapi gagal di prasyarat (butuh skema training terpisah yang
dibatasi ke window baru-baru saja, mis. TRAIN/VALIDATION/TEST semua
setelah 2025-01, yang berarti dataset jauh lebih kecil dengan jauh lebih
sedikit kerusakan positif - proyek terpisah dengan trade-off dan risiko
sendiri, tidak terjamin tembus 85% juga, di luar cakupan sesi ini). Tidak
ada kode permanen ditambah - skrip ablasi tetap di scratchpad, tidak masuk
repo. Dengan ini, ketiga avenue yang masuk akal untuk sesi ini (tuning
threshold/horizon E-46, perbaikan fitur dari data yang sudah dipakai E-35,
data baru yang benar-benar belum terpakai E-48) sudah dicoba dan
ditolak/mentok - kesimpulan akhir dikembalikan ke user.

---

## E-49 · FASE 8 Langkah A: gerbang presisi di tingkat LIFECYCLE (first-alert), bukan per-baris

2026-08-26 · `engines/failure/gate.py::lifecycle_metrics()`/
`select_lifecycle_threshold()` (baru, aditif), `cli.py::lifecycle-gate-experiment` (baru)

**Pertanyaan** User meminta objective baru "maximize recall dengan syarat
presisi >= 85%", dievaluasi "berdasarkan kejadian nyata/first alert per
PART atau lifecycle, bukan hanya per snapshot data" - bukan lagi Top-200.
E-46/E-47 sudah mencari gerbang presisi>=85% tapi metriknya PER BARIS
snapshot (satu PART aktif lama menyumbang puluhan baris 30-harian); kalau
beberapa baris untuk PART yang SAMA kebetulan sama-sama lolos threshold,
itu terhitung sebagai beberapa "alert" terpisah - padahal production
(`serving/alerts.py`, ditambahkan sesi sebelumnya) cuma membuka SATU alert
per lifecycle sampai diselesaikan (`resolve-alert`), tidak flag ulang.
Apakah dedup ke tingkat lifecycle ini sendiri mengubah kelayakan gerbang
85%, sebelum menambah fitur apa pun?

**Metode** `lifecycle_metrics(dataset, scores, threshold)`: kelompokkan
baris per `installation_cycle_id`, ambil baris PERTAMA (urut
`observation_on`) yang skornya >= threshold sebagai "alert" cycle itu -
meniru persis perilaku dedup `serving/alerts.py::register_flagged()`.
Precision/recall/false-positive/false-negative/lead-time dihitung per
CYCLE (bukan per baris): precision = cycle yang alert pertamanya benar
(`target_failure=True`) / cycle yang pernah dialert; recall = cycle yang
alert pertamanya benar / cycle yang memang berakhir rusak.
`select_lifecycle_threshold()`: sweep SEMUA nilai skor unik (bukan grid
quantile - lihat catatan bug di bawah) di VALIDATION, ambil yang
memaksimalkan recall lifecycle dengan syarat presisi lifecycle >= target;
diuji SEKALI jujur di TEST lewat `lifecycle_metrics()` langsung - metodologi
identik E-46/E-47 (VALIDATION-only search, TEST touch-once), model v4
production APA ADANYA (tidak retrain). Divalidasi dengan 13 test baru
(`tests/test_gate.py`) termasuk kasus dedup eksplisit (satu cycle dengan
alert pertama SALAH dan baris "benar" belakangan yang sengaja tidak pernah
dipakai).

**Bug ditemukan saat implementasi**: draf pertama `select_lifecycle_threshold`
memakai grid 200 titik quantile merata sebagai kandidat threshold - GAGAL
menemukan threshold gate production yang sudah diketahui berguna (0,375,
26 alert VALIDATION) karena hanya 26/49.660 baris VALIDATION (0,052%) ada
di atasnya - percentile gap itu (~0,05%) lebih sempit dari spacing grid
quantile (~0,5%), jadi grid melompat langsung ke threshold degenerate
berikutnya (1,0) tanpa pernah menyentuh 0,375. Kalibrator isotonic cuma
menghasilkan 28 nilai unik di VALIDATION - diperbaiki dengan sweep NILAI
SKOR UNIK langsung (`np.unique(scores)`), bukan quantile, persis seperti
`sklearn.precision_recall_curve` melakukannya untuk gerbang per-baris.

**Hasil** Perbandingan per-baris (E-46/production) vs lifecycle di
threshold production 0,375 saat ini - **identik**, dedup tidak mengubah
apa pun di titik operasi ini (alert terlalu sedikit untuk PART yang sama
sempat kena dua kali):

| Metrik | Per-baris | Lifecycle |
|---|---|---|
| VALIDATION presisi/recall/alert | 0,4231/0,0116/26 | 0,4231/0,0116/26 |
| TEST presisi/recall/alert (beku) | 0,6250/0,0055/8 | 0,6250/0,0055/8 |

Tapi di threshold LEBIH RENDAH, dedup jelas mengurangi jumlah alert (PART
yang sama sempat lolos threshold di lebih dari satu snapshot):

| Target presisi | VAL presisi/recall/alert | TEST presisi/recall/alert (beku) |
|---:|---|---|
| 0,30 | 0,3333 / 0,0475 / 135 | 0,5289 / 0,0710 / 121 |
| 0,40 | 0,4231 / 0,0116 / 26 | 0,6250 / 0,0055 / 8 |
| 0,50 - 0,85 | 1,0000 / 0,0021 / 2 | **0,0000 / 0,0000 / 0** |

(Bandingkan target 0,30 per-baris E-47: VAL 0,301/0,078/246 - lifecycle
135 alert vs 246 baris, ~45% di antaranya adalah PART yang sama terhitung
berulang di metodologi lama.)

**Temuan** Metodologi lifecycle/first-alert TERBUKTI mengubah angka secara
material di target presisi rendah-menengah (jumlah alert turun ~45% di
target 0,30 - ini yang akan production benar-benar lihat lewat
`serving/alerts.py`, bukan angka per-baris yang menghitung ganda). **Tapi
TIDAK mengubah kelayakan gerbang 85%** - degenerate persis sama seperti
E-46/E-47 (threshold jatuh ke 1,0, cuma 2 lifecycle VALIDATION paling
ekstrem, kolaps ke 0 alert TEST). Ini mengonfirmasi diagnosis E-47 bukan
artifak penghitungan ganda per-baris - baik dihitung per-baris maupun per-
lifecycle, model v4 dengan fitur yang ada TIDAK punya cukup daya pisah di
ekor skor manapun untuk presisi ekstrem yang genuinely generalize.
Kesimpulannya sama: **presisi 85% butuh sinyal baru** (bukan cuma
perbaikan metodologi evaluasi) - ini yang jadi alasan Langkah B/C/D (fitur
work order/QC/MTBF) berikutnya.

**Keputusan** `gate.lifecycle_metrics()`/`select_lifecycle_threshold()`
dan `cli.py::lifecycle-gate-experiment` **DIPERTAHANKAN sebagai infrastruktur
permanen** (aditif, tidak mengubah `select_precision_constrained_threshold()`/
`honest_test_evaluation()`/gerbang production yang ada) - dipakai ulang di
Langkah B/C/D/E untuk mengevaluasi kandidat fitur baru dengan metrik yang
benar (lifecycle, bukan per-baris) sejak awal, bukan ditambal belakangan.
Tidak ada perubahan pada `CURRENT`/model production dari eksperimen ini.

---

## E-50 · FASE 8 Langkah B: audit pembentukan observation/label - base rate 1,65% observation-level vs 47,2% lifecycle-level

2026-08-26 · audit ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** User curiga base rate ~2% yang selama ini dipakai (README,
E-46/E-47) mungkin ARTIFISIAL - hasil skema observasi (landmark 30-harian
tetap dari install sampai cycle berakhir), bukan cerminan risiko bisnis
sesungguhnya. Kalau benar, PR-AUC 0,1961/precision@ekor yang selama ini jadi
acuan "model lemah" bisa jadi salah kerangka, bukan salah model/fitur.
Bagaimana prevalence sesungguhnya di level observation vs unique PART vs
lifecycle vs bulan vs failure event, dan seberapa banyak baris negatif itu
duplikatif?

**Metode** Pada dataset eligible yang sama dengan `train.py::build_dataset()`
(356.100 baris, 12.461 lifecycle, TIDAK mengubah TRAIN/VALIDATION/TEST
apa pun - murni pengukuran):
1. Prevalence dihitung 3 cara pada populasi yang SAMA: per baris observasi
   (`target_failure.mean()`), per lifecycle (`installation_cycle_id` dengan
   >=1 baris positif), per PART unik (`item_identifier_clean` dengan >=1
   baris positif) - digabung dan per split.
2. Prevalence bulanan (`observation_on` di-floor ke bulan) untuk lihat
   variasi/tren.
3. Distribusi jumlah baris NEGATIF per lifecycle, dan pangsa baris negatif
   yang disumbang lifecycle paling panjang umurnya (top 1/5/10/20%).
4. "Near-duplicate check": persentase baris negatif yang histori count-nya
   (`total_prior_events`/`prior_failure_count`/`prior_corrective_count`)
   PERSIS SAMA dengan snapshot 30-hari sebelumnya di lifecycle yang sama
   (tidak ada event apa pun tercatat di antara keduanya - satu-satunya yang
   berubah adalah umur).
5. Cakupan: total failure episode nyata (`data_reader.get_failure_episodes`)
   vs yang masuk cohort awal vs yang benar-benar jadi baris positif eligible
   - untuk pastikan skema label tidak diam-diam kehilangan kejadian nyata.

**Hasil**

Prevalence per level (gabungan seluruh split, lalu per split):

| Populasi | Observation-level | Lifecycle-level | Unique-PART-level |
|---|---:|---:|---:|
| Seluruh split | 1,6501% (5.876/356.100) | **47,1551%** (5.876/12.461) | 39,3517% (3.727/9.471) |
| TRAIN | 1,5312% | **51,3463%** | 42,1644% |
| VALIDATION | 1,9070% | 16,2102% | 14,1237% |
| TEST | 2,3458% | 12,0475% | 10,4257% |

Distribusi baris negatif per lifecycle: mean=30,47 median=16 p90=89 p99=147
max=165. Lifecycle paling panjang umurnya (top 20%) menyumbang **57,0%**
dari SEMUA baris negatif (top 10% -> 34,0%, top 5% -> 19,4%, top 1% -> 5,1%).

Near-duplicate: **336.599/350.224 baris negatif (96,1%)** punya histori
count PERSIS SAMA dengan snapshot 30-hari sebelumnya di lifecycle yang
sama - satu-satunya yang berubah antar baris itu adalah umur
(`days_since_installation`, `month_sin`/`month_cos`).

Cakupan: 6.715 failure episode nyata di database -> 5.876 masuk cohort awal
(`is_initial_model_cohort` - penyaringan yang SUDAH ADA dan disengaja, soal
pencocokan kode model ke inventaris, bukan bug baru) -> **5.876/5.876**
(100%) dari cohort awal itu berhasil jadi baris positif eligible. Tidak ada
kejadian nyata yang hilang dari skema pelabelan itu sendiri.

Prevalence bulanan: 162 bulan, rate median 1,39% (rentang 0%-33,7%
tergantung bulan). Lonjakan di bulan-bulan paling akhir (Jul 2026: 33,7%
dari cuma 460 baris) adalah artefak batas `data_end` - baris NEGATIF baru
boleh masuk eligible setelah lewat window konfirmasi (embargo horizon),
sementara baris POSITIF (kerusakan yang sudah terjadi) langsung eligible -
jadi observasi paling dekat `data_end` secara struktural bias ke arah
positif. Ini TIDAK memengaruhi cutoff production (`assign_split()` pakai
batas 1 Januari, bukan `data_end`), tapi penting diketahui saat membaca
angka bulanan mentah.

**Temuan**

1. **Base rate "2%" adalah artefak level observasi, BUKAN risiko bisnis
   sesungguhnya.** Di level yang sebenarnya dipakai keputusan bisnis
   ("apakah lifecycle pemasangan PART ini akan berakhir rusak") prevalence-
   nya 47,2% gabungan (bahkan 51,3% di TRAIN) - bukan 1,65%. Kerangka
   "PR-AUC 0,1961 pada base rate 2,35%" (README, E-46/E-47) benar sebagai
   ukuran DI SKEMA OBSERVASI SEKARANG, tapi menyiratkan masalahnya "model
   lemah membedakan kelas langka" padahal masalah sebenarnya lebih ke
   "SATU kejadian gagal/tidak-gagal per lifecycle diencerkan jadi puluhan
   snapshot 30-harian yang sebagian besar tidak menambah informasi baru."
2. **Duplikasi baris negatif nyata dan besar** - 96,1% baris negatif
   sekadar mengulang histori count yang identik dengan baris sebelumnya
   (hanya umur yang berbeda), dan seperlima lifecycle terpanjang menyumbang
   lebih dari separuh SEMUA baris negatif. Ini TIDAK membuat kelas negatif
   "salah" (masing-masing baris tetap snapshot valid dari kondisi yang
   memang belum berubah), tapi berarti model CatBoost menghabiskan sebagian
   besar kapasitasnya belajar dari baris yang informasinya sangat
   berkorelasi satu sama lain dalam satu lifecycle, bukan dari variasi
   independen lintas populasi - salah satu kandidat penjelasan kenapa ekor
   skor terlalu tipis (E-47).
3. **TRAIN (51,3% lifecycle) vs VALIDATION/TEST (16,2%/12,0%) timpang
   jauh** - ini artefak censoring yang DIHARAPKAN, bukan bug: cohort TRAIN
   (instalasi 2014-2023) sudah bertahun-tahun waktu untuk berakhir
   rusak/reinstall, sedangkan cohort VALIDATION/TEST (instalasi lebih
   baru) BANYAK yang masih aktif/tersensor di `data_end` dan belum sempat
   menunjukkan hasil akhirnya. Konsekuensinya: distribusi kelas yang
   dilihat model saat TRAIN (lifecycle-level) jauh lebih seimbang daripada
   populasi yang dinilai saat serving (dekat VALIDATION/TEST) - potensi
   sumber pergeseran (drift) antara training dan serving yang belum pernah
   diukur eksplisit sebelum audit ini.
4. Skema pelabelan tidak kehilangan kejadian nyata (100% cakupan cohort
   awal) - jadi masalah presisi/recall bukan soal "failure yang tidak
   ketemu observasinya," melainkan soal SEBERAPA BERGUNA sinyal di setiap
   observasi yang memang sudah ada.

**Implikasi (belum dieksekusi - keputusan dikembalikan ke user)**: temuan
ini memberi dasar empiris kuat untuk skema observasi alternatif yang
diminta user ("monthly landmark, lifecycle-aware observation, atau
pendekatan lain") - kandidat paling langsung adalah **mengurangi kepadatan
landmark untuk lifecycle yang sudah lama tidak ada perubahan** (mis. hanya
tambah landmark baru saat ada event baru ATAU sudah lewat N bulan sejak
landmark terakhir, bukan tetap setiap 30 hari sepanjang umur) - berpotensi
memangkas duplikasi 96,1% itu tanpa membuang kejadian nyata, karena
cakupan failure sudah 100%. Ini PERUBAHAN ARSITEKTUR (`training_observations()`
di `features.py`) yang mempengaruhi SEMUA model hilir (CatBoost, RSF,
scrap) - butuh persetujuan eksplisit sebelum dikerjakan, bukan diputuskan
sepihak di sesi ini.

**Keputusan** Murni pengukuran - tidak ada kode production/dataset yang
diubah. Dilaporkan ke user sebagai temuan Langkah B untuk menentukan urutan
kerja Langkah C dan seterusnya (fitur baru vs perombakan skema observasi
dulu).

---

## E-51 · FASE 8 Langkah C: thinning baris negatif TRAIN berdasar "tidak ada event baru" DITOLAK - merusak sinyal umur, bukan menghilangkan redundansi

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** E-50 menemukan 96,1% baris negatif TRAIN histori count-nya
(`total_prior_events`/`prior_failure_count`/`prior_corrective_count`)
PERSIS SAMA dengan snapshot 30-hari sebelumnya di lifecycle yang sama.
User memilih opsi berisiko-rendah: coba buang redundansi itu HANYA di
TRAIN (VALIDATION/TEST/baris positif tidak disentuh sama sekali - evaluasi
harus tetap representasi realistis), sebelum memutuskan perombakan skema
observasi (`features.py::training_observations()`) yang jauh lebih besar
dan berisiko.

**Metode** `thinning_keep_mask()`: per lifecycle di TRAIN, buang baris
NEGATIF "tengah" (bukan baris pertama/terakhir) yang histori count-nya
(3 kolom yang sama dengan E-50) IDENTIK dengan baris sebelumnya di
lifecycle yang sama - baris pertama, baris terakhir, baris dengan
perubahan count apa pun, seluruh baris POSITIF, dan SELURUH baris
VALIDATION/TEST selalu dipertahankan. Retrain CatBoost pakai
`train.py::train_model()` APA ADANYA (fitur/hyperparameter/kalibrasi
identik production) pada dataset yang sudah di-thin, evaluasi row-level
(ROC-AUC/PR-AUC standar) dan lifecycle-level (`gate.select_lifecycle_threshold()`/
`lifecycle_metrics()` dari E-49) pada VALIDATION/TEST yang TIDAK berubah.

**Hasil**

TRAIN menyusut jauh lebih drastis dari yang diperkirakan: 251.568 -> 19.606
baris (**cuma 7,8% tersisa** - 3.852 baris positif dipertahankan penuh,
sisanya 15.754/247.716 baris negatif atau 6,4%). VALIDATION/TEST
diverifikasi identik (49.660/38.451 baris, tidak berubah).

Row-level, VALIDATION/TEST (tidak berubah) - **regresi besar di semua metrik**:

| | TRAIN (baru) | VALIDATION | TEST | TEST baseline v4 |
|---|---:|---:|---:|---:|
| ROC-AUC | 0,8842 | 0,7404 | **0,5855** | 0,8319 |
| PR-AUC | 0,5476 | 0,0559 | **0,0348** | 0,1961 |

Lifecycle-level (metodologi E-49), kandidat thinned vs baseline v4 (tanpa
retrain, dari E-49):

| Target presisi | Kandidat TEST presisi/recall/alert | Baseline v4 TEST presisi/recall/alert |
|---:|---|---|
| 0,30 | 0,1111 / 0,0022 / 18 | 0,5289 / 0,0710 / 121 |
| 0,40 | 0,1818 / 0,0022 / 11 | 0,6250 / 0,0055 / 8 |
| 0,85 | INFEASIBLE (maks VAL 0,5000) | INFEASIBLE (sama) |

Kandidat kalah di HAMPIR SEMUA metrik - recall jatuh ke hampir nol di
setiap target presisi, ROC-AUC TEST turun ke 0,59 (nyaris tebak acak).

**Temuan - kenapa ini gagal, bukan cuma "gagal"**: hipotesis E-50 keliru
soal APA yang membuat baris-baris itu "duplikat". Diff hanya dicek pada 3
kolom COUNT (`total_prior_events` dkk) - tapi baris "tengah" yang dibuang
itu TETAP beda di fitur UMUR (`days_since_installation`,
`installation_age_band`, `log_days_since_installation`, `month_sin/cos`)
yang justru salah satu sinyal PALING dipakai model (ada di
`CATEGORICAL_FEATURES`/`NUMERIC_FEATURES` production). Dengan menyisakan
cuma baris pertama+terakhir+baris-yang-count-nya-berubah per lifecycle,
thinning ini DIAM-DIAM membuang hampir seluruh cakupan model terhadap
"PART umur X bulan, tidak ada apa pun yang terjadi, masih terpasang" di
SELURUH rentang umur - persis data yang dibutuhkan model untuk belajar
kurva hazard-vs-umur yang genuinely menjelaskan sebagian besar kekuatan
prediksi model saat ini. "Redundansi" yang ditemukan E-50 nyata SECARA
COUNT-HISTORY, tapi TIDAK redundan secara UMUR - dua hal yang keliru
disamakan di desain eksperimen ini.

**Keputusan** **DITOLAK** - thinning berbasis "tidak ada event count baru"
tidak dipakai, tidak ada kode production diubah. Pelajaran untuk Langkah
berikutnya (kalau perombakan skema observasi tetap ingin dicoba):
definisi "redundan" HARUS memperhitungkan fitur umur, bukan cuma histori
event - kandidat yang lebih aman adalah menurunkan RESOLUSI waktu secara
seragam (mis. landmark tiap 60/90 hari, bukan 30 hari, untuk SEMUA
lifecycle termasuk yang stabil) supaya cakupan umur tetap merata tapi
kepadatannya menurun, alih-alih membuang baris "tengah" secara selektif
berdasar count histori saja. Tidak dieksekusi di sesi ini - dikembalikan
ke user sebagai opsi, bukan diputuskan sepihak.

---

## E-52 · FASE 8 Langkah D1: fitur durasi penanganan (handling/turnaround, dari work order) DITOLAK - cakupan terlalu tipis

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** User meminta eksplorasi histori work order untuk pola
sebelum kerusakan, termasuk "durasi penanganan". Audit skema
`journal.t_work_order` (17.627 baris, `work_type_code`/`current_status`
berupa kode tanpa lookup yang sudah dijelajahi, semua contoh baris dari
2014) menunjukkan nilai tambahnya di luar yang sudah dipakai
(`prior_corrective_count`, `prior_corrective_30/60/90d`,
`failure_interval_trend_ratio` dkk, semua dari `journal.t_item_journey`
langsung) tidak jelas. Sinyal genuinely baru yang bisa diambil TANPA
menyentuh tabel WO sama sekali: durasi dari status DISMANTLED ke INSTALLED
berikutnya per item (waktu PART "di luar layanan" untuk diperbaiki) -
apakah PART yang riwayat penanganannya lambat/memburuk lebih berisiko?

**Metode** Fitur baru (point-in-time safe - HANYA pasangan
DISMANTLED->INSTALLED yang KEDUANYA sudah terjadi sebelum
`observation_on`, dihitung dari `data_reader.get_events()`, tidak
menyentuh `journal.t_work_order`): `has_handling_duration_history`,
`log_mean_handling_duration_days`, `log_last_handling_duration_days`,
`handling_duration_trend_ratio` (last/mean, pola sama seperti
`failure_interval_trend_ratio` yang sudah ada). Ditambahkan ke fitur
production v4 (aditif, bukan pengganti), retrain CatBoost
identik-hyperparameter, evaluasi row-level (ROC-AUC/PR-AUC) dan
lifecycle-level (`gate` E-49) di VALIDATION/TEST yang sama dengan baseline.

**Hasil**

Cakupan: hanya **10,92%** baris eligible punya >=1 histori durasi
penanganan yang sudah selesai sebelum observation_on (median durasi
3,7 hari log1p, atau ~39 hari mentah - masuk akal untuk siklus
perbaikan).

| | VALIDATION | TEST | TEST baseline v4 |
|---|---:|---:|---:|
| ROC-AUC | 0,8194 | 0,8350 | 0,8319 |
| PR-AUC | 0,1048 | 0,1798 | 0,1961 |

4 fitur baru **tidak masuk 15 fitur terpenting** (importance
`PredictionValuesChange` - kalah jauh dari `log_prior_distinct_places`,
`model_failure_rate_90d`, dst yang sudah ada). Lifecycle-level (E-49):

| Target presisi | Kandidat TEST presisi/recall/alert | Baseline v4 TEST presisi/recall/alert |
|---:|---|---|
| 0,30 | 0,5854 / 0,0266 / 41 | 0,5289 / 0,0710 / 121 |
| 0,40 | **0,0000 / 0,0000 / 0** | 0,6250 / 0,0055 / 8 |
| 0,85 | (threshold degenerate, 1 lifecycle VAL) | (sama, degenerate) |

Presisi TEST sedikit lebih tinggi di target 0,30, tapi recall/jumlah alert
turun tajam di SEMUA target (0,30: 41 vs 121 alert; 0,40: kolaps ke 0 vs 8
baseline) - kandidat kalah bersih di titik operasi yang genuinely
generalize (0,30-0,40).

**Temuan** Cakupan 10,92% adalah akar masalahnya, bukan kualitas sinyal:
fitur ini HANYA terisi untuk PART yang sudah pernah menjalani SATU siklus
perbaikan penuh (dismantle -> pasang lagi) sebelum observasi - persis
POPULASI YANG BUKAN blind spot (E-35/E-47 sudah menunjukkan model relatif
baik di `has_prior_corrective`). PART tanpa histori perbaikan sama sekali
(first-failure, target utama yang diminta ditangkap) TIDAK PERNAH dapat
nilai dari fitur ini - fitur ini secara struktural tidak bisa membantu
populasi yang justru paling perlu dibantu. Konsisten dengan pola penolakan
fitur berbasis histori-per-item lain di riwayat proyek (E-30, E-35).

**Keputusan** **DITOLAK** - tidak ditambahkan ke `config.FEATURE_COLUMNS`/
`features.py`. Pelajaran untuk kandidat berikutnya: fitur yang menyasar
first-failure/no-corrective-history HARUS berasal dari sinyal yang
tersedia SEJAK instalasi (mis. kepadatan armada/model, konteks terminal,
umur - yang sudah ada) atau dari populasi eksternal (fleet-level, bukan
riwayat PART itu sendiri) - bukan dari histori kejadian PART itu sendiri,
karena populasi first-failure by definition belum punya histori kejadian
apa pun untuk diringkas.

---

## E-53 · FASE 8 Langkah E1: kepadatan kerusakan per LOKASI di CatBoost - perbaikan agregat kecil, TIDAK menyentuh blind spot first-failure sama sekali

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** E-27 (2026-08-22) menguji kepadatan lokasi (place) HANYA
digabung dengan client (match rate gabungan 87,8%, gagal gerbang PR-AUC) -
place SENDIRIAN belum pernah diuji terpisah. E-26 menguji konteks terminal
sebagai kategorikal mentah (DITOLAK, kalah 5 metrik) tapi bukan sebagai
fitur kepadatan. Karena tiga percobaan berbasis-histori-PART sebelumnya
(E-51, E-52, dan pola lama E-30/E-35) sama-sama gagal untuk populasi
first-failure/tanpa-riwayat-corrective (E-35/E-47) - apakah sinyal
fleet/population-level yang TERSEDIA SEJAK HARI PERTAMA instalasi (bukan
riwayat PART itu sendiri) bisa menolong populasi itu?

**Metode** `install_place` (lokasi saat status INSTALLED tercatat, join
langsung ke `get_events()`, cakupan 99,99%) dipasangkan ke observations/
cycles/episodes lewat pola JOIN yang SAMA dengan `attach_install_context()`
(item_type) yang sudah ada, lalu dipakai ulang fungsi GENERIK
`feature_builder.local_density()` (sudah ada, dipakai `LOCAL_DENSITY_FEATURES`
production untuk item_type) - TIDAK ada fungsi baru untuk menghitung
kepadatan, cuma group_column baru. 4 fitur: `log_place_failures_90d`,
`place_failure_rate_90d`, `log_place_failures_180d`, `place_failure_rate_180d`.
Ditambahkan ke fitur production v4 (aditif), retrain CatBoost
identik-hyperparameter, evaluasi row-level + lifecycle-level (E-49) SETARA
seluruh populasi DAN - pengujian utama sesi ini - recall DI DALAM SAJA
subpopulasi blind spot (`days_since_installation > 90 AND
prior_corrective_count == 0`, definisi sama dengan E-35: 328 lifecycle
rusak TEST, 902 lifecycle rusak TEST keseluruhan) pada threshold yang
SAMA dengan yang dipakai populasi penuh (bukan dicari ulang khusus blind
spot - meniru cara production sesungguhnya menilai SEMUA PART dengan satu
model/satu threshold).

**Hasil**

Row-level, TEST: ROC-AUC 0,8389 vs baseline v4 0,8319 (+0,0070); PR-AUC
0,1958 vs 0,1961 (nyaris identik, -0,0003). Lifecycle-level (E-49),
seluruh populasi:

| Target presisi | Kandidat TEST presisi/recall/alert | Baseline v4 TEST presisi/recall/alert |
|---:|---|---|
| 0,30 | 0,5312 / 0,0754 / 128 | 0,5289 / 0,0710 / 121 |
| 0,40 | 0,5652 / 0,0144 / 23 | 0,6250 / 0,0055 / 8 |

Sedikit lebih baik di kedua target (recall naik, presisi sebanding/sedikit
turun di 0,40) - perbaikan kecil tapi konsisten arahnya, TIDAK menang
telak.

**Temuan utama - recall di subpopulasi blind spot, KEDUA model** (target
0,30 DAN 0,40, threshold masing-masing dicari dari VALIDATION penuh):

| Model | Target | TEST presisi/recall/alert - SELURUH populasi | TEST presisi/recall/alert - BLIND SPOT SAJA |
|---|---:|---|---|
| Baseline v4 | 0,30 | 0,5289 / 0,0710 / 121 | **0,0000 / 0,0000 / 0** |
| Baseline v4 | 0,40 | 0,6250 / 0,0055 / 8 | **0,0000 / 0,0000 / 0** |
| +place_density | 0,30 | 0,5464 / 0,0588 / 97 | **0,0000 / 0,0000 / 0** |
| +place_density | 0,40 | 0,6000 / 0,0033 / 5 | **0,0000 / 0,0000 / 0** |

**NOL** lifecycle blind-spot yang tertangkap (dari 328 yang benar-benar
rusak di TEST) - di KEDUA model, di KEDUA threshold. Kepadatan lokasi
sama sekali tidak menggeser satu pun PART blind-spot melewati ambang.

(Catatan metodologi: angka row-level/lifecycle populasi-penuh kandidat di
tabel pertama sedikit berbeda dari perhitungan ulang di tabel kedua -
0,5312/128 vs 0,5464/97 pada target sama 0,30 - ditelusuri ke URUTAN KOLOM
fitur baru yang beda antar dua skrip ad-hoc terpisah, CatBoost dengan
`thread_count=1`+seed tetap ternyata masih sensitif terhadap urutan kolom.
Noise numerik kecil ini TIDAK mengubah kesimpulan utama - recall blind-spot
tetap PERSIS NOL di kedua percobaan, arah temuan robust terhadap noise ini.)

**Analisis** Kepadatan fleet/lokasi memperbaiki RANKING di antara PART
yang SUDAH punya sinyal pembeda (histori, umur, model) - itu yang
menjelaskan perbaikan agregat kecil. Tapi untuk PART yang benar-benar
tanpa histori DAN berada di lokasi/model dengan kepadatan kerusakan
biasa-biasa saja, tidak ada apa pun yang mendorongnya melewati threshold -
kepadatan lokasi HANYA menggeser sedikit PART yang lokasinya kebetulan
sedang "panas", bukan mengidentifikasi PART yang genuinely berisiko tanpa
riwayat. Ini bukti ke-4 (setelah E-30, E-35, dan pola serupa) bahwa blind
spot ini BUKAN soal "fitur fleet-level yang belum dicoba" - E-47 sudah
mendiagnosis ini sebagai batas struktural ketersediaan data (butuh sinyal
intensitas pemakaian/kondisi lingkungan yang TIDAK ada di database ini,
`log.t_log_device_monitoring` dkk terbukti kosong di E-48).

**Keputusan** Perbaikan agregat TERLALU KECIL dan TIDAK KONSISTEN (menang
tipis di 0,30, kalah di 0,40) untuk direkomendasikan wiring ke production
- dan yang lebih penting, GAGAL TOTAL di tujuan utama eksperimen ini
(blind spot first-failure). **TIDAK ditambahkan ke `config.FEATURE_COLUMNS`.**
Dengan ini, SELURUH avenue fitur point-in-time-safe yang plausibel dari
data yang sudah tersedia penuh sepanjang TRAIN (histori PART sendiri:
E-51/E-52; fleet/lokasi: E-53 ini; item_type sudah di production sejak
v4) sudah dicoba - yang tersisa HANYA jalur berskema-terbatas (QC/MTBF
2025+, E-48) atau perubahan arsitektur (two-stage/ensemble, observation
scheme). Dilaporkan ke user untuk keputusan arah selanjutnya.

---

## E-54 · FASE 8 Langkah F1: hard-negative mining (sample weight) - perbaikan recall nyata di target presisi 0,30, trade-off PR-AUC

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah/dipromosikan

**Pertanyaan** User memilih "terima batas presisi 85%, perkuat yang ada":
tuning teknik training (bukan fitur baru) pada operating point yang
genuinely generalize (~0,30-0,40, E-47). Apakah hard-negative mining -
upweight baris TRAIN negatif yang skornya PALING TINGGI dari model
baseline (paling "membingungkan" model) - memperbaiki recall tanpa merusak
sinyal umur seperti E-51?

**Metode** Pass 1: latih CatBoost baseline (fitur/hyperparameter identik
production) untuk skor semua baris TRAIN. Tandai 5% baris NEGATIF TRAIN
dengan skor tertinggi (cutoff 0,7992) sebagai "hard" (12.386/247.716
baris) - TIDAK ADA baris yang dibuang (beda dari E-51). Pass 2: retrain
dengan `sample_weight` MANUAL (mengganti `auto_class_weights="Balanced"`):
baris positif diberi bobot `n_neg/n_pos` (setara Balanced), baris hard-
negative dikalikan multiplier tambahan {1 (baseline persis), 2, 3, 5}.
VALIDATION/TEST TIDAK disentuh sama sekali. Evaluasi row-level (ROC-AUC/
PR-AUC) dan lifecycle-level (E-49) di target presisi 0,30 dan 0,40.

**Hasil**

| Multiplier | TEST ROC-AUC | TEST PR-AUC | Target 0,30: TEST presisi/recall/alert | Target 0,40: TEST presisi/recall/alert |
|---:|---:|---:|---|---|
| 1,0 (=baseline v4, sanity check) | 0,8319 | 0,1961 | 0,5289 / 0,0710 / 121 | 0,6250 / 0,0055 / 8 |
| 2,0 | 0,8381 | 0,2032 | 0,4427 / **0,0942** / **192** | 1,0000 / 0,0011 / 1 (degenerate) |
| 3,0 | 0,8402 | 0,1756 | 0,4768 / 0,0798 / 151 | 0,5263 / 0,0111 / 19 |
| 5,0 | 0,8375 | 0,1678 | 0,5789 / 0,0244 / 38 | (identik dgn 0,30 - threshold collapse) |

Multiplier=1,0 cocok PERSIS dengan angka production v4 (sanity check
metodologi benar).

**Temuan** Multiplier=2,0 di target 0,30 adalah kandidat TERKUAT: recall
TEST naik **+33%** (0,0710->0,0942) dan jumlah alert naik **+59%**
(121->192) sambil presisi TEST tetap di atas target (0,4427 >= 0,30, masih
di zona "genuinely generalize" E-47 0,30-0,45) - PR-AUC row-level juga
naik (0,1961->0,2032). TAPI presisi TURUN dari baseline (0,5289->0,4427) -
ini trade-off eksplisit recall-vs-presisi DI DALAM syarat presisi>=0,30
yang diminta, bukan pelanggaran syarat. Multiplier=3,0 di target 0,40
juga menarik: recall TEST 2x lipat (0,0055->0,0111), alert 8->19, tapi
presisi turun (0,6250->0,5263) dan PR-AUC row-level agregat turun
(0,1961->0,1756) - sinyal campuran, bukan menang bersih di semua metrik.
Multiplier=5,0 terlalu agresif - recall/alert malah turun dari multiplier
2/3 (upweight berlebihan mendominasi loss, model "terlalu takut" pada
region hard-negative sampai kehilangan recall di tempat lain).

**Keputusan** **BELUM dipromosikan ke production** - ini trade-off yang
harus diputuskan pengguna sistem (lebih banyak alert & recall vs presisi
sedikit lebih rendah), bukan keputusan teknis murni. Dilaporkan ke user
sebagai kandidat konkret (multiplier=2,0, target 0,30) untuk diputuskan:
promosikan lewat jalur resmi (wiring ke `train.py::train_model()` sebagai
opsi, retrain resmi, evaluasi ulang lengkap) atau tolak.

**Update 2026-08-26 (lanjutan sesi)**: user memutuskan v4 TETAP baseline,
E-54 multiplier=2,0 disimpan sebagai **kandidat watchlist**, TIDAK
dipromosikan sebagai production utama - fokus dialihkan dulu ke feature
engineering (E-55 dst.) sebelum revisit teknik training.

**Peringatan penting (lihat E-55 update)**: kandidat ini **BELUM
di-rolling-backtest**. E-55 membuktikan kemenangan di SATU TEST split bisa
jadi artefak fold tunggal pada jumlah alert serendah ini - klaim "recall
+33%" di atas HANYA berdasar satu TEST split dan wajib divalidasi ulang
lewat rolling backtest (seperti G4 di E-55) sebelum benar-benar dianggap
kandidat serius, bukan cuma "watchlist" berdasar satu pengukuran.

**Update 2026-08-26 (Langkah H2) - rolling backtest 6-fold**: metodologi
identik E-55 update (fold logic E-44, evaluasi lifecycle E-49). Hasil
ringkasan:

| Target | Baseline (6 fold) | Hard-neg mult=2,0 (6 fold) | Selisih recall per-fold |
|---|---|---|---|
| 0,30 | presisi=0,3043+/-0,2605 recall=0,0386+/-0,0320 | presisi=**0,3861**+/-0,2530 recall=**0,0464**+/-0,0389 | +0,0078+/-0,0391 (TIDAK signifikan) |
| 0,40 | presisi=0,4306+/-0,4312 (4/6 feasible) recall=0,0055+/-0,0041 | presisi=0,1296+/-0,2128 (6/6 feasible) recall=0,0071+/-0,0112 | +0,0052+/-0,0098 (TIDAK signifikan) |

Berbeda dari E-55 (yang jelas kalah rata-rata), kandidat ini justru
menang di KEDUA metrik rata-rata di target 0,30 (presisi 0,3043->0,3861,
recall 0,0386->0,0464) - TAPI selisihnya tetap TIDAK melewati 1 SD
selisih per-fold, jadi menurut kriteria E-44 sendiri BELUM bisa diklaim
signifikan. Fold 3 (baseline maupun kandidat presisi 0) dan fold 5-6
sangat menentukan rata-rata di kedua arah - dengan cuma 6 fold, satu-dua
fold ekstrem masih bisa menggeser kesimpulan.

**Keputusan final** **BELUM TERBUKTI, TETAP WATCHLIST** (bukan
DITOLAK seperti E-55, karena arahnya konsisten positif meski belum
signifikan). Tidak dipromosikan ke production. Catatan metodologi
penting: pada skala alert seperti ini (single-digit sampai puluhan per
fold ~2 bulan), variansi antar-fold TAMPAKNYA memang sangat besar secara
struktural (SD 0,21-0,43 pada presisi) - kemungkinan kriteria "1 SD"
warisan E-44 (dirancang untuk perbandingan model row-level dengan jumlah
sampel jauh lebih besar) terlalu ketat untuk dipenuhi kandidat MANA PUN
di skala lifecycle-alert serendah ini. Perlu dipikirkan ulang apakah
kriteria signifikansi untuk evaluasi lifecycle-level butuh penyesuaian
(mis. lebih banyak fold, window lebih panjang, atau ambang berbeda) -
dicatat sebagai pertanyaan terbuka, bukan diputuskan sepihak di sini.

---

## E-60 · FASE 8 Langkah H3: lifecycle weighting (downweight, bukan buang) - DITOLAK, mengulang kesalahan E-51 dalam bentuk lebih halus

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** E-51 gagal karena MEMBUANG baris - user meminta versi lebih
aman: DOWNWEIGHT (bukan buang) baris TRAIN dari lifecycle dengan banyak
snapshot, supaya PART berumur panjang tidak mendominasi loss cuma karena
menyumbang lebih banyak baris, TANPA kehilangan cakupan umur (semua baris
tetap ada, cuma bobotnya dikecilkan).

**Metode** Dua skema, dibandingkan terhadap tanpa-weighting: `inverse_count`
(bobot = 1/jumlah_baris_lifecycle_ini_di_TRAIN) dan `inverse_sqrt_count`
(1/akar jumlah baris - peredaman lebih lembut). Dikalikan dengan bobot
balance-kelas seperti biasa. Rolling backtest 6-fold, evaluasi lifecycle
(E-49).

**Hasil**

| Skema | Target 0,30: presisi/recall (6 fold) | Target 0,40: presisi/recall |
|---|---|---|
| none (baseline) | 0,3043+/-0,2605 / 0,0386+/-0,0320 | 0,4306+/-0,4312 (4/6) / 0,0055+/-0,0041 |
| inverse_count | **0,0625+/-0,1531 / 0,0025+/-0,0061** | 0,0625+/-0,1531 / 0,0025+/-0,0061 |
| inverse_sqrt_count | 0,1056+/-0,1366 / 0,0066+/-0,0077 (5/6) | 0,0889+/-0,1449 / 0,0045+/-0,0061 (5/6) |

KALAH TELAK di kedua skema, kedua target, hampir semua fold - bukan
trade-off, murni lebih buruk (recall turun 6-15x lipat).

**Analisis** Downweighting berat (`inverse_count`) secara EFEKTIF
menyamai efek MEMBUANG baris (E-51) - kontribusi gradiennya jadi nyaris
nol, walau barisnya secara teknis "masih ada" di data. Sinyal umur yang
sama dengan yang dihancurkan E-51 (via pembuangan) di sini dihancurkan
lewat jalur berbeda (via bobot mendekati nol) - hasil akhirnya sama:
model kehilangan cakupan hazard-vs-umur yang tersebar di baris "tengah"
lifecycle panjang. `inverse_sqrt_count` (peredaman lebih lembut) sedikit
kurang buruk tapi tetap kalah jauh dari baseline - bahkan peredaman
PARSIAL sudah cukup merusak.

**Keputusan** **DITOLAK, kedua skema** - tidak ada perubahan pada
`train.py`/production. Pelajaran: masalah "lifecycle panjang menyumbang
banyak baris" (E-50) BUKAN sesuatu yang perlu/boleh diperbaiki lewat
downweight/removal berbasis JUMLAH BARIS - baris-baris itu, walau
berkorelasi tinggi satu sama lain secara HISTORI (E-50), membawa variasi
UMUR yang genuinely dibutuhkan model. Kalau perombakan skema observasi
tetap ingin dicoba (E-50's saran resolusi seragam 60/90 hari), itu HARUS
mengurangi KEPADATAN landmark di skema pembuatannya (features.py::
training_observations()), bukan menambal lewat sample weight di sisi
training.

---

## E-61 · FASE 8 Langkah H4: discrete-time hazard model (logistic regression) vs CatBoost - jauh lebih buruk, tapi INCONCLUSIVE (kemungkinan kurang tuning)

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** User meminta perbandingan pendekatan discrete-time hazard/
survival vs CatBoost classification, populasi & split temporal SAMA -
apakah model hazard klasik (person-period logistic regression: P(gagal di
window ini | bertahan sampai sekarang) = sigmoid(kovariat termasuk umur),
bentuk hazard diskrit paling standar) menangani struktur repeated-
observation/umur/censoring lebih baik daripada CatBoost yang memperlakukan
tiap baris sebagai independen?

**Metode** Dataset OBSERVASI SAMA PERSIS dengan CatBoost (`training_
observations()`, tidak ada perubahan skema). Fitur di-encode: kategorikal
one-hot (`OneHotEncoder`), numerik distandarkan (`StandardScaler`, fit di
TRAIN saja). `LogisticRegression(penalty="l2", C=1.0, class_weight="balanced")`
- regularisasi/hyperparameter DEFAULT, tidak di-tuning. Kalibrasi isotonic
di VALIDATION sama seperti CatBoost. Rolling backtest 6-fold, evaluasi
lifecycle (E-49).

**Hasil**

| Target | CatBoost (6 fold) | Hazard/logreg (6 fold) |
|---|---|---|
| 0,30 | 3/6->6/6 feasible, presisi=0,3043+/-0,2605 recall=0,0386+/-0,0320 | **3/6 feasible, presisi=0,0000+/-0,0000 recall=0,0000+/-0,0000** |
| 0,40 | 4/6 feasible, presisi=0,4306+/-0,4312 recall=0,0055+/-0,0041 | **2/6 feasible, presisi=0,0000+/-0,0000 recall=0,0000+/-0,0000** |

Model hazard INFEASIBLE di sebagian besar fold, dan di fold yang feasible
sekalipun presisi/recall-nya PERSIS NOL (threshold ditemukan tapi tidak
menangkap satu pun kerusakan dengan benar) - kalah telak, bukan sekadar
kalah tipis.

**Analisis - kenapa ini TIDAK bisa langsung disimpulkan "hazard model
lebih buruk"**: model dilatih dengan hyperparameter DEFAULT tanpa tuning
sama sekali (C=1,0, tanpa pencarian regularisasi, tanpa target-encoding
untuk kategorikal berkardinalitas tinggi seperti `part_model_category`
setelah one-hot). Kegagalan total (presisi 0 di semua fold feasible)
lebih konsisten dengan model yang GAGAL BELAJAR sama sekali pada data
setimpang ekstrem ini (base rate row-level ~1,7%, lihat E-50) daripada
model yang "belajar tapi kalah" - regularisasi L2 default kemungkinan
menarik seluruh koefisien ke nol pada skala fitur/kelas seimpang ini.
**Ini BUKAN bukti valid bahwa pendekatan hazard/survival kalah dari
CatBoost** - baru bukti bahwa satu konfigurasi TANPA tuning kalah.
Perbandingan yang adil butuh minimal: sweep C, pertimbangkan target/
frequency encoding untuk kategorikal, dan/atau elastic-net.

**Keputusan** **INCONCLUSIVE, bukan DITOLAK** - tidak cukup bukti untuk
menyimpulkan pendekatan hazard/survival secara umum kalah dari CatBoost.
Dicatat sebagai upaya awal yang gagal karena kurang tuning, bukan
kesimpulan final. Mengingat pola SELURUH eksperimen struktural sesi ini
(E-54 update, E-59, E-60, E-61) tidak satu pun menghasilkan perbaikan
tervalidasi, dan RSF (model survival yang SUDAH ada dan matang,
`engines/survival/`) sendiri terbukti (E-24, catatan lama) unggul PR-AUC
tapi kalah presisi/recall@kapasitas dibanding CatBoost - kemungkinan besar
tuning lebih lanjut pada logistic-regression hazard TIDAK akan mengubah
kesimpulan mendasar, tapi ini TIDAK dibuktikan di sesi ini, cuma
diperkirakan berdasar pola. Investigasi lanjutan (tuning proper) di luar
cakupan waktu sesi ini - dicatat sebagai item terbuka.

---

## E-62 · FASE 8 sintesis: 13 eksperimen struktural/fitur/training berturut-turut gagal menaikkan recall secara tervalidasi - bottleneck kemungkinan besar keterbatasan data, bukan feature engineering/model choice

2026-08-26 · sintesis, bukan eksperimen baru - merangkum E-49 s/d E-61

**Konteks** User meminta: kalau seluruh eksperimen struktural tetap tidak
mampu menaikkan recall secara material, didokumentasikan eksplisit bahwa
bottleneck kemungkinan dari keterbatasan informasi kondisi/usage di
database, bukan sekadar feature engineering/model choice. Fase 8 (sesi
2026-08-26) sudah mencoba 13 pendekatan berbeda dengan metodologi disiplin
(VALIDATION-only search, TEST touch-once, evaluasi lifecycle E-49, rolling
backtest 6-fold sejak E-55 membuktikan satu TEST split tidak cukup).

**Ringkasan seluruh percobaan**:

| # | Pendekatan | Kategori | Verdict |
|---|---|---|---|
| E-51 | Thinning baris negatif (buang) | Dataset | DITOLAK - rusak sinyal umur |
| E-52 | Durasi penanganan WO | Fitur (histori PART) | DITOLAK - cakupan 10,9% |
| E-53 | Kepadatan lokasi | Fitur (fleet-level) | Lemah, blind spot tetap 0 |
| E-54 | Hard-negative mining | Training technique | TIDAK signifikan (rolling) |
| E-55 | Umur relatif thd umur-gagal-khas | Fitur (fleet-level) | DIBATALKAN - artefak 1 fold |
| E-57 | Physical age lintas cycle | Fitur (histori PART) | DITOLAK - campuran |
| E-59 | Pemendekan lifecycle | Fitur (histori PART) | DITOLAK - cakupan 9,3% + tidak stabil |
| E-60 | Lifecycle weighting (2 skema) | Training technique | DITOLAK TELAK |
| E-61 | Discrete-time hazard (logreg) | Model choice | Kalah telak (inconclusive, kurang tuning) |

**Ditambah temuan diagnostik** (E-35, E-46, E-47, E-48 dari sesi
sebelumnya + E-56, E-58 sesi ini):
- Ceiling presisi genuinely generalize ada di ~0,30-0,45 (E-47), BUKAN
  0,85 - dikonfirmasi ulang di bawah metodologi lifecycle (E-49).
- 99,4% kerusakan TEST terlewat oleh model production (E-56) - skor
  median FN cuma 0,05, JAUH di bawah threshold manapun (bukan soal
  kalibrasi/threshold).
- **DUA blind spot besar, penyebab BERBEDA**: first-failure/tanpa-
  corrective (37,3% FN, E-56) dan **late-life/histori-tenang** (40,1%
  dari SEMUA kerusakan TEST, recall 0%, E-58) - late-life PART tidak
  punya red flag APA PUN sampai detik gagal.
- 5 model PART penyumbang FN terbesar tersebar di SELURUH rentang umur
  (0 - 6,5 tahun) dan **ZERO cakupan `journal.t_mtbf`** (E-58) - jalur
  MTBF 2025+ TIDAK akan menjawab populasi FN terbesar ini.
- Satu-satunya data device-monitoring yang plausibel jadi proxy
  intensitas pemakaian (`log.t_log_device_monitoring` dkk) **kosong
  total** di database ini (E-48).

**Kesimpulan** Pola yang konsisten di 13 percobaan: setiap kali sinyal
BARU ditambahkan (fitur histori PART, fitur fleet-level, teknik training,
bahkan arsitektur model berbeda), hasilnya SELALU salah satu dari: (a)
tidak lolos rolling backtest (menang di satu TEST split, hilang di 5 fold
lain - E-55, E-59), (b) menang tipis tapi tidak konsisten dan tidak
menyentuh blind spot (E-53, E-54), atau (c) kalah telak (E-51, E-57,
E-60, E-61). TIDAK ADA satu pun yang menaikkan recall SECARA MATERIAL dan
TERVALIDASI. Kombinasi dengan E-56/E-58 (skor median FN jauh di bawah
threshold, dua populasi blind-spot besar dengan penyebab berbeda, model
dominan-FN tanpa cakupan MTBF) mengarah ke satu kesimpulan yang sama dari
BANYAK sudut berbeda: **bottleneck-nya adalah keterbatasan informasi
kondisi/usage di database ini** (intensitas pemakaian, kondisi lingkungan
operasional, faktor fisik yang tidak tercatat sama sekali) - BUKAN
feature engineering yang belum ditemukan atau pilihan model/arsitektur
training yang salah. Ini bukan kesimpulan baru - E-47 (2026-08-25) sudah
mendiagnosis ini; sesi Fase 8 (2026-08-26, 13 percobaan tambahan dengan
metodologi lebih ketat) MENGONFIRMASI ULANG dari lebih banyak arah,
bukan membantahnya.

**Implikasi untuk keputusan produksi**: mengejar presisi 85% dengan data
yang tersedia SEKARANG kemungkinan besar tidak akan berhasil lewat
feature engineering/model choice tambahan. Jalur yang MASIH belum
tervalidasi dan berpotensi (tidak dijamin) mengubah kesimpulan ini:
(1) QC/MTBF pada window 2025+ TERBATAS (E-48) - TAPI E-58 menunjukkan ini
tidak akan menjawab 5 model dominan-FN terbesar; (2) data eksternal yang
sama sekali di luar database ini (sensor IoT/kondisi lingkungan
sungguhan) - di luar cakupan proyek ini; (3) menerima ceiling ~0,30-0,45
sebagai titik operasi realistis dan fokus ke kualitas produksi/monitoring
(sudah dikerjakan sebagian - lihat commit alert lifecycle + monitoring
sesi sebelumnya). Keputusan akhir dikembalikan ke user.

---

## E-55 · FASE 8 Langkah G1: umur relatif terhadap umur-gagal-khas model - kemenangan BERSIH pertama (presisi DAN recall naik bersama) di target 0,40

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah/dipromosikan

**Pertanyaan** User meminta fitur "umur PART relatif terhadap umur failure
normal" - berbeda dari E-53 (kepadatan kerusakan MENTAH per lokasi, tanpa
normalisasi umur), fitur ini menggabungkan umur PART SAAT INI dengan "pada
umur berapa model ini BIASANYA gagal" - sinyal fleet-level (tidak butuh
histori PART sendiri, tersedia sejak hari pertama) TAPI dinormalisasi
umur, tidak seperti kepadatan mentah E-53. Apakah kombinasi ini akhirnya
menolong, termasuk blind spot first-failure?

**Metode** `attach_model_typical_failure_age()`: untuk tiap baris, hitung
rata-rata KUMULATIF `duration_days` (umur saat gagal) dari SEMUA cycle
model yang sama yang SUDAH gagal SEBELUM `observation_on` (point-in-time
safe, pola searchsorted+cumsum yang sama dengan `corrective_degradation_
trend`/`local_density` yang sudah ada) - minimal 3 kegagalan model itu
supaya tidak noise. Fallback ke rata-rata GLOBAL (semua model) kalau model
spesifiknya belum punya cukup kegagalan sendiri. 3 fitur baru:
`age_ratio_to_typical_failure` (umur PART / umur-gagal-khas model),
`log_typical_failure_age_days`, `has_typical_failure_age_estimate`.
Ditambahkan ke fitur production v4, retrain identik-hyperparameter,
evaluasi row-level + lifecycle-level (E-49) + recall khusus blind spot
(E-35, sama seperti E-53).

**Hasil**

Cakupan: 71,64% baris punya histori KEGAGALAN MODEL SENDIRI yang cukup
(>=3), naik ke 98,86% dengan fallback global.

| | VALIDATION | TEST | TEST baseline v4 |
|---|---:|---:|---:|
| ROC-AUC | 0,8202 | 0,8306 | 0,8319 |
| PR-AUC | 0,1081 | 0,1945 | 0,1961 |

Kedua fitur baru MASUK 12 fitur terpenting (`log_typical_failure_age_days`
peringkat 6, `age_ratio_to_typical_failure` peringkat 11) - model
BENAR-BENAR memakainya, beda dari E-52 (WO duration, tidak masuk 15
besar).

Lifecycle-level (E-49):

| Target presisi | Kandidat TEST presisi/recall/alert | Baseline v4 TEST presisi/recall/alert |
|---:|---|---|
| 0,30 | 0,4270 / 0,0876 / 185 | 0,5289 / 0,0710 / 121 |
| **0,40** | **0,7778 / 0,0233 / 27** | 0,6250 / 0,0055 / 8 |
| 0,50-0,85 | INFEASIBLE (maks VAL 0,4706) | INFEASIBLE (degenerate, 2 lifecycle VAL) |

**Di target 0,40: presisi DAN recall naik BERSAMAAN** - presisi TEST
0,6250->0,7778 (+24%), recall 0,0055->0,0233 (+324%, 4x lipat), alert
8->27. Ini kemenangan BERSIH pertama di seluruh sesi Fase 8 (E-49 s/d
E-55) - bukan trade-off. Bonus: target 0,50-0,85 jadi INFEASIBLE bersih
(bukan lagi degenerate 2-lifecycle) - ekor skor kandidat tidak
menghasilkan "presisi 1,0 palsu dari 2 sampel" seperti baseline.

Blind spot (umur>90 hari, tanpa prior corrective) - **TETAP 0/0/0** di
kedua target, sama seperti E-53. Fitur ini menolong PART yang MODEL-nya
sudah cukup dikenal (>=3 kegagalan sebelumnya), TIDAK menolong PART yang
benar-benar tanpa histori kejadian sama sekali (lihat E-56 untuk kenapa).

**Keputusan (awal)** ~~KANDIDAT KUAT~~ - **DIBATALKAN, lihat update di
bawah.** Awalnya dianggap kandidat kuat berdasar satu TEST split. Rolling
backtest (lanjutan sesi, di bawah) membuktikan ini TIDAK stabil.

**Update 2026-08-26 (lanjutan sesi) - rolling backtest 6-fold membatalkan
temuan di atas**: metodologi sama dengan E-44 (`cli.py::_rolling_fold_windows`/
`_assign_rolling_split`, window 60 hari/fold, reuse langsung bukan
reimplementasi) tapi evaluasi LIFECYCLE-based (E-49), bukan row-level.
Hasil per fold (target 0,30):

| Fold | TEST window | Baseline presisi/recall/alert | +typical_age presisi/recall/alert |
|---|---|---|---|
| 1 | 2025-08-08 s/d 2025-10-07 | 0,2297/0,0837/74 | 0,2069/0,0296/29 |
| 2 | 2025-10-07 s/d 2025-12-06 | 0,3333/0,0599/30 | 0,4000/0,0479/20 |
| 3 | 2025-12-06 s/d 2026-02-04 | 0,0000/0,0000/3 | 0,1111/0,0051/9 |
| 4 | 2026-02-04 s/d 2026-04-05 | 0,2800/0,0422/25 | 0,2857/0,0120/7 |
| 5 | 2026-04-05 s/d 2026-06-04 | 0,2000/0,0050/5 | 0,0000/0,0000/4 |
| 6 (=TEST asli E-55) | 2026-06-04 s/d 2026-08-03 | 0,7826/0,0407/23 | 0,7403/0,1290/77 |

Ringkasan 6 fold: target 0,30 - baseline presisi=0,3043+/-0,2605
recall=0,0386+/-0,0320; kandidat presisi=0,2907+/-0,2599
recall=0,0373+/-0,0482 - **kandidat SEDIKIT LEBIH BURUK rata-rata**, dan
SD di kedua model LEBIH BESAR dari selisih rata-rata keduanya (klasik
"tidak signifikan" per kriteria E-44 sendiri: klaim menang cuma sah kalau
selisih rata-rata melebihi 1 SD selisih per-fold). Target 0,40: kandidat
feasible cuma di 3/6 fold (vs baseline 4/6) dan presisi rata-ratanya LEBIH
RENDAH (0,2356 vs 0,4306).

**Fold 6 - kebetulan** adalah window yang SAMA PERSIS dengan TEST asli
yang dipakai mengukur "kemenangan bersih" di atas - dan cuma di fold ITU
kandidat menang jelas (recall 0,1290 vs 0,0407). Di 5 fold lain,
performanya biasa saja atau lebih buruk. **Kemenangan "bersih" yang
dilaporkan di atas adalah artefak SATU TEST split, persis risiko yang
diperingatkan user saat meminta rolling backtest.**

**Keputusan final** **DIBATALKAN/DITOLAK** - `age_ratio_to_typical_failure`
dkk TIDAK ditambahkan ke `config.FEATURE_COLUMNS`. Pelajaran metodologi
penting untuk SEMUA eksperimen lanjutan: pada jumlah alert serendah ini
(single-digit sampai puluhan per window 2 bulan), evaluasi SATU TEST split
- betapapun disiplin metodologinya (VALIDATION-only search, TEST touch-once,
lifecycle-based) - TIDAK CUKUP untuk mengklaim kemenangan. Rolling backtest
WAJIB sebelum kandidat mana pun (termasuk E-54 hard-negative mining, yang
BELUM di-rolling-backtest) dianggap kandidat serius.

---

## E-56 · FASE 8 Langkah G2: analisis mendalam false negative/false positive v4 - masalah UTAMA adalah recall (99,4% kerusakan TEST terlewat), bukan presisi

2026-08-26 · analisis ad-hoc (skrip di scratchpad, tidak masuk repo) - murni pengukuran, tidak ada kode diubah

**Pertanyaan** User meminta analisis mendalam FN (dikelompokkan first-
failure/tanpa-corrective/umur/lifecycle/model) dan FP, untuk mengarahkan
feature engineering berikutnya dengan bukti, bukan tebakan.

**Metode** Skor v4 production (threshold gate 0,375) pada seluruh TEST,
klasifikasi tiap lifecycle rusak jadi TP/FN lewat `gate._first_alert_per_cycle`
(E-49 methodology - first-alert, bukan snapshot). Untuk tiap lifecycle
rusak, ambil baris "onset" (baris dengan `target_failure=True`) untuk
melihat kondisi PART persis sebelum seharusnya di-flag. Untuk FP, ambil
baris first-alert-nya sendiri.

**Hasil**

902 lifecycle rusak di TEST -> **TP=5, FN=897 (99,4% terlewat)**. Alert
total cuma 8, jadi **FP=3**.

**Profil FN (897)**:
- 55,9% first-cycle PART ini (belum pernah ada cycle sebelumnya sama
  sekali - `has_previous_cycle=False`)
- 38,7% tanpa histori corrective sama sekali
- **37,3% KEDUANYA sekaligus** (blind spot inti E-35/E-47)
- **42,8% PUNYA KEDUANYA** (histori cycle sebelumnya DAN corrective di
  cycle ini) - PART BERHISTORI pun tetap sering terlewat, bukan cuma
  masalah blind spot murni
- Umur saat onset SANGAT bimodal: p25=0 hari (gagal nyaris seketika
  setelah instalasi - kemungkinan cacat pabrik/rusak saat pengiriman,
  bukan pola "aus"), median=270 hari, p75=2070 hari (~5,7 tahun -
  kegagalan umur sangat panjang, mungkin genuinely acak di ujung masa
  pakai). Kedua ekor ini SAMA-SAMA sulit ditangkap model yang dilatih
  pada pola risiko usia "tipikal".
- Skor model saat onset SANGAT rendah: median=0,0509, p90 cuma 0,2418 -
  jauh di bawah threshold 0,375. **Ini BUKAN kasus "hampir kena
  threshold"** - model benar-benar tidak punya sinyal pembeda untuk
  sebagian besar FN, bukan soal kalibrasi/threshold yang kurang pas.
- Terkonsentrasi di segelintir model: `0120204` (113), `0120201` (103),
  `0521202` (76), `0620505` (70), `0720301` (68) - >40% dari SEMUA FN
  cuma dari 5 kode model.

**Profil TP (5, sangat kecil untuk generalisasi kuat)**: SEMUA di umur=0
hari (gagal dalam window observasi PERTAMA setelah instalasi) dan skor
PERSIS 0,3750 (identik ke-4-desimal untuk kelima kasus) - pola ini
konsisten dengan "kombinasi kategori model/client yang sudah dikenal
sangat buruk sejak awal", bukan sinyal degradasi bertahap yang terdeteksi
tepat waktu.

**Profil FP (3)**: histori SANGAT berat (median 11 kegagalan sebelumnya,
60 catatan korektif) - satu model (`0120401`) berulang, di-flag di umur 30
hari (baru dipasang lagi setelah perbaikan). Bukan PART sehat yang salah
di-flag - ini PART yang genuinely bermasalah kronis, cuma kebetulan tidak
rusak lagi PERSIS di window 30 hari TEST ini.

**Temuan** Masalah UTAMA model v4 di threshold production BUKAN "terlalu
banyak alarm palsu" (FP cuma 3, dan ketiganya masuk akal) - **masalahnya
model nyaris tidak pernah cukup yakin untuk membunyikan alarm sama
sekali** (8 alert dari 902 kerusakan nyata dalam ~7 bulan). Ini
mengonfirmasi ulang E-47 dari sudut lain: skor untuk-mayoritas-FN jauh di
bawah threshold (bukan di dekatnya), jadi perbaikan kecil di kalibrasi/
threshold tidak akan banyak membantu - butuh sinyal yang benar-benar
mengangkat skor populasi yang sekarang median-nya cuma 0,05. Temuan baru:
proporsi FN yang PUNYA histori (42,8%) hampir sama besar dengan yang TIDAK
(55,9%/37,3%) - artinya penguatan sinyal histori (bukan cuma fitur fleet-
level untuk blind spot) MASIH punya ruang untuk membantu populasi
berhistori yang tetap terlewat.

**Keputusan** Murni analisis, tidak ada kode diubah. Mengarahkan prioritas
berikutnya: (1) segelintir model dominan-FN (`0120204` dkk) layak
diselidiki lebih lanjut kalau ada waktu - mungkin ada pola khusus model
itu; (2) populasi umur ekstrem (nyaris 0 hari ATAU >5 tahun) butuh
pendekatan berbeda dari populasi umur "tipikal" yang sudah dilayani fitur
umur yang ada; (3) fitur histori yang LEBIH TAJAM (bukan cuma fleet-level
baru) masih relevan untuk 42,8% FN yang sudah punya histori tapi tetap
terlewat.

---

## E-57 · FASE 8 Langkah G3: physical_age_now (umur total lintas cycle) - lemah/campuran, DITOLAK

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** User meminta "physical age PART" sebagai fitur terpisah.
Logikanya SUDAH tervalidasi untuk model survival
(`features_survival.py::cumulative_cycle_age()`, dipakai
`log_cumulative_prior_cycle_days` yang SUDAH ada di CatBoost production)
- yang belum pernah dicoba adalah MENJUMLAHKANNYA dengan umur cycle
berjalan jadi satu angka `physical_age_now` (umur PART SEJAK PERTAMA KALI
terpasang, lintas semua cycle, bukan cuma cycle sekarang).

**Metode** `log_physical_age_now = log1p(cumulative_prior_cycle_days +
days_since_installation)` (reuse `cumulative_cycle_age()`, tanpa fungsi
baru). Ablasi tunggal (1 fitur) terhadap fitur production v4, metodologi
identik E-53/E-55.

**Hasil** Fitur masuk 12 besar (peringkat 8, importance 3,754) tapi
TEST PR-AUC turun (0,1961->0,1844). Lifecycle-level (E-49):

| Target presisi | Kandidat TEST presisi/recall/alert | Baseline v4 TEST presisi/recall/alert |
|---:|---|---|
| 0,30 | 0,5047 / 0,0599 / 107 | 0,5289 / 0,0710 / 121 |
| 0,40 | 0,5714 / 0,0133 / 21 | 0,6250 / 0,0055 / 8 |

Target 0,30: KALAH di presisi DAN recall sekaligus (bukan trade-off, murni
lebih buruk). Target 0,40: presisi turun, recall naik - trade-off
campuran, bukan menang bersih seperti E-55. Blind spot: tetap 0/0/0.

**Analisis** `physical_age_now` secara matematis MENDOMINASI kalau PART
sudah punya banyak cycle sebelumnya (cumulative_prior_cycle_days besar)
tapi untuk mayoritas PART (first-cycle, ~56% dari populasi rusak per
E-56) nilainya SAMA PERSIS dengan `log_days_since_installation` yang sudah
ada (cumulative=0) - fitur ini pada dasarnya cuma menduplikasi sinyal umur
yang sudah ada untuk kebanyakan populasi, hanya berbeda untuk minoritas
PART yang sudah reinstall berkali-kali, dan itu tidak cukup untuk menang
bersih.

**Keputusan** **DITOLAK** - tidak ditambahkan ke `config.FEATURE_COLUMNS`.
Beda dengan E-55 (umur RELATIF terhadap pola kegagalan model - sinyal
baru, bukan variasi dari umur mentah yang sudah ada), `physical_age_now`
terlalu mirip fitur umur yang sudah ada untuk populasi yang paling
menentukan (first-cycle).

---

## E-58 · FASE 8 Langkah H1: analisis cohort umur + investigasi 5 model dominan-FN - LATE-LIFE (40% kerusakan) adalah blind spot KEDUA, beda penyebab dari first-failure

2026-08-26 · analisis ad-hoc (skrip di scratchpad, tidak masuk repo) - murni pengukuran, tidak ada kode diubah

**Pertanyaan** User meminta analisis cohort umur (early/normal-life/late-
life) SEBELUM membangun model terpisah per cohort, dan investigasi khusus
5 model PART penyumbang FN terbesar (`0120204`, `0120201`, `0521202`,
`0620505`, `0720301`, teridentifikasi di E-56).

**Metode** Cohort umur DATA-DRIVEN dari distribusi umur onset TEST (E-56:
p25=0, median=270, p75=2062) - EARLY (<=90 hari, sejalan `installation_
age_band` yang sudah ada), NORMAL (91-730 hari), LATE (>730 hari, >2
tahun). Untuk 5 model dominan-FN: total failure sepanjang sejarah, recall
TEST, umur saat onset, proporsi first-cycle, skor model saat onset, 3
client teratas, dukungan TRAIN, dan cek langsung `journal.t_mtbf` untuk
kelima kode model itu.

**Hasil**

Cohort umur (902 lifecycle rusak TEST):

| Cohort | n | share | recall | prior_failure_count (mean) | prior_corrective_count (mean) | model_failure_rate_90d (mean) |
|---|---:|---:|---:|---:|---:|---:|
| EARLY (<=90d) | 372 | 41,2% | 1,34% (5/372) | 1,56 | 11,17 | 0,157 |
| NORMAL (91-730d) | 168 | 18,6% | **0,00%** | 1,94 | 10,52 | 0,083 |
| LATE (>730d) | 362 | 40,1% | **0,00%** | 0,08 | 0,73 | 0,030 |

5 model dominan-FN - SEMUANYA didominasi 1-2 client (KCI/LRT Jabodebek),
rentang umur onset SANGAT beragam antar model (bukan pola tunggal):

| Model | Failure TEST | Tertangkap | Umur onset (median) | First-cycle | Skor median | Client dominan | Baris t_mtbf |
|---|---:|---:|---:|---:|---:|---|---:|
| 0120204 | 115 | 2 (1,7%) | 90 hari | 32% | 0,0509 | KCI (94%) | **0** |
| 0120201 | 103 | 0 | 1380 hari (~3,8th) | 78% | 0,0419 | KCI (100%) | **0** |
| 0521202 | 76 | 0 | 0 hari | 53% | 0,1716 | KCI (99%) | **0** |
| 0620505 | 70 | 0 | 900 hari (~2,5th) | 81% | 0,0188 | LRT (66%) | **0** |
| 0720301 | 68 | 0 | 2370 hari (~6,5th) | 96% | 0,0252 | KCI (91%) | **0** |

**Temuan**

1. **LATE-LIFE (>730 hari) adalah blind spot KEDUA yang BERBEDA PENYEBAB
   dari first-failure** - 40,1% dari SEMUA kerusakan TEST, recall 0%,
   TAPI bukan karena tanpa histori (E-35/E-47/E-56 punya blind spot
   "tanpa corrective") - PART late-life justru sudah lama terpasang
   TENANG (prior_corrective_count rata-rata cuma 0,73, jauh di bawah
   EARLY/NORMAL yang ~11). Ini pola "aus diam-diam lalu gagal tiba-tiba"
   - tidak ada red flag APA PUN di histori sampai detik terakhir. Model
   manapun yang dilatih pola "banyak kejadian -> risiko naik" TIDAK PUNYA
   cara mendeteksi ini dari fitur yang ada sekarang.
2. **NORMAL-life (91-730 hari) JUSTRU punya histori PALING BERAT**
   (prior_failure_count 1,94, TERTINGGI dari 3 cohort) tapi recall TETAP
   0% - mengonfirmasi E-56: histori yang ada TIDAK CUKUP TAJAM bahkan
   untuk PART yang "terlihat" berisiko di data.
3. **EARLY (<=90 hari) satu-satunya cohort yang tertangkap SAMA SEKALI**
   (1,34%, cuma 5 kasus) - dan cohort ini justru yang PALING mirip PART
   "bermasalah kronis" (corrective count tertinggi, model_failure_rate_90d
   tertinggi) - model menangkap yang PALING JELAS saja.
4. **5 model dominan-FN BUKAN satu pola tunggal** - rentang umur onset
   dari 0 sampai 6,5 TAHUN, campuran first-cycle (32-96%) - satu-satunya
   kesamaan adalah VOLUME (client KCI/LRT Jabodebek, dukungan TRAIN besar)
   dan **ZERO baris `journal.t_mtbf`** untuk KELIMANYA - MTBF (kalaupun
   dipakai lewat jalur 2025+ terpisah nanti) TIDAK akan membantu
   populasi FN paling besar ini sama sekali, karena datanya memang tidak
   ada untuk model-model ini.

**Keputusan** Murni analisis. Mengonfirmasi DAN memperluas E-56/E-47:
bottleneck bukan cuma "PART tanpa histori" (first-failure) tapi JUGA
"PART dengan histori tenang yang gagal di ujung umur panjang" (late-life,
40% dari total!) - dua populasi besar dengan penyebab BERBEDA, sama-sama
tidak terjawab oleh fitur histori/fleet-density yang sudah dicoba (E-51
s/d E-55, E-57). Ini memperkuat hipotesis E-47: masalahnya bukan fitur
yang belum ditemukan dari data yang ADA, tapi SEBAGIAN BESAR kerusakan
(EARLY yang bukan chronic + NORMAL + LATE, total 902-5=897 dari 902)
genuinely tidak punya sinyal pembeda di database ini. MTBF (5 model
dominan-FN nol cakupan) TIDAK akan menjawab ini bahkan kalau jalur 2025+
dikerjakan.

---

## E-59 · FASE 8 Langkah G5: fitur pemendekan lifecycle - cakupan terlalu tipis (9,3%), tidak stabil di rolling backtest, DITOLAK

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** "Pemendekan lifecycle" (apakah cycle konfirmasi-gagal
PALING BARU milik PART ini lebih pendek dari rata-rata cycle sebelumnya) -
reuse `features_survival.py::audit_previous_cycle_features()` (sudah
tervalidasi untuk RSF). Diuji LANGSUNG dengan rolling backtest 6-fold
(wajib sejak E-55), bukan satu TEST split.

**Metode** `lifecycle_shortening_ratio = last_confirmed_failure_lifetime /
previous_cycle_confirmed_failure_lifetime_mean` + 3 fitur pendukung
(`age_ratio_to_last_confirmed_failure`, `log_last_confirmed_failure_lifetime`,
`has_lifecycle_shortening_estimate`). Rolling backtest 6-fold identik E-55/
E-44 (`cli.py::_rolling_fold_windows`/`_assign_rolling_split`, evaluasi
`gate` lifecycle-based).

**Hasil** Cakupan cuma **9,29%** (butuh PART dengan >=1 cycle KONFIRMASI-
gagal SEBELUM cycle sekarang - jarang, mirip masalah cakupan E-52).

| Target | Baseline (6 fold) | Kandidat (6 fold) |
|---|---|---|
| 0,30 | presisi=0,3043+/-0,2605 recall=0,0386+/-0,0320 | presisi=0,2614+/-0,2777 recall=0,0421+/-0,0515 |
| 0,40 | presisi=0,4306+/-0,4312 (4/6 feasible) recall=0,0055+/-0,0041 | presisi=0,3194+/-0,2810 (6/6 feasible) recall=0,0096+/-0,0136 |

Selisih recall di kedua target JAUH lebih kecil dari SD masing-masing -
sama seperti E-55, tidak melewati kriteria "signifikan" (E-44: selisih
rata-rata harus melebihi 1 SD selisih per-fold). Presisi malah cenderung
turun.

**Keputusan** **DITOLAK** - cakupan terlalu tipis DAN tidak lolos rolling
backtest. Tidak ditambahkan ke `config.FEATURE_COLUMNS`.

---

## E-63 · FASE 8 Langkah I1: threshold adaptif per cohort umur - sinyal lemah tersembunyi ditemukan di cohort NORMAL, tapi belum tervalidasi (satu TEST split)

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah, TIDAK retrain (skor v4 apa adanya)

**Pertanyaan** Setelah §12 (docs/DECISIONS.md) menutup pencarian fitur/
model, user mengusulkan dua ide baru: (1) ranking-loss objective, (2)
threshold adaptif per subgroup umur (bukan satu threshold global) -
memakai analisis cohort E-58 yang sudah ada. Apakah threshold TERPISAH per
cohort umur (dicari sendiri-sendiri di VALIDATION, target presisi sama)
membuka recall yang selama ini tertutup oleh SATU threshold global yang
didominasi kebutuhan cohort EARLY?

**Metode** Beda dari E-58 (cohort umur SAAT GAGAL, retrospektif) - cohort
di sini didefinisikan dari `days_since_installation` SAAT OBSERVASI
(prospektif, satu-satunya yang diketahui saat serving sungguhan). TIDAK
retrain - pakai skor v4 production apa adanya. `gate.select_lifecycle_
threshold()` dijalankan TERPISAH per cohort (EARLY<=90d, NORMAL 91-730d,
LATE>730d) pada subset VALIDATION masing-masing, target presisi 0,30.
Threshold hasil pencarian per cohort diterapkan sebagai array PER-BARIS
(fungsi baru `lifecycle_metrics_variable_threshold()`, generalisasi
`gate.lifecycle_metrics()` untuk threshold yang boleh beda per baris) ke
TEST, dibandingkan dengan satu threshold global (E-49 baseline).

**Hasil**

Threshold per cohort di VALIDATION, target 0,30 (SEMUA cohort ternyata
feasible - beda dari kesan E-58 yang pakai definisi retrospektif):

| Cohort | Baris VAL | Lifecycle rusak VAL | Threshold | VAL presisi/recall/alert |
|---|---:|---:|---:|---|
| EARLY | 7.652 | 314 | 0,3165 | 0,3333/0,1433/135 |
| NORMAL | 22.310 | 291 | **0,0876** | 0,3077/0,0412/39 |
| LATE | 19.698 | 342 | **0,0796** | 0,3333/0,0234/24 |

Threshold global (E-49) = 0,3165 - PERSIS sama dengan threshold EARLY
sendiri (EARLY mendominasi karena butuh threshold TERTINGGI untuk capai
presisi 0,30). NORMAL/LATE punya threshold JAUH lebih rendah yang tetap
memenuhi presisi 0,30 DI POPULASI MEREKA SENDIRI - sinyal ini
tersembunyi/tertutup total oleh threshold global.

TEST, gabungan vs global:

| | TEST presisi | TEST recall | TEST alert | TP/FP/FN |
|---|---:|---:|---:|---|
| GLOBAL (baseline) | 0,5289 | 0,0710 | 121 | 64/57/838 |
| PER-COHORT | 0,5077 | 0,0732 | 130 | 66/64/836 |

Rincian TEST per cohort (threshold masing-masing dari VALIDATION):

| Cohort | Lifecycle rusak TEST | TEST presisi | TEST recall | TEST alert |
|---|---:|---:|---:|---:|
| EARLY | 372 | 0,5289 | 0,1720 | 121 |
| NORMAL | 168 | 0,2222 | **0,0119** | 9 |
| LATE | 362 | 0,0000 | 0,0000 | 1 |

**Temuan** NORMAL cohort **menangkap 2 lifecycle yang SEBELUMNYA (E-58,
threshold global) SELALU 0%** - sinyal lemah TAPI NYATA tersembunyi di
bawah threshold global yang terlalu tinggi untuk populasi ini. LATE
cohort TIDAK ikut tergeneralisasi ke TEST (VALIDATION menjanjikan
presisi 0,3333, TEST-nya 1 alert dan presisi 0) - pola PERSIS SAMA dengan
E-55/E-59 (menang di VALIDATION/satu split, tidak bertahan). Gabungan
keduanya: recall total naik tipis (+3%) tapi presisi turun tipis (-4%) -
bukan kemenangan bersih, dan **BELUM divalidasi rolling backtest** -
sangat mungkin recall NORMAL yang +2 lifecycle ini JUGA artefak satu
split mengingat riwayat sesi ini (E-55, E-59 kandidat serupa besarnya
runtuh di rolling backtest).

**Keputusan** **BELUM TERBUKTI, DITUTUP** - hasil sesuai perkiraan awal
user (Sedang, tidak menutup blind spot struktural, cuma margin kecil).
User memutuskan TIDAK melanjutkan ke rolling backtest (walau biayanya
murah - tidak perlu retrain, cuma re-run gate search per fold) mengingat
pola sesi ini (kemenangan kecil di satu split berulang kali tidak
bertahan - E-55, E-59). TIDAK diimplementasikan ke production. Kalau
ingin dibuka lagi nanti: jalur tercepat adalah rolling backtest
threshold-per-cohort (murah, `v4` apa adanya, cuma gate search per fold)
sebelum implementasi apa pun.

---

## E-64 · FASE 8 Langkah I2: ranking-loss objective (YetiRank) - DITOLAK, jauh lebih buruk dari Logloss di sanity check satu split

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** Apakah CatBoost dengan `loss_function="YetiRank"` (belajar
mengurutkan PART DI DALAM grup, bukan memprediksi probabilitas per baris
independen) memperbaiki skor tail yang lemah (E-56: median skor FN cuma
0,05) dibanding Logloss (production)?

**Metode** Sanity check CEPAT (satu split TEST RESMI production, BUKAN
rolling backtest - baru lanjut ke situ kalau menjanjikan). `CatBoostRanker`,
grup = bulan `observation_on` (132 grup unik di TRAIN), fitur/hyperparameter
lain identik production (minus `auto_class_weights`, tidak berlaku untuk
ranking). Kalibrasi isotonic di VALIDATION seperti biasa (skor mentah
ranking bukan probabilitas, tapi isotonic tidak butuh itu - cukup
monoton). Evaluasi row-level (ROC-AUC/PR-AUC) dan lifecycle (E-49).

**Hasil**

| | TEST ROC-AUC | TEST PR-AUC |
|---|---:|---:|
| YetiRank | 0,7633 | 0,0600 |
| Baseline v4 (Logloss) | 0,8319 | 0,1961 |

Lifecycle-level: **INFEASIBLE di SEMUA target presisi (0,30/0,40/0,85)**
- presisi maksimum yang bisa dicapai di VALIDATION cuma 0,1481, jauh di
bawah target terendah yang diuji (0,30).

**Analisis** Kalah telak dan jelas, bukan trade-off tipis - tidak perlu
rolling backtest untuk memastikan (beda dari E-54/E-55 yang butuh rolling
karena hasilnya tipis/ambigu). Kemungkinan penyebab: definisi grup per
bulan mencampur PART di titik siklus yang SANGAT berbeda (baru pertama
diobservasi vs sudah berbulan-bulan berjalan) dalam satu grup ranking, dan
label positif (`target_failure`) sangat jarang per grup (base rate
row-level ~1,7%, E-50) - sinyal pairwise/listwise per grup jadi terlalu
tipis untuk dipelajari. Definisi grup ALTERNATIF (per item_model_code,
per installation_age_band) belum dicoba dan mungkin beda hasilnya - tapi
di luar cakupan sesi ini.

**Keputusan** **DITOLAK** untuk definisi grup yang diuji. Tidak
dilanjutkan ke rolling backtest (kalah terlalu jelas untuk dipertanyakan
validitasnya) maupun definisi grup alternatif (keputusan user - fokus
dialihkan, bukan ditolak permanen kalau ada waktu di sesi mendatang).

---

## E-65 · FASE 8 Langkah J1: hyperparameter CatBoost arah lebih dangkal/regularisasi kuat - menutup pertanyaan terbuka E-34, TETAP DITOLAK

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** User bertanya langsung "apakah tidak ada cara lagi untuk
menaikkan PR-AUC?". E-34 (2026-08-22) sudah menguji grid CatBoost tapi
HANYA ke arah lebih kompleks (depth 5-6, iterations 300-400, l2 12-15) -
semua kalah, dan catatannya eksplisit: "grid ke arah sebaliknya - lebih
dangkal/regularisasi lebih kuat - belum dicoba". Mengingat seluruh sesi
Fase 8 menemukan ekor skor yang sangat tipis (E-47) dan variansi antar-
fold yang besar (E-54 update dst.) - apakah model LEBIH SEDERHANA
(kurang rentan overfit ke noise sampel kecil) menaikkan PR-AUC yang
genuinely generalize ke TEST?

**Metode** 7 kandidat kombinasi depth (2-4) x l2_leaf_reg (10-40) x
iterations (100-200) SEMUA ke arah lebih dangkal/regularisasi lebih kuat
dari baseline, dipilih via VAL PR-AUC (metodologi PERSIS E-34), pada split
resmi production (data terbaru, bukan snapshot 2026-08-22). Kandidat VAL
PR-AUC tertinggi diuji lebih lanjut lewat gerbang lifecycle (E-49).

**Hasil**

| Kandidat | VAL PR-AUC | TEST PR-AUC |
|---|---:|---:|
| baseline (depth=4 iter=200 l2=10) | 0,1116 | 0,1961 |
| depth=3 l2=10 | 0,1126 | 0,1763 |
| depth=2 l2=10 | 0,1026 | 0,1789 |
| depth=4 l2=20 | 0,1092 | 0,1775 |
| **depth=4 l2=40** | **0,1132** | 0,1903 |
| depth=3 l2=20 | 0,1108 | 0,1770 |
| depth=3 l2=20 iter=100 | 0,0984 | 0,1789 |
| depth=2 l2=30 iter=100 | 0,0898 | 0,1696 |

VALIDATION PR-AUC baseline (0,1116) cocok PERSIS dengan angka E-34
2026-08-22 (sanity check metodologi identik). **SEMUA 7 kandidat kalah
TEST PR-AUC dari baseline** (0,1961) - termasuk kandidat dengan VAL
PR-AUC tertinggi (depth=4 l2=40, TEST cuma 0,1903). Gerbang lifecycle
untuk kandidat terbaik itu: target 0,30 TEST presisi=0,5133/recall=0,0643/
alert=113 (baseline 0,5289/0,0710/121 - sedikit lebih buruk); target 0,40
TEST presisi=0,6429/recall=0,0100/alert=**14** (baseline 0,6250/0,0055/8 -
sedikit lebih baik, tapi cuma 14 alert, terlalu sedikit untuk dipercaya
mengingat pelajaran variansi antar-fold sepanjang sesi ini).

**Temuan** Arah regularisasi-lebih-kuat yang ditandai "belum dicoba" di
E-34 TERNYATA juga tidak membantu - menutup pertanyaan terbuka itu dengan
jawaban negatif yang jelas, bukan cuma dugaan. Konsisten dengan pola
seluruh sesi: VALIDATION PR-AUC tidak reliable memprediksi TEST PR-AUC
pada skala data/base-rate ini (kandidat VAL PR-AUC tertinggi BUKAN
kandidat TEST PR-AUC tertinggi) - gejala yang sama dengan `docs/
DECISIONS.md §10` (v3-vs-v4). Hyperparameter CatBoost (baik arah lebih
kompleks maupun lebih sederhana) BUKAN lever yang tersisa untuk PR-AUC.

**Keputusan** **DITOLAK** - konfigurasi production TIDAK diubah. Dengan
ini, pertanyaan terbuka E-34 resmi tertutup: grid hyperparameter CatBoost
(kedua arah) sudah dicoba menyeluruh dan tidak ada yang mengalahkan
konfigurasi sekarang di TEST. Menguatkan kesimpulan E-62/§12: bottleneck
PR-AUC bukan soal tuning model, melainkan keterbatasan sinyal yang
tersedia di data.

---

## E-66 · FASE 8 Langkah K1/K3: MTBF pada window 2025+ TERBATAS - kemenangan NYATA dan STABIL lintas 3 fold, temuan positif TERKUAT sepanjang sesi

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** §12 (docs/DECISIONS.md) menutup pencarian fitur/model tapi
menyisakan SATU jalur belum tervalidasi: MTBF pada window 2025+ TERBATAS
(E-48 sudah menunjukkan `journal.t_mtbf` cuma berisi sejak 2025-01-15,
skema training lama TIDAK BISA mengujinya karena TRAIN 2014-2024 cakupan
0%). User meminta ini dicoba serius - membangun skema TRAIN/VALIDATION/
TEST yang SELURUHNYA di dalam window 2025-01-15 s/d data_end supaya
CatBoost benar-benar BISA belajar dari fitur MTBF selama training, bukan
cuma melihatnya kosong sepanjang TRAIN seperti E-48.

**Metode** `assign_restricted_split()`: TRAIN/VALIDATION/TEST semua di
dalam `[2025-01-15, data_end]` (fold utama: VAL mulai 2025-10-01, TEST
mulai 2026-01-01 - TEST-nya SAMA PERSIS dengan window TEST production,
jadi bisa dibandingkan). Fitur MTBF (3 kolom, POINT-IN-TIME SAFE via
searchsorted per item - HANYA baca `t_mtbf` dengan `created_on <=
observation_on`) dan QC (3 kolom, POINT-IN-TIME SAFE - HANYA `t_item_
quality_control` dengan `created_on <= observation_on`, BUKAN QC dari
kejadian yang sedang diprediksi) dipasangkan lewat pemetaan `sn_ref ->
item_pairing_code` (`inventory.t_item`, pola sama dengan E-48).
Perbandingan: baseline (fitur production v4 SAJA, dilatih ULANG pada
window terbatas - supaya adil, bukan model v4 asli yang dilatih di
12 tahun data) vs +MTBF vs +QC vs +MTBF+QC. Karena window terbatas cuma
~19 bulan (tidak muat 6-fold seperti E-44/E-49), stabilitas dicek lewat
**3 fold** dengan boundary VALIDATION/TEST berbeda (Langkah K3), bukan
satu split saja - pelajaran wajib sejak E-55.

**Hasil - atribusi (fold utama, TEST = window production Jan-Ags 2026)**

| Varian | Cakupan | TEST ROC-AUC | TEST PR-AUC |
|---|---:|---:|---:|
| baseline (window terbatas, TANPA MTBF/QC) | - | 0,8247 | 0,1251 |
| **+MTBF saja** | 17,46% | **0,8639** | **0,1771** |
| +QC saja | 4,04% | 0,8260 | 0,1540 |
| +MTBF+QC | - | 0,8513 | 0,1540 |

MTBF SENDIRIAN mengalahkan bahkan gabungan MTBF+QC - QC (cakupan cuma
4%) mengencerkan sinyal MTBF saat digabung, bukan menambah.

**Hasil - stabilitas 3 fold (baseline vs +MTBF SAJA, window boundary beda-beda)**

| Fold | TEST window | Baseline ROC-AUC/PR-AUC | +MTBF ROC-AUC/PR-AUC |
|---|---|---|---|
| 1 | mulai 2025-11-01 (1.089 kerusakan) | 0,8028 / 0,1112 | **0,8392** / **0,1307** |
| 2 | mulai 2026-01-01 (902 kerusakan) | 0,8245 / 0,1250 | **0,8638** / **0,1772** |
| 3 | mulai 2026-03-01 (748 kerusakan) | 0,8299 / 0,1631 | **0,8856** / **0,2000** |

**+MTBF menang di SEMUA 3 fold, di KEDUA metrik, dengan margin substansial
dan KONSISTEN** (ROC-AUC +0,036 s/d +0,056; PR-AUC +0,020 s/d +0,052) -
BEDA TOTAL dari pola seluruh sesi sebelumnya (E-55/E-59/E-54: menang di
satu fold, hilang/terbalik di fold lain). Ini SATU-SATUNYA kandidat Fase
8 yang lolos uji multi-fold dengan arah dan besaran yang konsisten.

Lifecycle-level (target presisi 0,30), fold utama: +MTBF presisi=0,4097
recall=0,0654 alert=144 vs baseline presisi=0,3519 recall=0,0211 alert=54
- MTBF menang presisi DAN recall (recall 3x lipat) sekaligus.

**Catatan penting - INI BUKAN "lebih baik dari v4 production" begitu
saja**: baseline window-terbatas (TANPA MTBF) SUDAH lebih lemah dari v4
asli (ROC-AUC 0,8247 vs 0,8319; PR-AUC 0,1251 vs 0,1961 - wajar, TRAIN
cuma 33.355 baris vs 251.568 baris v4) karena kehilangan 12 tahun histori.
+MTBF menutup SEBAGIAN JARAK itu (ROC-AUC 0,8639 malah MELEWATI v4 0,8319,
tapi PR-AUC 0,1771 masih 10% di bawah v4 0,1961) - sinyal MTBF per-baris
genuinely kuat, tapi volume TRAIN yang jauh lebih kecil (window terbatas
2025+) sebagian mengimbangi keuntungannya pada PR-AUC. Trade-off ini
SEHARUSNYA mengecil seiring waktu (window 2025+ terus bertambah data
setiap bulan berjalan), tapi PER HARI INI belum otomatis "menang total"
melawan v4 - butuh dipantau, bukan langsung dipromosikan.

**Keputusan** **KANDIDAT PALING KUAT sepanjang Fase 8 - BELUM dipromosikan
ke production** (butuh keputusan arsitektur: model TERPISAH untuk window
2025+ vs v4 penuh-histori, bukan sekadar ganti fitur). Dilaporkan ke user
sebagai temuan utama sesi ini untuk diputuskan jalur produksinya.

---

**Update 2026-08-30 - re-check stabilitas dengan `train_mtbf_candidate.py`
(E-68) pada data terbaru: HANYA 1 dari 3 fold menang, TIDAK sekonsisten
klaim awal.** Dipicu diskusi dengan user soal status kandidat ini setelah
`models/failure/CURRENT` ternyata sudah `v6` (bukan v4 lagi - lihat catatan
di §7 docs/DECISIONS.md dan E-78 soal ini). Satu run cepat via
`python -m partrisk.cli train-mtbf-candidate` (window default, TEST>=
2026-04-29) awalnya terlihat sangat meyakinkan - kandidat menang ROC-AUC
DAN PR-AUC vs model production saat ini (v6): 0,9011/0,3539 vs 0,8636/0,3232.
TAPI sesuai pelajaran E-55/E-59/E-63/E-67/E-72/E-74 - satu window TIDAK
CUKUP - dicek ulang 2 fold TAMBAHAN (skrip ad-hoc, dihapus setelah
dipakai) dengan boundary mundur ~60 dan ~120 hari:

| Fold | TEST mulai | Kandidat ROC-AUC/PR-AUC | v6 (production sekarang) ROC-AUC/PR-AUC | Menang? |
|---|---|---|---|---|
| A (default) | 2026-04-29 | 0,9011 / 0,3539 | 0,8636 / 0,3232 | **YA** |
| B (~60 hari mundur) | 2026-02-28 | 0,8831 / 0,2201 | 0,8627 / **0,2769** | TIDAK (PR-AUC kalah) |
| C (~120 hari mundur) | 2025-12-30 | 0,8633 / 0,1712 | 0,8496 / **0,2108** | TIDAK (PR-AUC kalah) |

Di gerbang presisi lifecycle (E-49) fold C, v6 malah menang jelas di
KEDUA metrik (presisi 0,5684/recall 0,0480 vs kandidat 0,4138/0,0320 pada
target 0,30). **Hasil 1/3 fold ini TIDAK sekonsisten dengan klaim awal
E-66 ("+MTBF menang di SEMUA 3 fold") - kemenangan yang terlihat di
fold A adalah pola SATU-FOLD yang sama seperti E-55/E-59/E-63/E-67/E-72/
E-74, BUKAN properti stabil kandidat ini seperti diklaim sebelumnya.**
Kemungkinan penyebab (belum diverifikasi lebih lanjut): (1) baseline
pembanding sekarang `v6` bukan `v4` asli E-66 (walau E-78 menunjukkan
v6 secara substansi setara v4 di VALIDATION, jadi kemungkinan bukan
penyebab utama), (2) window TEST fold B/C tumpang tindih dengan periode
berbeda dari 3 fold ASLI E-66 (bukan replikasi persis, boundary dipilih
independen) - MTBF signal mungkin genuinely bervariasi per periode/musim,
bukan keunggulan universal.

**Keputusan (revisi)**: klaim "stabil lintas 3 fold" di atas TIDAK LAGI
berlaku sebagai dasar kepercayaan - status kandidat kembali ke "BELUM
tervalidasi cukup untuk promosi", bahkan lebih hati-hati dari sebelumnya
(dulu 3/3 menang meyakinkan, sekarang re-check independen cuma 1/3).
`models/failure_mtbf_2025plus/v2` (hasil fold A) tetap tersimpan sebagai
riwayat pemantauan, TIDAK dipromosikan. Rekomendasi: jangan ambil
keputusan arsitektur (model terpisah vs status quo) dari SATU angka
run manapun ke depan - kalau mau menilai ulang, ulangi cek multi-fold
seperti ini (bukan `train-mtbf-candidate` sekali jalan) tiap kali.

**Update 2026-08-30 (lanjutan) - ablasi tersegmentasi: sinyal MTBF ITU
SENDIRI stabil dan konsisten, terkonsentrasi di populasi first-failure -
tapi TIDAK ADA jalur tersedia sekarang untuk memanfaatkannya di model
production.** Dipicu pertanyaan user: apakah keunggulan MTBF terkonsentrasi
di populasi first-failure (blind spot terbesar, E-56/E-58, 37% kegagalan
TEST terlewat) atau merata? Diuji baseline (window 2025+ TANPA MTBF) vs
+MTBF, TRAIN/VALIDATION/TEST SAMA PERSIS (mengisolasi efek fitur MTBF
sendiri, TIDAK tercampur efek ukuran TRAIN seperti perbandingan vs
production di atas), 3 fold yang sama, PR-AUC dipecah per segmen
`has_prior_failure`:

| Fold | first-failure (tanpa riwayat) | punya riwayat kegagalan |
|---|---:|---:|
| A | +51% (0,2209->0,3346) | +24% (0,3414->0,4233) |
| B | +31% (0,1402->0,1832) | +17% (0,2439->0,2846) |
| C | +39% (0,1006->0,1393) | +12% (0,2004->0,2236) |

**+MTBF menang di KEDUA segmen di SEMUA 3 fold, DAN kenaikan first-failure
konsisten 2-3x lipat lebih besar dari kenaikan populasi-punya-riwayat** -
beda dari perbandingan "vs v6 production" (1/3 fold, TIDAK stabil), ablasi
within-window ini STABIL PENUH 3/3 fold di kedua segmen. Kesimpulan:
sinyal MTBF genuinely berguna dan genuinely terkonsentrasi di blind spot
first-failure seperti dihipotesiskan - bukan klaim yang gagal.

**TAPI**: ditelusuri jalur untuk memakainya di model production PENUH
(12 tahun histori, bukan window 2025+ terbatas) - **terhalang batas
kalender struktural, bukan kekurangan usaha**. `validation_start` (batas
atas TRAIN) = 1 Januari (tahun `data_end` - 1); selama `data_end` masih di
tahun 2026, TRAIN production TIDAK PERNAH bisa melewati awal Desember
2024 (mundur horizon 30 hari dari 1 Jan 2025) - sementara `journal.t_mtbf`
baru mulai 15 Januari 2025. TRAIN akan **SELALU 0% tercakup MTBF** sampai
`data_end` masuk tahun 2027 (baru `validation_start` bergeser ke 1 Jan
2026, mencakup sebagian 2025) - ini PERSIS penyebab E-48 gagal total
(ROC-AUC/PR-AUC identik 4 desimal, model tidak belajar apa-apa), dan
penyebabnya BELUM berubah dan TIDAK BISA dipercepat selain menunggu
kalender berjalan. Direncanakan mengulang percobaan model-penuh+MTBF di
sesi ini, DIBATALKAN setelah kendala ini disadari sebelum eksekusi -
mengulang E-48 sekarang dijamin memberi hasil identik (TRAIN 0% tercakup),
bukan sinyal baru.

Ditinjau ulang seluruh jalur yang PERNAH dicoba untuk menggabungkan MTBF
ke production: **fitur langsung ke model penuh (E-48, gagal - TRAIN 0%
tercakup), model terpisah window 2025+ (E-66/di atas, tidak stabil vs
production), cascade dua tahap (E-67, ditolak), ensemble/blend skor
(E-70, ditolak), geser batas TRAIN/VALIDATION/TEST supaya TRAIN lebih
dekat sekarang (E-76, ditolak - split saat ini tetap terbaik).** Ruang
kemungkinan yang masuk akal untuk sesi-sesi sejauh ini SUDAH HABIS
dicoba - tidak ada jalur baru yang belum diuji selain menunggu kalender.

**Keputusan akhir topik MTBF (sesi ini)**: sinyal MTBF valid dan berguna,
TAPI tidak actionable untuk production sampai salah satu dari dua hal
terjadi - (1) `data_end` masuk tahun 2027 (pemantauan pasif, ulangi E-48
saat itu), atau (2) `models/failure_mtbf_2025plus` (window 2025+) suatu
saat mengalahkan production konsisten multi-fold (belum terjadi, 1/3 di
re-check terakhir). Tidak ada tindakan lebih lanjut yang produktif
dikerjakan sekarang - ditutup, revisit di salah satu dari dua kondisi
di atas.

---

## E-67 · FASE 8 Langkah K2: two-stage cascade (jaring lebar + saring tajam) - campuran, tidak menang bersih tapi kompetitif di target tinggi

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** Kalau satu model tidak bisa presisi DAN recall sekaligus,
apakah cascade dua tahap - stage 1 (model jaring lebar, threshold rendah)
menyaring kandidat, stage 2 (model BARU dilatih HANYA pada populasi
kandidat stage 1) menyaring lebih tajam di dalam populasi yang sudah
"kelihatan berisiko" - membantu?

**Metode** Stage 1 = CatBoost identik production, kandidat = top 5%
TRAIN by skor mentah (12.580/251.568 baris, 1.381 positif). Stage 2 =
CatBoost BARU, dilatih HANYA pada kandidat stage 1 TRAIN. Skor akhir =
skor stage 2 untuk baris yang lolos kandidat stage 1, else 0. Split resmi
production, evaluasi row-level + lifecycle (E-49).

**Hasil** Row-level GLOBAL jauh lebih buruk (ROC-AUC 0,6795 vs baseline
0,8319, PR-AUC 0,1481 vs 0,1961) - **tapi ini SEBAGIAN BESAR karena
desain cascade sendiri** (baris di luar kandidat stage 1 semua diberi
skor 0, merusak ranking global yang dievaluasi ROC-AUC/PR-AUC atas
SELURUH populasi - bukan cerminan performa di titik operasi gerbang).
Lifecycle-level (E-49) lebih relevan:

| Target | Cascade TEST presisi/recall/alert | Baseline TEST presisi/recall/alert |
|---:|---|---|
| 0,30 | 0,5159 / 0,0721 / 126 | 0,5289 / 0,0710 / 121 |
| **0,40** | **0,6538 / 0,0188 / 26** | 0,6250 / 0,0055 / 8 |
| 0,50-0,85 | **1,0000 / 0,0033 / 3** (feasible!) | INFEASIBLE (degenerate) |

Target 0,30: nyaris seri (cascade recall sedikit lebih tinggi, presisi
sedikit lebih rendah). Target 0,40: cascade menang presisi DAN recall
(recall 3,4x). Target 0,50-0,85: cascade justru FEASIBLE (3 alert presisi
sempurna) di titik yang bagi baseline selalu degenerate/kosong - stage 2
yang dilatih di populasi SEMPIT tampaknya memberi kalibrator resolusi
lebih baik di ekor skor tinggi, persis masalah yang didiagnosis E-47.

**Keputusan (awal)** ~~BELUM cukup meyakinkan, menjanjikan~~ - **DITOLAK,
lihat update rolling backtest di bawah.**

**Update 2026-08-26 (Langkah L1) - rolling backtest 6-fold**: metodologi
identik E-54/E-55/E-59/E-60/E-61 (fold logic E-44, evaluasi lifecycle
E-49). Hasil per fold (target 0,30):

| Fold | TEST window | Baseline presisi/recall/alert | Cascade presisi/recall/alert |
|---|---|---|---|
| 1 | 2025-08-08 s/d 2025-10-07 | 0,2297/0,0837/74 | 0,1951/0,0394/41 |
| 2 | 2025-10-07 s/d 2025-12-06 | 0,3333/0,0599/30 | 0,1333/0,0120/15 |
| 3 | 2025-12-06 s/d 2026-02-04 | 0,0000/0,0000/3 | 0,0000/0,0000/3 |
| 4 | 2026-02-04 s/d 2026-04-05 | 0,2800/0,0422/25 | 0,5000/0,0361/12 |
| 5 | 2026-04-05 s/d 2026-06-04 | 0,2000/0,0050/5 | 0,0000/0,0000/1 |
| 6 (=TEST asli E-67) | 2026-06-04 s/d 2026-08-03 | 0,7826/0,0407/23 | **0,8696/0,0905/46** |

Ringkasan 6 fold: target 0,30 - baseline recall=0,0386+/-0,0320; cascade
recall=0,0297+/-0,0344 (LEBIH BURUK rata-rata). Target 0,40: baseline
recall=0,0055+/-0,0041; cascade recall=0,0029+/-0,0048 (LEBIH BURUK).
Selisih di kedua target TIDAK melebihi 1 SD (kriteria E-44) - tidak
signifikan, DAN arahnya NEGATIF (bukan cuma "tidak terbukti", tapi
cenderung kalah).

**Pola PERSIS E-55/E-59**: fold 6 - kebetulan window yang SAMA dengan
TEST asli yang dipakai mengukur "kompetitif di target tinggi" sebelumnya
- HANYA di fold itu cascade menang jelas. Di 4 dari 5 fold lain, cascade
performanya sama atau lebih buruk dari baseline (fold 5 bahkan kolaps ke
0-1 alert). Kesimpulan "kompetitif di target 0,40+" yang dilaporkan
sebelumnya adalah artefak SATU fold, bukan properti umum cascade ini.

**Keputusan final** **DITOLAK** - two-stage cascade (stage 1 jaring lebar
top-5%, stage 2 dilatih di kandidat itu saja) tidak dipakai. Dengan ini,
E-66 (MTBF) TETAP SATU-SATUNYA kandidat Fase 8 yang lolos validasi
multi-fold - bukan kebetulan bahwa satu-satunya yang bertahan adalah
yang berbasis SINYAL BARU (data yang belum pernah dipakai sebelumnya),
bukan rekayasa ulang model/fitur dari data yang sudah ada.

---

## E-68 · FASE 8: `train-mtbf-candidate` - infrastruktur pemantauan permanen untuk kandidat E-66

2026-08-26 · `engines/failure/train_mtbf_candidate.py` (baru), `cli.py::train-mtbf-candidate` (baru)

**Konteks** E-66 menemukan MTBF (window 2025+ terbatas) menang konsisten
di 3 fold, TAPI PR-AUC-nya masih di bawah v4 karena TRAIN jauh lebih
kecil - jaraknya diperkirakan mengecil seiring window 2025+ bertambah
data tiap bulan. User memutuskan: JANGAN promosikan sekarang, JANGAN
terus mencari fitur baru - bangun dulu tooling yang bisa dijalankan
ULANG secara berkala untuk memantau kapan kandidat ini benar-benar
melampaui v4.

**Yang dibangun** Modul BARU, TERPISAH dari `train.py` production -
artifact-nya (`models/failure_mtbf_2025plus/`) TIDAK PERNAH dibaca
`predict.py`/serving apa pun, jadi tidak ada risiko ke production hanya
dengan menjalankannya. Reuse langsung `training_failure.train_model()`
(training/kalibrasi) dan `gate.py` (evaluasi lifecycle E-49) - TIDAK
menduplikasi logic, cuma skema dataset (window `[2025-01-15, data_end]`,
TEST = 120 hari terakhir, VALIDATION = 90 hari sebelum itu, TRAIN sisanya
- window BERGULIR mengikuti `data_end`, jadi TRAIN otomatis tumbuh tiap
kali dijalankan ulang di bulan berikutnya) dan fitur MTBF (point-in-time
safe, pola sama dengan E-48/E-66). Tiap run: melatih kandidat, membandingkan
row-level (ROC-AUC/PR-AUC) DAN lifecycle-level (gate E-49, target 0,30/0,40)
langsung terhadap v4 pada POPULASI TEST YANG SAMA, menyimpan versi baru
(`v1`, `v2`, ...) dengan metadata lengkap termasuk hasil perbandingan,
mencetak verdict jelas "SUDAH melampaui v4" atau "BELUM, lanjut pantau".

**Hasil run pertama (v1, TEST = 2026-04-05 s/d 2026-08-03, window PALING
BARU yang tersedia)**:

| | ROC-AUC | PR-AUC |
|---|---:|---:|
| v4 (pada window TEST yang sama) | 0,8481 | **0,2855** |
| kandidat +MTBF | **0,8764** | 0,2281 |

**Verdict: BELUM** - kandidat menang ROC-AUC tapi KALAH PR-AUC di window
PALING BARU ini - beda dari E-66 (menang KEDUA metrik di 3 fold yang
diuji sebelumnya, semua window lebih lama/lebih besar). Gerbang lifecycle
juga tipis di kedua sisi (VALIDATION cuma 159 kerusakan/11.482 baris -
lebih kecil dari fold E-66, sehingga lebih berisik): target 0,30/0,40
kandidat presisi=0,2500 recall=0,0016 alert=4 vs v4 presisi=0,7500
recall=0,0047 alert=4 - v4 lebih baik di run spesifik ini.

**Analisis** Ini BUKAN kontradiksi terhadap E-66 - ini justru
mendemonstrasikan PERSIS kenapa tooling pemantauan (bukan keputusan
sekali jalan) diperlukan: window TEST paling baru (Apr-Ags 2026) belum
tentu berperilaku sama dengan window yang diuji E-66 (Nov 2025-Mar 2026).
Variansi antar-window sudah terbukti besar sepanjang sesi Fase 8 - satu
run TIDAK cukup untuk klaim menang ATAU kalah. Verdict yang benar: "belum
terbukti unggul KONSISTEN," bukan "gagal" atau "berhasil."

**Keputusan** Tooling **DIPERTAHANKAN sebagai infrastruktur permanen**,
TIDAK ada kode production yang diubah/dipengaruhi. Rencana: jalankan
`python -m partrisk.cli train-mtbf-candidate` secara berkala (mis.
bulanan) seiring window 2025+ terus bertambah data - promosi manual
HANYA dipertimbangkan kalau kandidat mengalahkan v4 di KEDUA metrik
row-level SECARA KONSISTEN lintas beberapa run berturut-turut, bukan
sekali menang.

---

## E-69 · FASE 8 Langkah M1/M2: sweep hyperparameter kandidat MTBF - TIDAK ada yang mengalahkan default secara konsisten

2026-08-26 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** User bertanya langsung: bisakah PR-AUC kandidat MTBF
dinaikkan lewat tuning hyperparameter? E-34/E-65 sudah menyimpulkan
tuning tidak membantu untuk v4 (dataset PENUH, 251rb baris) - tapi
dataset kandidat MTBF jauh lebih kecil (33-48rb baris), jadi profil
regularisasi optimal BISA beda.

**Metode** Langkah M1: 12 kandidat (kombinasi depth/l2_leaf_reg/
iterations/learning_rate) dilatih pada dataset `train_mtbf_candidate.
build_dataset()` (reuse penuh, tidak membangun ulang query/fitur MTBF),
dipilih via VAL PR-AUC (metodologi E-34/E-65). Langkah M2: kandidat
dengan TEST PR-AUC tertinggi di M1 (`iterations=100, learning_rate=0,06`)
diuji ULANG lintas 3 fold yang SAMA dengan validasi E-66 - wajib
mengingat pola sesi ini (kandidat yang bagus di satu window sering tidak
bertahan).

**Hasil M1** (satu window, TEST mulai 2026-04-05): kandidat VAL PR-AUC
tertinggi TETAP konfigurasi default (0,0914) - sama seperti E-65, tidak
ada yang mengalahkannya di VALIDATION. Tapi beberapa kandidat LAIN punya
TEST PR-AUC lebih tinggi dari default (0,2281): `iter=100 lr=0,06`
mencapai **0,2530** - kesenjangan VAL-vs-TEST yang sama seperti
ditemukan berulang kali sepanjang sesi (VALIDATION terlalu kecil,
159 kerusakan, untuk memilih dengan andal).

**Hasil M2 - 3 fold, default vs kandidat TEST-PR-AUC-tertinggi M1**:

| Fold | Default TEST PR-AUC | Tuned (iter=100 lr=0,06) TEST PR-AUC |
|---|---:|---:|
| 1 | 0,1307 | 0,1264 (lebih buruk) |
| 2 | 0,1772 | 0,1562 (lebih buruk) |
| 3 | 0,2000 | 0,1985 (lebih buruk) |

**Kandidat yang tampak terbaik di M1 (satu window) KALAH di SEMUA 3 fold**
begitu diuji ulang - pola PERSIS sama dengan E-55/E-59/E-67. Gerbang
lifecycle juga memburuk (fold 1: default masih feasible 197 alert,
tuned jadi INFEASIBLE).

**Keputusan** **DITOLAK** - hyperparameter default (identik v4/production)
TETAP dipakai untuk kandidat MTBF, tidak ada perubahan pada `train_mtbf_
candidate.py`. Menjawab pertanyaan user secara definitif: TIDAK, tuning
hyperparameter tidak menaikkan PR-AUC kandidat MTBF secara tervalidasi -
sama seperti kesimpulan E-34/E-65 untuk v4. Jarak PR-AUC ke v4 (E-66:
~10% di bawah pada fold-fold yang diuji) hanya akan mengecil lewat
BERTAMBAHNYA data window 2025+ dari waktu ke waktu, bukan lewat tuning -
sesuai rencana pemantauan berkala E-68.

---

## E-70 · Ensemble sederhana v4 + kandidat MTBF (blend probabilitas, alpha di VALIDATION) - DITOLAK, tidak konsisten lintas fold

2026-08-27 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** Satu sudut yang belum pernah dicoba dari seluruh Fase 8:
bukan cascade (E-67, sudah ditolak) tapi ENSEMBLE sederhana - blend
konveks `p = alpha*p_v4 + (1-alpha)*p_mtbf` antara v4 production (histori
penuh) dan kandidat MTBF (E-66/E-68/E-69, window 2025+ terbatas), alpha
dicari HANYA di VALIDATION (grid 0,00-1,00 step 0,05, maksimalkan
VALIDATION PR-AUC), diuji SEKALI di TEST - metodologi sama dengan
gate.py::select_precision_constrained_threshold.

**Metode** Populasi sama dengan E-66/E-69 (window `t_mtbf` 2025-01-15+),
3 fold rolling PERSIS sama dengan E-66/E-69. v4 = model production asli
(fitur/kalibrator BEKU miliknya sendiri, `failure_model.load_failure_
model()`), TIDAK dilatih ulang. Kandidat MTBF dilatih fresh per fold
(hyperparameter default, sesuai E-69).

**Hasil**

| Fold | Alpha (dipilih di VAL) | TEST v4 PR-AUC | TEST MTBF PR-AUC | TEST ENSEMBLE PR-AUC | Delta vs v4 |
|---|---:|---:|---:|---:|---:|
| 1 | 0,35 | 0,1516 | 0,1055 | 0,1512 | -0,0004 |
| 2 | 0,05 | 0,1754 | 0,1582 | 0,1809 | **+0,0055** |
| 3 | 0,00 | 0,2348 | 0,1689 | 0,1689 | **-0,0659** |

**Fold 3 adalah kegagalan telak**: alpha yang dipilih di VALIDATION
(0,00 - ensemble degenerasi jadi MTBF MURNI) ternyata pilihan TERBURUK di
TEST - v4 SENDIRIAN (0,2348) jauh mengalahkan MTBF sendirian (0,1689) di
fold ini, tapi VALIDATION (cuma ~10rb baris, ratusan positif) tidak
punya cukup sinyal untuk mendeteksi itu dan malah memilih bobot yang
salah total. Rata-rata delta PR-AUC 3 fold: **-0,0203** (NEGATIF, bukan
+0,01 s/d +0,02 yang ditarget) - didorong sepenuhnya oleh kegagalan fold
3. Gerbang lifecycle (target presisi 0,30): fold 1 alert=280 presisi
0,3714 recall=0,0955; fold 2 alert=250 presisi 0,4120 recall=0,1142;
fold 3 INFEASIBLE di VALIDATION.

Catatan: ROC-AUC ensemble MENANG di ketiga fold (0,8200->0,8422;
0,8289->0,8614; 0,8414->0,8786) - tapi ROC-AUC bukan metrik yang relevan
di base rate serendah ini (E-56), dan PR-AUC (metrik yang ditarget)
justru rata-rata memburuk.

**Analisis** Pola PERSIS sama dengan E-55/E-59/E-63/E-67/E-69: sesuatu
yang dipilih lewat pencarian di VALIDATION (di sini: bobot blend) tidak
otomatis generalize ke TEST begitu diuji multi-fold, karena ukuran
VALIDATION per fold (~10rb baris, ratusan positif) terlalu bising untuk
membedakan alpha yang benar-benar baik dari yang kebetulan. Ensemble
konveks sederhana TIDAK aman dipakai di sini tanpa regularisasi
tambahan (mis. shrink ke alpha=1 sebagai prior, atau syarat minimum
delta VALIDATION sebelum menjauh dari v4 murni) - tapi menambah itu
sudah masuk kategori "menambah kerumitan untuk menyelamatkan pendekatan
yang sudah gagal", persis yang diminta DIHINDARI (lihat instruksi user
poin 7). Tidak dilanjutkan.

**Keputusan** **DITOLAK** - tidak dipromosikan menjadi challenger atau
dipakai di production. Bukan sinyal baru (MTBF-nya sendiri sudah
diketahui dari E-66), murni soal cara MENGGABUNGKAN dua model yang
ternyata tidak aman dengan pendekatan paling sederhana. Menutup satu-
satunya sudut Fase 8 yang belum tercoba sebelumnya; tidak ada lagi ide
struktural baru yang belum diuji per E-62.

---

## E-71 · Audit Active Risk Set: populasi VALIDATION/TEST tercemar baris PART yang sudah dilepas (RETURNED/DISMANTLED) - bug korektnes NYATA, TAPI bukan lever PR-AUC via retraining

2026-08-27 · audit + eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode production diubah

**Pertanyaan** User meminta audit: apakah TRAIN/VALIDATION/TEST memasukkan
PART yang sebenarnya sedang dismantled/repairing/warehouse/belum
terpasang, bukan yang benar-benar installed/operational/exposed ke
risiko kerusakan? Ini definisi ULANG population-at-risk, BUKAN thinning
acak (dilarang eksplisit oleh user).

**Temuan populasi**: `get_cycles()` (`data_reader.py`) mendefinisikan
"cycle" murni dari event INSTALLED ke INSTALLED berikutnya (atau
failure/`data_end`) - TIDAK ADA pelacakan event pelepasan (RETURNED/
DISMANTLED/dst) di antaranya. Akibatnya, PART yang sudah dilepas TAPI
belum tercatat dipasang ulang tetap dianggap "masih dalam siklus, masih
berisiko" sampai `data_end` (`cycle_end_reason='RIGHT_CENSORED_AT_DATA_
END'`).

Diverifikasi dengan status mentah TERBARU tiap PART (`get_events()`):
dari 16.877 PART yang dianggap aktif oleh `current_observations()`
(populasi yang di-skor SEKARANG di production), **hanya 13.775 (81,6%)**
statusnya benar-benar INSTALLED. Sisanya (**18,4%**, ~3.100 PART):
DISMANTLED (131, eksplisit), RETURNED->OK (2.911+25, pola pasangan
event dalam hitungan detik - PART dikembalikan & diverifikasi OK,
BUKAN berarti masih terpasang), dan beberapa status lain.

Dampak ke baris observasi (`observation_on` > waktu event pelepasan
pertama SETELAH install terakhir, tanpa install ulang sejak itu):

| Split | Baris tercemar | Total baris | Persentase |
|---|---:|---:|---:|
| TRAIN | 10.097 | 251.568 | 4,0% |
| VALIDATION | 20.189 | 49.660 | **40,7%** |
| TEST | 11.288 | 38.451 | **29,4%** |

**Eksperimen**: latih model baru (hyperparameter identik v4) di TRAIN
yang sudah dibersihkan, banding v4 (production, tidak diubah), DIUJI di
KEDUA populasi (asli vs terkoreksi) supaya efek POPULASI dan efek MODEL
terpisah jelas:

| Skenario | ROC-AUC | PR-AUC |
|---|---:|---:|
| v4 di TEST ASLI (yang selama ini dilaporkan) | 0,8289 | 0,1754 |
| v4 di TEST TERKOREKSI (model SAMA, populasi dibersihkan) | 0,8015 | **0,1832** (+0,0078) |
| Model BARU (dilatih di populasi bersih) di TEST TERKOREKSI | 0,8114 | 0,1792 |
| Model BARU di TEST ASLI | 0,8370 | 0,1712 |

**Analisis** Dua temuan terpisah:
1. **Retraining di populasi bersih TIDAK menang** dibanding v4 yang
   dievaluasi ulang di populasi yang sama (0,1792 < 0,1832) - kontaminasi
   di TRAIN cuma 4,0%, terlalu kecil untuk merusak pelatihan model secara
   berarti. **Ini BUKAN lever PR-AUC lewat retraining.**
2. **v4 yang SAMA, diukur di populasi bersih, PR-AUC-nya sedikit lebih
   baik** (+0,0078) dari yang selama ini dilaporkan - tapi ROC-AUC malah
   turun (0,8289->0,8015). Baris tercemar (PART yang sudah dilepas)
   cenderung skor rendah dan gampang diberi ranking benar, jadi
   menyumbang "true negative mudah" yang mengerek ROC-AUC tapi bukan
   PR-AUC. Artinya: angka PR-AUC yang selama ini jadi acuan (0,1754,
   README/E-46/E-47/dst) **sedikit under-report** performa asli v4 - tapi
   arah baiknya kecil (+0,0078, di bawah target +0,01) dan ini **satu
   split saja**, belum diverifikasi rolling-fold seperti standar sesi ini.

**Implikasi produksi (terpisah dari soal PR-AUC)**: ~18% populasi yang
di-skor SEKARANG di `current_observations()` (serving) bukan PART yang
benar-benar terpasang - mereka dapat prediksi risiko kerusakan yang
tidak bermakna (PART yang sudah dilepas ditampilkan seolah masih
berisiko). Ini bug korektnes NYATA, independen dari hasil PR-AUC di
atas - butuh perbaikan `current_observations()` (`features.py`) untuk
mengecualikan PART yang status TERBARU-nya bukan INSTALLED, TERLEPAS
dari apakah itu mengubah PR-AUC atau tidak.

**Keputusan** **Bukan eksperimen model yang bisa dipromosikan** -
retraining tidak menang. **DITEMUKAN bug korektnes population-at-risk
yang nyata dan terpisah** - diperbaiki di jalur SERVING (lihat
docs/DECISIONS.md §18) sebagai perbaikan korektnes, bukan demi PR-AUC.
Perbaikan `get_cycles()` sendiri (melacak event pelepasan di level
CYCLE, bukan cuma di level snapshot-aktif) - yang akan mengubah komposisi
TRAIN/VALIDATION/TEST juga, bukan cuma populasi live - TETAP belum
dikerjakan, itu perubahan arsitektur lebih besar yang menyentuh SEMUA
model hilir (failure/scrap/survival), dicatat di §18 supaya tidak
hilang.

**Update 2026-08-31**: gap ini sudah ditutup sebagai P0 correctness fix;
lihat E-81 dan `docs/DECISIONS.md` §20. Catatan "belum dikerjakan" di
atas adalah status historis saat E-71 dijalankan, bukan status repo kini.

---

## E-72 · Terminal Peer Risk + Terminal-Model Lineage Risk - menang di satu split, DITOLAK setelah rolling 6-fold (pola sama dengan E-55/E-59/E-63)

2026-08-27 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** Dua sudut BARU yang belum pernah dicoba sepanjang sesi:
(1) Terminal Peer Risk - apakah kerusakan PART LAIN di Terminal fisik
yang sama menaikkan risiko PART yang sedang diprediksi (mis. masalah
kelistrikan/thermal di satu Terminal ikut membebani semua PART di
dalamnya)? (2) Terminal-Model Lineage Risk - apakah histori kerusakan
serial-serial SEBELUMNYA di slot Terminal+model yang sama membantu
menilai serial BARU yang belum punya histori sendiri (first-failure)?

**Metode** Terminal fisik diambil dari `data_reader.get_terminal_context()`
yang diperluas Fase 2 sesi ini (`terminal_inventory_item_id`, cakupan
99,8% dari seluruh cycle - JAUH lebih baik dari MTBF ~20%), hanya status
relasi tervalidasi (`VALID_POINT_IN_TIME_RELATION`/`VALID_RELATION_
RECORDED_AFTER_INSTALLATION`) dipakai. Fitur dihitung point-in-time safe
lewat primitif `_count_before` yang SUDAH dipakai `attach_fleet`/
`local_density` (searchsorted per grup terurut), DITAMBAH pengurangan
diri sendiri eksplisit ("total di grup - milik item ini sendiri di grup
yang sama") - PART yang diprediksi TIDAK pernah masuk hitungan peer-nya
sendiri, syarat eksplisit user. 7 fitur: peer failure count (total/90d),
peer failure rate, exposure Terminal (prior installs), lineage failure
count/rate, lineage previous serials. Dilatih ULANG (ablasi, bukan
dibanding v4 beku) di split production yang sama (`assign_split`),
VALIDATION untuk pilih, TEST sekali - metodologi E-44/E-46 dst.

**Hasil (satu split, BELUM rolling-fold)**:

| Varian | VAL PR-AUC | TEST PR-AUC | Delta TEST vs baseline |
|---|---:|---:|---:|
| baseline (fitur v4 saja) | 0,1063 | 0,1628 | - |
| + Terminal (semua 7 fitur) | 0,0987 | 0,1614 | -0,0015 |
| **+ sinyal saja (tanpa 2 fitur exposure)** | **0,1079** | **0,1649** | **+0,0021** |

**Feature importance (varian "semua 7 fitur")**: `log_terminal_prior_
installs` (exposure Terminal) mendominasi (54,9 - jauh di atas semua
fitur lain), sementara fitur SINYAL sebenarnya (peer/lineage failure
count/rate) semuanya <3. Membuang 2 fitur exposure (`log_terminal_
prior_installs`, `log_lineage_previous_serials`) membalik hasil dari
NEGATIF (-0,0015) menjadi POSITIF (+0,0021) - fitur exposure-nya yang
merusak (kemungkinan proxy umur-Terminal yang menambah noise/split sia-
sia), BUKAN konsep peer/lineage-nya.

**Analisis** Varian "sinyal saja" adalah salah satu dari sedikit hasil
sesi ini yang naik di KEDUA metrik (ROC-AUC dan PR-AUC) di KEDUA split
(VALIDATION dan TEST) secara SEARAH - beda karakter dari kebanyakan
kandidat lain (E-55/E-59/E-63/E-70 semua menang di satu tempat, kalah/
berbalik di tempat lain). TAPI besarnya kecil (+0,0021, jauh di bawah
target +0,01) dan **INI SATU SPLIT SAJA** - E-55 dulu juga terlihat
"bersih" di satu split sebelum terbukti artefak lewat rolling backtest.
Cakupan sangat baik (99,9% observasi punya terminal_id, peer failure
>0 pada 7,8% baris, lineage failure >0 pada 14,4% baris) - jauh lebih
baik dari MTBF, jadi BUKAN masalah cakupan tipis kalau memang tidak
lolos rolling-fold nanti.

**Konfirmasi rolling 6-fold** (metodologi PERSIS `cli.py rolling-backtest`/
E-44, `_rolling_fold_windows`/`_assign_rolling_split` dipakai ulang
langsung - bukan ditulis ulang): baseline vs baseline+5 fitur sinyal,
6 fold TEST bergulir 60-hari sepanjang 2025-08 s/d 2026-08.

| Fold | TEST window | Baseline PR-AUC | +Sinyal Terminal PR-AUC | Delta |
|---|---|---:|---:|---:|
| 1 | 2025-08-08 | 0,1109 | 0,0956 | -0,0153 |
| 2 | 2025-10-07 | 0,0997 | 0,0952 | -0,0045 |
| 3 | 2025-12-06 | 0,0550 | 0,0503 | -0,0047 |
| 4 | 2026-02-04 | 0,0927 | 0,0947 | **+0,0021** |
| 5 | 2026-04-05 | 0,1311 | 0,1282 | -0,0029 |
| 6 | 2026-06-04 | 0,3510 | 0,3457 | -0,0053 |

**Menang di 1/6 fold saja** (fold 4 - persis kemenangan +0,0021 yang
sama dengan hasil single-split di atas, mengonfirmasi hasil awal itu
MEMANG cuma artefak fold itu, bukan sinyal genuine). Mean delta lintas
6 fold = **-0,0051** (sd=0,0057) - net NEGATIF, walau tidak melebihi
1 sd (tidak bisa diklaim "baseline lebih baik secara signifikan" juga,
tapi jelas TIDAK ada bukti "sinyal Terminal lebih baik").

**Keputusan** **DITOLAK**. Pola PERSIS sama dengan E-55/E-59/E-63/E-70:
kemenangan kecil di satu split TIDAK bertahan begitu diuji rolling-fold
sungguhan, walau cakupan datanya jauh lebih baik dari MTBF (99,9% vs
~20%) - membuktikan masalahnya BUKAN cakupan/sparsity fitur, tapi
sinyal peer/lineage-nya sendiri memang tidak cukup kuat/konsisten untuk
membantu CatBoost, setidaknya dengan formulasi 5-fitur ini. Tidak
direkomendasikan dilanjutkan (mis. coba varian fitur lain seperti
"days since last peer failure") tanpa alasan kuat kenapa itu akan
berbeda - risiko mengulang pola yang sudah terbukti gagal.

---

## E-73 · Concept-drift/recency Q2: full-history vs rolling window 12/18/24/36bln vs recency-weighted - full-history TETAP terbaik, menang telak di mean DAN worst-fold

2026-08-27 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** Apakah concept-drift membuat histori lama JUSTRU merugikan
model - dites lewat rolling TRAIN window (12/18/24/36 bulan sebelum
VALIDATION) dan recency-weighted training (bobot exponential half-life
365 hari), dibanding full-history (skema production).

**Metode** 6 fold rolling PERSIS `cli.py rolling-backtest`
(`_rolling_fold_windows`/`_assign_rolling_split`, embargo 30 hari sudah
baked-in). 6 varian per fold: full-history, window {12,18,24,36} bulan,
recency-weighted. Dipilih berdasarkan MEAN dan WORST-FOLD, bukan satu
split (syarat eksplisit user).

**Hasil**

| Varian | Mean PR-AUC | Worst-fold | Mean n_train |
|---|---:|---:|---:|
| **full-history (production)** | **0,1506** | **0,0520** | 260.042 |
| window 12bln | 0,0987 | 0,0185 | 41.632 |
| window 18bln | 0,1164 | 0,0294 | 61.011 |
| window 24bln | 0,1177 | 0,0543 | 74.420 |
| window 36bln | 0,1226 | 0,0543 | 97.257 |
| recency-weighted | 0,1155 | 0,0555 | 260.042 |

Full-history menang di MEAN dan WORST-FOLD atas SEMUA 5 alternatif -
window 12bln kalah di 6/6 fold (kadang TP=0 total).

**Analisis** Kerusakan sudah sangat langka (~1,65% baris eligible).
Memotong TRAIN dari 260rb ke 41-97rb baris membuang sinyal kerusakan
langka itu - jauh lebih merugikan daripada manfaat "lebih relevan
dengan pola terkini" dari concept-drift. Tidak ditemukan bukti concept-
drift merugikan model; sebaliknya, histori lama tetap bernilai.

**Keputusan** **DITOLAK semua varian** - full-history (skema production
saat ini) TETAP pilihan terbaik, tidak ada perubahan. Karena tidak ada
window yang menang, tahap lanjutan "window terbaik + MTBF" yang diminta
user tidak relevan dijalankan (premisnya tidak terpenuhi).

---

## E-74 · Multi-Horizon OOF Stacking (p_7d/p_14d -> model 30-hari) - menjanjikan di satu split, TAPI tidak konsisten setelah rolling 6-fold (mean +0,0032, dalam 1 sd)

2026-08-27 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** Apakah menambahkan prediksi model auxiliary horizon LEBIH
PENDEK (7 hari, 14 hari) sebagai fitur ke model final 30-hari membantu
membedakan risiko IMMINENT dari risiko jangka panjang? 60-hari SENGAJA
DILEWATI - embargo pipeline yang ada cuma 30 hari, verifikasi negatif di
60 hari butuh skema embargo terpisah yang belum ada (di luar cakupan).

**Metode** Target 7d/14d dihitung pada populasi eligible yang SAMA
dengan target 30d produksi - AMAN karena baris yang terverifikasi
negatif pada embargo 30 hari OTOMATIS negatif juga pada 7/14 hari
(nested, tidak menambah leakage). Prediksi auxiliary di TRAIN dibangun
lewat OOF 3-fold (`StratifiedKFold` + prediksi hold-out, pola PERSIS
`scrap/train.py::cross_val_predict`) - BUKAN model yang memprediksi
baris yang dia lihat sendiri saat fit. VALIDATION/TEST diskor model
auxiliary yang di-fit di SELURUH TRAIN (stacking standar). Model final:
fitur v4 + [p_7d, p_14d].

**Hasil - satu split (production split, sanity check awal)**:

| Varian | VAL PR-AUC | TEST PR-AUC | Lifecycle TP/FP/FN | Presisi/Recall |
|---|---:|---:|---|---|
| baseline | 0,1065 | 0,1933 | 3/3/1118 | 0,500/0,003 |
| + p_7d/p_14d | 0,1069 | **0,2052** | **46/22/1075** | **0,676/0,041** |

Delta TEST PR-AUC = **+0,0119**, TP naik 15x lipat - lompatan lifecycle
terbesar sepanjang sesi di SATU split, cukup besar untuk memicu
kehati-hatian EKSTRA (bukan optimisme), mengingat pola E-55/E-59/E-63/
E-67/E-70/E-72 semua "bersih" di satu split lalu gagal rolling-fold.

**Hasil - konfirmasi rolling 6-fold** (metodologi PERSIS E-73, 10
training/fold: 2 horizon x 3 OOF + 2 full-train aux + 2 model final):

| Fold | Baseline PR-AUC | Stacked PR-AUC | Delta | TP baseline/stacked |
|---|---:|---:|---:|---|
| 1 | 0,0944 | 0,0959 | +0,0015 | 1/3 |
| 2 | 0,0751 | 0,0736 | -0,0015 | 0/0 |
| 3 | 0,0520 | 0,0477 | -0,0042 | 1/0 |
| 4 | 0,0855 | 0,0956 | +0,0101 | INFEASIBLE/INFEASIBLE |
| 5 | 0,1840 | 0,2015 | +0,0175 | 0/0 |
| 6 | 0,4129 | 0,4084 | -0,0045 | 21/27 |

Mean PR-AUC: baseline=0,1506, stacked=0,1538 (**delta mean=+0,0032**,
sd=0,0089 - **DALAM 1 sd, tidak signifikan**). Menang di **3/6 fold**
saja - persis setengah, bukan mayoritas. Total TP lintas 6 fold:
baseline=23, stacked=**30** (+7, +30%) - TAPI total FP juga naik
baseline=15 -> stacked=**24** (+9). Presisi gabungan malah SEDIKIT
turun (23/38=0,605 -> 30/54=0,556) - trade-off nyata, bukan kemenangan
bersih seperti yang terlihat di satu split.

**Analisis** Pola E-55/E-59/E-63/E-67/E-70/E-72 terulang: hasil satu-
split yang terlihat sangat kuat (+0,0119, TP 15x) ternyata jauh lebih
lemah dan tidak konsisten begitu diuji multi-fold (mean +0,0032, menang
cuma 3/6 fold). BEDA dari kandidat-kandidat lain sesi ini, hasil ini
TIDAK berbalik jadi negatif jelas - dia jadi genuinely AMBIGU (tidak
menang, tidak jelas kalah, dalam batas noise). Sesuai kriteria user
sendiri ("PR-AUC naik konsisten di mayoritas/all folds") - 3/6 BUKAN
mayoritas, jadi tidak memenuhi syarat.

**Keputusan** **TIDAK DIPROMOSIKAN** - tidak menang konsisten multi-fold
sesuai syarat eksplisit user ("jangan ubah production sebelum kandidat
menang konsisten multi-fold"). Bukan ditolak setegas E-64/E-70/E-72/E-75
(masih ada 3/6 fold menang, dan TP total naik) - dicatat sebagai
kandidat AMBIGU, bukan sinyal negatif ataupun positif yang jelas. Kalau
mau dicoba lagi nanti, arah yang mungkin lebih menjanjikan: OOF fold
lebih banyak (stabilitas OOF sendiri), atau horizon auxiliary berbeda -
di luar cakupan sesi ini.

---

## E-75 · Specialist model untuk 5 model dominan-FN (E-58) - DITOLAK jelas, general model menang di 4/5 kode (PR-AUC per kode)

2026-08-27 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** Apakah model TERPISAH yang dilatih HANYA pada 5 model
PART penyumbang FN terbesar (`0120204`/`0120201`/`0521202`/`0620505`/
`0720301`, diidentifikasi E-58) mengalahkan model general (production)
untuk kelima kode itu?

**Audit populasi (sebelum melatih apa pun)**: TRAIN 5-model gabungan
cuma 66.772 baris/377 kerusakan (vs 251rb+ baris model general). Timpang
parah PER MODEL: `0120201` sendirian menyumbang 320/377 (85%) TRAIN
positif; `0521202` **0 TRAIN positif sama sekali**; `0120204`/`0620505`
cuma 8/16 positif. Sudah terlihat sejak sebelum training bahwa
"specialist" gabungan akan jadi model `0120201` secara efektif, dan
`0521202` mustahil dipelajari model APAPUN karena tidak ada contoh
positif untuk dipelajari.

**Hasil (TEST, 5-model subgroup saja)**:

| | ROC-AUC | PR-AUC |
|---|---:|---:|
| v4/production (general) | 0,7946 | **0,2037** |
| Specialist (5 model saja) | 0,7829 | 0,1539 (**-0,0498**) |

**Update - audit lanjutan diminta user** (breakdown awal di atas pakai
recall@0,30/ambang tetap, BUKAN PR-AUC - user meminta 4 diagnostik untuk
memastikan hasil bukan artefak):

1. **Positif TEST per kode**: 76-144 per kode - cukup untuk PR-AUC
   bermakna (beda dari TRAIN yang timpang parah).
2. **n_unique skor specialist per kode**: 21-32 - granular, TIDAK
   collapse.
3. **Calibrator specialist collapse?** **Tidak** - 21 level kalibrasi
   unik dari 41 breakpoint VALIDATION; di SELURUH TEST 5-model
   specialist punya **74 nilai unik** vs v4 cuma **33** - specialist
   LEBIH granular, bukan collapse ke skor konstan (skor maksimum cuma
   0,333, tapi itu wajar - isotonic ikut base rate VALIDATION-nya).
4. **PR-AUC per kode dengan fungsi SAMA (`average_precision_score`)**,
   BUKAN recall@ambang seperti sebelumnya:

| Kode | Positif TEST | PR-AUC v4 | PR-AUC specialist |
|---|---:|---:|---:|
| 0120204 | 144 | 0,2346 | 0,1900 |
| 0120201 | 128 | 0,1623 | 0,1284 |
| 0521202 | 95 | 0,5571 | 0,4396 |
| 0620505 | 76 | 0,0488 | 0,0388 |
| 0720301 | 80 | 0,0725 | **0,0937** |

**general menang di 4/5 kode**, specialist menang di 1/5 (`0720301`,
keduanya lemah). Dua kode yang breakdown AWAL sebut "seri 0-0" ternyata
PUNYA sinyal PR-AUC nyata (cuma di bawah ambang 0,30, tidak memicu
alert) - breakdown recall@ambang sebelumnya menyembunyikan itu. PR-AUC
AGGREGATE dihitung ulang dengan fungsi yang sama persis mengonfirmasi
angka semula (v4=0,2037, specialist=0,1539) - bukan bug/fluke.

**Analisis** Audit lanjutan ini justru MEMPERKUAT kesimpulan awal, bukan
melemahkan - bukan artefak calibrator atau metodologi yang salah (poin
1-3 semua bersih). Yang perlu dikoreksi hanya CARA MELAPORKAN breakdown
per-kode (recall@ambang -> PR-AUC), bukan kesimpulannya. Sesuai dugaan
dari audit populasi - specialist kehilangan sinyal lintas-populasi
(251rb+ baris general) tanpa cukup data per-model untuk menggantinya.
Kekalahan besar dan konsisten arah - tidak perlu rolling-fold, sama
seperti E-64 (YetiRank kalah telak).

**Keputusan** **DITOLAK**. Memperkuat E-58/E-62: 5 model dominan-FN
BUKAN satu pola tunggal yang bisa dipelajari model khusus - mayoritas
kode-nya genuinely kekurangan contoh positif untuk dipelajari model
APAPUN, general maupun specialist.

---

## E-76 · Geser boundary temporal split untuk tambah positive TRAIN - DITOLAK, split v6 saat ini TETAP terbaik di VALIDATION

2026-08-28 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** Apakah menggeser boundary `validation_start`/`test_start`
LEBIH LAMBAT (bukan random split - urutan TRAIN->VALIDATION->TEST dan
embargo 30 hari tetap) supaya lebih banyak baris positif masuk TRAIN
(3.852 -> ~4.000-4.500) meningkatkan PR-AUC, TANPA membuat VALIDATION/
TEST jadi terlalu kecil untuk dipercaya?

**Metode** Boundary dicari lewat grid search murni hitung (tidak
melatih apa pun) untuk mendekati 3 target user: A (~4000/800/700), B
(~4300/800/700), C (~4500/700/700) - boundary TETAP mengikuti tanggal
alami (bukan dipaksa pas). 4 konfigurasi dilatih (baseline v6 + A/B/C),
fitur/hyperparameter IDENTIK v6. **VALIDATION PR-AUC sebagai dasar
pemilihan** - TEST HANYA disentuh SEKALI, untuk konfigurasi yang menang.

**Hasil boundary yang ditemukan & VALIDATION**:

| Konfigurasi | validation_start | test_start | TRAIN pos | VAL pos | TEST pos | VAL ROC-AUC | **VAL PR-AUC** |
|---|---|---|---:|---:|---:|---:|---:|
| **v6-baseline** | 2025-01-01 | 2026-01-01 | 3.852 | 947 | 1.121 | 0,8234 | **0,1038** |
| A | 2025-06-05 | 2026-04-01 | 4.141 | 826 | 868 | 0,8206 | 0,0886 |
| B | 2025-07-31 | 2026-05-27 | 4.351 | 814 | 705 | 0,8287 | 0,0816 |
| C | 2025-08-30 | 2026-05-27 | 4.444 | 709 | 705 | 0,8323 | 0,0835 |

**v6-baseline MENANG telak di VALIDATION PR-AUC** atas KETIGA kandidat -
menggeser boundary untuk menambah positif TRAIN justru MENURUNKAN
PR-AUC VALIDATION, bukan menaikkan (walau ROC-AUC C sedikit lebih
tinggi dari baseline - PR-AUC yang jadi metrik utama, sesuai instruksi
user, dan arahnya konsisten kalah di ketiga kandidat).

**Pemenang: v6-baseline** (tidak berubah) - TEST disentuh sekali, hasil
persis mendekati angka v6 resmi: ROC-AUC=0,8418 PR-AUC=0,1950 (selisih
kecil dari metadata v6 resmi 0,8497/0,2116 karena skrip ad-hoc ini
memakai `cumulative_support` langsung, bukan jalur frozen-support
evaluate_incumbent() resmi - bukan bug, cuma beda metodologi evaluasi
minor yang tidak memengaruhi kesimpulan perbandingan ANTAR konfigurasi
karena keempatnya dievaluasi dengan cara yang SAMA). Gerbang lifecycle:
TP=8 FP=2 FN=1113 presisi=0,800 recall=0,007 alert=10. First-failure
recall=0,0 (n=604), late-life recall=0,0 (n=403) - konsisten dengan
E-56/E-58 (blind spot sudah diketahui, bukan temuan baru).

Distribusi (cek stabilitas per syarat user): top-3 model PART penyumbang
positif VALIDATION relatif stabil lintas 4 konfigurasi (`0120204`/
`0120401` selalu di 2 teratas), jumlah lifecycle unik VALIDATION naik
seiring window (5.896 -> 7.199-7.252) - tidak ada tanda ketimpangan
ekstrem yang mencurigakan.

**Analisis** TRAIN sudah punya ~3.852 positif - menambah beberapa ratus
lagi (jadi 4.100-4.450) TIDAK membantu, karena harganya adalah
VALIDATION/TEST yang lebih kecil dan lebih bising (VALIDATION PR-AUC
makin variance tinggi dengan makin sedikit positif untuk dievaluasi).
Ini konsisten dengan E-50 (base rate rendah adalah artefak skema
observasi, bukan kekurangan VOLUME TRAIN) dan E-62 (bottleneck adalah
keterbatasan sinyal, bukan kuantitas data mentah) - menambah data
historis yang SAMA JENISNYA tidak menciptakan sinyal baru.

**Keputusan** **DITOLAK** ketiganya (A/B/C). Split v6 saat ini
(`validation_start=data_end tahun ini 1 Jan, test_start=1 Jan tahun
berjalan`) TETAP konfigurasi terbaik yang divalidasi - tidak ada
perubahan pada `assign_split()` atau model production.

---

## E-77 · Feature-family ablation (32 fitur v6) - corrective/failure history mendominasi sinyal, CORE+HISTORY (16 fitur) SETARA FULL secara statistik

2026-08-28 · eksperimen ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** Apakah sebagian dari 32 fitur v6 redundant/noisy? CORE
minimal (`part_model_category` saja) ditambah tiap keluarga fitur satu
per satu: HISTORY (16 fitur - corrective/failure count berbagai window
+ tren interval), AGE (7 fitur - umur/lifecycle), TEMPORAL (3 - month_
sin/cos), CLIENT (2 - client_category), DENSITY (8 - kepadatan
kerusakan model/item-type). FULL (32 fitur, persis v6) sebagai acuan.
6 fold rolling PERSIS metodologi E-73/E-74/cli.py rolling-backtest.

**Hasil (mean/worst-fold PR-AUC, 6 fold)**

| Varian | n fitur | Mean PR-AUC | Worst-fold | Delta vs FULL |
|---|---:|---:|---:|---:|
| FULL (v6) | 32 | 0,1506 | 0,0520 | - |
| CORE+HISTORY | 16 | 0,1488 | 0,0489 | **-0,0019 (sd selisih 0,0078 - DALAM 1 sd)** |
| CORE+AGE | 7 | 0,0908 | 0,0409 | -0,0598 |
| CORE+DENSITY | 8 | 0,0606 | 0,0384 | -0,0900 |
| CORE+CLIENT | 2 | 0,0445 | 0,0202 | -0,1061 |
| CORE+TEMPORAL | 3 | 0,0441 | 0,0280 | -0,1065 |
| CORE saja | 1 | 0,0387 | 0,0192 | -0,1119 |

CORE+HISTORY menang di 2/6 fold atas FULL. First-failure recall dan
late-life recall = **0,0 di SEMUA 7 varian, termasuk FULL** (n cukup di
sebagian besar fold - 604 first-failure, 403 late-life di fold TEST
terakhir contohnya) - tidak ada kombinasi fitur yang menembus blind spot
ini.

**Analisis** Corrective/failure history (keluarga terbesar, 15 fitur di
luar CORE) menyumbang HAMPIR SELURUH sinyal prediktif v6 - CORE+HISTORY
secara statistik TIDAK BEDA dari FULL (selisih dalam 1 sd, kriteria
sama dipakai sepanjang sesi). AGE kontributor individu kedua terkuat
tapi jauh di bawah History. TEMPORAL dan CLIENT nyaris tidak menambah
apa pun di atas CORE kosong. **PENTING**: desain ini ADDITIVE (CORE +
satu keluarga), BUKAN leave-one-out - hasil ini menunjukkan seberapa
besar kontribusi MANDIRI tiap keluarga di atas CORE, TAPI TIDAK
membuktikan keluarga mana yang aman dibuang DARI DALAM set 32 fitur
yang sudah ada (mis. Density mungkin masih penting BERSAMA History
walau lemah sendirian - belum diuji). first-failure/late-life recall
nol di FULL SEKALIPUN mengonfirmasi ulang E-56/E-58 dari sudut BARU
(bukan cuma "v4 gagal", tapi "kombinasi fitur apa pun dari database
ini gagal" untuk dua populasi ini).

**Keputusan** **BELUM ada KEEP/REMOVE untuk fitur individual** - butuh
eksperimen leave-one-out terpisah (FULL minus satu keluarga) sebelum
memutuskan pruning apa pun, sesuai instruksi eksplisit user (density
TIDAK otomatis dibuang meski kontribusi mandirinya kecil). Temuan yang
SUDAH solid: (1) history adalah tulang punggung sinyal v6, (2) tidak
ada rekonfigurasi fitur yang akan memperbaiki recall first-failure/
late-life - itu tetap murni soal data yang tidak tersedia (E-62),
BUKAN soal pemilihan fitur. Model production TIDAK diubah.

---

## E-78 · Reformulasi model failure ke level LIFECYCLE - A_H90 (fixed-window 90 hari sejak install) TIDAK terbukti lebih baik dari model observasi - trade-off presisi/recall awal ternyata cuma titik operasi berbeda di kurva yang SETARA

2026-08-30 · skrip ad-hoc di `scratch/lifecycle_reform/` (tidak masuk repo) - tidak ada kode/model production diubah, database read-only

**Pertanyaan** Model failure Q2 dilatih di level observasi (satu baris per
snapshot 30-harian, base rate ~1,66%), tapi gerbang keputusan operasional
(E-49) sudah bekerja di level lifecycle (first-alert per siklus instalasi) -
ada mismatch training vs evaluasi/pemakaian. E-50 menunjukkan base rate
1,66% observasi sebagian besar artefak skema (96,1% baris negatif
duplikatif). Hipotesis: melatih langsung di level lifecycle (satu baris per
siklus instalasi, dari fitur v6 APA ADANYA - tidak ada fitur baru)
menghasilkan **lift** yang lebih baik daripada model observasi yang
diagregasi ke level lifecycle, konsisten di rolling backtest?

**Metode**

1. **Audit ulang E-50** pada data terbaru (24.291 siklus, s/d 2026-08-27,
   naik dari titik E-50): base rate observasi 1,6556% vs lifecycle-level
   47,7814% - dekat dengan angka E-50 (1,6501%/47,1551%), TEST naik sedikit
   (12,05%->14,41%) karena lebih banyak lifecycle lama sudah resolve.
   Konfirmasi: E-50 masih berlaku.
2. Reuse TOTAL mekanisme censoring `features_survival.assign_lifecycle_
   outcome()`/`cohort_cycles()` (sudah diaudit, dipakai model survival) -
   tidak ada aturan eligibility baru. Bangun dua formulasi label:
   - **Formulasi A** (fixed-window sejak install): gagal dalam H hari sejak
     `installed_on`? Diuji H=90/180/365.
   - **Formulasi B** (landmark): bertahan sampai umur L, gagal dalam H hari
     berikutnya? Diuji L=90/180/365 (sama dengan `ANCHOR_BASE_AGES_DAYS`
     survival), H=90.
   Fitur v6 (32 kolom, `config.FEATURE_COLUMNS`) dihitung point-in-time pada
   landmark lewat fungsi yang SAMA dengan model observasi
   (`attach_history`/`attach_degradation_history`/`attach_fleet`/
   `attach_item_type_density`/`build_features`) - tidak ada fitur baru.
   `CATBOOST_PARAMS` tidak diubah.
3. **Temuan kritis SEBELUM training apa pun**: VALIDATION/TEST di repo ini
   masing-masing hanya lebar **1 tahun kalender**. H=365 (Formulasi A) dan
   L=180/365 (Formulasi B) DEGENERATE di bawah skema ini - lifecycle
   VALIDATION/TEST nyaris tidak pernah punya cukup umur SEBELUM cutoff-nya
   sendiri untuk jadi negatif tepercaya:
   - A H=365: VALIDATION 385/2316 baris labelable, TEST 520/3062 - **base
     rate 100% di keduanya**.
   - B L=180: TEST 11/1543 labelable (99% dibuang, base rate 100%).
   - B L=365: VALIDATION dan TEST **kosong total** (landmark tidak pernah
     tercapai dalam 1 fold split).
   Ketiganya DIBUANG dari eksperimen (bukan dipaksa jalan) - keputusan
   eksplisit user. Sisa varian sehat: A_H90, A_H180 (ditandai berisiko -
   VALIDATION/TEST dibuang 24-33%, base rate naik 19%->25%), B_L90_H90.
4. **Langkah 3** (sanity check, fixed split, 5 seed 42/1/2/3/4,
   `CATBOOST_PARAMS` tidak diubah): A_H180 base rate naik ~29% (19,29%->
   24,85%) dan PR-AUC naik ~28% (0,6332->0,8098) - HAMPIR PROPORSIONAL,
   sementara **lift TIDAK naik** (3,28x->3,26x) dan gerbang presisi>=40%
   collapse ke recall 97-98% dengan 800-1000+ alert dari ~2.050 baris TEST
   (menandai model tidak melakukan triase, hanya menangkap hampir semua
   baris). Sesuai aturan §3 master prompt eksperimen ini ("kalau kenaikan
   cuma efek base rate, katakan terus terang dan hentikan") - **A_H180
   DIBUANG, dikonfirmasi base-rate artifact, bukan sinyal asli.**
   A_H90 (lift 3,28x) dan B_L90_H90 (lift 5,96x, tapi gerbang presisi>=40%
   cuma feasible 3/5 seed) dibawa ke rolling backtest.
5. **Langkah 4, rolling backtest** - percobaan pertama pakai metodologi
   PERSIS `cli.py` (6 fold x 60 hari, validasi 365 hari) TERBUKTI TIDAK
   COCOK untuk skala lifecycle: window 60 hari cuma menangkap 194-1.612
   lifecycle per fold (vs puluhan ribu observasi per fold di skema
   observation-level yang jadi basis desain protokol itu), dan 2-3 fold
   TERAKHIR (dekat `data_end`) degenerate (base rate 100% atau TEST kosong)
   karena lifecycle yang baru terpasang belum sempat kelewat H hari follow-
   up nyata. **Diperbaiki** (keputusan eksplisit user): fold diperlebar ke
   180 hari, dikurangi jadi 4 fold, dan fold yang `installed_on`-nya
   terlalu dekat `data_end` untuk sempat resolve H dibuang lewat buffer
   (`landmark_age + horizon + 14 hari margin`) - `rolling_lifecycle_
   outcome()` di `scratch/lifecycle_reform/lifecycle_dataset.py`
   menggeneralisasi `assign_lifecycle_outcome()` supaya cutoff mengikuti
   BATAS FOLD (bukan `data_end` tunggal), split tetap berdasar
   `installed_on` (bukan `observation_on`) sesuai instruksi master prompt.
6. **Langkah 5, apple-to-apple vs model observasi production**: model
   observasi (v6 APA ADANYA, `build_dataset()`) dilatih ulang per fold
   (embargo TRAIN/VALIDATION standar, SAMA seperti production), diagregasi
   first-alert-per-lifecycle (`gate.py::lifecycle_metrics()`/
   `select_lifecycle_threshold()`, metodologi E-49) - PADA populasi TEST
   dan definisi "positif" yang **identik** dengan A_H90 (lifecycle yang
   `installed_on`-nya jatuh di fold yang sama, label = gagal <=90 hari
   sejak install, dipaksa lewat mapping label otoritatif, bukan dihitung
   ulang longgar). Percobaan pertama SALAH (target model observasi
   "pernah gagal kapan pun" tanpa batas horizon, byas ke recall lebih
   tinggi secara artifisial - dikoreksi sebelum angka final diambil).
7. **Langkah 6 (follow-up user, krusial)**: Langkah 5 menemukan A_H90
   recall lebih tinggi TAPI presisi lebih rendah - pertanyaan wajib
   sebelum menyimpulkan apa pun: itu titik kurva presisi-recall yang
   BENAR-BENAR lebih baik, atau cuma titik operasi berbeda (threshold
   VALIDATION kebetulan mendarat di tempat berbeda) pada kurva yang
   sama bagusnya? Dijawab dengan oracle recall@presisi>=0,40 dihitung
   LANGSUNG di kurva presisi-recall TEST (`gate.select_precision_
   constrained_threshold()` dipanggil pada TEST itu sendiri, bukan
   threshold dari VALIDATION) - untuk KEDUA model, populasi/label sama
   persis dengan Langkah 5 (model observasi diagregasi lewat skor
   MAKSIMUM per lifecycle dalam window <=90hr, setara first-alert pada
   threshold manapun). Ini diagnostik bentuk-kurva, BUKAN simulasi
   threshold produksi (itu sudah dijawab jujur di Langkah 4/5).

**Hasil** (4 fold, `installed_on` window masing-masing 180 hari, 5 seed;
gerbang presisi target 0,40; PR-AUC/lift dari Langkah 4, presisi/recall dari
Langkah 5 - populasi & label identik lintas kedua model per fold):

| Fold (installed_on) | Base rate lifecycle (<=90hr) | A_H90 presisi/recall | Model observasi (agregasi first-alert, <=90hr) presisi/recall |
|---|---:|---|---|
| 1: 2024-05->2024-11 | 35,25% | 0,58 / 0,31 | 0,69 / 0,29 |
| 2: 2024-11->2025-05 | 8,01% | 0,35 / 0,57 | 0,49 / 0,13 |
| 3: 2025-05->2025-11 | 21,13% | 0,30 / 0,67 | 0,53 / 0,15 |
| 4: 2025-11->2026-05 | 6,17% | 0,24 / 0,62 | 0,25 / 0,015 |
| **Mean** | | **0,3681 presisi / 0,5427 recall** | **0,4914 presisi / 0,1463 recall** |

A_H90 (Langkah 4): PR-AUC 0,4158+/-0,1168, lift 3,30x+/-1,78 (sd besar,
didominasi fold 3 yang base rate-nya kecil - 2,61% - lift meledak jadi
10,65x di fold itu sendirian; TANPA fold itu variasinya jauh lebih moderat),
gerbang presisi>=40% feasible 18/20 (fold x seed). B_L90_H90 (fixed-split
Langkah 3, lift 5,96x) TIDAK bertahan di rolling backtest lebar: lift jatuh
ke 2,28x+/-0,68, gerbang feasible cuma 6/20 (fold x seed) - **persis pola
E-55/E-59/E-63/E-72/E-74: kandidat menang di satu split, kalah di rolling
backtest** - B_L90_H90 DIBUANG dari perbandingan final.

**Langkah 6, oracle recall@presisi>=0,40 di TEST (4 fold, 5 seed, populasi &
label identik dengan Langkah 5)**:

| Fold | A_H90 oracle recall | Model observasi oracle recall |
|---|---:|---:|
| 1 | 0,7860 | 0,6791 |
| 2 | 0,1925 | 0,4472 |
| 3 | 0,2995 | 0,2896 |
| 4 | 0,2361 | 0,0827 |
| **Mean** | **0,3785 +/- 0,2752** | **0,3746 +/- 0,2519** |

Menang-kalah TIDAK konsisten per fold (2-2), dan selisih mean (0,0039)
JAUH di bawah sd (~0,26-0,28) - **secara statistik tidak bisa dibedakan**.

**Temuan**

1. **Trade-off presisi/recall yang ditemukan Langkah 5 (A_H90 recall 3,7x
   lebih tinggi, presisi lebih rendah) TERNYATA BUKAN model yang lebih
   baik - itu titik operasi berbeda pada kurva presisi-recall yang SETARA.**
   Langkah 6 membuktikan ini langsung: pada presisi>=0,40 yang sama persis
   (dicari di TEST itu sendiri, oracle/ceiling, bukan threshold dari
   VALIDATION), A_H90 dan model observasi mencapai recall yang SAMA secara
   statistik (0,3785 vs 0,3746, selisih 0,0039 << sd 0,26-0,28, menang di
   fold berbeda-beda 2-2). Threshold yang dipilih `select_lifecycle_
   threshold()`/`select_precision_constrained_threshold()` dari VALIDATION
   di Langkah 5 kebetulan mendarat di titik recall lebih tinggi/presisi
   lebih rendah untuk A_H90 dan sebaliknya untuk model observasi - itu
   ARTEFAK PEMILIHAN THRESHOLD, bukan perbedaan kualitas ranking model.
   **KESIMPULAN AKHIR: hipotesis eksperimen ini (melatih di level lifecycle
   menghasilkan lift/kualitas ranking yang lebih baik daripada model
   observasi yang diagregasi ke level lifecycle) TIDAK TERBUKTI.** Kedua
   pendekatan setara pada populasi dan gerbang presisi yang identik -
   sesuai instruksi eksplisit master prompt eksperimen ini ("kalau
   reformulasi ini hanya menaikkan angka lewat base rate/artefak tanpa
   menaikkan sinyal asli, katakan itu terus terang dan hentikan
   eksperimen").
2. **Formulasi label lifecycle sangat sensitif terhadap lebar window
   split** - H/L yang mendekati atau melebihi lebar VALIDATION/TEST (1
   tahun) collapse ke base rate degenerate (100% atau kosong). Ini bukan
   soal statistik lemah, tapi struktural: negatif tepercaya butuh RUNWAY
   (L+H hari) yang tersedia SEBELUM cutoff split - kalau window split lebih
   sempit dari L+H, nyaris tidak ada lifecycle yang punya runway itu.
3. **Protokol rolling-backtest `cli.py` (6 fold x 60 hari) dikalibrasi untuk
   skala observation-level (ratusan ribu baris) - TIDAK portable apa
   adanya ke skala lifecycle** (~13-20rb baris TOTAL, bukan per fold).
   Dibutuhkan fold lebih lebar (180 hari) dan lebih sedikit (4, bukan 6),
   plus buffer eksplisit sebelum `data_end` supaya fold TIDAK terlalu dekat
   untuk sempat resolve H - kalau eksperimen lifecycle-level lain
   dilakukan ke depan, WAJIB pakai `scratch/lifecycle_reform/
   step4b_wide_rolling_backtest.py::wide_fold_windows()` (atau turunannya),
   BUKAN `cli.py::_rolling_fold_windows()` apa adanya.
4. **A_H90 lift TIDAK stabil lintas fold** (3,30x+/-1,78) - satu fold
   (base rate 2,61%) menyumbang lift 10,65x sendirian, jauh di atas fold
   lain (1,24x-5,09x). sd yang besar ini berarti klaim "A_H90 lebih baik"
   TIDAK melebihi noise antar-fold untuk metrik PR-AUC/lift murni - yang
   robust lintas fold justru metrik recall/presisi apple-to-apple di atas
   (Langkah 5), karena populasi & labelnya dipaksa identik per fold
   (mengurangi variasi akibat definisi, bukan model).
5. 44-48% baris di semua varian lifecycle jatuh ke `LOW_HISTORICAL_SUPPORT`
   untuk `part_model_category` - `MIN_PART_MODEL_SUPPORT=300` dikalibrasi
   untuk skala observation-level (251rb baris), bukan skala lifecycle
   (13-20rb baris). Confound yang sama persis dengan jebakan threshold yang
   sudah didokumentasikan di `features_survival.py` untuk model survival
   (200 vs 300, E-03) - TIDAK diperbaiki di sini (instruksi eksplisit "fitur
   v6 apa adanya"), tapi kemungkinan MENAHAN performa A_H90 lebih jauh dari
   yang seharusnya bisa dicapai kalau threshold-nya disesuaikan skala.

**Keputusan** **TIDAK dipromosikan/di-deploy - hipotesis DITOLAK.** Melatih
langsung di level lifecycle (fitur v6 apa adanya, formulasi A/B yang diuji)
TIDAK menghasilkan kualitas ranking yang lebih baik daripada model observasi
production yang diagregasi ke level lifecycle (E-49) - keduanya setara pada
gerbang presisi/populasi/label yang identik (Langkah 6). Trade-off
presisi/recall yang sempat terlihat di Langkah 5 murni artefak titik
threshold, bukan sinyal model. Kode TETAP di `scratch/lifecycle_reform/`
(tidak masuk `src/partrisk/`), model production TIDAK diubah - CatBoost
`v4` observation-level tetap satu-satunya mesin keputusan Q2.

**Kenapa ini bukan kegagalan sia-sia**: pertanyaan asli (E-49/E-50) - apakah
mismatch training-observasi vs evaluasi-lifecycle itu sendiri yang membatasi
recall - sekarang punya jawaban empiris: TIDAK. Bottleneck recall repo ini
(E-56/E-58/E-62: 99,4% kerusakan TEST terlewat, blind spot first-failure
dan late-life) bukan soal LEVEL training (observasi vs lifecycle), karena
mengubah level itu saja tidak mengubah kurva presisi-recall yang bisa
dicapai - konsisten dengan kesimpulan E-62 bahwa bottleneck-nya data
(informasi yang tersedia untuk membedakan PART berisiko), bukan pilihan
skema/arsitektur. `MIN_PART_MODEL_SUPPORT` yang dikalibrasi utk skala
observation-level (temuan 5) TETAP confound yang belum diuji terpisah -
kalau ingin dipastikan bukan penahan performa A_H90, itu eksperimen kecil
tersendiri (bukan alasan untuk membuka lagi pencarian lifecycle-training
secara umum, sesuai `docs/DECISIONS.md` §12 - jangan re-buka pencarian
struktural tanpa sinyal baru yang belum dicoba).

---

## E-79 · Audit awal model scrap (Q3) - dua sumber data baru diuji, DUA-DUANYA DITOLAK: QC/damage_report bocor waktu, MTBF menurunkan performa

2026-08-30 · audit + ablasi ad-hoc (skrip di scratchpad, tidak masuk repo) - tidak ada kode/model production diubah, database read-only

**Konteks** Model scrap (Q3, `models/scrap/`, saat ini `v2`) belum pernah
punya sesi investigasi khusus seperti FASE 8 (E-46..E-77) untuk model
kerusakan - nol entri sebelumnya di dokumen ini. TEST cuma 28 kejadian
scrap (489 baris), TRAIN 25 kejadian (1.093 baris), era data 2025-04-01+
saja (`SCRAP_ERA_START`). Model saat ini: `Gabungan LogReg+RF` (dipilih di
muka, bukan dari data - alasan sudah didokumentasikan di
`docs/METHODOLOGY.md`), 7 fitur (`item_type_category`, umur PART/siklus,
riwayat repair/kegagalan SEBELUM `failure_onset_on`). User meminta
dikembangkan - diaudit dulu apakah ada sumber sinyal genuinely belum
terpakai (pola sama dengan E-48 untuk model kerusakan), sebelum menyentuh
fitur yang sudah ada (7 fitur itu terlalu sedikit datanya untuk ablasi
family seperti E-77).

**Kandidat 1 - `journal.t_item_quality_control`/`t_item_test_quality_control`
(damage_report/damage_analysis, hasil tes diagnostik per komponen)**

**Pertanyaan** 863 catatan QC (2025-04-22 s/d sekarang, berhimpit dengan
era scrap), mencakup 42/53 kejadian scrap era ini lewat pemetaan
`item_pairing_code` (langsung, tidak perlu indirection `sn_ref` seperti
MTBF). Kelihatan seperti sinyal diagnostik yang tercatat SEBELUM vonis
final (repairable/unrepairable) - kalau benar, legitimate untuk prediksi
(beda dari kasus E-48 yang jelas-jelas SETELAH vonis).

**Metode** Pasangkan tiap catatan QC ke episode kegagalan TERDEKAT
SEBELUMNYA (`merge_asof` per item, direction="backward", lag<=60 hari
buang yang kemungkinan salah pasang ke episode lama) - 665 match valid.
Rekonstruksi timestamp event TERMINAL (vonis REPAIRED/UNREPAIRABLE/BROKEN)
per episode dari `t_item_journey` langsung (bukan cuma status-nya, timestamp-nya), bandingkan dengan `created_on` QC.

**Hasil** 660/665 QC record memang `created_on <= verdict_time` (urutan
waktu secara teknis benar) - TAPI selisihnya **median 0 hari, maksimum 15
detik**. QC record BUKAN langkah diagnostik yang mendahului vonis dengan
lead-time berarti - dia tercatat PRAKTIS BERSAMAAN dengan vonis, kemungkinan
besar satu transaksi administratif yang sama (teknisi submit hasil QC ->
sistem langsung mencatat status akhir).

**Keputusan** **DITOLAK** - walau urutan waktu formalnya benar, memakai
data ini sebagai fitur prediksi = model membaca ulang kesimpulan teknisi
yang sudah final, bukan memprediksi. Kelas kebocoran sama dengan E-48
(data hasil repair), dibuktikan lewat pengukuran lag langsung kali ini
(bukan diasumsikan dari nama tabel).

**Kandidat 2 - `journal.t_mtbf` (bacaan jam-operasi, sama dengan E-48/E-66
untuk model kerusakan, tapi di sini landmark-nya `failure_onset_on` bukan
`installed_on`)**

**Pertanyaan** MTBF mulai 2025-01-15, TRAIN model kerusakan mulai 2014
(makanya E-48 gagal - TRAIN 0% tercakup). TRAIN model SCRAP mulai
2025-04-01 - SUDAH SELURUHNYA di dalam window cakupan MTBF. Masalah
kalender yang menghalangi MTBF di model kerusakan seharusnya TIDAK
berlaku di sini - kandidat yang belum pernah dicoba untuk scrap.

**Metode** Reuse TOTAL fungsi MTBF point-in-time yang sudah ada
(`train_mtbf_candidate.py::_sn_to_item_mapping()`/`_mtbf_records_by_item()`/
`attach_mtbf_features()`, TIDAK diimplementasi ulang), landmark
`observation_on = failure_onset_on` (bukan `installed_on`). Baseline (7
fitur production apa adanya) vs +MTBF (10 fitur), model SAMA
(`Gabungan LogReg+RF`), metodologi evaluasi SAMA dengan `compare_models()`
production (rolling cutoffs pra-TEST + TEST sekali).

**Hasil** Cakupan MTBF SANGAT TINGGI seperti diprediksi - TRAIN 99,73%,
TEST 97,96% (vs 0% untuk model kerusakan). TAPI menambahkannya
**menurunkan performa drastis**, bukan menaikkan:

| Varian | Rolling ROC-AUC | Rolling PR-AUC | TEST ROC-AUC | TEST PR-AUC |
|---|---:|---:|---:|---:|
| baseline (7 fitur) | 0,8081 | 0,0674 | 0,7631 | **0,2596** |
| +MTBF (10 fitur) | 0,5472 | 0,0440 | 0,6219 | **0,1250** |

TEST PR-AUC turun hampir separuh (0,26->0,13), rolling ROC-AUC jatuh ke
nyaris acak (0,55). Arah penurunan KONSISTEN di kedua evaluasi (rolling
DAN TEST) - bukan sekadar derau satu arah.

**Temuan** Cakupan tinggi TIDAK otomatis berarti sinyal berguna - beda
dari model kerusakan (E-66, MTBF terbukti membantu KETIKA tercakup), di
sini menambah 3 kolom ke model dengan TRAIN cuma 25 kejadian scrap
kemungkinan besar cuma menambah noise/dimensi (VotingClassifier LogReg+RF
sensitif terhadap kolom tambahan pada sampel sekecil ini) - ATAU bacaan
jam-operasi genuinely tidak berkorelasi dengan "apakah kerusakan ini bisa
diperbaiki", beda dengan "apakah PART akan rusak" (pertanyaan model
kerusakan) di mana intensitas pemakaian relevan.

**Keputusan** **DITOLAK** - `journal.t_mtbf` TIDAK ditambahkan ke fitur
scrap. Kode ad-hoc dihapus, tidak ada perubahan production.

**Kandidat 3 - ablasi family-level 7 fitur yang SUDAH ADA (mirip E-77,
leave-one-family-out - bukan leave-one-out murni, 7 fitur terlalu sedikit
untuk itu bermakna)**

**Pertanyaan** Dua sumber data baru ditolak (di atas). Apakah salah satu
dari 7 fitur yang sudah ada justru MENAHAN performa (mirip E-77 yang
menemukan LOCAL_DENSITY_FEATURES kontribusi kecil untuk model kerusakan)?

**Metode** 4 family: `item_type` (1 kolom), `age` (umur total + umur
siklus, 2 kolom), `repair_history` (jumlah+ada-tidaknya repair sebelumnya,
2 kolom), `failure_history` (jumlah kegagalan sebelumnya + first-failure
flag, 2 kolom). FULL vs FULL-minus-tiap-family vs HANYA-tiap-family
sendirian, model & evaluasi SAMA PERSIS `compare_models()` production.

**Hasil**

| Varian | Rolling ROC/PR | TEST ROC/PR |
|---|---|---|
| FULL (baseline) | 0,8081/0,0674 | 0,7631/**0,2596** |
| minus item_type | 0,8708/0,0835 | 0,6179/0,0963 (jatuh jauh) |
| minus age | 0,5925/0,0311 (jatuh jauh) | 0,6541/0,1782 |
| minus repair_history | 0,7825/0,0706 | 0,7482/0,2516 (nyaris sama) |
| minus failure_history | 0,8103/0,0681 | 0,7557/**0,2596** (PR-AUC IDENTIK) |
| HANYA item_type sendiri | 0,5972/0,0305 | 0,6553/0,1660 |
| HANYA age sendiri | 0,8435/0,0746 | 0,5168/0,0682 (TEST jatuh drastis) |
| HANYA repair_history sendiri | 0,5528/0,0161 | 0,5205/0,0618 |
| HANYA failure_history sendiri | 0,7109/0,0230 | 0,4884/0,0585 (di bawah acak) |

**Temuan** `item_type` dan `age` PENTING (membuang salah satunya
menjatuhkan TEST atau rolling drastis), TAPI keduanya SENDIRIAN juga
lemah - efeknya dari KOMBINASI, bukan satu fitur dominan (beda dari model
kerusakan di E-77 yang history mendominasi sendirian). `repair_history`
dan `failure_history` bisa dibuang TANPA mengubah TEST secara berarti
(minus failure_history bahkan PR-AUC IDENTIK ke 4 desimal) - TAPI dengan
TEST cuma 28 positif, ini TIDAK cukup kuat untuk diklaim "terbukti tidak
berguna", cuma "tidak menunjukkan kontribusi terukur pada sampel sekecil
ini". Tidak ada satu pun varian ablasi yang MENGALAHKAN FULL - jadi tidak
ada rekomendasi pruning yang actionable, cuma observasi.

**Keputusan** **Tidak ada perubahan fitur** - tidak ada variant yang
menang jelas dari FULL, dan sampel terlalu kecil untuk membedakan "sama
saja" dari "genuinely tidak berguna". Menutup audit awal Kandidat 3.

**Kesimpulan audit awal (lengkap, 3 kandidat)**: dua sumber data baru
(QC, MTBF) ditolak dengan bukti jelas (kebocoran waktu terverifikasi,
penurunan performa terverifikasi); ablasi 7 fitur yang ada tidak
menemukan satu pun family yang menahan performa secara actionable.
Konsisten dengan keyakinan awal user bahwa model ini data-limited (53
kejadian scrap total) - bukan soal fitur yang belum ditemukan ATAU fitur
yang salah dipilih. Tidak ada lever tersisa yang tervalidasi untuk sesi
ini. Opsi realistis: terima kondisi sekarang, atau tunggu lebih banyak
kejadian scrap terkumpul seiring waktu (era 2025-04-01+ terus bertambah,
sama seperti pertumbuhan window MTBF model kerusakan).

---

## E-80 · Leave-one-out E-77 (FULL 32 fitur minus satu keluarga) - AGE dan DENSITY genuinely dibutuhkan DI DALAM konteks penuh, TEMPORAL dan CLIENT aman dibuang - additive E-77 TERBUKTI menyesatkan untuk 2 dari 4 keluarga

2026-08-30 · skrip ad-hoc di scratchpad (tidak masuk repo) - tidak ada kode/model production diubah

**Pertanyaan** E-77 (additive dari CORE) sengaja MENAHAN keputusan pruning
- metodologi additive mengukur kontribusi MANDIRI tiap keluarga di atas
CORE kosong, TAPI TIDAK membuktikan keluarga mana yang aman dibuang DARI
DALAM 32 fitur yang sudah ada (redundansi/interaksi bisa membuat dua
metodologi berbeda kesimpulan - persis kekhawatiran eksplisit E-77:
"Density mungkin masih penting BERSAMA History walau lemah sendirian -
belum diuji"). Sekarang diuji leave-one-out yang sebenarnya: FULL(32)
minus SATU keluarga, keluarga lain (termasuk CORE+HISTORY) tetap utuh.

**Metode** 4 keluarga diuji (AGE=6, TEMPORAL=2, CLIENT=1, DENSITY=7 -
HISTORY/CORE tidak diuji, sudah mapan sebagai backbone sinyal di E-77).
Rolling 6 fold IDENTIK metodologi E-73/E-74/E-77/`cli.py::rolling-backtest`
(`_rolling_fold_windows`/`_assign_rolling_split`), `CATBOOST_PARAMS`/
seed TIDAK diubah. `cat_features` CatBoost disesuaikan otomatis per
varian (hanya kolom kategorikal yang benar-benar tersisa).

**Hasil (mean +/- sd, 6 fold; klaim beda hanya kalau melebihi 1 sd
selisih per-fold, kriteria SAMA sepanjang sesi)**

| Varian dibuang | PR-AUC vs FULL | ROC-AUC vs FULL | Verdict |
|---|---|---|---|
| AGE (6 fitur) | -0,0130 +/- 0,0076 | -0,0080 +/- 0,0108 | **PR-AUC LEBIH BURUK** (melebihi 1sd) |
| DENSITY (7 fitur) | -0,0076 +/- 0,0080 (tidak signifikan) | **-0,0380 +/- 0,0176** | **ROC-AUC LEBIH BURUK** (melebihi 1sd) |
| TEMPORAL (2 fitur) | -0,0034 +/- 0,0085 | -0,0022 +/- 0,0086 | TIDAK signifikan, setara FULL |
| CLIENT (1 fitur) | +0,0022 +/- 0,0081 | -0,0015 +/- 0,0043 | TIDAK signifikan, setara FULL |

**Temuan** Kekhawatiran eksplisit E-77 TERBUKTI BENAR: metodologi additive
MENYESATKAN untuk AGE dan DENSITY. E-77 (additive) menilai AGE kontributor
kedua terkuat (CORE+AGE -0,0598 vs FULL) dan DENSITY kontributor lemah
(CORE+DENSITY -0,0900, mirip CLIENT/TEMPORAL) - urutan yang SAMA SEKALI
TIDAK cocok dengan leave-one-out: DENSITY (bukan AGE) yang paling parah
kalau dibuang (ROC-AUC -0,038, 2x lebih besar dari selisih AGE), dan
keduanya (AGE, DENSITY) genuinely dibutuhkan DI DALAM konteks 32 fitur
penuh - kontribusinya MANDIRI kecil TAPI berinteraksi dengan History/
fitur lain. TEMPORAL dan CLIENT KONSISTEN lemah di kedua metodologi -
additive DAN leave-one-out sama-sama bilang tidak signifikan, sinyal
yang lebih bisa dipercaya karena dua metodologi berbeda sepakat.

**Keputusan** **CORE+HISTORY (16 fitur) dari E-77 DITOLAK sebagai
kandidat pruning** - additive-nya SEKARANG terbukti salah arah (akan
membuang AGE dan DENSITY yang leave-one-out konfirmasi genuinely
dibutuhkan). **TEMPORAL (`month_sin`/`month_cos`) dan CLIENT
(`client_category`) adalah kandidat pruning yang JUJUR** (konsisten
tidak signifikan di DUA metodologi berbeda) - simplifikasi 32->29 fitur,
BUKAN 32->16. **TIDAK dieksekusi di sesi ini** - ini cuma mengonfirmasi
KANDIDAT aman dibuang, bukan keputusan pruning final (butuh retrain
model production sungguhan + evaluasi gerbang precision@40% lifecycle
sebelum benar-benar mengubah `config.FEATURE_COLUMNS`, sesuai hard rule
"jangan ubah production sebelum tervalidasi"). Model production TIDAK
diubah.

---

## E-81 · P0 lifecycle correction — dataset bersih dari exposure setelah PART dilepas, rolling backtest diulang

2026-08-31 · implementasi korektnes + audit database + rolling 6-fold

**Pertanyaan**: apakah TRAIN/VALIDATION/TEST benar-benar hanya mengamati
PART ketika masih installed/exposed, dengan lifecycle berakhir pada
failure, `DISMANTLED`, atau `RETURNED`, lalu instalasi berikutnya membuka
cycle baru? Ini menindaklanjuti gap terbuka E-71; tujuan utamanya definisi
populasi bisnis yang benar, BUKAN mengejar PR-AUC.

**Temuan data tambahan**: memperlakukan string status `RETURNED` secara
literal belum cukup. Pada identifier PART yang sama, 2.845 cycle aktif
palsu di snapshot sekarang berakhir sebagai `status=OK, activity=RECEPTION`
(representasi histori lama untuk PART yang kembali). Karena itu pola ini
dinormalisasi ke `cycle_end_reason='RETURNED'`. Tanpa normalisasi tersebut,
active cohort masih berisi 2.845 PART berstatus terbaru `OK`.

**Implementasi**:

- `get_cycles()` mengambil event penutup pertama per installation sequence:
  `FAILURE`, `DISMANTLED`, `RETURNED`/`OK+RECEPTION`, install berikutnya,
  atau data end;
- failure menang jika event `DISMANTLED` yang sama juga merupakan onset;
- failure setelah pelepasan tidak lagi ditempelkan ke cycle yang sudah tutup;
- klasifikasi hanya memakai negatif sampai `cycle_end_on - 30 hari`;
- survival mencensor `RETURNED`/`DISMANTLED` tepat pada `cycle_end_on`.

**Audit lifecycle pada snapshot data**:

| `cycle_end_reason` | Cycle |
|---|---:|
| RIGHT_CENSORED_AT_DATA_END | 13.857 |
| FAILURE | 5.937 |
| RETURNED | 3.455 |
| REINSTALL_WITHOUT_RECORDED_FAILURE | 559 |
| DISMANTLED | 483 |

Active initial-model cohort menjadi **13.767 PART dan 100% latest status
`INSTALLED`**. Sebanyak 168 onset yang dulu terasosiasi dengan cycle lama
ternyata terjadi setelah penutup lebih awal dan sekarang tidak lagi
dilabeli sebagai failure cycle itu. Audit row menghasilkan **0 observasi
pada/setelah waktu pelepasan** dan **0 negatif eligible setelah
`last_confirmable_observation_on`**.

Dataset fresh yang dibangun oleh backtest: 1.338.765 observasi mentah ->
366.965 eligible (5.919 failure). Split production yang masuk evaluasi:

| Split | Baris | Failure | Baris post-removal |
|---|---:|---:|---:|
| TRAIN | 288.006 | 3.828 | **0** |
| VALIDATION | 32.865 | 860 | **0** |
| TEST | 32.206 | 1.060 | **0** |

**Rolling lifecycle backtest v4, retrain fresh tiap fold**:

| Fold TEST | Baris | Failure | target 0,30 (P/R/alert) | target 0,40 (P/R/alert) |
|---|---:|---:|---:|---:|
| 2025-09-01—2025-10-31 | 6.696 | 168 | 0,3250 / 0,0774 / 40 | 0,2500 / 0,0119 / 8 |
| 2025-10-31—2025-12-30 | 6.749 | 172 | 0,2941 / 0,0291 / 17 | 0,2941 / 0,0291 / 17 |
| 2025-12-30—2026-02-28 | 8.152 | 142 | 0,1707 / 0,0493 / 41 | 0 / 0 / 0 |
| 2026-02-28—2026-04-29 | 9.457 | 145 | 0 / 0 / 0 | 0 / 0 / 0 |
| 2026-04-29—2026-06-28 | 9.770 | 397 | 0,4118 / 0,0176 / 17 | 0,7500 / 0,0076 / 4 |
| 2026-06-28—2026-08-27 | 5.136 | 380 | 0,7778 / 0,1105 / 54 | INFEASIBLE |

Ringkasan: target 0,30 feasible 6/6, precision **0,3299 +/- 0,2618**,
recall **0,0473 +/- 0,0409**. Target 0,40 feasible 5/6, precision
**0,2588 +/- 0,3068**, recall **0,0097 +/- 0,0120**. Target 0,85 hanya
feasible 1/6 dan menghasilkan 0 alert/0 recall.

**Keputusan**: koreksi lifecycle **DITERIMA sebagai P0 correctness fix**.
Angka backtest baru menjadi baseline evaluasi pada population-at-risk yang
benar; perbedaannya dari hasil rolling lama tidak boleh diklaim sebagai
improvement model karena populasi/label yang dievaluasi memang berubah.
Tidak ada retrain/promotion artifact production pada eksperimen ini.

---

## E-82 · Retrain failure v7 pada lifecycle terkoreksi — tersimpan, promosi DITAHAN

2026-08-31 · `python -m partrisk.engines.failure.train`, tanpa
`--force-promote`

**Dataset**: definisi lifecycle E-81, 288.006 TRAIN / 32.865 VALIDATION /
32.206 TEST; 5.919 failure eligible. Active cohort untuk basis cutoff
13.767 PART.

| Split | ROC-AUC | PR-AUC |
|---|---:|---:|
| TRAIN | 0,8658 | 0,1224 |
| VALIDATION | 0,7810 | 0,1153 |
| TEST | 0,8218 | 0,2007 |

Perbandingan promotion memakai dukungan fitur beku dan VALIDATION sebagai
decisive split:

| Model | VALIDATION PR-AUC | ROC-AUC | Recall@capacity | Precision@capacity | Brier |
|---|---:|---:|---:|---:|---:|
| kandidat v7 | 0,1152 | 0,7806 | 0,2860 | 0,1105 | 0,0242 |
| incumbent v6 | **0,1155** | 0,7748 | **0,3105** | **0,1199** | 0,0243 |

TEST informasional juga tidak mendukung promosi: kandidat PR-AUC 0,2005
vs v6 0,2183; recall@capacity 0,3660 vs 0,3849. Gerbang target precision
40% memilih threshold 0,4444 dari VALIDATION, tetapi pada TEST hanya
precision 0,1667, recall 0,0009, dan 6 alert.

**Keputusan**: artifact lengkap disimpan sebagai `models/failure/v7/`,
tetapi promosi **DITAHAN** oleh gerbang VALIDATION. `models/failure/CURRENT`
tetap `v6`; tidak dilakukan force-promote.

---

## E-83 · Retrain Q1 survival v2 pada lifecycle terkoreksi — promosi DITAHAN setelah evaluasi apples-to-apples

2026-08-31 · `python -m partrisk.engines.survival.train`, tanpa force
promotion

**Alasan retrain**: lifecycle/censoring adalah target langsung Q1.
Dataset terkoreksi menghasilkan 86.190 landmark TRAIN (15.058 lifecycle),
5.542 VALIDATION (2.343 lifecycle), dan 6.581 TEST (3.090 lifecycle).

Training awal menghasilkan RSF C-index full-landmark 0,8474 pada
VALIDATION dan 0,8759 pada TEST. Gerbang bawaan menahan kandidat karena
Brier@30d VALIDATION 0,0356 sedikit membaik dari metadata incumbent
0,0357, tetapi Brier@90d 0,0529 sedikit memburuk dari 0,0528.

Karena metadata v1 berasal dari definisi lifecycle lama, v1 dan v2 lalu
dievaluasi ulang pada dataset lifecycle baru yang sama:

| Populasi | Model | C-index | Brier@30d | Brier@90d |
|---|---|---:|---:|---:|
| VALIDATION full landmark | v1 | 0,843170 | 0,035695 | **0,052580** |
| VALIDATION full landmark | v2 | **0,847435** | **0,035604** | 0,052907 |
| VALIDATION t0-only | v1 | 0,812060 | 0,062144 | **0,083532** |
| VALIDATION t0-only | v2 | **0,814320** | **0,061754** | 0,084485 |
| TEST full landmark | v1 | 0,874908 | **0,049892** | **0,053995** |
| TEST full landmark | v2 | **0,875860** | 0,050299 | 0,054024 |
| TEST t0-only | v1 | **0,812213** | **0,097401** | **0,099152** |
| TEST t0-only | v2 | 0,811017 | 0,098254 | 0,099305 |

**Keputusan**: hasil campuran dan TEST t0-only tidak mendukung promosi.
Artifact v2 disimpan di `models/survival/v2/`, tetapi
`models/survival/CURRENT` tetap `v1`. Tidak ada force promotion.

---
