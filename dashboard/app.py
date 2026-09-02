# ruff: noqa: E501
from __future__ import annotations

import api_client
import streamlit as st
import ui

ui.page_setup("Dashboard Operasi")
system_status = ui.sidebar_status()

try:
    overview = api_client.overview(top=15)
except api_client.ApiError as error:
    ui.top_status_bar(status=system_status)
    ui.page_header(
        "OPERASI HARI INI",
        "Ringkasan",
        "Kondisi aset dan PART yang paling perlu ditindaklanjuti hari ini.",
    )
    st.error("Data belum bisa dimuat. Periksa koneksi ke layanan data lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

summary = overview["summary"]
scored = overview["scored_at"]
data_through = scored["data_through"]

ui.top_status_bar(data_through, system_status)
ui.page_header(
    "OPERASI HARI INI",
    "Ringkasan",
    "Kondisi PART yang paling perlu ditindaklanjuti.",
)

ui.official_queue_status(
    int(summary.get("official_queue_size") or 0),
    data_through,
    active_parts=int(summary.get("active_parts") or 0),
    high_risk_parts=int(summary.get("high_risk_parts") or 0),
    expected_failures_30d=float(summary.get("expected_failures_by_horizon", {}).get("30d") or 0.0),
)

top_priority = overview.get("top_priority") or []

ui.section_label("PRIORITAS TERTINGGI")
ui.priority_table(
    top_priority,
    key="ringkasan_top",
    as_of=data_through,
    columns=[
        "rank",
        "item_id",
        "item_type",
        "location",
        "failure_risk_level",
        "failure_probability_30d",
        "days_until_survival_90pct",
        "estimasi_bulan_rusak",
        "recommended_action",
    ],
    empty_message="Tidak ada PART yang perlu ditindaklanjuti saat ini.",
)
ui.probability_caption()

st.page_link("pages/1_Parts.py", label="Lihat semua PART", icon=":material/arrow_forward:")
