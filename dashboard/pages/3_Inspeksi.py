# ruff: noqa: E501
from __future__ import annotations

import api_client
import streamlit as st
import ui

ui.page_setup("Inspeksi")
system_status = ui.sidebar_status()

try:
    data = api_client.recommendations(official_queue_only=False, limit=500)
except api_client.ApiError as error:
    ui.top_status_bar(status=system_status)
    ui.page_header(
        "PRIORITAS INSPEKSI",
        "Inspeksi",
        "PART dikelompokkan menurut seberapa mendesak pemeriksaannya.",
    )
    st.error("Daftar inspeksi belum bisa dimuat. Periksa koneksi lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

data_through = data["scored_at"]["data_through"]
ui.top_status_bar(data_through, system_status)
ui.page_header(
    "PRIORITAS INSPEKSI",
    "Inspeksi",
    "PART dikelompokkan menurut seberapa mendesak pemeriksaannya. Kelompok dihitung dari kondisi "
    "terbaru; tidak ada status inspeksi yang tersimpan di sini.",
)

items = data["items"]
high = [item for item in items if item.get("priority") == "HIGH"]
normal = [item for item in items if item.get("priority") in ("MEDIUM", "LOW")]

groups = [
    ("HIGH — MENDESAK", high, "Jadwalkan pemeriksaan dalam beberapa hari."),
    ("NORMAL", normal, "Pantau, belum perlu tindakan segera."),
]

columns = [
    "rank",
    "item_id",
    "item_type",
    "location",
    "failure_risk_level",
    "failure_probability_30d",
    "recommended_action",
]

for index, (label, group_items, note) in enumerate(groups):
    ui.section_label(f"{label} · {len(group_items):,}")
    st.caption(note)
    ui.priority_table(
        group_items,
        key=f"inspeksi_{label.split()[0].lower()}",
        as_of=data_through,
        columns=columns,
        empty_message="Tidak ada PART pada kelompok ini saat ini.",
    )
    if index < len(groups) - 1:
        ui.rule()

ui.probability_caption()
