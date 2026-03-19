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

# =========================
# AI EXTRACT
# =========================
def ai_extract(text):
    prompt = f"""
Extract bond data from the following text.

Return STRICT JSON:
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

    import json

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        return json.loads(response.choices[0].message.content)

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
        data = ai_extract(text)

    st.success("Done!")

    st.json(data)