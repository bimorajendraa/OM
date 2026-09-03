# ruff: noqa: E501
from __future__ import annotations

import api_client
import pandas as pd
import streamlit as st
import ui

ui.page_setup("Parts")
system_status = ui.sidebar_status()

try:
    overview = api_client.overview(top=1)
    available = api_client.filters()
except api_client.ApiError as error:
    ui.top_status_bar(status=system_status)
    ui.page_header(
        "SEMUA ASET",
        "Parts",
        "Cari, saring, dan urutkan seluruh PART aktif.",
    )
    st.error("Data belum bisa dimuat. Periksa koneksi ke layanan data lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

summary = overview["summary"]
data_through = overview["scored_at"]["data_through"]

ui.top_status_bar(data_through, system_status)
ui.page_header(
    "SEMUA ASET",
    "Parts",
    "Cari, saring, dan urutkan seluruh PART aktif. Pilih satu baris untuk membuka detailnya.",
)

ui.section_label("CARI DAN SARING")
with st.container(border=True):
    search_col, official_col = st.columns([2, 1])
    with search_col:
        search_term = st.text_input(
            "Cari PART",
            placeholder="Masukkan Item ID atau jenis PART",
        ).strip().lower()
    with official_col:
        official_only = st.toggle(
            "Antrian dengan risiko tinggi",
            value=False,
            help="Hanya menampilkan PART yang memiliki potensi risiko tinggi.",
        )

    filter_cols = st.columns(4)

    def choice(key: str, label: str, options: list[str], default: str | None = None) -> str | None:
        values = ["Semua", *options]
        if default is not None and default in values:
            st.session_state[key] = default
        elif st.session_state.get(key) not in values:
            st.session_state[key] = "Semua"
        selected = st.selectbox(label, values, key=key)
        return None if selected == "Semua" else selected

    default_location = st.session_state.pop("map_location_filter", None)
    with filter_cols[0]:
        item_type = choice("filter_item_type", "Jenis PART", available["item_types"])
    with filter_cols[1]:
        location = choice("filter_location", "Lokasi", available["locations"], default_location)
    with filter_cols[2]:
        client = choice("filter_client", "Client", available["clients"])
    with filter_cols[3]:
        horizon = st.selectbox("Horizon", [30, 60, 90, 120], format_func=lambda days: f"{days} hari")

try:
    data = api_client.recommendations(
        item_type=item_type,
        client=client,
        location=location,
        official_queue_only=official_only,
        limit=500,
    )
except api_client.ApiError as error:
    st.error("Daftar PART belum bisa dimuat. Periksa koneksi lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

items = data["items"]
if search_term:
    items = [
        item for item in items
        if search_term in str(item.get("item_id", "")).lower()
        or search_term in str(item.get("item_type", "")).lower()
    ]

ui.section_label("DAFTAR PART")
st.caption(
    f"{len(items):,} PART ditampilkan · diurutkan berdasarkan prioritas · "
    f"peluang menggunakan horizon {horizon} hari"
)

probability_column = f"failure_probability_{horizon}d"
ui.priority_table(
    items,
    key="daftar_part",
    as_of=data_through,
    columns=[
        "rank",
        "item_id",
        "item_type",
        "location",
        "failure_risk_level",
        probability_column,
        "recommended_action",
    ],
    empty_message=(
        "Tidak ada PART yang cocok dengan pencarian atau filter yang digunakan."
    ),
)
ui.probability_caption()

if items:
    csv_bytes = pd.DataFrame(items).to_csv(index=False).encode("utf-8")
    st.download_button(
        "Ekspor daftar CSV",
        csv_bytes,
        file_name="daftar_part.csv",
        mime="text/csv",
    )

    ui.rule()
    with st.expander("Analisis kapasitas tim"):
        capacity = int(
            st.number_input(
                "Kapasitas pemeriksaan (PART per bulan)",
                min_value=1,
                max_value=500,
                value=min(50, len(items)),
                step=10,
            )
        )
        frame = pd.DataFrame(items)
        active_parts = int(summary.get("active_parts") or 0)
        expected_total = float(
            summary.get("expected_failures_by_horizon", {}).get(f"{horizon}d", 0.0)
        )
        rate = expected_total / active_parts if active_parts else 0.0
        curve = pd.DataFrame({
            "n": range(1, len(frame) + 1),
            "tertangkap": frame[probability_column].cumsum(),
        })
        curve["acak"] = curve["n"] * rate
        capped = min(capacity, len(frame))
        caught = float(frame[probability_column].iloc[:capped].sum())
        st.markdown(
            f"Memeriksa **{capped:,} PART** teratas diperkirakan mencakup "
            f"**~{caught:.0f} kerusakan** dalam {horizon} hari."
        )
        st.altair_chart(ui.capture_curve_chart(curve, capped), width="stretch")
        st.caption("Garis biru mengikuti urutan prioritas; garis putus-putus menunjukkan pemeriksaan acak.")
