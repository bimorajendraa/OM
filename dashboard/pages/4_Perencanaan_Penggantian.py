# ruff: noqa: E501
from __future__ import annotations

import api_client
import pandas as pd
import streamlit as st
import ui

ui.page_setup("Perencanaan Penggantian")
system_status = ui.sidebar_status()

try:
    data = api_client.recommendations(
        replacement_candidates_only=True, official_queue_only=False, limit=200
    )
except api_client.ApiError as error:
    ui.top_status_bar(status=system_status)
    ui.page_header(
        "PERENCANAAN STOK",
        "Perencanaan Penggantian",
        "PART yang kemungkinan membutuhkan pengganti lebih tinggi.",
    )
    st.error("Daftar perencanaan belum bisa dimuat. Periksa koneksi lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

data_through = data["scored_at"]["data_through"]
ui.top_status_bar(data_through, system_status)
ui.page_header(
    "PERENCANAAN STOK",
    "Perencanaan Penggantian",
    "PART dengan kemungkinan membutuhkan pengganti lebih tinggi. Gunakan untuk perencanaan stok, bukan keputusan penggantian langsung.",
)

count = int(data.get("total") or 0)
with st.container(border=True):
    metric_col, note_col = st.columns([1, 3], vertical_alignment="center")
    metric_col.metric("Kandidat penggantian", f"{count:,}")
    note_col.info(
        "Daftar ini membantu menyiapkan stok. Konfirmasi kondisi fisik tetap diperlukan sebelum penggantian."
    )

items = data["items"]
if not items:
    st.success("Belum ada PART yang perlu disiapkan penggantinya saat ini.")
    st.stop()

ui.section_label("KANDIDAT PENGGANTIAN")
ui.priority_table(
    items,
    key="replacement_candidates",
    as_of=data_through,
    columns=[
        "rank",
        "item_id",
        "item_type",
        "location",
        "failure_risk_level",
        "failure_probability_30d",
        "scrap_risk_level",
        "scrap_probability",
        "estimasi_bulan_rusak",
        "recommended_action",
    ],
)
ui.probability_caption()

csv_bytes = pd.DataFrame(items).to_csv(index=False).encode("utf-8")
st.download_button(
    "Ekspor rencana CSV",
    csv_bytes,
    file_name="rencana_penggantian.csv",
    mime="text/csv",
)

ui.rule()
with st.expander("Sebaran kandidat menurut jenis PART"):
    type_counts: dict[str, int] = {}
    for item in items:
        label = item.get("item_type") or "Tidak diketahui"
        type_counts[label] = type_counts.get(label, 0) + 1
    chart_data = pd.DataFrame(
        sorted(type_counts.items(), key=lambda pair: pair[1], reverse=True),
        columns=["Jenis PART", "Jumlah"],
    )
    st.bar_chart(chart_data.set_index("Jenis PART"), horizontal=True, color="#155EEF")
    st.caption("Distribusi ini digunakan untuk memperkirakan kebutuhan stok menurut jenis PART.")
