# ruff: noqa: E501
from __future__ import annotations

import api_client
import streamlit as st
import ui

ui.page_setup("Sistem")
system_status = ui.sidebar_status()
ui.top_status_bar(status=system_status)
ui.page_header(
    "ADMIN",
    "Sistem",
    "Versi model, kesegaran data, dan kesehatan model. Halaman ini untuk pemeriksaan teknis, "
    "bukan untuk keputusan operasional harian.",
)

try:
    model = api_client.model_info()
    metrics = api_client.monitoring_metrics()
except api_client.ApiError as error:
    st.error("Informasi sistem belum bisa dimuat. Periksa koneksi lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

failure_model = model.get("failure") or {}
failure_metrics = metrics.get("failure") or {}
failure_offline = failure_metrics.get("offline") or {}
failure_live = failure_metrics.get("live") or {}

ui.section_label("MODEL KERUSAKAN")
ui.fact_grid([
    ("Versi model", failure_model.get("model_version") or "-", True),
    ("Tanggal training", failure_model.get("training_date") or "-", True),
    ("Data sampai", ui.format_date(failure_live.get("data_through")), True),
    ("PART aktif dinilai", f"{failure_live.get('active_parts', 0):,}", True),
])
risk_counts = failure_live.get("risk_level_counts") or {}
st.caption(
    f"Distribusi risiko saat ini — Tinggi: {risk_counts.get('HIGH', 0):,} · "
    f"Sedang: {risk_counts.get('MEDIUM', 0):,} · Rendah: {risk_counts.get('LOW', 0):,}"
)
with st.expander("Metrik uji model kerusakan"):
    st.json(failure_offline.get("test_metrics") or {})
    gate = failure_offline.get("gate")
    if gate:
        st.caption("Status gerbang kualitas model")
        st.json(gate)

ui.rule()
st.caption(
    "Prediksi membantu menentukan prioritas dan tidak menggantikan hasil inspeksi teknisi di lapangan."
)
