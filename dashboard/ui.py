# ruff: noqa: E501
from __future__ import annotations

import hmac
import html
import os
from datetime import datetime

import altair as alt
import api_client
import pandas as pd
import streamlit as st

RISK_LEVEL_LABELS = {"HIGH": "TINGGI", "MEDIUM": "SEDANG", "LOW": "RENDAH"}
PRIORITY_LABELS = {
    "HIGH": "MENDESAK",
    "MEDIUM": "SEDANG",
    "LOW": "RENDAH",
}
ACTION_LABELS = {
    "PRIORITIZE_INSPECTION": "Periksa segera",
    "SCHEDULE_INSPECTION": "Jadwalkan pemeriksaan",
    "MONITOR": "Pantau kondisi",
}

PROBABILITY_COLUMNS = {
    "failure_probability_30d",
    "failure_probability_60d",
    "failure_probability_90d",
    "failure_probability_120d",
    "top_risk_probability",
}

COLUMN_LABELS = {
    "rank": "#",
    "item_id": "PART",
    "item_type": "JENIS PART",
    "item_model_code": "MODEL",
    "client": "CLIENT",
    "location": "LOKASI",
    "terminal_id": "ID TERMINAL",
    "terminal_label": "TERMINAL",
    "terminal_model_name": "MODEL TERMINAL",
    "installation_age_days": "UMUR PASANG",
    "failure_probability_30d": "PELUANG 30H",
    "failure_probability_60d": "PELUANG 60H",
    "failure_probability_90d": "PELUANG 90H",
    "failure_probability_120d": "PELUANG 120H",
    "failure_risk_level": "RISIKO",
    "priority": "PRIORITAS",
    "recommended_action": "TINDAKAN",
    "active_parts": "PART AKTIF",
    "high_risk_parts": "RISIKO TINGGI",
    "medium_risk_parts": "RISIKO SEDANG",
    "low_risk_parts": "RISIKO RENDAH",
    "top_risk_item_id": "PART PALING BERISIKO",
    "top_risk_probability": "PELUANG 30H",
    "part_type": "JENIS PART (MODEL)",
    "installed_count": "TERPASANG",
    "open_alert_count": "ALERT TERBUKA",
    "date": "TANGGAL",
    "status": "STATUS",
    "wo_type": "JENIS WO",
    "first_seen": "PERTAMA TERCATAT",
    "last_seen": "TERAKHIR TERCATAT",
    "events": "JUMLAH CATATAN",
}

BULAN_ID = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
    7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des",
}

NAV_PAGES = [
    ("app.py", "Ringkasan", ":material/space_dashboard:"),
    ("pages/1_Parts.py", "Parts", ":material/list_alt:"),
    ("pages/3_Inspeksi.py", "Inspeksi", ":material/fact_check:"),
    ("pages/5_Terminal.py", "Terminal", ":material/location_on:"),
]

GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
:root {
  --bg-page: #F4F5F3;
  --bg-surface: #FAFAF8;
  --bg-subtle: #ECEEEB;
  --bg-active: #E4E8EB;
  --text-primary: #171A1D;
  --text-secondary: #565E66;
  --text-muted: #747D85;
  --border-default: #CBD1D8;
  --border-strong: #929BA6;
  --accent: #33547D;
  --accent-hover: #24405F;
  --accent-soft: #E7ECF1;
  --risk-low: #237A4B;
  --risk-low-bg: #E6F3EA;
  --risk-medium: #946200;
  --risk-medium-bg: #FFF1C7;
  --risk-high: #B42318;
  --risk-high-bg: #FCE8E6;
  --sidebar-bg: #20252A;
  --sidebar-border: #343B42;
  --sidebar-text: #E8ECEF;
  --sidebar-text-muted: #8F9AA5;
}

html, body, [class*="css"], .stApp, .stApp *:not([data-testid="stIconMaterial"]) {
  font-family: "Plus Jakarta Sans", "Segoe UI", sans-serif !important;
}
html, body, [class*="css"] {
  color: var(--text-primary);
}
[data-testid="stJson"], [data-testid="stJson"] * {
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace !important;
}
.stApp, [data-testid="stAppViewContainer"] { background: var(--bg-page); }
[data-testid="stHeader"] { background: transparent; height: 3rem; }
/* Keep stToolbar itself present (it houses the sidebar re-expand button when
   collapsed) - only hide the Deploy menu / running-status chrome inside it. */
[data-testid="stToolbar"] { background: transparent; box-shadow: none; }
[data-testid="stMainMenu"], [data-testid="stAppDeployButton"], [data-testid="stStatusWidget"], [data-testid="stDecoration"] { display: none !important; }
[data-testid="stMainBlockContainer"] {
  max-width: 1360px;
  padding-top: 1rem;
  padding-bottom: 5rem;
}

@keyframes fadeInUp { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }
[data-testid="stMainBlockContainer"] { animation: fadeInUp .3s ease-out; }
[data-testid="stSidebar"] [data-testid="stPageLink"] a { transition: background-color .15s ease, color .15s ease; }

/* Sidebar: persistent, industrial, dark neutral (not a floating pill bar) */
[data-testid="stSidebar"] {
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  min-width: 240px;
}
/* Streamlit only reveals the collapse/expand toggle on hover by default -
   force it always visible so it doesn't read as "disappearing". */
[data-testid="stSidebarCollapseButton"], [data-testid="stExpandSidebarButton"] {
  opacity: 1 !important; visibility: visible !important;
}
[data-testid="stSidebar"] * { color: var(--sidebar-text); }
[data-testid="stSidebarNav"] { display: none; }
[data-testid="stSidebarUserContent"] { padding-top: 1.1rem; }
[data-testid="stSidebar"] [data-testid="stPageLink"] a {
  min-height: 44px;
  border-radius: 6px;
  padding: .55rem .65rem;
  gap: .55rem;
  font-size: .88rem;
  font-weight: 560;
}
[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover { background: #30373E; }
[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"] {
  background: var(--bg-active);
  color: var(--text-primary);
}
[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"] * { color: var(--text-primary); }
[data-testid="stSidebar"] hr { border-color: var(--sidebar-border); margin: .9rem 0; }
.brand-lockup { padding: .3rem .35rem 1.1rem; }
.brand-kicker { color: var(--sidebar-text-muted); font-size: .68rem; font-weight: 700; letter-spacing: .13em; }
.brand-title { color: #FAFAF8; font-size: 1.05rem; font-weight: 660; margin-top: .3rem; }
.nav-label { color: var(--sidebar-text-muted); font-size: .68rem; font-weight: 700; letter-spacing: .12em; margin: .2rem .35rem .45rem; }
.system-panel { border-top: 1px solid var(--sidebar-border); margin-top: 1.1rem; padding: 1rem .35rem 0; }
.system-row { display:flex; align-items:center; gap:.55rem; font-size:.82rem; font-weight:600; }
.system-dot { width:8px; height:8px; border-radius:50%; background:#52B788; box-shadow:0 0 0 3px rgba(82,183,136,.15); flex-shrink:0; }
.system-dot.degraded { background:#E7A93B; box-shadow:0 0 0 3px rgba(231,169,59,.15); }
.system-meta { color: var(--sidebar-text-muted); font-size:.73rem; line-height:1.45; margin-top:.5rem; }
.system-link {
  display: block; margin-top: .8rem; color: var(--sidebar-text-muted) !important;
  font-size: .78rem; font-weight: 560; text-decoration: none;
}
.system-link:hover { color: var(--sidebar-text) !important; text-decoration: underline; }

h1, h2, h3 { letter-spacing: -.015em; }
h1 { font-size: clamp(1.65rem, 3vw, 2rem) !important; font-weight: 670 !important; margin-bottom: .25rem !important; }
h2 { font-size: 1.28rem !important; font-weight: 650 !important; }
h3 { font-size: 1.02rem !important; font-weight: 650 !important; }
p, li { line-height: 1.48; }
.top-status-bar {
  min-height: 28px; display:flex; align-items:center; justify-content:flex-end; gap:1rem;
  border-bottom:1px solid var(--border-default); padding:0 0 .7rem; margin-bottom:1.5rem;
  color:var(--text-secondary); font-size:.8rem;
}
.page-kicker { color:var(--text-muted); font-size:.72rem; font-weight:750; letter-spacing:.11em; text-transform:uppercase; margin-bottom:.35rem; }
.page-description { color:var(--text-secondary); max-width:760px; font-size:.95rem; margin:.25rem 0 1.1rem; }
.section-label { color:var(--text-muted); font-size:.72rem; font-weight:750; letter-spacing:.11em; text-transform:uppercase; margin:1.15rem 0 .5rem; }
.rule { border-top:1px solid var(--border-default); margin:1.1rem 0; }

.recommendation-panel { border:1px solid var(--border-default); background:var(--bg-surface); border-radius:14px; box-shadow:0 1px 2px rgba(23,26,29,.04); padding:1.2rem 1.3rem; margin:.6rem 0 1.1rem; }
.recommendation-panel .eyebrow { color:var(--text-muted); font-size:.7rem; font-weight:750; letter-spacing:.12em; }
.recommendation-panel .action { font-size:1.35rem; font-weight:700; margin:.38rem 0 .25rem; }
.recommendation-panel .message { color:var(--text-secondary); }
.risk-badge { display:inline-block; border:1px solid currentColor; border-radius:999px; padding:.17rem .58rem; font-size:.68rem; line-height:1.35; font-weight:780; letter-spacing:.055em; }
.risk-high { color:var(--risk-high); background:var(--risk-high-bg); }
.risk-medium { color:var(--risk-medium); background:var(--risk-medium-bg); }
.risk-low { color:var(--risk-low); background:var(--risk-low-bg); }
.risk-unknown { color:var(--text-secondary); background:var(--bg-subtle); }
[data-testid="stVerticalBlockBorderWrapper"] {
  background:var(--bg-surface); border-color:var(--border-default) !important; border-radius:14px !important;
  box-shadow:0 1px 2px rgba(23,26,29,.04);
}
[data-testid="stMetric"] { background:transparent; padding:.15rem 0; }
[data-testid="stMetricLabel"] { color:var(--text-muted); font-size:.76rem; text-transform:uppercase; letter-spacing:.045em; }
[data-testid="stMetricValue"] { font-variant-numeric:tabular-nums; font-size:1.45rem; }
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  min-height:44px; border-radius:10px; border:1px solid var(--border-strong); box-shadow:none; font-weight:650;
}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] > button[kind="primary"] { background:var(--accent); border-color:var(--accent); }
.stButton > button:focus-visible, input:focus-visible, textarea:focus-visible, [role="combobox"]:focus-visible {
  outline:2px solid var(--accent) !important; outline-offset:2px;
}
[data-baseweb="select"] > div, [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input { min-height:44px; border-radius:10px; }
[data-testid="stDataFrame"] { border:1px solid var(--border-default); border-radius:10px; overflow:hidden; }
[data-testid="stDataFrame"] * { font-variant-numeric:tabular-nums; }
[data-testid="stVegaLiteChart"] { max-width:100% !important; overflow-x:auto !important; }
[data-testid="stVegaLiteChart"] > div { max-width:100%; overflow-x:auto; }
.mobile-part { border-top:1px solid var(--border-default); padding:.95rem .1rem .5rem; }
.mobile-part-head { display:flex; justify-content:space-between; gap:.75rem; align-items:flex-start; }
.mobile-part-rank { color:var(--text-muted); font-variant-numeric:tabular-nums; font-size:.73rem; }
.mobile-part-id { font-weight:720; font-size:1rem; font-variant-numeric:tabular-nums; }
.mobile-part-meta { color:var(--text-secondary); font-size:.82rem; margin:.2rem 0 .65rem; }
.mobile-part-risk { display:flex; align-items:center; justify-content:space-between; gap:.75rem; margin:.25rem 0 .55rem; }
.mobile-part-prob { font-weight:650; font-size:.83rem; font-variant-numeric:tabular-nums; }
.mobile-part-chips { color:var(--text-secondary); font-size:.82rem; margin:.2rem 0 .5rem; font-variant-numeric:tabular-nums; }
.mobile-part-action { font-size:.88rem; font-weight:620; margin:.35rem 0 .6rem; }
div:has(> [class*="st-key-mobile_row_"]) { display:none; }
.fact-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:.75rem; margin:.5rem 0 1.1rem; }
.fact-item { background:var(--bg-surface); border:1px solid var(--border-default); border-radius:14px; padding:1.1rem 1.2rem; box-shadow:0 1px 2px rgba(23,26,29,.04); }
.fact-label { color:var(--text-muted); font-size:.7rem; font-weight:720; letter-spacing:.07em; text-transform:uppercase; }
.fact-value { margin-top:.5rem; font-size:.93rem; font-weight:620; }
.fact-item.lead { background:var(--sidebar-bg); border-color:var(--sidebar-bg); }
.fact-item.lead .fact-label { color:var(--sidebar-text-muted); }
.fact-item.lead .fact-value { font-size:1.9rem; font-weight:700; color:#FAFAF8; }
.fact-item.danger .fact-value { color:var(--risk-high); }
.mono { font-variant-numeric:tabular-nums; }
@media (max-width: 768px) {
  [data-testid="stMainBlockContainer"] { padding:1rem 1rem 6.5rem; }
  .top-status-bar { margin-bottom:1.2rem; }
  [class*="st-key-priority_desktop_"] { display:none; }
  div:has(> [class*="st-key-mobile_row_"]) { display:block; }
  [class*="st-key-mobile_row_"] { display:block; }
  .fact-grid { grid-template-columns:1fr 1fr; }
  [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width:calc(50% - .5rem) !important; }
}
@media (max-width: 420px) {
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width:100% !important; }
  .fact-grid { grid-template-columns:1fr; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior:auto !important; transition:none !important; animation:none !important; }
}
</style>
"""


def require_login() -> None:
    password = os.getenv("DASHBOARD_PASSWORD", "").strip()
    if not password or st.session_state.get("authenticated"):
        return
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display:none !important; }
        [data-testid="stMain"] { justify-content:center; }
        [data-testid="stMainBlockContainer"] {
          max-width:clamp(600px, 60vw, 820px) !important; padding-top:12vh !important;
        }
        [class*="st-key-login_card"] [data-testid="stVerticalBlockBorderWrapper"] { padding:2.4rem 2.6rem !important; }
        [class*="st-key-login_card"] [data-testid="stForm"] {
          border:0 !important; padding:0 !important; background:transparent !important; margin-top:1.3rem;
        }
        [class*="st-key-login_card"] h1 { margin-bottom:.4rem !important; font-size:2.1rem !important; }
        [class*="st-key-login_card"] .page-description,
        [class*="st-key-login_card"] [data-testid="stCaptionContainer"] { font-size:1rem !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(border=True, key="login_card"):
        st.title("Masuk")
        st.caption("Gunakan akses internal untuk membuka dashboard maintenance.")
        with st.form("login_form", border=False):
            entered = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Masuk", type="primary")
    if submitted:
        if hmac.compare_digest(entered, password):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Password salah. Periksa kembali lalu coba lagi.")
    st.stop()


def page_setup(title: str) -> None:
    st.set_page_config(
        page_title=f"{title} · Predictive Maintenance",
        page_icon=":material/build:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
    require_login()


def sidebar_status() -> dict | None:
    try:
        status = api_client.health()
    except api_client.ApiError:
        status = None

    is_ok = bool(status and status.get("status") == "ok")
    system_label = "Sistem normal" if is_ok else "Perlu diperiksa"
    dot_class = "" if is_ok else " degraded"

    with st.sidebar:
        st.markdown(
            '<div class="brand-lockup"><div class="brand-kicker">PREDICTIVE MAINTENANCE</div>'
            '<div class="brand-title">Dashboard Maintenance</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="nav-label">MENU</div>', unsafe_allow_html=True)
        for page, label, icon in NAV_PAGES:
            _page_link(page, label=label, icon=icon)
        st.markdown(
            '<div class="system-panel">'
            f'<div class="system-row"><span class="system-dot{dot_class}"></span>{html.escape(system_label)}</div>'
            '<div class="system-meta">Status layanan data</div>'
            "</div>",
            unsafe_allow_html=True,
        )
    return status


def _page_link(page: str, label: str, icon: str) -> None:
    """Render navigation; AppTest does not register multipage URL metadata."""
    try:
        st.page_link(page, label=label, icon=icon)
    except KeyError:
        st.markdown(f"<span style='padding:.65rem'>{html.escape(label)}</span>", unsafe_allow_html=True)


def top_status_bar(data_through: str | None = None, status: dict | None = None) -> None:
    data_label = f"Data sampai {format_date(data_through)}" if data_through else "Menunggu pembaruan data"
    st.markdown(
        f'<div class="top-status-bar"><span>{html.escape(data_label)}</span></div>',
        unsafe_allow_html=True,
    )


def page_header(kicker: str, title: str, description: str) -> None:
    st.markdown(f'<div class="page-kicker">{html.escape(kicker)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="page-description">{html.escape(description)}</div>', unsafe_allow_html=True)


def section_label(label: str) -> None:
    st.markdown(f'<div class="section-label">{html.escape(label)}</div>', unsafe_allow_html=True)


def rule() -> None:
    st.markdown('<div class="rule"></div>', unsafe_allow_html=True)


def official_queue_status(
    count: int,
    data_through: str | None,
    active_parts: int = 0,
    high_risk_parts: int = 0,
    expected_failures_30d: float = 0.0,
) -> None:
    count_foot = "Tidak ada tindakan mendesak" if count == 0 else "Memenuhi gerbang keyakinan"
    fact_grid([
        ("Perlu ditindaklanjuti", f"{count:,}", True, "lead"),
        ("Risiko tinggi", f"{high_risk_parts:,}", True, "danger" if high_risk_parts else ""),
        ("PART aktif dipantau", f"{active_parts:,}", True, ""),
        ("Part yang diperkirakan akan rusak", f"~{expected_failures_30d:,.0f}", True, ""),
    ])
    st.caption(count_foot)


def recommendation_panel(action: str | None, message: str, priority: str | None) -> None:
    st.markdown(
        '<div class="recommendation-panel"><div class="eyebrow">TINDAKAN YANG DISARANKAN</div>'
        f'<div class="action">{html.escape(action_label(action))}</div>'
        f'<div class="message">{html.escape(message)}</div>'
        f'<div style="margin-top:.8rem">{badge_html(priority, priority=True)}</div></div>',
        unsafe_allow_html=True,
    )


def format_date(value: str | None) -> str:
    if not value:
        return "-"
    try:
        date = datetime.fromisoformat(str(value)[:10])
        return f"{date.day} {BULAN_ID[date.month]} {date.year}"
    except ValueError:
        return str(value)


def risk_label(level: str | None) -> str:
    return RISK_LEVEL_LABELS.get(level or "", "BELUM BISA DINILAI")


def priority_label(priority: str | None) -> str:
    return PRIORITY_LABELS.get(priority or "", "BELUM BISA DINILAI")


def badge_html(value: str | None, priority: bool = False) -> str:
    label = priority_label(value) if priority else risk_label(value)
    severity = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(value or "", "unknown")
    return f'<span class="risk-badge risk-{severity}">{html.escape(label)}</span>'


def action_label(action: str | None) -> str:
    return ACTION_LABELS.get(action or "", action or "Belum ada tindakan")


def days_label(days: float | None) -> str:
    return "Belum cukup data" if days is None or pd.isna(days) else f"~{days:.0f} hari"


def _display_frame(items: list[dict], columns: list[str], as_of: str | pd.Timestamp | None) -> pd.DataFrame:
    frame = pd.DataFrame(items).copy()
    present = [column for column in columns if column in frame.columns]
    display = frame[present].copy()
    for column in PROBABILITY_COLUMNS & set(display.columns):
        display[column] = display[column].map(api_client.percent)
    if "failure_risk_level" in display.columns:
        display["failure_risk_level"] = display["failure_risk_level"].map(risk_label)
    if "priority" in display.columns:
        display["priority"] = display["priority"].map(priority_label)
    if "recommended_action" in display.columns:
        display["recommended_action"] = display["recommended_action"].map(action_label)
    if "installation_age_days" in display.columns:
        display["installation_age_days"] = display["installation_age_days"].map(days_label)
    return display.rename(columns=COLUMN_LABELS)


_CHIP_SKIP_COLUMNS = {
    "rank", "item_id", "item_type", "location", "terminal_label",
    "failure_risk_level", "priority", "recommended_action",
}


def _chip_text(column: str, item: dict, as_of: str | pd.Timestamp | None) -> str | None:
    label = COLUMN_LABELS.get(column, column)
    if column in PROBABILITY_COLUMNS:
        return f"{label} {api_client.percent(item.get(column))}"
    if column == "installation_age_days":
        return f"{label} {days_label(item.get(column))}"
    value = item.get(column)
    if value in (None, ""):
        return None
    return f"{label} {value}"


def priority_table(
    items: list[dict],
    columns: list[str],
    key: str = "priority_table",
    as_of: str | pd.Timestamp | None = None,
    empty_message: str = "Tidak ada PART yang cocok. Coba ubah filter yang digunakan.",
) -> None:
    if not items:
        st.info(empty_message)
        return

    frame = pd.DataFrame(items)
    display = _display_frame(items, columns, as_of)
    with st.container(key=f"priority_desktop_{key}"):
        st.caption("Klik satu baris untuk langsung membuka detail PART.")
        event = st.dataframe(
            display,
            width="stretch",
            height=min(610, 42 + 52 * len(display)),
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=f"{key}_table",
        )
        rows = event.selection.rows if event and event.selection else []
        if rows:
            selected_id = str(frame.iloc[rows[0]]["item_id"])
            st.session_state["detail_item_id"] = selected_id
            st.switch_page("pages/2_Part_Detail.py")

    for index, item in enumerate(items[:50]):
        item_id = str(item.get("item_id", "-"))
        with st.container(key=f"mobile_row_{key}_{index}"):
            location = item.get("location") or "Lokasi belum tercatat"
            item_type = item.get("item_type") or "Jenis belum tercatat"
            badges = "".join(
                badge_html(item.get(field), priority=(field == "priority"))
                for field in ("failure_risk_level", "priority")
                if field in columns
            )
            chips = " · ".join(
                text
                for field in columns
                if field not in _CHIP_SKIP_COLUMNS
                for text in [_chip_text(field, item, as_of)]
                if text
            )
            action = action_label(item.get("recommended_action")) if "recommended_action" in columns else ""
            st.markdown(
                '<div class="mobile-part">'
                f'<div class="mobile-part-head"><div><span class="mobile-part-rank">#{int(item.get("rank", index + 1)):02d}</span> '
                f'<span class="mobile-part-id">{html.escape(item_id)}</span></div>{badges}</div>'
                f'<div class="mobile-part-meta">{html.escape(item_type)} · {html.escape(location)}</div>'
                + (f'<div class="mobile-part-chips">{html.escape(chips)}</div>' if chips else "")
                + (f'<div class="mobile-part-action">{html.escape(action)}</div>' if action else "")
                + "</div>",
                unsafe_allow_html=True,
            )
            if st.button("Buka detail PART →", key=f"{key}_open_{index}", use_container_width=True):
                st.session_state["detail_item_id"] = item_id
                st.switch_page("pages/2_Part_Detail.py")


def labeled_table(
    items: list[dict],
    columns: list[str],
    empty_message: str = "",
    key: str | None = None,
    id_field: str | None = None,
) -> str | None:
    if not items:
        if empty_message:
            st.info(empty_message)
        return None

    display = _display_frame(items, columns, None)
    if not key or not id_field:
        st.dataframe(display, width="stretch", hide_index=True)
        return None

    frame = pd.DataFrame(items)
    event = st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    rows = event.selection.rows if event and event.selection else []
    return str(frame.iloc[rows[0]][id_field]) if rows else None


def horizon_metrics(failure: dict) -> None:
    columns = st.columns(4)
    for column, days in zip(columns, (30, 60, 90, 120), strict=True):
        key = f"failure_probability_{days}d"
        columns_value = api_client.percent(failure.get(key))
        column.metric(f"Rusak dalam {days} hari", columns_value)


def probability_caption() -> None:
    st.caption("Persentase menunjukkan peluang, bukan kepastian bahwa PART akan rusak.")


def fact_grid(items: list[tuple[str, str, bool] | tuple[str, str, bool, str]]) -> None:
    cells = []
    for entry in items:
        label, value, mono = entry[0], entry[1], entry[2]
        modifier = entry[3] if len(entry) > 3 else ""
        classes = " ".join(part for part in ("fact-item", modifier) if part)
        cells.append(
            f'<div class="{classes}"><div class="fact-label">{html.escape(label)}</div>'
            f'<div class="fact-value{" mono" if mono else ""}">{value}</div></div>'
        )
    st.markdown(f'<div class="fact-grid">{"".join(cells)}</div>', unsafe_allow_html=True)


def capture_curve_chart(curve: pd.DataFrame, capacity: int) -> alt.LayerChart:
    base = alt.Chart(curve).encode(x=alt.X("n:Q", title="Jumlah PART yang diperiksa"))
    actual = base.mark_line(color="#155EEF", strokeWidth=2.2).encode(
        y=alt.Y("tertangkap:Q", title="Perkiraan kerusakan yang tercakup")
    )
    random_line = base.mark_line(color="#929BA6", strokeDash=[4, 4]).encode(y="acak:Q")
    rule_chart = alt.Chart(pd.DataFrame({"n": [capacity]})).mark_rule(
        color="#B42318", strokeWidth=1.4
    ).encode(x="n:Q")
    return (actual + random_line + rule_chart).properties(height=220)


