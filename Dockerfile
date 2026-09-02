# Satu image untuk dua proses: API dan dashboard.
#
# Keduanya memakai kode yang sama persis dan bedanya hanya perintah start, jadi
# membangun dua image terpisah cuma menggandakan hal yang harus dijaga sinkron.
# docker-compose.yml menjalankan image ini dua kali dengan command berbeda.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependensi disalin lebih dulu supaya layer-nya dipakai ulang selama daftarnya
# tidak berubah. Versi PERSIS (bukan requirements-serving.txt yang rentang)
# supaya image production reproducible - lihat requirements.lock.txt.
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

# scikit-survival terpisah dari baris di atas (--no-deps): dependensinya
# `ecos` tidak punya wheel py3.13 dan image ini tidak punya gcc untuk build
# dari source - ecos cuma dipakai SurvivalSVM yang tidak dipakai proyek ini.
# Dependensi RIIL scikit-survival (numexpr, osqp, dst) sudah terpasang lewat
# requirements.lock.txt di atas - lihat catatan di file itu dan
# reports/gate_decision.md G7.
RUN pip install --no-cache-dir --no-deps scikit-survival==0.28.0

# Paket partrisk sendiri (src/partrisk/) di layer terpisah dari requirements
# di atas - perubahan kode tidak memaksa install ulang seluruh dependensi.
# --no-deps: requirements.lock.txt di atas SUDAH otoritatif untuk versi
# dependensi runtime (lihat pyproject.toml soal kenapa dependencies=[]).
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# `pip install .` (BUKAN -e) MENYALIN src/partrisk/ ke site-packages, terlepas
# dari /app - config.py punya default struktural (naik dari lokasi file
# config.py sendiri) yang jadi SALAH begitu package pindah lokasi seperti
# ini. PARTRISK_HOME eksplisit di sini memastikan models/ dan .env tetap
# ditemukan di /app, bukan di dalam site-packages - lihat Fase B0/B1 restrukturisasi.
ENV PARTRISK_HOME=/app

# Model ikut masuk image: versinya harus persis yang sudah diuji, bukan yang
# kebetulan ada di host saat container jalan.
COPY . .

# Jalan sebagai user biasa. Aplikasi ini hanya MEMBACA - tidak menulis apa pun
# ke database maupun ke filesystem.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

CMD ["uvicorn", "partrisk.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
