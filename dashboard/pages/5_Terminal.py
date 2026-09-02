from __future__ import annotations

import api_client
import streamlit as st
import ui

ui.page_setup("Terminal")
system_status = ui.sidebar_status()

try:
    data = api_client.terminals()
except api_client.ApiError as error:
    ui.top_status_bar(status=system_status)
    ui.page_header(
        "LOKASI OPERASIONAL",
        "Terminal",
        "Lihat terminal dengan konsentrasi PART berisiko paling tinggi.",
    )
    st.error("Data terminal belum bisa dimuat. Periksa koneksi lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

data_through = data["scored_at"]["data_through"]
ui.top_status_bar(data_through, system_status)
ui.page_header(
    "LOKASI OPERASIONAL",
    "Terminal",
    "Lihat terminal dengan konsentrasi PART berisiko paling tinggi.",
)

st.markdown(
    f"**{data['terminals_total']:,} terminal aktif** · "
    f"{data['parts_with_terminal']:,} PART terpetakan · "
    f"{data['parts_without_terminal']:,} PART belum diketahui terminalnya"
)
if data["parts_without_terminal"]:
    st.caption(
        "PART tanpa terminal tetap tercatat, tetapi tidak masuk dalam "
        "peringkat terminal di bawah."
    )

terminals = data["terminals"]
if not terminals:
    st.info("Belum ada terminal dengan PART aktif yang bisa dipetakan.")
    st.stop()

ui.section_label("PERINGKAT TERMINAL")
search_term = st.text_input(
    "Cari terminal",
    placeholder="Nama terminal atau lokasi",
).strip().lower()
filtered = [
    item for item in terminals
    if not search_term
    or search_term in str(item.get("terminal_label", "")).lower()
    or search_term in str(item.get("location", "")).lower()
]

st.caption("Klik satu baris untuk langsung membuka terminal tersebut.")
selected_terminal_id = ui.labeled_table(
    filtered,
    columns=[
        "terminal_label",
        "location",
        "active_parts",
        "high_risk_parts",
        "medium_risk_parts",
        "top_risk_item_id",
        "top_risk_probability",
    ],
    empty_message="Tidak ada terminal yang cocok. Coba ubah kata pencarian.",
    key="terminal_list",
    id_field="terminal_id",
)
if selected_terminal_id:
    st.session_state["selected_terminal_id"] = selected_terminal_id

selected_terminal_id = st.session_state.get("selected_terminal_id")
if selected_terminal_id and any(t["terminal_id"] == selected_terminal_id for t in terminals):
    terminal = next(t for t in terminals if t["terminal_id"] == selected_terminal_id)
    terminal_id = terminal["terminal_id"]
    st.markdown(f"### {terminal.get('terminal_label') or terminal_id}")
    st.caption(terminal.get("location") or "Lokasi belum tercatat")
    ui.fact_grid([
        ("PART aktif", f"{terminal.get('active_parts', 0):,}", True),
        ("Risiko tinggi", f"{terminal.get('high_risk_parts', 0):,}", True),
        ("Risiko sedang", f"{terminal.get('medium_risk_parts', 0):,}", True),
        ("Kandidat penggantian", f"{terminal.get('replacement_candidates', 0):,}", True),
    ])

    try:
        parts = api_client.recommendations(
            terminal_id=terminal_id,
            official_queue_only=False,
            limit=500,
        )
    except api_client.ApiError as error:
        st.error("Daftar PART terminal belum bisa dimuat.")
        with st.expander("Detail error"):
            st.code(str(error))
        st.stop()

    ui.section_label("PART DI TERMINAL")
    ui.priority_table(
        parts["items"],
        key="terminal_parts",
        as_of=data_through,
        columns=[
            "rank",
            "item_id",
            "item_type",
            "failure_risk_level",
            "failure_probability_30d",
            "days_until_survival_90pct",
            "estimasi_bulan_rusak",
            "recommended_action",
        ],
        empty_message="Terminal ini belum memiliki PART aktif yang bisa dinilai.",
    )
    ui.probability_caption()
