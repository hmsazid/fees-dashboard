import streamlit as st
import pandas as pd
import fitz  # PyMuPDF
import re
import io
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

# ---------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------
st.set_page_config(
    page_title="Fees Dashboard – Axcess & Unlimit",
    layout="wide"
)

# ---------------------------------------------------------
# GLOBAL THEME & STYLING
# ---------------------------------------------------------
st.markdown("""
<style>
[data-testid="stAppViewContainer"] {
    background: radial-gradient(circle at top left, #eef4ff 0, #f7fbff 35%, #faf5ff 100%);
}
h1, h2, h3, h4 { color: #11224d; }

div.stButton > button {
    border-radius: 999px;
    padding: 0.6rem 1.4rem;
    border: none;
    font-weight: 600;
    background: linear-gradient(135deg, #3f8efc, #00c49a);
    color: white;
    box-shadow: 0 8px 20px rgba(63,142,252,0.4);
    font-size: 15px;
}
div.stButton > button:hover {
    background: linear-gradient(135deg, #316bcc, #009875);
    box-shadow: 0 10px 26px rgba(63,142,252,0.55);
    transform: translateY(-1px);
}
[data-testid="stFileUploadDropzone"] {
    border: 2px dashed rgba(63,142,252,0.7) !important;
    border-radius: 18px !important;
    background: linear-gradient(135deg, rgba(63,142,252,0.04), rgba(6,194,153,0.04));
}
[data-testid="stMetric"] {
    background: rgba(255,255,255,0.9);
    border-radius: 14px;
    padding: 0.8rem;
    box-shadow: 0 4px 14px rgba(17,34,77,0.08);
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# AXCESS PDF EXTRACTION LOGIC
# ---------------------------------------------------------
def extract_info_from_pdf(pdf_bytes, filename):
    errors = []

    date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", filename)
    settlement_date = date_match.group(1) if date_match else None

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            all_blocks = []
            page0_blocks = []

            for i, page in enumerate(doc):
                for b in page.get_text("blocks"):
                    rec = (float(b[1]), float(b[0]), b[4])
                    if i == 0:
                        page0_blocks.append(rec)
                    all_blocks.append(rec)
    except Exception as e:
        return None, f"❌ Error reading PDF '{filename}': {str(e)}"

    all_blocks.sort(key=lambda x: (x[0], x[1]))
    text = re.sub(r"\s{2,}", " ", " ".join(t[2] for t in all_blocks))

    if not settlement_date:
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
        settlement_date = m.group(1) if m else None
        if not settlement_date:
            errors.append("Settlement Date not found.")

    m_fee = re.search(r"Total\s*fees\s*[-]?\$?\s*([-\d,]+\.\d{2})", text, re.I)
    total_fees = float(m_fee.group(1).replace(",", "")) if m_fee else None
    if total_fees is None:
        errors.append("Total Fees not found.")

    y_sales = next((y for y, x, t in page0_blocks if t.strip().lower() == "sales"), None)
    x_count = next((x for y, x, t in page0_blocks if t.strip().upper() == "COUNT"), None)

    sales_count = None
    if y_sales:
        candidates = [(x, int(t.strip()))
                      for y, x, t in page0_blocks
                      if re.fullmatch(r"\d{1,6}", t.strip()) and abs(y - y_sales) <= 4]
        if candidates:
            sales_count = min(candidates, key=lambda c: abs(c[0] - x_count))[1] if x_count else max(candidates)[1]

    if sales_count is None:
        errors.append("Sales Count not found.")

    if errors:
        return None, f"❌ {filename}: " + " | ".join(errors)

    return {
        "Settlement Date": settlement_date,
        "Total Fees (USD)": total_fees,
        "Sales Count": sales_count,
        "File Name": filename
    }, None

# ---------------------------------------------------------
# UNLIMIT CSV MERGE LOGIC
# ---------------------------------------------------------
def merge_csv_files_from_uploads(uploaded_files):
    combined = []
    issues = []

    required_headers = {"Order ID", "Date", "Transaction Amount", "Transaction Currency"}
    numeric_cols = ["Transaction Amount", "Settlement Amount", "Interchange Fee", "Scheme Fee", "Acquirer Fee"]

    for f in uploaded_files:
        try:
            df = pd.read_csv(f, delimiter=";", header=None, dtype=str, on_bad_lines="skip")

            header_found = False
            for idx, row in df.iterrows():
                if required_headers.issubset(set(str(v).strip() for v in row.values)):
                    df.columns = [str(v).strip() for v in row.values]
                    df = df.iloc[idx + 1:].reset_index(drop=True)
                    header_found = True
                    break

            if not header_found:
                issues.append({"file": f.name, "reason": "Missing headers"})
                continue

            df = df.dropna(how="all")
            first_col = df.columns[0]
            df[first_col] = df[first_col].astype(str)
            df = df[~df[first_col].str.startswith("Total")]

            df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True).dt.date

            for c in numeric_cols:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")

            df["Calculated"] = (
                df["Transaction Amount"] / df["Settlement Amount"] *
                (df["Interchange Fee"].fillna(0) +
                 df["Scheme Fee"].fillna(0) +
                 df["Acquirer Fee"].fillna(0)).abs()
            )

            df["Source File"] = f.name
            combined.append(df)

        except Exception as e:
            issues.append({"file": f.name, "reason": str(e)})

    return (pd.concat(combined, ignore_index=True) if combined else None), issues


# ---------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------
defaults = {
    "axcess_uploaded_files": None,
    "axcess_uploader_key": "axcess_init",
    "axcess_clear_triggered": False,
    "unlimit_uploaded_files": [],
    "unlimit_uploader_key": 0,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------
# HEADER
# ---------------------------------------------------------
st.title("Fees Calculation Dashboard")
st.caption("Upload Axcess PDFs or Unlimit CSVs, extract fees, view summaries and download reports.")

# ---------------------------------------------------------
# TABS
# ---------------------------------------------------------
tab_axcess, tab_unlimit = st.tabs(["💼 Axcess (PDF)", "💳 Unlimit (CSV)"])

# =========================================================
#                     AXCESS TAB
# =========================================================
with tab_axcess:

    st.subheader("📂 Upload Axcess PDF Statements")

    uploaded_axcess = st.file_uploader(
        "Upload PDF statements",
        type=["pdf"],
        accept_multiple_files=True,
        key=st.session_state.axcess_uploader_key
    )

    # Store files
    if uploaded_axcess and not st.session_state.axcess_clear_triggered:
        st.session_state.axcess_uploaded_files = uploaded_axcess
    else:
        st.session_state.axcess_clear_triggered = False

    # Buttons
    clear_axcess = st.button("🧹 Clear Axcess Uploaded Files")
    run_extract = st.button("🚀 Run Axcess Extraction")

    if clear_axcess:
        st.session_state.axcess_uploaded_files = None
        st.session_state.axcess_uploader_key = f"axcess_{datetime.now().isoformat()}"
        st.session_state.axcess_clear_triggered = True
        st.success("Axcess files cleared successfully.")
        st.rerun()

    # RUN EXTRACTION
    if run_extract:
        files = st.session_state.axcess_uploaded_files
        if not files:
            st.warning("Please upload Axcess PDFs first.")
            st.stop()

        results, errors = [], []
        for f in files:
            data, err = extract_info_from_pdf(f.read(), f.name)
            if err:
                errors.append(err)
            else:
                results.append(data)

        for e in errors:
            st.error(e)

        if not results:
            st.error("No valid Axcess data extracted.")
            st.stop()

        df_ax = pd.DataFrame(results)
        df_ax["Settlement Date"] = pd.to_datetime(df_ax["Settlement Date"])
        df_ax = df_ax.sort_values("Settlement Date")

        # ---------------------------------------------------------
        # METRICS FIRST
        # ---------------------------------------------------------
        st.subheader("📊 Axcess Summary Overview")

        c1, c2 = st.columns(2)
        c1.metric("Total Fees (USD)", f"{df_ax['Total Fees (USD)'].sum():,.2f}")
        c2.metric("Total Sales Count", int(df_ax["Sales Count"].sum()))

        # ---------------------------------------------------------
        # SUMMARY TABLE SECOND
        # ---------------------------------------------------------
        st.subheader("📄 Axcess Extracted Summary")
        st.dataframe(df_ax, use_container_width=True)

        # ---------------------------------------------------------
        # TREND CHART
        # ---------------------------------------------------------
        theme = st.get_option("theme.base")
        is_dark = theme == "dark"

        bg = "#0F1116" if is_dark else "white"
        paper = bg
        text_color = "white" if is_dark else "black"
        bar_color = "#3FA7FF" if is_dark else "#0073E6"

        st.subheader("📈 Axcess Fees Trend")

        fig_ax = make_subplots(specs=[[{"secondary_y": False}]])
        fig_ax.add_trace(go.Bar(
            x=df_ax["Settlement Date"],
            y=df_ax["Total Fees (USD)"],
            text=[f"{v:,.2f}" for v in df_ax["Total Fees (USD)"]],
            textposition="outside",
            marker_color=bar_color
        ))

        if len(df_ax) > 1:
            trend = df_ax["Total Fees (USD)"].rolling(2, min_periods=1).mean()
            fig_ax.add_trace(go.Scatter(
                x=df_ax["Settlement Date"],
                y=trend,
                mode="lines",
                line=dict(color="red", width=3)
            ))

        fig_ax.update_layout(
            title="Axcess – Total Fees Over Time",
            plot_bgcolor=bg,
            paper_bgcolor=paper,
            font=dict(color=text_color),
            height=550
        )

        st.plotly_chart(fig_ax, use_container_width=True)

        # ---------------------------------------------------------
        # DOWNLOAD EXCEL
        # ---------------------------------------------------------
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_ax.to_excel(writer, index=False)

        st.download_button(
            "📥 Download Axcess Excel Report",
            buffer.getvalue(),
            file_name="Axcess_Fees_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    else:
        st.info("Upload Axcess PDFs and click **Run Axcess Extraction** to begin.")
# =========================================================
#                     UNLIMIT TAB
# =========================================================
with tab_unlimit:

    st.subheader("📁 Upload Unlimit CSV Files")

    uploaded_unlimit = st.file_uploader(
        "Upload your Unlimit CSV files",
        type=["csv"],
        accept_multiple_files=True,
        key=f"unlimit_{st.session_state.unlimit_uploader_key}"
    )

    if uploaded_unlimit:
        st.session_state.unlimit_uploaded_files = uploaded_unlimit

    # Buttons
    clear_unlimit = st.button("🧹 Clear Unlimit Uploaded Files")
    run_merge = st.button("🚀 Run Unlimit Merge & Summary")

    if clear_unlimit:
        st.session_state.unlimit_uploaded_files = []
        st.session_state.unlimit_uploader_key += 1
        st.success("Unlimit files cleared successfully.")
        st.rerun()

    # RUN MERGE
    if run_merge:
        files = st.session_state.unlimit_uploaded_files

        if not files:
            st.warning("Please upload CSV files first.")
            st.stop()

        merged_df, issues = merge_csv_files_from_uploads(files)

        if issues:
            st.error("Some Unlimit files had issues:")
            st.dataframe(pd.DataFrame(issues), use_container_width=True)

        if merged_df is None or merged_df.empty:
            st.error("No valid Unlimit data extracted.")
            st.stop()

        st.success("Unlimit Merge Completed 🎉")

        # ---------------------------------------------------------
        # METRICS FIRST
        # ---------------------------------------------------------
        st.subheader("📘 Unlimit Overall Summary")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Files", len(files))
        col2.metric("Total Rows", f"{len(merged_df):,}")
        col3.metric("Total Amount", f"{merged_df['Transaction Amount'].sum():,.2f}")
        col4.metric("Total Fees", f"{merged_df['Calculated'].sum():,.2f}")

        # ---------------------------------------------------------
        # DATE-WISE SUMMARY SECOND
        # ---------------------------------------------------------
        st.subheader("📆 Date-wise Summary")

        summary = (
            merged_df.groupby("Date")
            .agg(
                Count=("Date", "size"),
                Amount=("Transaction Amount", "sum"),
                Fees=("Calculated", "sum")
            )
            .reset_index()
            .sort_values("Date")
        )

        st.dataframe(summary, use_container_width=True)

        # ---------------------------------------------------------
        # MERGED FILE PREVIEW THIRD
        # ---------------------------------------------------------
        st.subheader("📄 Preview of Merged CSV Data")
        st.dataframe(merged_df.head(200), use_container_width=True)
        st.caption("Showing first 200 rows. Full CSV is available for download below.")

        # ---------------------------------------------------------
        # TREND CHART FOURTH
        # ---------------------------------------------------------
        theme = st.get_option("theme.base")
        is_dark = (theme == "dark")

        bg = "#0F1116" if is_dark else "white"
        paper = bg
        text_color = "white" if is_dark else "black"
        bar_color = "#3FA7FF" if is_dark else "#0073E6"

        st.subheader("📈 Unlimit Fees Trend")

        summary["Date"] = pd.to_datetime(summary["Date"])

        fig_un = make_subplots(specs=[[{"secondary_y": False}]])
        fig_un.add_trace(go.Bar(
            x=summary["Date"],
            y=summary["Fees"],
            marker_color=bar_color,
            text=[f"{v:,.2f}" for v in summary["Fees"]],
            textposition="outside"
        ))

        if len(summary) > 1:
            trend_un = summary["Fees"].rolling(2, min_periods=1).mean()
            fig_un.add_trace(go.Scatter(
                x=summary["Date"],
                y=trend_un,
                mode="lines",
                line=dict(color="red", width=3)
            ))

        fig_un.update_layout(
            title="Unlimit – Fees Over Time",
            xaxis_title="Date",
            yaxis_title="Fees (Calculated)",
            plot_bgcolor=bg,
            paper_bgcolor=paper,
            font=dict(color=text_color),
            height=550
        )

        st.plotly_chart(fig_un, use_container_width=True)

        # ---------------------------------------------------------
        # DOWNLOAD CSV FIFTH
        # ---------------------------------------------------------
        csv_bytes = merged_df.to_csv(index=False).encode("utf-8-sig")

        st.download_button(
            "⬇ Download Unlimit Merged CSV",
            csv_bytes,
            file_name="Unlimit_Merged_Data.csv",
            mime="text/csv"
        )

    else:
        st.info("Upload Unlimit CSVs and click **Run Unlimit Merge & Summary** to begin.")
