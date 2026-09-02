# Predictive Maintenance

Predictive maintenance untuk PART armada: prediksi kerusakan, prioritas
perawatan, dan perkiraan risiko scrap (dibuang vs diperbaiki). Database
**hanya dibaca** - repo ini tidak pernah membuat, mengubah, atau menghapus
apa pun di database, dan tidak bergantung pada schema `analytics` hasil
research.

## Tiga pertanyaan, tiga model

| # | Pertanyaan | Model | Status |
|---|---|---|---|
| Q1 | Kapan PART ini akan rusak? | Random Survival Forest (landmark) | **advisory** - tidak mengatur keputusan |
| Q2 | PART mana yang dirawat duluan? | CatBoost `v6`, 32 fitur, horizon 30 hari | **mesin keputusan utama** |
| Q3 | Kalau rusak, bisa diperbaiki atau scrap? | LogReg+RF `v2`, 7 fitur | **flag kualitatif saja** |

```
PART terpasang normal
        |
        v
  Q2  MODEL KERUSAKAN   peluang rusak 30/60/90/120 hari  -> menentukan
        |                                                   antrian kerja
        v                                                   (tier_score)
   PART RUSAK (masuk bengkel)
        |
        v
  Q3  MODEL SCRAP       peluang TIDAK bisa diperbaiki (bersyarat)
        |
        v
   Dibuang  /  Diperbaiki & dipasang lagi

  Q1  MODEL SURVIVAL berjalan paralel dari awal - "risiko mulai naik ~N
      hari lagi" (days_until_survival_90pct). Field advisory pada
      FailurePrediction, TIDAK PERNAH mengubah risk_level/tier_score/rank
      Q2 - lihat docs/DECISIONS.md §1.
```

Ketiganya berbagi satu jalur pembacaan data (`data_reader.py` -> `features.py`),
jadi definisi "kerusakan", "siklus pemasangan", dan pembersihan datanya
dijamin sama untuk ketiganya.

## Cara jalan

### 1. Persiapan

```bash
pip install -r requirements-serving.txt   # atau requirements.txt untuk training saja
cp .env.example .env                      # lalu isi kredensial database
```

`requirements.txt` hanya kebutuhan training; `requirements-serving.txt`
menambah FastAPI, Streamlit, dan test. `requirements.lock.txt` adalah
snapshot versi PERSIS untuk deployment yang butuh reproduksi environment.

### 2. Training / retraining

```bash
python -m partrisk.engines.failure.train          # model kerusakan (Q2)
python -m partrisk.engines.failure.train --force-promote
python -m partrisk.engines.scrap.train             # model scrap (Q3)
python -m partrisk.engines.survival.train          # model survival (Q1, advisory)
```

Hasil tersimpan sebagai versi baru (`models/failure/vN/`, dst). Model
production hanya diganti kalau kandidat **tidak lebih buruk** pada data uji -
lihat `docs/DECISIONS.md` §5 untuk aturan promosi tiap model.

### 3. Menjalankan API

```bash
uvicorn partrisk.api.app:app --reload
```

Dokumentasi interaktif: <http://127.0.0.1:8000/docs>

### 4. Menjalankan dashboard

API harus sudah jalan lebih dulu.

```bash
streamlit run dashboard/app.py
```

<http://localhost:8501>

### 5. Docker

```bash
docker compose up --build
```

API di `localhost:8000`, dashboard di `localhost:8501`. Satu image dipakai
dua kali dengan perintah start berbeda. Database **tidak** ikut di-container -
yang dipakai adalah PostgreSQL yang sudah ada, kredensial dari `.env` di host.

### CLI manual lainnya

```bash
python -m partrisk.cli pipeline               # uji jalur database -> fitur, tanpa model
python -m partrisk.cli predict --top 20        # batch prediction manual ke terminal/CSV
python -m partrisk.cli golden-batch generate --out FILE   # oracle regresi (lihat docs/DECISIONS.md)
python -m partrisk.cli golden-batch compare A B
python -m partrisk.cli baseline-performance    # RSS/latency model kerusakan
python -m partrisk.cli baseline-comparison     # precision@kapasitas vs kebijakan tanpa model
python -m partrisk.cli evaluate-survival       # evaluasi model survival vs model statis
python -m partrisk.cli rolling-backtest              # backtest temporal bergulir (row-level)
python -m partrisk.cli rolling-lifecycle-backtest    # wajib sebelum klaim kandidat baru (E-49)
python -m partrisk.cli train-mtbf-candidate          # pantau kandidat +MTBF window 2025+ (E-66)
python -m partrisk.cli bootstrap-ci                  # CI bootstrap metrik headline
```

Command riset FASE 8 lainnya (`attach-gate`, `precision-gate-experiment`,
`lifecycle-gate-experiment`) ada di `cli.py` - alat sekali-pakai untuk
eksperimen yang sudah terdokumentasi di `docs/EXPERIMENTS.md`, bukan
dijalankan rutin.

## Endpoint

| Endpoint | Kegunaan |
|---|---|
| `GET /health` | status aplikasi, versi model, kesegaran cache batch (`?check_database=true` untuk ikut menguji koneksi DB) |
| `GET /api/v1/model` | versi, fitur, ambang risiko, metrik uji kedua model |
| `GET /api/v1/parts/{item_id}/failure` | peluang rusak 30/60/90/120 hari + field advisory survival (Q1) |
| `GET /api/v1/parts/{item_id}/scrap` | peluang tidak bisa diperbaiki **jika** rusak |
| `GET /api/v1/parts/{item_id}/assessment` | gabungan Q1+Q2+Q3 + rekomendasi + faktor risiko |
| `GET /api/v1/parts/{item_id}/history` | tanggal kerusakan dan lokasi yang pernah tercatat |
| `POST /api/v1/parts/{item_id}/resolve-alert` | tutup alert lifecycle aktif untuk PART ini (`serving/alerts.py`) |
| `GET /api/v1/recommendations` | antrian RESMI: hanya PART yang lolos gerbang presisi (`official_queue_only=true`, default) - ukuran dinamis, boleh kosong. `official_queue_only=false` = mode eksplorasi (seluruh armada, terurut `tier_score`, tidak disaring `risk_level`) |
| `GET /api/v1/overview` | angka ringkas armada + daftar teratas |
| `GET /api/v1/filters` | nilai filter yang benar-benar ada di data |
| `GET /api/v1/terminals` | ringkasan per Terminal fisik (jumlah PART, sebaran risiko, PART paling berisiko) |
| `GET /api/v1/locations/map` | sebaran risiko per lokasi + koordinat (geocoding, kalau ada) |
| `GET /api/v1/monitoring/metrics`, `/failure`, `/scrap` | metrik monitoring: offline (dari training) + live (populasi aktif) |

Filter `/api/v1/recommendations`: `search`, `risk`, `priority`, `item_type`,
`client`, `location`, `replacement_candidates_only`, `limit`, `offset`.

## Angka performa - dan artinya

**Q2, model kerusakan (`v6`, TEST n=45.023, positif 1.121 = 2,49%):**
ROC-AUC 0,8501 · PR-AUC 0,2116 (lift ~8,5x dibanding acak) · Brier
terkalibrasi 0,0221. Angka mode eksplorasi (peringkat penuh, bukan lagi
dasar antrian resmi): precision@200/bln 0,2604 · recall@kapasitas 0,3640.
**Keputusan promosi antar-versi digerbang di VALIDATION, bukan TEST**
(`docs/DECISIONS.md` §13) - VALIDATION jauh lebih stabil antar-retrain
(ROC-AUC/PR-AUC ~0,815/~0,111) daripada TEST, jangan bandingkan versi
model dari angka TEST saja (lihat `docs/EXPERIMENTS.md` E-44).

**Antrian resmi (`docs/DECISIONS.md` §11, sejak 2026-08-25):** gerbang
presisi, bukan kuota tetap - PART hanya direkomendasikan kalau
`failure_probability_30d` melewati threshold yang teruji jujur di TEST
(dicari dari VALIDATION saja). `v6`: threshold 0,4762, TEST presisi
0,50, recall 0,0027, 6 alert. **Antrian boleh kosong** kalau
memang tidak ada PART yang cukup meyakinkan - model tidak dipaksa mengisi
kuota. **Bukan** "model tahu kapan PART akan rusak" - model mengurutkan
risiko, bukan meramal tanggal.

**Q3, model scrap (`v2`, TEST n=489, positif 28):** ROC-AUC 0,763 · PR-AUC
0,260 · presisi 39,1% · recall 32,1% - **bertumpu pada 9 true positive**.
Selalu tampilkan sebagai band + jumlah kejadian pendukung, **jangan pernah**
persen desimal - datanya terlalu sedikit untuk presisi sebesar itu.

**Q1, model survival:** `median_days_to_failure` mayoritas `None` (kurva
S(t) belum turun sampai 50% dalam jangkauan data - itu jujur, bukan bug).
Field yang terisi dan bisa ditindaklanjuti adalah `days_until_survival_90pct`
("risiko mulai naik ~N hari lagi").

Detail metrik lengkap, interval kepercayaan, dan metodologi ada di
`docs/METHODOLOGY.md`.

## Yang tidak bisa dijawab sistem ini

- **Tanggal kerusakan pasti** - ketiga model memberi peluang/perkiraan
  jangka waktu, bukan tanggal.
- **PART di luar 9 tipe yang dikenal model scrap** - ditandai
  `item_type_known_to_model: false`, diperlakukan hati-hati, bukan dihitung
  sebagai LOW.
- **PART yang sedang tidak terpasang** - `status: NOT_SCORABLE`, bukan
  `LOW`. Tidak ada risiko kerusakan yang perlu diperkirakan untuk PART yang
  tidak sedang dipakai.
- **Horizon di atas ~1 tahun** - di luar jangkauan follow-up data training;
  model survival menolak ekstrapolasi (area di luar jangkauan ditandai, bukan
  disembunyikan).

## Struktur

```
src/partrisk/
├── core/
│   ├── config.py              konstanta ketiga model + kredensial database
│   ├── data_reader.py          SELECT read-only: event, siklus, kerusakan
│   ├── features.py             fitur Q2 (kerusakan) + Q3 (scrap) dari data mentah
│   └── features_survival.py    fitur Q1 (survival/landmark) - re-anchored per landmark
├── engines/
│   ├── predict.py             predict(item_id) Q2 (hazard chaining 30/60/90/120 hari), predict_scrap(item_id) Q3
│   ├── failure/
│   │   ├── train.py            latih Q2 + util versi/promosi model (`current_version`/`next_version` di-reuse survival & scrap)
│   │   ├── gate.py             gerbang presisi row-level & lifecycle (first-alert, E-49)
│   │   └── train_mtbf_candidate.py   kandidat +MTBF window 2025+ (E-66, TIDAK memengaruhi production)
│   ├── scrap/train.py          latih Q3
│   └── survival/
│       ├── train.py            latih Q1 + dataset landmark + evaluasi t0-only
│       ├── predict.py          predict(item_id) Q1, dinilai pada kondisi SEKARANG
│       └── curve.py            kurva survival, kalibrasi, fitting RSF/Cox PH
├── serving/
│   ├── single.py               prediksi satu PART, rekomendasi, penjelasan, riwayat
│   ├── batch.py                prediksi SELURUH PART aktif sekaligus (vectorized)
│   └── alerts.py                alert lifecycle (dedup, resolve-alert), tanpa persistence
├── api/
│   ├── app.py                  FastAPI: app, routes, db pool, settings, logging
│   ├── schemas.py               bentuk request/response API
│   └── services.py              geocoding (peta) + agregasi monitoring
└── cli.py                      pipeline/predict/golden-batch/baseline/backtest/dst - lihat `python -m partrisk.cli -h`

dashboard/       Streamlit (app.py + pages/); hanya bicara ke API lewat HTTP, tidak pernah ke DB
tests/           conftest.py + test_pipeline.py + test_lifecycle.py + test_gate.py +
                 test_serving.py + test_api.py (193 test)
docs/            METHODOLOGY.md (indeks per simbol) · CODE_NOTES.md (catatan
                 implementasi dari kode) · EXPERIMENTS.md (log eksperimen
                 kronologis, 80+) · DECISIONS.md (ADR)
models/          failure/{CURRENT,v3..v6} · scrap/{CURRENT,v1,v2} ·
                 survival/{CURRENT,v1} (advisory Q1, lihat docs/DECISIONS.md §7) ·
                 failure_mtbf_2025plus/ (kandidat pemantauan, bukan production, E-66/E-68)
```

`serving/` sengaja tidak punya `predict.py` sendiri untuk Q2/Q3 - keduanya
dipanggil lewat `engines/predict.py` (Q2, hazard chaining) dan
`engines/scrap/train.py`/fungsi terkait (Q3); `serving/single.py`
membungkus keduanya untuk lapisan HTTP satu-PART.

Fitur dihitung oleh **fungsi yang sama** untuk training maupun prediction -
kesetaraan single vs batch dan lintas ketiga model dijaga `tests/test_lifecycle.py`,
jadi tidak mungkin ada perbedaan antara fitur yang dipelajari model dan yang
dipakai production.

## Dokumentasi lanjutan

- **`docs/METHODOLOGY.md`** - keputusan teknis per simbol/konstanta
  (`grep NAMA_KONSTANTA docs/METHODOLOGY.md`).
- **`docs/CODE_NOTES.md`** - seluruh komentar implementasi yang sebelumnya
  berada di `src/`, `dashboard/`, dan `tests/`, dikelompokkan menurut file,
  scope fungsi/class, dan lokasi historisnya.
- **`docs/DECISIONS.md`** - ADR: kenapa survival tidak menggantikan Q2,
  aturan promosi per model, arah dependensi `api -> serving`, prosedur
  rollback lewat `CURRENT`, prasyarat deployment (autentikasi API, dst).
- **`docs/EXPERIMENTS.md`** - 42+ eksperimen penelitian, satu section per
  eksperimen, kronologis. Eksperimen baru menambah section, bukan file baru.

## Pagar keras (tidak boleh dilanggar)

- Database tetap read-only; sesi dipaksa `default_transaction_read_only=on`.
- `serving*` tidak pernah meng-import `api*` - arah hanya `api -> serving`.
- `dashboard/` tidak meng-import `partrisk`, tidak menyentuh DB/model - semua
  angka lewat HTTP.
- Survival (Q1) tidak pernah menentukan `risk_level`/`tier_score`/`rank`.
- Data hilang tidak pernah diisi nilai karangan - `NOT_SCORABLE` ≠ `LOW`.
- Nol kredensial di kode/image; client tidak pernah menerima SQL/DSN/stack
  trace.

## Test

```bash
pytest                                # seluruhnya - 143 test
pytest tests/test_serving.py -k recommend   # subset logic murni, tanpa DB
```

Test yang butuh database/model/internet di-skip (bukan gagal) kalau tidak
tersedia. Set `REQUIRE_DATABASE=1` supaya ketidaktersediaan jadi kegagalan
keras - lihat `tests/conftest.py`.
