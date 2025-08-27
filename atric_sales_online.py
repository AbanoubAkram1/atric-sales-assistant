#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atric Sales Assistant (Online)
- Reads live availability from Google Sheets (public viewer link, no API keys)
- Smart text query (project, status, bedrooms, price, area, building, unit)
- Sidebar filters
- Per-unit PDF + Video display using in-app MEDIA_MAP (no need to edit Google Sheet)
- Ready for Streamlit Cloud deployment
"""

import re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Atric Sales Assistant (Online)", layout="wide")

# ====== CONFIG: put your Google Sheet ID here ======
SHEET_ID = "1z3thCqp7QA-4Bq7Kn6zc5ptPGLYXUYwdGMu6Atncs-w"
SHEETS = ["Boardwalk", "BOHO"]  # sheet/tab names inside Google Sheets
REFRESH_SECONDS = 90            # cache TTL (seconds) for auto-refresh
# ====================================================

def csv_export_url(sheet_name: str) -> str:
    # Use the gviz CSV export for public sheets
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet={sheet_name}"

def coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "").str.strip(), errors="coerce")

def cleanup_cols(df: pd.DataFrame) -> pd.DataFrame:
    # Normalize object columns
    for c in df.select_dtypes(include=["object"]).columns:
        df[c] = df[c].astype(str).str.strip()
        df[c] = df[c].replace({"nan": pd.NA})
    # Normalize fields
    if "floor" in df.columns:
        df["floor"] = df["floor"].str.replace(r"\s+", " ", regex=True)
    if "configuration" in df.columns:
        df["configuration"] = df["configuration"].str.replace(r"\s+", " ", regex=True)
    # Convert numerics
    for num_col in ["price", "selling_area", "land_area", "garden_area", "terrace_area"]:
        if num_col in df.columns:
            df[num_col] = coerce_numeric(df[num_col])
    # Status default
    if "status" in df.columns:
        df["status"] = df["status"].fillna("Available")
    return df

# Column maps like your Excel headers -> normalized names
BW_KEEP = {
    "#": "row_no",
    "Building No.#": "building",
    "Unit No.#": "unit_no",
    "Floor No.#": "floor",
    "Configuration": "configuration",
    "Selling Area": "selling_area",
    "Land Area": "land_area",
    "Open Terrace\n Area": "terrace_area",
    "10 Years 0%": "price",
    "Status": "status",
}
BOHO_KEEP = {
    "#": "row_no",
    "Unit Type": "unit_type",
    "Building No#": "building",
    "Unit No.#": "unit_no",
    "Floor No.#": "floor",
    "Configuration": "configuration",
    "Selling Area": "selling_area",
    "Land Area": "land_area",
    "Garden area": "garden_area",
    "Original Value": "price",
    "Status": "status",
}

EXPECTED_BW_HEADERS = set(BW_KEEP.keys())
EXPECTED_BOHO_HEADERS = set(BOHO_KEEP.keys())

def try_load_sheet(url: str) -> pd.DataFrame:
    """Try header row at index 0 first; if it doesn't match, try Excel-style header at row 1."""
    df0 = pd.read_csv(url)
    if len(set(df0.columns) & (EXPECTED_BW_HEADERS | EXPECTED_BOHO_HEADERS)) >= 4:
        return df0
    # Otherwise, try header at row 1 (like your Excel pattern)
    raw = pd.read_csv(url, header=None)
    if len(raw) >= 2:
        header = raw.iloc[1].tolist()
        df = raw.iloc[2:].copy()
        df.columns = header
        return df
    return df0

@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def load_all_units() -> pd.DataFrame:
    # Load both sheets
    bw_raw = try_load_sheet(csv_export_url("Boardwalk"))
    boho_raw = try_load_sheet(csv_export_url("BOHO"))

    # Keep/rename important columns
    bw = bw_raw[[c for c in BW_KEEP if c in bw_raw.columns]].rename(columns=BW_KEEP)
    bw["project"] = "Boardwalk"
    boho = boho_raw[[c for c in BOHO_KEEP if c in boho_raw.columns]].rename(columns=BOHO_KEEP)
    boho["project"] = "BOHO"

    # Clean
    bw, boho = cleanup_cols(bw), cleanup_cols(boho)

    # Harmonize & concat
    all_cols = sorted(set(bw.columns).union(set(boho.columns)))
    bw, boho = bw.reindex(columns=all_cols), boho.reindex(columns=all_cols)
    units = pd.concat([bw, boho], ignore_index=True).dropna(how="all")

    # Column order
    key_order = [
        "project", "building", "unit_no", "floor", "configuration", "unit_type",
        "selling_area", "land_area", "garden_area", "terrace_area", "price", "status"
    ]
    ordered = key_order + [c for c in units.columns if c not in key_order]
    units = units.reindex(columns=[c for c in ordered if c in units.columns])

    # Add lowercase helper columns (not shown) for matching
    units["_unit_upper"] = units["unit_no"].astype(str).str.upper()
    units["_building_upper"] = units["building"].astype(str).str.upper()
    return units

def parse_number(s: str):
    if not s:
        return None
    s = str(s).strip().lower().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([mk])?", s)
    if not m:
        try:
            return float(s)
        except Exception:
            return None
    val = float(m.group(1))
    if m.group(2) == "m":
        val *= 1_000_000
    elif m.group(2) == "k":
        val *= 1_000
    return val

def parse_query(q: str):
    ql = q.lower()
    f = {}
    # project
    if "boho" in ql: f["project"] = ["BOHO"]
    if "boardwalk" in ql: f.setdefault("project", []).append("Boardwalk")
    # status
    if "available" in ql: f["status"] = ["Available"]
    if "hold" in ql: f["status"] = ["Hold"]
    # bedrooms
    m = re.search(r"(\d+)\s*(bed|bedroom|bedrooms)", ql)
    if m: f["bedrooms"] = m.group(1)
    # price
    m = re.search(r"(under|<=|less than)\s*([\d\.,]+)\s*([mk])?", ql)
    if m:
        val = float(m.group(2).replace(",", "")); unit = m.group(3) or ""
        f["max_price"] = val * (1_000_000 if unit == "m" else 1_000 if unit == "k" else 1)
    m = re.search(r"(over|>=|more than|above)\s*([\d\.,]+)\s*([mk])?", ql)
    if m:
        val = float(m.group(2).replace(",", "")); unit = m.group(3) or ""
        f["min_price"] = val * (1_000_000 if unit == "m" else 1_000 if unit == "k" else 1)
    # area range
    m = re.search(r"(area|sqm)\s*(>=|over|min)\s*([\d\.,]+)", ql)
    if m: f["min_area"] = float(m.group(3).replace(",", ""))
    m = re.search(r"(area|sqm)\s*(<=|under|max)\s*([\d\.,]+)", ql)
    if m: f["max_area"] = float(m.group(3).replace(",", ""))
    # exact area if user typed only a plain number like "180"
    m_all = re.findall(r"\b(\d{2,4})\b", ql)
    if m_all and "bedrooms" not in f and "min_price" not in f and "max_price" not in f:
        f["exact_area"] = float(m_all[0])
    # building like C1, A2
    m = re.search(r"\b([a-z]\d{1,2})\b", ql, re.IGNORECASE)
    if m: f["building"] = m.group(1).upper()
    # exact unit like C1-002
    m = re.search(r"\b([a-z]\d{1,2}-\d{2,3})\b", ql, re.IGNORECASE)
    if m: f["unit_no"] = m.group(1).upper()
    return f

def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    out = df.copy()
    if "project" in f: out = out[out["project"].isin(f["project"])]
    if "status" in f: out = out[out["status"].isin(f["status"])]
    if "bedrooms" in f:
        regex = rf"(^|\s){re.escape(f['bedrooms'])}\s*Bedrooms?"
        out = out[out["configuration"].fillna("").str.contains(regex, case=False, regex=True)]
    if "min_price" in f: out = out[out["price"].fillna(0) >= f["min_price"]]
    if "max_price" in f: out = out[out["price"].fillna(0) <= f["max_price"]]
    if "min_area" in f: out = out[out["selling_area"].fillna(0) >= f["min_area"]]
    if "max_area" in f: out = out[out["selling_area"].fillna(0) <= f["max_area"]]
    if "exact_area" in f: out = out[out["selling_area"].fillna(0) == f["exact_area"]]
    if "building" in f: out = out[out["_building_upper"] == f["building"]]
    if "unit_no" in f: out = out[out["_unit_upper"] == f["unit_no"]]
    return out

# ====== In-app media mapping (web URLs) ======
# Put web-accessible URLs here (YouTube, Google Drive preview links, S3, company CDN...)
# Example for Google Drive PDF preview: https://drive.google.com/file/d/FILE_ID/preview
# Example for direct YouTube: https://youtu.be/VIDEO_ID
MEDIA_MAP = {
    # "C1-002": {"pdf": "https://drive.google.com/file/d/FILE_ID/preview",
    #            "video": "https://youtu.be/VIDEO_ID"},
}

# =================== UI ===================
st.title("Atric Sales Assistant 🤖 (Online)")
st.caption("Live from Google Sheets • Multi-user • Click a unit to see its PDF & video")

# Data + refresh control
left, right = st.columns([1,1])
with left:
    if st.button("🔄 Refresh data now"):
        st.cache_data.clear()
with right:
    st.info(f"Data auto-refreshes every {REFRESH_SECONDS} seconds.", icon="⏱️")

units = load_all_units()
if units.empty:
    st.error("No units loaded. Please check the Google Sheet access or sheet names.")
    st.stop()

# Sidebar filters
st.sidebar.header("Quick Filters")
project = st.sidebar.multiselect("Project", sorted(units["project"].dropna().unique().tolist()))
status = st.sidebar.multiselect("Status", sorted(units["status"].dropna().unique().tolist()))
bedrooms = st.sidebar.text_input("Bedrooms (e.g., 2, 3, 4)")

min_price = st.sidebar.text_input("Min Price (e.g., 5m, 7500000)")
max_price = st.sidebar.text_input("Max Price (e.g., 12m, 12000000)")
min_area = st.sidebar.text_input("Min Area (e.g., 90)")
max_area = st.sidebar.text_input("Max Area (e.g., 200)")

def filter_df(df):
    out = df.copy()
    if project: out = out[out["project"].isin(project)]
    if status: out = out[out["status"].isin(status)]
    if bedrooms:
        nums = [n.strip() for n in bedrooms.split(",") if n.strip()]
        if nums:
            regex = r"(^|\s)(" + "|".join([re.escape(n) for n in nums]) + r")\s*Bedrooms?"
            out = out[out["configuration"].fillna("").str.contains(regex, case=False, regex=True)]
    lo_p, hi_p = parse_number(min_price), parse_number(max_price)
    lo_a, hi_a = parse_number(min_area), parse_number(max_area)
    if lo_p is not None: out = out[out["price"].fillna(0) >= lo_p]
    if hi_p is not None: out = out[out["price"].fillna(0) <= hi_p]
    if lo_a is not None: out = out[out["selling_area"].fillna(0) >= lo_a]
    if hi_a is not None: out = out[out["selling_area"].fillna(0) <= hi_a]
    return out

filtered = filter_df(units)

# Chat-like query
st.subheader("Chat")
prompt = st.text_input("e.g., 'Available BOHO 2 bedrooms under 12m', 'Boardwalk C1-002', 'C1 180'")

chat_df = filtered
if prompt:
    f = parse_query(prompt)
    chat_df = apply_filters(filtered, f)

st.write(f"**Matches:** {len(chat_df)}")
st.dataframe(chat_df, use_container_width=True)

# Unit selection
unit_choices = chat_df["unit_no"].dropna().unique().tolist()
sel = st.selectbox("Choose a unit to preview PDF & video:", ["--"] + unit_choices, index=0)

# If the chat produced exactly one unit, auto-select it
if prompt and "unit_no" in parse_query(prompt) and len(unit_choices) == 1:
    sel = unit_choices[0]

if sel and sel != "--":
    row = chat_df[chat_df["_unit_upper"] == str(sel).upper()]
    if not row.empty:
        row = row.iloc[0]
        st.markdown(f"### Unit {row['unit_no']} in {row['project']}")
        st.write(row[["project","building","unit_no","floor","configuration","selling_area","price","status"]])

        # Show media from in-app mapping
        media = MEDIA_MAP.get(str(row["unit_no"]).upper())
        if media:
            pdf = media.get("pdf")
            vid = media.get("video")

            if pdf:
                st.markdown("**📄 Layout PDF:**")
                # If it's a Google Drive preview URL or any embeddable URL, use iframe
                st.components.v1.iframe(pdf, height=650)
                st.link_button("Open PDF in new tab", pdf)

            if vid:
                st.markdown("**🎥 Video:**")
                st.video(vid)
        else:
            st.warning("No PDF/Video registered for this unit in MEDIA_MAP. Edit MEDIA_MAP in the code to add links.", icon="⚠️")

# Download results
csv = chat_df.drop(columns=[c for c in chat_df.columns if c.startswith("_")], errors="ignore").to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Download results as CSV", data=csv, file_name="filtered_units.csv")
