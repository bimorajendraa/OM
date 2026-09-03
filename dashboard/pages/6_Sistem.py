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
    "Versi dan metrik uji model yang sedang aktif. Halaman ini untuk pemeriksaan teknis, "
    "bukan untuk keputusan operasional harian.",
)

try:
    model = api_client.model_info()
except api_client.ApiError as error:
    st.error("Informasi sistem belum bisa dimuat. Periksa koneksi lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

failure_model = model.get("failure") or {}

ui.section_label("MODEL KERUSAKAN")
ui.fact_grid([
    ("Versi model", failure_model.get("model_version") or "-", True),
    ("Tanggal training", failure_model.get("training_date") or "-", True),
    ("Data sampai", ui.format_date(failure_model.get("data_through")), True),
])
with st.expander("Metrik uji model kerusakan"):
    st.json(failure_model.get("test_metrics") or {})
    gate = failure_model.get("gate")
    if gate:
        st.caption("Status gerbang kualitas model")
        st.json(gate)

ui.rule()
st.caption(
    "Prediksi membantu menentukan prioritas dan tidak menggantikan hasil inspeksi teknisi di lapangan."
)
