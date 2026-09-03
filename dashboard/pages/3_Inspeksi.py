# ruff: noqa: E501
from __future__ import annotations

import api_client
import pandas as pd
import pydeck as pdk
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

tab_list, tab_map = st.tabs(["Daftar Inspeksi", "Peta Persebaran"])

with tab_list:
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

with tab_map:
    st.caption(
        "Titik berasal dari pencarian otomatis nama lokasi di OpenStreetMap, "
        "dibatasi pola nama stasiun publik dan area Indonesia. Lokasi yang "
        "belum ketemu koordinatnya tetap dihitung dalam rekomendasi dan "
        "tercatat di tabel di bawah peta."
    )
    if st.button("Coba cari koordinat lagi", help="Untuk lokasi yang belum punya titik."):
        api_client.locations_map.clear()

    try:
        map_data = api_client.locations_map(resolve=True, budget_seconds=60)
    except api_client.ApiError as error:
        st.error("Peta persebaran belum bisa dimuat.")
        with st.expander("Detail error"):
            st.code(str(error))
        st.stop()

    resolved = map_data["resolved"]
    unresolved = map_data["unresolved"]
    checked = [item for item in unresolved if item["checked"]]
    pending = [item for item in unresolved if not item["checked"]]
    all_locations = resolved + unresolved

    ui.section_label("REKOMENDASI KUNJUNGAN")
    top_location = (
        max(all_locations, key=lambda item: (item["high_risk_parts"], item["medium_risk_parts"]))
        if all_locations else None
    )
    if top_location and (top_location["high_risk_parts"] > 0 or top_location["medium_risk_parts"] > 0):
        with st.container(border=True):
            st.markdown(f"#### {top_location['location']}")
            st.caption("Lokasi dengan konsentrasi kerusakan PART tertinggi saat ini.")
            ui.fact_grid([
                ("Risiko tinggi", f"{top_location['high_risk_parts']:,}", True, "danger"),
                ("Risiko sedang", f"{top_location['medium_risk_parts']:,}", True),
                ("PART aktif", f"{top_location['active_parts']:,}", True),
            ])
            if st.button(f"Lihat daftar PART di {top_location['location']}", key="goto_top_location"):
                st.session_state["map_location_filter"] = top_location["location"]
                st.switch_page("pages/1_Parts.py")
    else:
        st.info("Belum ada lokasi dengan PART risiko tinggi atau sedang saat ini.")

    ui.section_label("PETA PERSEBARAN")
    with st.container(border=True):
        stat_cols = st.columns(4)
        stat_cols[0].metric("Lokasi aktif", f"{len(all_locations):,}")
        stat_cols[1].metric("Sudah ada titik di peta", f"{len(resolved):,}")
        stat_cols[2].metric("Belum ketemu koordinatnya", f"{len(checked):,}")
        stat_cols[3].metric("Belum sempat dicoba", f"{len(pending):,}")

    if pending:
        st.info(
            f"{len(pending)} lokasi belum sempat dicari koordinatnya (anggaran waktu "
            "habis). Klik \"Coba cari koordinat lagi\" di atas untuk melanjutkan."
        )

    if resolved:
        frame = pd.DataFrame(resolved)
        frame["color"] = frame.apply(
            lambda row: ui.risk_marker_color(row["high_risk_parts"], row["medium_risk_parts"]),
            axis=1,
        )
        frame["radius"] = frame["high_risk_parts"].map(ui.risk_marker_radius)

        layer = pdk.Layer(
            "ScatterplotLayer",
            id="inspeksi-risk-points",
            data=frame,
            get_position="[lon, lat]",
            get_fill_color="color",
            get_radius="radius",
            pickable=True,
            auto_highlight=True,
        )
        view_state = pdk.ViewState(
            latitude=float(frame["lat"].mean()),
            longitude=float(frame["lon"].mean()),
            zoom=4,
        )
        deck = pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            tooltip={
                "html": (
                    "<b>{location}</b><br/>"
                    "PART aktif: {active_parts}<br/>"
                    "Risiko tinggi: {high_risk_parts}<br/>"
                    "Risiko sedang: {medium_risk_parts}"
                )
            },
        )
        event = st.pydeck_chart(
            deck, on_select="rerun", selection_mode="single-object", key="inspeksi_risk_map"
        )

        selected = event.selection.objects.get("inspeksi-risk-points", []) if event and event.selection else []
        if selected:
            point = selected[0]
            with st.container(border=True):
                st.markdown(f"### {point['location']}")
                ui.fact_grid([
                    ("PART aktif", f"{point['active_parts']:,}", True),
                    ("Risiko tinggi", f"{point['high_risk_parts']:,}", True),
                    ("Risiko sedang", f"{point['medium_risk_parts']:,}", True),
                ])
                if st.button(f"Lihat daftar PART di {point['location']}", key="goto_selected_location"):
                    st.session_state["map_location_filter"] = point["location"]
                    st.switch_page("pages/1_Parts.py")

        st.caption(
            ":red-badge[merah] = ada PART risiko tinggi · "
            ":orange-badge[oranye] = ada PART risiko sedang, tidak ada yang tinggi · "
            ":green-badge[hijau] = tidak ada PART risiko tinggi/sedang di lokasi ini. "
            "Ukuran titik mengikuti jumlah PART risiko tinggi."
        )

        with st.expander(f"Tabel lokasi di peta ({len(resolved)})"):
            sorted_resolved = sorted(resolved, key=lambda item: -item["high_risk_parts"])
            ui.labeled_table(
                sorted_resolved,
                columns=[
                    "location", "active_parts", "high_risk_parts",
                    "medium_risk_parts",
                ],
            )
    else:
        st.info("Belum ada lokasi yang berhasil dipetakan.")

    if unresolved:
        with st.expander(f"Lokasi yang belum punya titik di peta ({len(unresolved)})"):
            st.caption(
                "Tetap diurutkan berdasarkan risiko supaya tidak terlewat hanya karena "
                "belum ada koordinatnya."
            )
            status_label = {True: "Sudah dicoba, tidak ketemu", False: "Belum dicoba"}
            sorted_unresolved = [
                {**item, "checked": status_label[item["checked"]]}
                for item in sorted(unresolved, key=lambda item: -item["high_risk_parts"])
            ]
            ui.labeled_table(
                sorted_unresolved,
                columns=[
                    "location", "active_parts", "high_risk_parts",
                    "medium_risk_parts", "checked",
                ],
            )
