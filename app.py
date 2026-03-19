import sys
import types

# 🔥 jiter fix
fake_jiter = types.ModuleType("jiter")
fake_jiter.from_json = lambda x: x
sys.modules['jiter'] = fake_jiter

import streamlit as st
from openai import OpenAI
import pdfplumber
import re

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# =========================
# CLEAN TEXT
# =========================
def clean_text(text):
    text = text.upper()
    text = re.sub(r'\s+', ' ', text)
    return text
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

def parse_bond(text):
    data = {
        "name": "N/A",
        "isin": extract_isin(text),
        "coupon": extract_coupon(text),
        "issue": "N/A",
        "maturity": "N/A",
        "settlement": "N/A"
    }

    ai_data = ai_extract(text)

    for k in data:
        if data[k] == "N/A":
            val = ai_data.get(k, "N/A")

            if val and val not in ["N/A", "Sample Bond", "Test Bond"]:
                data[k] = val

    return data
# =========================
# AI EXTRACT
# =========================
def ai_extract(text):
    import json

    prompt = f"""
You are a strict financial data extractor.

Extract ONLY data that EXISTS in the text.
DO NOT guess.
DO NOT invent values.

Return ONLY valid JSON.

If a field is not clearly found → return "N/A"

Format:
{{
"name": "",
"isin": "",
"coupon": "",
"issue": "",
"maturity": "",
"settlement": ""
}}

TEXT:
{text[:4000]}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        content = response.choices[0].message.content.strip()

        # 🔥 καθαρισμός αν βάλει ```json
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()

        return json.loads(content)

    except Exception as e:
        return {
            "name": "N/A",
            "isin": "N/A",
            "coupon": "N/A",
            "issue": "N/A",
            "maturity": "N/A",
            "settlement": "N/A",
            "error": str(e)
        }
# =========================
# PDF READ
# =========================
def read_pdf(file):
    text = ""
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text

# =========================
# UI
# =========================
st.title("Bond Analyzer AI 🚀")

uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])

if uploaded_file:
    text = read_pdf(uploaded_file)
    text = clean_text(text)

    with st.spinner("Analyzing..."):
        data = parse_bond(text)

    st.success("Done!")

    st.json(data)