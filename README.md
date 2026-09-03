# Predictive Maintenance

Prediksi kerusakan (failure prediction) untuk PART armada: peluang rusak
30/60/90/120 hari dan prioritas perawatan. Data operasional **hanya
dibaca** - repo ini tidak pernah membuat, mengubah, atau menghapus apa pun
di schema operasional. Riwayat prediksi ditulis ke schema `predictive`
terpisah - lihat `docs/DATABASE.md`.

## Satu model: prediksi kerusakan

| Pertanyaan | Model | Status |
|---|---|---|
| PART mana yang dirawat duluan? | CatBoost, 32 fitur, horizon 30 hari | mesin keputusan utama |

```
PART terpasang normal
        |
        v
  MODEL KERUSAKAN   peluang rusak 30/60/90/120 hari  -> menentukan
        |                                                antrian kerja
        v                                                (tier_score)
   PART masuk antrian inspeksi (gate presisi) / dipantau (mode eksplorasi)
```

**Survival (perkiraan tanggal rusak) dan Scrap (peluang tidak bisa
diperbaiki) sudah dihapus dari scope production** - lihat `docs/DECISIONS.md`
untuk riwayat keputusan itu (append-only, catatan lama tidak dihapus meski
sudah tidak berlaku). Kebutuhan stok/penggantian spare part sepenuhnya
ditentukan oleh teknisi/aplikasi eksternal, bukan sistem ini.

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
python -m partrisk.engines.failure.train
python -m partrisk.engines.failure.train --force-promote
```

Hasil tersimpan sebagai versi baru (`models/failure/vN/`). Model production
hanya diganti kalau kandidat **tidak lebih buruk** pada data uji - lihat
`docs/DECISIONS.md` §5 untuk aturan promosi.

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
python -m partrisk.cli score-and-persist       # skor + simpan ke predictive DB (dipanggil scheduler)
python -m partrisk.cli golden-batch generate --out FILE   # oracle regresi (lihat docs/DECISIONS.md)
python -m partrisk.cli golden-batch compare A B
python -m partrisk.cli baseline-performance    # RSS/latency model kerusakan
python -m partrisk.cli baseline-comparison     # precision@kapasitas vs kebijakan tanpa model
python -m partrisk.cli rolling-backtest              # backtest temporal bergulir (row-level)
python -m partrisk.cli rolling-lifecycle-backtest    # wajib sebelum klaim kandidat baru (E-49)
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
| `GET /api/v1/model` | versi, fitur, ambang risiko, metrik uji model |
| `GET /api/v1/parts/{item_id}/failure` | peluang rusak 30/60/90/120 hari |
| `GET /api/v1/parts/{item_id}/assessment` | peluang rusak + rekomendasi + faktor risiko |
| `GET /api/v1/parts/{item_id}/history` | tanggal kerusakan dan lokasi yang pernah tercatat |
| `POST /api/v1/parts/{item_id}/resolve-alert` | tutup alert lifecycle aktif untuk PART ini (`serving/alerts.py`) |
| `GET /api/v1/recommendations` | antrian RESMI: hanya PART yang lolos gerbang presisi (`official_queue_only=true`, default) - ukuran dinamis, boleh kosong. `official_queue_only=false` = mode eksplorasi (seluruh armada, terurut `tier_score`, tidak disaring `risk_level`) |
| `GET /api/v1/overview` | angka ringkas armada + daftar teratas |
| `GET /api/v1/filters` | nilai filter yang benar-benar ada di data |
| `GET /api/v1/terminals` | ringkasan per Terminal fisik (jumlah PART, sebaran risiko, PART paling berisiko) |
| `GET /api/v1/terminals/{terminal_id}/parts` | level 2 hierarki: ringkasan per Part Type (`item_model_code`) dalam satu Terminal |
| `GET /api/v1/terminals/{terminal_id}/parts/{part_type}` | level 3 hierarki: daftar Item untuk satu Part Type dalam satu Terminal |

Filter `/api/v1/recommendations`: `search`, `risk`, `priority`, `item_type`,
`client`, `location`, `terminal_id`, `limit`, `offset`.

## Angka performa - dan artinya

**Model kerusakan (`v6`, TEST n=45.023, positif 1.121 = 2,49%):**
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

Detail metrik lengkap, interval kepercayaan, dan metodologi ada di
`docs/METHODOLOGY.md`.

## Yang tidak bisa dijawab sistem ini

- **Tanggal kerusakan pasti** - model memberi peluang per horizon tetap,
  bukan tanggal.
- **PART yang sedang tidak terpasang** - `status: NOT_SCORABLE`, bukan
  `LOW`. Tidak ada risiko kerusakan yang perlu diperkirakan untuk PART yang
  tidak sedang dipakai.
- **Horizon di atas 120 hari** - di luar jangkauan target training.
- **Kapan/apakah PART bisa diperbaiki jika rusak, atau kebutuhan stok
  pengganti** - sengaja di luar scope sistem ini; keputusan itu ada di
  teknisi/aplikasi eksternal, bukan model.

## Struktur

```
src/partrisk/
├── core/
│   ├── config.py              konstanta model + kredensial database
│   ├── data_reader.py          SELECT read-only: event, siklus, kerusakan
│   └── features.py             fitur model kerusakan dari data mentah
├── engines/
│   ├── predict.py             predict(item_id) - hazard chaining 30/60/90/120 hari
│   └── failure/
│       ├── train.py            latih model + util versi/promosi
│       └── gate.py             gerbang presisi row-level & lifecycle (first-alert, E-49)
├── serving/
│   ├── single.py               prediksi satu PART, rekomendasi, penjelasan, riwayat
│   ├── batch.py                prediksi SELURUH PART aktif sekaligus (vectorized)
│   └── alerts.py                alert lifecycle (dedup, resolve-alert), tanpa persistence
├── predictive/
│   ├── db.py                   koneksi tulis schema `predictive` + migration runner
│   └── scoring.py               model_run + item_prediction (append-only) - `score-and-persist`
├── api/
│   ├── app.py                  FastAPI: app, routes, db pool, settings, logging
│   └── schemas.py               bentuk request/response API
└── cli.py                      pipeline/predict/golden-batch/baseline/backtest/dst - lihat `python -m partrisk.cli -h`

migrations/predictive/   SQL migrasi schema predictive, terurut nomor - lihat docs/DATABASE.md
dashboard/       Streamlit (app.py + pages/); hanya bicara ke API lewat HTTP, tidak pernah ke DB
tests/           conftest.py + test_pipeline.py + test_lifecycle.py + test_gate.py +
                 test_serving.py + test_api.py + test_predictive.py
docs/            METHODOLOGY.md (indeks per simbol) · CODE_NOTES.md (catatan
                 implementasi dari kode) · EXPERIMENTS.md (log eksperimen
                 kronologis, 80+) · DECISIONS.md (ADR) · DATABASE.md (schema predictive)
models/          failure/{CURRENT,v3..v6}
```

Fitur dihitung oleh **fungsi yang sama** untuk training maupun prediction -
kesetaraan single vs batch dijaga `tests/test_lifecycle.py`, jadi tidak
mungkin ada perbedaan antara fitur yang dipelajari model dan yang dipakai
production.

## Dokumentasi lanjutan

- **`docs/METHODOLOGY.md`** - keputusan teknis per simbol/konstanta
  (`grep NAMA_KONSTANTA docs/METHODOLOGY.md`).
- **`docs/CODE_NOTES.md`** - seluruh komentar implementasi yang sebelumnya
  berada di `src/`, `dashboard/`, dan `tests/`, dikelompokkan menurut file,
  scope fungsi/class, dan lokasi historisnya.
- **`docs/DECISIONS.md`** - ADR: aturan promosi model, arah dependensi
  `api -> serving`, prosedur rollback lewat `CURRENT`, prasyarat deployment
  (autentikasi API, dst), dan riwayat keputusan menghapus Survival/Scrap
  dari scope production.
- **`docs/EXPERIMENTS.md`** - 42+ eksperimen penelitian, satu section per
  eksperimen, kronologis. Eksperimen baru menambah section, bukan file baru.

## Pagar keras (tidak boleh dilanggar)

- Schema operasional tetap read-only; sesi `data_reader.py` dipaksa
  `default_transaction_read_only=on`. Hanya `predictive/db.py` yang menulis,
  dan hanya ke schema `predictive` - lihat `docs/DATABASE.md`.
- `serving*` tidak pernah meng-import `api*` - arah hanya `api -> serving`.
- `dashboard/` tidak meng-import `partrisk`, tidak menyentuh DB/model - semua
  angka lewat HTTP.
- Data hilang tidak pernah diisi nilai karangan - `NOT_SCORABLE` ≠ `LOW`.
- Nol kredensial di kode/image; client tidak pernah menerima SQL/DSN/stack
  trace.

## Test

```bash
pytest                                # seluruhnya
pytest tests/test_serving.py -k recommend   # subset logic murni, tanpa DB
```

Test yang butuh database/model/internet di-skip (bukan gagal) kalau tidak
tersedia. Set `REQUIRE_DATABASE=1` supaya ketidaktersediaan jadi kegagalan
keras - lihat `tests/conftest.py`.
