# Workflow n8n - PartRisk

Empat file workflow (importable langsung ke n8n: Workflows -> Import from File)
sesuai 4 lane di diagram operasional PartRisk. **Tidak ada kode Python yang
diubah untuk ini** - semua logika bisnis tetap di `src/partrisk/`, workflow
di sini murni orkestrasi (jadwal, SSH command, health check).

| File | Lane diagram | Trigger |
|---|---|---|
| `workflow_0_deployment_migration.json` | Deployment & Database Migration | Manual |
| `workflow_1_scheduled_scoring.json` | Scheduled Scoring (NO DB PULL) | Cron (`0 */6 * * *`, sesuaikan) |
| `workflow_2_inspection_api_gateway_optional.json` | External App -> Inspection API | Webhook (**opsional**, lihat catatan di bawah) |
| `workflow_3_retraining_promotion.json` | Retraining & Model Promotion | Cron (`0 3 1 * *`, sesuaikan) |

## Sebelum dipakai

1. **Kredensial SSH**: buat credential n8n tipe SSH bernama `OM Server SSH`
   (host, user, key/password ke server tempat PartRisk jalan), lalu di tiap
   node SSH pilih credential itu (placeholder `REPLACE_WITH_SSH_CREDENTIAL_ID`
   akan otomatis diminta n8n saat pertama kali dibuka di editor).
2. **Path**: semua command pakai `/opt/OM` - ganti sesuai lokasi instalasi
   sebenarnya di server Anda.
3. **API_BASE_URL**: node HTTP Request pakai `http://127.0.0.1:8000` sebagai
   contoh - ganti ke alamat OM API yang bisa dijangkau n8n (mis. lewat VPN/
   private network, JANGAN expose API tanpa `API_KEY` ke internet publik -
   lihat warning startup di `src/partrisk/api/app.py`).
4. **Jadwal cron**: sesuaikan `workflow_1`/`workflow_3` dengan kebutuhan tim
   (contoh di file cuma placeholder masuk akal, bukan keharusan).

## Tentang Workflow 2 (gateway opsional)

Endpoint sesungguhnya, `POST /api/v1/inspections`, dilayani LANGSUNG oleh
FastAPI (`src/partrisk/api/app.py`) - aplikasi eksternal **boleh dan biasanya
cukup** memanggilnya langsung, tanpa lewat n8n sama sekali. Workflow ini
cuma berguna kalau organisasi Anda memang mau n8n jadi lapisan gateway di
depan API (logging terpusat, rate limiting, dsb). Kalau tidak butuh, jangan
aktifkan workflow ini.

Field body sudah memakai nama TERBARU (`host_serial_code` +
`idempotency_key`) - diagram sumber yang diberikan masih menyebut
`external_event_id`/`/api/v1/interventions`, nama LAMA sebelum rename yang
terjadi di sesi perbaikan sebelumnya (lihat docs/DECISIONS.md soal
"inspection" vs "intervention"). Sudah disesuaikan di sini supaya konsisten
dengan kode yang benar-benar berjalan sekarang.

## Catatan implementasi

- Semua node "exit code 0?" mengasumsikan node SSH mengembalikan field
  `exitCode` pada output-nya (perilaku node SSH n8n versi terbaru) dengan
  `continueOnFail` diaktifkan supaya command yang gagal tidak langsung
  menghentikan eksekusi sebelum sempat dicek. Kalau versi n8n Anda berbeda
  skema outputnya, sesuaikan ekspresi di node IF terkait.
- `workflow_3`: perbandingan "CURRENT Berubah?" membaca ulang file
  `models/failure/CURRENT` sebelum dan sesudah `train.py` jalan - **JANGAN**
  tambahkan `--force-promote` di command training terjadwal (itu untuk
  override manual manusia, bukan otomatisasi rutin - lihat
  `engines/failure/train.py::decide_promotion`).
