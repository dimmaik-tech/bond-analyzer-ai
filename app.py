from datetime import datetime
from dateutil.relativedelta import relativedelta
import streamlit as st
from openai import OpenAI
import pdfplumber
import re

# =========================
# OPENAI CLIENT
# =========================
api_key = st.secrets.get("OPENAI_API_KEY")

if not api_key:
    st.error("❌ Missing OPENAI_API_KEY in Streamlit secrets")
    st.stop()

client = OpenAI(api_key=api_key)

# =========================
# CLEAN TEXT
# =========================
def clean_text(text):
    if not text:
        return ""

    text = text.replace(".", "/")
    text = text.upper()
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# =========================
# EXTRACT BASIC
# =========================
def extract_isin(text):
    if not text:
        return "N/A"

    m = re.search(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", text)
    return m.group(0) if m else "N/A"


def extract_coupon(text):
    if not text:
        return "N/A"

    matches = re.findall(r"(\d{1,2}[.,]\d{1,3})\s*%", text)
    for m in matches:
        try:
            v = float(m.replace(",", "."))
            if 0 < v < 20:
                return f"{v:.3f}".rstrip("0").rstrip(".") + "%"
        except:
            pass

    return "N/A"


def extract_frequency(text):
    if not text:
        return "N/A"

    if "SEMI-ANNUAL" in text or "SEMI ANNUAL" in text or "SEMIANNUAL" in text:
        return "Semi-Annual"
    if "QUARTERLY" in text:
        return "Quarterly"
    if "ANNUAL" in text:
        return "Annual"

    return "N/A"

# =========================
# DATE HELPERS
# =========================
def normalize_date(date_str):
    if not date_str or date_str == "N/A":
        return "N/A"

    date_str = str(date_str).strip()

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d.%m.%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%d/%m/%Y")
        except:
            pass

    return date_str


def generate_coupon_dates(issue, maturity, frequency):
    try:
        issue_date = datetime.strptime(issue, "%d/%m/%Y")
        maturity_date = datetime.strptime(maturity, "%d/%m/%Y")
    except:
        return []

    if frequency == "Annual":
        step = 12
    elif frequency == "Quarterly":
        step = 3
    else:
        step = 6

    dates = []
    current = issue_date + relativedelta(months=step)

    while current <= maturity_date:
        dates.append(current.strftime("%d/%m/%Y"))
        current += relativedelta(months=step)

    return dates

# =========================
# AI EXTRACT
# =========================
def ai_extract(text):
    import json

    if not text:
        return {}

    prompt = f"""
Extract bond data from the following text.

Rules:
- Return JSON only
- Do not explain
- If a value is not clearly present, return "N/A"
- coupon_frequency must be one of:
  "Annual", "Semi-Annual", "Quarterly", "N/A"

Fields:
name, isin, coupon, issue, maturity, settlement, coupon_frequency

TEXT:
{text[:3000]}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        content = response.choices[0].message.content.strip()

        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()

        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}

    except:
        return {}

# =========================
# PARSE BOND
# =========================
def parse_bond(text):
    data = {
        "name": "N/A",
        "isin": extract_isin(text),
        "coupon": extract_coupon(text),
        "coupon_frequency": extract_frequency(text),
        "issue": "N/A",
        "maturity": "N/A",
        "settlement": "N/A",
        "coupon_dates": []
    }

    ai_data = ai_extract(text)

    # merge AI only where missing
    for k in data:
        if data[k] == "N/A":
            val = ai_data.get(k, "N/A")
            if val and val != "N/A":
                data[k] = val

    # normalize frequency
    freq = str(data.get("coupon_frequency", "N/A")).strip().lower()
    if freq == "annual":
        data["coupon_frequency"] = "Annual"
    elif freq in ["semi-annual", "semi annual", "semiannual"]:
        data["coupon_frequency"] = "Semi-Annual"
    elif freq == "quarterly":
        data["coupon_frequency"] = "Quarterly"
    else:
        data["coupon_frequency"] = "N/A"

    # normalize dates
    data["issue"] = normalize_date(data["issue"])
    data["maturity"] = normalize_date(data["maturity"])
    data["settlement"] = normalize_date(data["settlement"])

    # generate schedule
    if data["issue"] != "N/A" and data["maturity"] != "N/A":
        freq = data["coupon_frequency"]
        if freq == "N/A":
            freq = "Semi-Annual"

        data["coupon_dates"] = generate_coupon_dates(
            data["issue"],
            data["maturity"],
            freq
        )

    return data

# =========================
# READ PDF
# =========================
def read_pdf(file):
    text = ""

    try:
        file.seek(0)
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    except:
        pass

    return text or ""

# =========================
# UI
# =========================
st.title("Bond Analyzer AI 🚀")

uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])

if uploaded_file:
    with st.spinner("Reading PDF..."):
        raw_text = read_pdf(uploaded_file)

    st.write("LEN TEXT:", len(raw_text))

    if raw_text:
        st.write("Preview:", raw_text[:300])
    else:
        st.error("❌ Το PDF δεν διαβάστηκε. Πιθανό scan / image PDF.")
        st.stop()

    text = clean_text(raw_text)

    if not text or len(text.strip()) < 5:
        st.error("❌ Το PDF δεν έχει αναγνώσιμο κείμενο")
        st.stop()

    with st.spinner("Analyzing..."):
        data = parse_bond(text)

    st.success("Done!")
    st.json(data)
