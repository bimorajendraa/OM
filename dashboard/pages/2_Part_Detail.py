# ruff: noqa: E501
from __future__ import annotations

import html

import api_client
import streamlit as st
import ui

ui.page_setup("Detail PART")
system_status = ui.sidebar_status()
ui.top_status_bar(status=system_status)
ui.page_header(
    "PENCARIAN PART",
    "Detail PART",
    "Temukan rekomendasi, tingkat risiko, dan riwayat satu PART.",
)

if "detail_item_id" in st.session_state:
    st.session_state["part_detail_item_id"] = st.session_state.pop("detail_item_id")
elif "part_detail_item_id" not in st.session_state:
    st.session_state["part_detail_item_id"] = st.query_params.get("item_id", "")

with st.form("part_search", border=False):
    search_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
    with search_col:
        item_id = st.text_input(
            "Item ID",
            key="part_detail_item_id",
            placeholder="Contoh: 011201100101164",
        ).strip()
    with button_col:
        st.form_submit_button("Cari PART", type="primary", use_container_width=True)

if not item_id:
    st.info("Masukkan Item ID untuk melihat tindakan yang disarankan.")
    st.stop()

try:
    data = api_client.assessment(item_id)
except api_client.ApiError as error:
    st.error("Data PART belum bisa dimuat. Periksa koneksi lalu coba kembali.")
    with st.expander("Detail error"):
        st.code(str(error))
    st.stop()

if data.get("status") == "NOT_FOUND":
    st.error(data.get("message", f"PART '{item_id}' tidak ditemukan."))
    st.stop()

if data.get("status") == "NOT_SCORABLE":
    st.warning("PART ini belum bisa dinilai.")
    st.write(data.get("reason", "Data kondisi belum cukup untuk menghasilkan penilaian."))
    st.caption("Belum bisa dinilai tidak berarti risikonya rendah.")
    st.stop()

try:
    history = api_client.history(data["item_id"])
except api_client.ApiError:
    history = None
if history and history.get("status") == "NOT_FOUND":
    history = None

failure = data["failure"]
recommendation = data["recommendation"]
current_location = history["locations"][0]["location"] if history and history["locations"] else None
location = data.get("location") or current_location or "Belum tercatat"
item_type = data.get("item_type") or "Jenis belum tercatat"

ui.rule()
st.markdown("<div class='page-kicker'>DETAIL PART</div>", unsafe_allow_html=True)
st.subheader(f"PART {data['item_id']}")
st.caption(f"{item_type} · {location}")

ui.fact_grid([
    ("Status", ui.badge_html(failure.get("risk_level")), False),
    ("Peluang rusak 30 hari", html.escape(api_client.percent(failure.get("failure_probability_30d"))), True),
    ("Data terakhir", html.escape(ui.format_date(data.get("as_of"))), True),
])

ui.recommendation_panel(
    recommendation.get("action"),
    recommendation.get("message") or "Gunakan hasil inspeksi lapangan untuk menentukan langkah berikutnya.",
    recommendation.get("priority"),
)

ui.section_label("RISIKO")
with st.container(border=True):
    ui.horizon_metrics(failure)
    ui.probability_caption()

explanation = data.get("explanation")
if explanation:
    ui.section_label("KENAPA DIPRIORITASKAN")
    with st.container(border=True):
        for factor in explanation.get("factors", []):
            marker = {"RISK_FACTOR": "↑", "MITIGATING": "↓", "CONTEXT": "->"}.get(
                factor.get("direction"), "·"
            )
            st.markdown(f"**{marker}** {factor['label']}")
        for note in explanation.get("notes", []):
            st.caption(note)
        for caveat in explanation.get("caveats", []):
            st.warning(caveat)

if history:
    ui.section_label("RIWAYAT")
    with st.expander("Riwayat kerusakan", expanded=bool(history["failures"])):
        ui.labeled_table(
            history["failures"],
            columns=["date", "location", "status", "wo_type"],
            empty_message="Belum pernah tercatat rusak.",
        )
    with st.expander("Riwayat lokasi"):
        ui.labeled_table(
            history["locations"],
            columns=["location", "first_seen", "last_seen", "events"],
            empty_message="Belum ada lokasi yang tercatat.",
        )
