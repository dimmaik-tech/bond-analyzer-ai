from datetime import datetime
from dateutil.relativedelta import relativedelta
import pytesseract
import streamlit as st
from openai import OpenAI
import pdfplumber
import re
from pdf2image import convert_from_bytes

# 🔥 SET TESSERACT PATH
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# 🔥 OPENAI CLIENT
import streamlit as st
from openai import OpenAI

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
   

# =========================
# CLEAN TEXT
# =========================
def clean_text(text):
    text = text.replace(".", "/")
    text = text.upper()
    text = re.sub(r'\s+', ' ', text)
    return text


# =========================
# EXTRACT BASIC
# =========================
def extract_isin(text):
    m = re.search(r'\b[A-Z]{2}[A-Z0-9]{9}\d\b', text)
    return m.group(0) if m else "N/A"


def extract_coupon(text):
    matches = re.findall(r'(\d{1,2}\.\d{1,3})\s*%', text)
    for m in matches:
        v = float(m)
        if 0 < v < 20:
            return f"{v}%"
    return "N/A"


def extract_frequency(text):
    if "SEMI-ANNUAL" in text or "SEMI ANNUAL" in text:
        return "Semi-Annual"
    if "ANNUAL" in text:
        return "Annual"
    if "QUARTERLY" in text:
        return "Quarterly"
    return "N/A"


# =========================
# DATE FIX
# =========================
def normalize_date(date_str):
    try:
        if "-" in date_str:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        return date_str
    except:
        return date_str


# =========================
# GENERATE COUPON DATES
# =========================
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

    prompt = f"""
Extract bond data from text.

Return JSON only.

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

        return json.loads(content)

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

    # merge AI
    for k in data:
        if data[k] == "N/A":
            val = ai_data.get(k, "N/A")
            if val and val != "N/A":
                data[k] = val

    # fix dates
    if data["issue"] != "N/A":
        data["issue"] = normalize_date(data["issue"])

    if data["maturity"] != "N/A":
        data["maturity"] = normalize_date(data["maturity"])

    # schedule
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
# READ PDF + OCR
# =========================
def read_pdf(file):
    text = ""

    try:
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
        text = read_pdf(uploaded_file)

    if not text or len(text.strip()) < 20:
        st.error("❌ Το PDF είναι scan ή δεν υποστηρίζεται")
        st.stop()

    st.write("Preview:", text[:300])

    with st.spinner("Analyzing..."):
        data = parse_bond(text)

    st.success("Done!")
    st.json(data)