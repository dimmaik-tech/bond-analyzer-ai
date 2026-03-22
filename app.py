from datetime import datetime
from dateutil.relativedelta import relativedelta
from io import BytesIO
import base64
import json
import re

import streamlit as st
from openai import OpenAI
from pdf2image import convert_from_bytes
from PIL import Image

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Bond Analyzer v8.6 - Vision PDF", layout="wide")

POPPLER_PATH = r"C:\poppler-25.12.0\Library\bin"

# =========================
# CONSTANTS
# =========================
MONTHS = {
    "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRUARY": 2,
    "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4,
    "MAY": 5,
    "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10,
    "NOV": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBER": 12
}

ALLOWED_FREQUENCIES = ["N/A", "Annual", "Semi-Annual", "Quarterly", "Monthly"]
ALLOWED_RATE_TYPES = ["N/A", "Fixed", "Floating"]

ALL_LOCKABLE_FIELDS = [
    "name",
    "issuer",
    "isin",
    "coupon",
    "coupon_frequency",
    "issue",
    "maturity",
    "settlement",
    "price",
    "nominal",
    "currency",
    "day_count",
    "rate_type",
    "accrued_interest",
    "interest_days",
]

DEFAULT_PROFILE_NAME = "Default"

BANK_KEYWORDS = {
    "Citigroup": ["CITIGROUP"],
    "UBS": ["UBS", "UNION BANK OF SWITZERLAND"],
    "Credit Suisse": ["CREDIT SUISSE"],
    "Deutsche": ["DEUTSCHE BANK"],
    "Caixa": ["CAIXABANK", "CAIXA BANK"],
}

# =========================
# HELPERS
# =========================
def compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_text_field(val) -> str:
    if val is None:
        return "N/A"
    s = compact_spaces(str(val))
    return s if s else "N/A"


def normalize_percent_str(val) -> str:
    if val is None:
        return "N/A"

    s = str(val).strip()
    if not s or s.upper() == "N/A":
        return "N/A"

    s = s.replace("%", "").replace(",", ".").strip()

    try:
        num = float(s)
        if num <= 0 or num >= 200:
            return "N/A"
        return f"{num:.6f}".rstrip("0").rstrip(".") + "%"
    except Exception:
        return "N/A"


def percent_to_float(val: str):
    if not val or val == "N/A":
        return None
    try:
        return float(str(val).replace("%", "").replace(",", "."))
    except Exception:
        return None


def normalize_nominal_str(val) -> str:
    if val is None:
        return "N/A"

    s = compact_spaces(str(val))
    if not s or s.upper() == "N/A":
        return "N/A"

    s = s.replace("’", "'").replace("‘", "'")

    m = re.match(r"^(EUR|USD|GBP|CHF)\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        ccy = m.group(1).upper()
        amt = m.group(2).replace(" ", "").replace("'", "")
        return f"{ccy} {amt}"

    return s


def normalize_date(date_str) -> str:
    if date_str is None:
        return "N/A"

    s = str(date_str).strip()
    if not s or s.upper() == "N/A":
        return "N/A"

    s = s.replace(".", "/").replace("-", "/")
    s = re.sub(r"\s+", " ", s)

    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        d, mth, y = m.groups()
        d = int(d)
        mth = int(mth)
        y = int(y)
        if y < 100:
            y += 2000
        try:
            return datetime(y, mth, d).strftime("%d/%m/%Y")
        except Exception:
            return "N/A"

    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        y, mth, d = m.groups()
        try:
            return datetime(int(y), int(mth), int(d)).strftime("%d/%m/%Y")
        except Exception:
            return "N/A"

    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", s)
    if m:
        d, mon, y = m.groups()
        mon_num = MONTHS.get(mon.upper())
        if mon_num:
            try:
                return datetime(int(y), mon_num, int(d)).strftime("%d/%m/%Y")
            except Exception:
                return "N/A"

    return "N/A"


def parse_date_to_dt(date_str: str):
    try:
        return datetime.strptime(date_str, "%d/%m/%Y")
    except Exception:
        return None


def isin_checksum_valid(isin: str) -> bool:
    if not isin or len(isin) != 12:
        return False
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
        return False

    converted = ""
    for ch in isin:
        if ch.isdigit():
            converted += ch
        else:
            converted += str(ord(ch) - 55)

    total = 0
    reverse_digits = converted[::-1]
    for i, ch in enumerate(reverse_digits):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
        total += n // 10 + n % 10

    return total % 10 == 0


def normalize_isin(isin) -> str:
    if isin is None:
        return "N/A"
    s = re.sub(r"\s+", "", str(isin).upper())
    if isin_checksum_valid(s):
        return s
    return "N/A"


def generate_coupon_dates(issue: str, maturity: str, frequency: str):
    issue_dt = parse_date_to_dt(issue)
    maturity_dt = parse_date_to_dt(maturity)

    if not issue_dt or not maturity_dt:
        return []
    if maturity_dt <= issue_dt:
        return []

    if frequency == "Annual":
        step_months = 12
    elif frequency == "Quarterly":
        step_months = 3
    elif frequency == "Monthly":
        step_months = 1
    else:
        step_months = 6

    dates = []
    current = issue_dt + relativedelta(months=step_months)
    while current <= maturity_dt:
        dates.append(current.strftime("%d/%m/%Y"))
        current += relativedelta(months=step_months)

    return dates


def validate_coupon_frequency(freq: str) -> str:
    return freq if freq in ALLOWED_FREQUENCIES else "N/A"


def validate_rate_type(rate_type: str) -> str:
    return rate_type if rate_type in ALLOWED_RATE_TYPES else "N/A"


def infer_frequency_from_text(raw_text: str) -> str:
    t = raw_text.upper()
    if any(x in t for x in ["QUARTERLY", "3 MONTH EURIBOR", "3M EURIBOR", "FRN", "FLOATING RATE", "CPS. 28.02./4", "CPS 28.02./4", "/4"]):
        return "Quarterly"
    if any(x in t for x in ["SEMI-ANNUAL", "SEMI ANNUAL", "SEMIANNUAL", "TWICE A YEAR"]):
        return "Semi-Annual"
    if "MONTHLY" in t:
        return "Monthly"
    if any(x in t for x in ["ANNUAL", "YEARLY"]):
        return "Annual"
    return "N/A"


def infer_rate_type_from_text(raw_text: str) -> str:
    t = raw_text.upper()
    if any(x in t for x in ["FLOATING RATE", "FRN", "3 MONTH EURIBOR", "3M EURIBOR"]):
        return "Floating"
    if "FIXED RATE" in t:
        return "Fixed"
    return "N/A"


def validate_and_fix_data(data: dict, raw_text: str = "", source: str = "ai"):
    out = {
        "name": normalize_text_field(data.get("name", "N/A")),
        "issuer": normalize_text_field(data.get("issuer", "N/A")),
        "isin": normalize_isin(data.get("isin", "N/A")),
        "coupon": normalize_percent_str(data.get("coupon", "N/A")),
        "coupon_frequency": validate_coupon_frequency(normalize_text_field(data.get("coupon_frequency", "N/A"))),
        "issue": normalize_date(data.get("issue", "N/A")),
        "maturity": normalize_date(data.get("maturity", "N/A")),
        "settlement": normalize_date(data.get("settlement", "N/A")),
        "price": normalize_percent_str(data.get("price", "N/A")),
        "nominal": normalize_nominal_str(data.get("nominal", "N/A")),
        "currency": normalize_text_field(data.get("currency", "N/A")),
        "day_count": normalize_text_field(data.get("day_count", "N/A")),
        "rate_type": validate_rate_type(normalize_text_field(data.get("rate_type", "N/A"))),
        "accrued_interest": normalize_text_field(data.get("accrued_interest", "N/A")),
        "interest_days": normalize_text_field(data.get("interest_days", "N/A")),
        "coupon_dates": []
    }

    issue_dt = parse_date_to_dt(out["issue"])
    maturity_dt = parse_date_to_dt(out["maturity"])
    settlement_dt = parse_date_to_dt(out["settlement"])

    if issue_dt and maturity_dt and issue_dt >= maturity_dt:
        out["issue"] = "N/A"

    if settlement_dt and maturity_dt and settlement_dt > maturity_dt:
        out["maturity"] = "N/A"

    if source == "ai":
        inferred_freq = infer_frequency_from_text(raw_text)
        inferred_rate_type = infer_rate_type_from_text(raw_text)

        if out["coupon_frequency"] != "N/A" and inferred_freq == "N/A":
            out["coupon_frequency"] = "N/A"

        if out["rate_type"] != "N/A" and inferred_rate_type == "N/A":
            out["rate_type"] = "N/A"

        if out["coupon_frequency"] == "N/A" and inferred_freq != "N/A":
            out["coupon_frequency"] = inferred_freq

        if out["rate_type"] == "N/A" and inferred_rate_type != "N/A":
            out["rate_type"] = inferred_rate_type

        coupon_val = percent_to_float(out["coupon"])
        price_val = percent_to_float(out["price"])

        if coupon_val is not None and price_val is not None:
            if abs(coupon_val - price_val) < 0.0001:
                out["coupon"] = "N/A"

        if coupon_val is not None and coupon_val > 25:
            out["coupon"] = "N/A"

        raw_upper = raw_text.upper()
        if out["rate_type"] == "Floating" and out["coupon"] != "N/A":
            if not any(x in raw_upper for x in ["COUPON", "INTEREST RATE", "CURRENT RATE", "3 MONTH EURIBOR", "3M EURIBOR", "FRN"]):
                out["coupon"] = "N/A"

        if out["maturity"] != "N/A" and out["settlement"] != "N/A":
            mdt = parse_date_to_dt(out["maturity"])
            sdt = parse_date_to_dt(out["settlement"])
            if mdt and sdt and (mdt - sdt).days < 30:
                out["maturity"] = "N/A"

    if (
        out["issue"] != "N/A"
        and out["maturity"] != "N/A"
        and out["coupon_frequency"] != "N/A"
    ):
        out["coupon_dates"] = generate_coupon_dates(
            out["issue"],
            out["maturity"],
            out["coupon_frequency"]
        )

    return out


def apply_locked_fields(new_data: dict, locked_data: dict, locked_fields: list):
    result = dict(new_data)
    for field in locked_fields:
        if field in locked_data:
            result[field] = locked_data[field]

    if (
        result.get("issue", "N/A") != "N/A"
        and result.get("maturity", "N/A") != "N/A"
        and result.get("coupon_frequency", "N/A") != "N/A"
    ):
        result["coupon_dates"] = generate_coupon_dates(
            result["issue"],
            result["maturity"],
            result["coupon_frequency"]
        )
    else:
        result["coupon_dates"] = []

    return result


def make_profile_payload(profile_name: str, locked_fields: list, locked_snapshot: dict) -> bytes:
    payload = {
        "profile_name": profile_name,
        "locked_fields": locked_fields,
        "locked_snapshot": locked_snapshot
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def load_profile_payload(file_obj):
    raw = file_obj.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    data = json.loads(raw)

    profile_name = normalize_text_field(data.get("profile_name", DEFAULT_PROFILE_NAME))
    locked_fields = data.get("locked_fields", [])
    locked_snapshot = data.get("locked_snapshot", {})

    valid_fields = [f for f in locked_fields if f in ALL_LOCKABLE_FIELDS]
    valid_snapshot = {k: v for k, v in locked_snapshot.items() if k in ALL_LOCKABLE_FIELDS}

    return profile_name, valid_fields, valid_snapshot


def dict_to_json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def dict_to_csv_bytes(data: dict) -> bytes:
    rows = [["field", "value"]]
    for key, value in data.items():
        if isinstance(value, list):
            rows.append([key, " | ".join(value)])
        else:
            rows.append([key, value])

    csv_str = ""
    for row in rows:
        escaped = []
        for item in row:
            s = str(item).replace('"', '""')
            escaped.append(f'"{s}"')
        csv_str += ",".join(escaped) + "\n"

    return csv_str.encode("utf-8")


# =========================
# PROFILE HELPERS
# =========================
def ensure_default_profile():
    if "profiles" not in st.session_state:
        st.session_state["profiles"] = {
            DEFAULT_PROFILE_NAME: {
                "locked_fields": [],
                "locked_snapshot": {}
            }
        }
    if "active_profile" not in st.session_state:
        st.session_state["active_profile"] = DEFAULT_PROFILE_NAME


def get_active_profile_data():
    ensure_default_profile()
    active = st.session_state["active_profile"]
    return st.session_state["profiles"].get(active, {"locked_fields": [], "locked_snapshot": {}})


def sync_profile_to_legacy_state():
    profile = get_active_profile_data()
    st.session_state["locked_fields"] = profile.get("locked_fields", [])
    st.session_state["locked_snapshot"] = profile.get("locked_snapshot", {})


def save_current_profile(profile_name: str, locked_fields: list, locked_snapshot: dict):
    ensure_default_profile()
    st.session_state["profiles"][profile_name] = {
        "locked_fields": list(locked_fields),
        "locked_snapshot": dict(locked_snapshot)
    }
    st.session_state["active_profile"] = profile_name
    sync_profile_to_legacy_state()


def delete_profile(profile_name: str):
    ensure_default_profile()
    if profile_name == DEFAULT_PROFILE_NAME:
        st.session_state["profiles"][DEFAULT_PROFILE_NAME] = {
            "locked_fields": [],
            "locked_snapshot": {}
        }
        st.session_state["active_profile"] = DEFAULT_PROFILE_NAME
    else:
        if profile_name in st.session_state["profiles"]:
            del st.session_state["profiles"][profile_name]
        st.session_state["active_profile"] = DEFAULT_PROFILE_NAME
    sync_profile_to_legacy_state()


def detect_bank_from_text(text: str) -> str:
    t = text.upper()
    for bank_name, keywords in BANK_KEYWORDS.items():
        if any(k in t for k in keywords):
            return bank_name
    return "N/A"


def find_matching_profile_for_bank(bank_name: str, profile_names: list) -> str:
    if bank_name == "N/A":
        return "N/A"

    bank_lower = bank_name.lower()
    for pname in profile_names:
        if pname.lower() == bank_lower:
            return pname

    for pname in profile_names:
        if bank_lower in pname.lower() or pname.lower() in bank_lower:
            return pname

    return "N/A"


def auto_apply_profile_if_possible(raw_text: str, enabled: bool):
    detected_bank = detect_bank_from_text(raw_text)
    suggested_profile = find_matching_profile_for_bank(detected_bank, list(st.session_state["profiles"].keys()))

    st.session_state["detected_bank"] = detected_bank
    st.session_state["suggested_profile"] = suggested_profile

    if enabled and suggested_profile != "N/A":
        st.session_state["active_profile"] = suggested_profile
        sync_profile_to_legacy_state()
        return suggested_profile

    return "N/A"


# =========================
# SECRET / API KEY
# =========================
def get_openai_api_key():
    try:
        secret_key = st.secrets["OPENAI_API_KEY"]
        if str(secret_key).strip():
            return str(secret_key).strip(), "secret"
    except Exception:
        pass
    return "", "manual"


# =========================
# PDF TO IMAGES
# =========================
def pdf_to_images(pdf_file, max_pages=2, dpi=220):
    pdf_bytes = pdf_file.read()
    images = convert_from_bytes(
        pdf_bytes,
        dpi=dpi,
        poppler_path=POPPLER_PATH
    )
    return images[:max_pages]


def pil_image_to_base64(img: Image.Image, max_width=1800, jpeg_quality=85) -> str:
    img = img.copy()

    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size)

    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=jpeg_quality)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# =========================
# OPENAI VISION EXTRACTION
# =========================
def build_vision_prompt():
    return """
You are extracting bond trade data from scanned bank PDF contract notes / settlement advices.

Return ONLY valid JSON.
Do not include markdown.
Do not include explanations.

Return exactly these fields:
{
  "name": "...",
  "issuer": "...",
  "isin": "...",
  "coupon": "...",
  "coupon_frequency": "...",
  "issue": "...",
  "maturity": "...",
  "settlement": "...",
  "price": "...",
  "nominal": "...",
  "currency": "...",
  "day_count": "...",
  "rate_type": "...",
  "accrued_interest": "...",
  "interest_days": "..."
}

Critical rules:
- Use DD/MM/YYYY for dates.
- If not found, return "N/A".
- ISIN must be the actual security ISIN only.
- coupon must be the bond coupon rate only. Do NOT use price, yield, accrued interest, spread, or any other percentage.
- price must be the trade price %. Do NOT use coupon as price.
- nominal must be the traded nominal amount and include currency if visible.
- settlement must be the settlement date / value date only.
- issue and maturity must be the bond dates only, not trade dates.
- coupon_frequency must be exactly one of:
  "Annual", "Semi-Annual", "Quarterly", "Monthly", "N/A"
- rate_type must be exactly one of:
  "Fixed", "Floating", "N/A"

Very important extraction discipline:
- Only output "Floating" if the page explicitly says "Floating Rate", "FRN", "3 month Euribor", or equivalent.
- Only output "Quarterly" if the page explicitly says quarterly, FRN with 3-month reset, 3M Euribor, or a clear /4 notation.
- If you are not sure about coupon_frequency or rate_type, return "N/A".
- Only output coupon if you can see the security coupon clearly in the security description or clearly labeled rate field.
- If multiple percentages exist, prefer the one tied to the security description, not the trade price.
- If a compact pattern like "2024-30.08.2027" appears in the security description, that usually means issue date 30/08/2024 and maturity 30/08/2027.
- If "Interest xx days" exists, return that number in interest_days.
- If accrued interest amount exists, return it in accrued_interest.
- Clean issuer and name as much as possible.
"""


def openai_extract_from_images(images, api_key: str, model: str):
    client = OpenAI(api_key=api_key)

    content = [{"type": "text", "text": build_vision_prompt()}]

    for img in images:
        b64 = pil_image_to_base64(img)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0
    )

    raw = response.choices[0].message.content.strip()

    if raw.startswith("```"):
        raw = re.sub(r"^```json", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"^```", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Το μοντέλο δεν επέστρεψε JSON object.")

    return parsed, raw


# =========================
# SESSION STATE
# =========================
def init_session_state():
    defaults = {
        "parsed_data": None,
        "raw_json_text": "",
        "debug_data": None,
        "page_count": 0,
        "rendered_images": [],
        "locked_fields": [],
        "locked_snapshot": {},
        "detected_bank": "N/A",
        "suggested_profile": "N/A",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    ensure_default_profile()
    sync_profile_to_legacy_state()


init_session_state()

# =========================
# UI
# =========================
st.title("Bond Analyzer v8.6 - Vision PDF")

secret_api_key, api_source = get_openai_api_key()

with st.sidebar:
    st.header("Settings")

    model_name = st.selectbox(
        "Vision model",
        ["gpt-4.1-mini", "gpt-4.1", "gpt-4o"],
        index=0
    )

    max_pages = st.selectbox("Pages to analyze", [1, 2, 3], index=1)
    dpi = st.selectbox("Render DPI", [180, 220, 280, 320], index=1)
    show_images = st.checkbox("Show rendered pages", value=True)
    show_debug = st.checkbox("Show debug info", value=True)
    auto_apply_profile = st.checkbox("Auto-apply suggested profile", value=True)

    st.markdown("---")
    if api_source == "secret":
        st.success("OpenAI API key loaded from secrets.")
    else:
        st.warning("No OpenAI API key found in secrets. Enter it manually below.")

    st.markdown("v8.6: auto-detect bank and suggest/apply profile.")

st.subheader("Inputs")

manual_api_key = ""
if api_source != "secret":
    manual_api_key = st.text_input("OpenAI API Key", type="password")

uploaded_pdf = st.file_uploader("Upload scanned bond PDF", type=["pdf"])

final_api_key = secret_api_key if secret_api_key else manual_api_key.strip()

# =========================
# PROFILE AREA
# =========================
st.markdown("---")
st.subheader("Profiles")

profile_names = list(st.session_state["profiles"].keys())
active_profile = st.selectbox(
    "Active profile",
    profile_names,
    index=profile_names.index(st.session_state["active_profile"]) if st.session_state["active_profile"] in profile_names else 0
)

if active_profile != st.session_state["active_profile"]:
    st.session_state["active_profile"] = active_profile
    sync_profile_to_legacy_state()

profile_name_input = st.text_input("Profile name", value=st.session_state["active_profile"])

profile_col1, profile_col2, profile_col3 = st.columns(3)

with profile_col1:
    if st.button("Save current as profile"):
        pname = normalize_text_field(profile_name_input)
        save_current_profile(
            pname,
            st.session_state["locked_fields"],
            st.session_state["locked_snapshot"]
        )
        st.success(f"Αποθηκεύτηκε profile: {pname}")

with profile_col2:
    if st.button("Delete active profile"):
        delete_profile(st.session_state["active_profile"])
        st.success("Το active profile διαγράφηκε ή μηδενίστηκε.")

with profile_col3:
    current_profile_json = make_profile_payload(
        st.session_state["active_profile"],
        st.session_state["locked_fields"],
        st.session_state["locked_snapshot"]
    )
    st.download_button(
        label="Download active profile",
        data=current_profile_json,
        file_name=f"{st.session_state['active_profile']}_profile.json",
        mime="application/json"
    )

uploaded_profile = st.file_uploader("Upload profile JSON", type=["json"], key="profile_json_upload")

if uploaded_profile is not None:
    try:
        pname, pfields, psnapshot = load_profile_payload(uploaded_profile)
        save_current_profile(pname, pfields, psnapshot)

        if st.session_state["parsed_data"] is not None:
            st.session_state["parsed_data"] = apply_locked_fields(
                st.session_state["parsed_data"],
                st.session_state["locked_snapshot"],
                st.session_state["locked_fields"]
            )

        st.success(f"Το profile φορτώθηκε: {pname}")
    except Exception as e:
        st.error(f"Σφάλμα φόρτωσης profile: {e}")

# =========================
# ANALYZE
# =========================
if st.button("Analyze PDF", type="primary"):
    if not final_api_key:
        st.error("Δεν βρέθηκε OpenAI API key. Βάλ' το στο secrets.toml ή στο πεδίο.")
    elif uploaded_pdf is None:
        st.error("Ανέβασε PDF.")
    else:
        try:
            with st.spinner("Rendering PDF pages..."):
                images = pdf_to_images(uploaded_pdf, max_pages=max_pages, dpi=dpi)
                st.session_state["rendered_images"] = images
                st.session_state["page_count"] = len(images)

            with st.spinner("Extracting bond data with vision..."):
                parsed, raw_json = openai_extract_from_images(
                    images=images,
                    api_key=final_api_key,
                    model=model_name
                )

            auto_profile_used = auto_apply_profile_if_possible(raw_json, auto_apply_profile)

            fixed = validate_and_fix_data(parsed, raw_text=raw_json, source="ai")

            if st.session_state["locked_fields"] and st.session_state["locked_snapshot"]:
                fixed = apply_locked_fields(
                    fixed,
                    st.session_state["locked_snapshot"],
                    st.session_state["locked_fields"]
                )

            st.session_state["parsed_data"] = fixed
            st.session_state["raw_json_text"] = raw_json
            st.session_state["debug_data"] = {
                "model": model_name,
                "pages_analyzed": len(images),
                "dpi": dpi,
                "api_source": api_source if secret_api_key else "manual",
                "raw_parsed": parsed,
                "active_profile": st.session_state["active_profile"],
                "detected_bank": st.session_state["detected_bank"],
                "suggested_profile": st.session_state["suggested_profile"],
                "auto_profile_used": auto_profile_used,
                "locked_fields_active": st.session_state["locked_fields"]
            }

            st.success("Done!")

        except Exception as e:
            st.error(f"Σφάλμα: {e}")

if st.session_state["detected_bank"] != "N/A":
    st.info(
        f"Detected bank: {st.session_state['detected_bank']} | "
        f"Suggested profile: {st.session_state['suggested_profile']}"
    )

if show_images and st.session_state["rendered_images"]:
    st.markdown("---")
    st.subheader("Rendered PDF pages")
    images = st.session_state["rendered_images"]
    cols = st.columns(min(len(images), 2))
    for idx, img in enumerate(images):
        with cols[idx % len(cols)]:
            st.image(img, caption=f"Page {idx + 1}", use_container_width=True)

# =========================
# DATA AREA
# =========================
if st.session_state["parsed_data"] is not None:
    data = st.session_state["parsed_data"]

    st.markdown("---")
    st.subheader("Locked fields")

    current_locked = st.multiselect(
        "Choose fields to lock",
        ALL_LOCKABLE_FIELDS,
        default=st.session_state["locked_fields"]
    )

    lock_col1, lock_col2 = st.columns(2)

    with lock_col1:
        if st.button("Save locked fields to active profile"):
            st.session_state["locked_fields"] = current_locked
            st.session_state["locked_snapshot"] = {
                field: data.get(field, "N/A")
                for field in current_locked
            }
            save_current_profile(
                st.session_state["active_profile"],
                st.session_state["locked_fields"],
                st.session_state["locked_snapshot"]
            )
            st.success("Τα locked fields αποθηκεύτηκαν στο active profile.")

    with lock_col2:
        if st.button("Clear locks of active profile"):
            st.session_state["locked_fields"] = []
            st.session_state["locked_snapshot"] = {}
            save_current_profile(
                st.session_state["active_profile"],
                [],
                {}
            )
            st.success("Καθαρίστηκαν τα locks του active profile.")

    if st.session_state["locked_fields"]:
        st.info("Locked fields: " + ", ".join(st.session_state["locked_fields"]))

    st.markdown("---")
    st.subheader("Manual correction panel")

    with st.form("manual_edit_form"):
        col_a, col_b = st.columns(2)

        with col_a:
            edit_name = st.text_input("Name", value=data.get("name", "N/A"))
            edit_issuer = st.text_input("Issuer", value=data.get("issuer", "N/A"))
            edit_isin = st.text_input("ISIN", value=data.get("isin", "N/A"))
            edit_coupon = st.text_input("Coupon", value=data.get("coupon", "N/A"))
            edit_coupon_frequency = st.selectbox(
                "Coupon frequency",
                ALLOWED_FREQUENCIES,
                index=ALLOWED_FREQUENCIES.index(
                    data.get("coupon_frequency", "N/A")
                    if data.get("coupon_frequency", "N/A") in ALLOWED_FREQUENCIES
                    else "N/A"
                )
            )
            edit_currency = st.text_input("Currency", value=data.get("currency", "N/A"))
            edit_day_count = st.text_input("Day count", value=data.get("day_count", "N/A"))
            edit_rate_type = st.selectbox(
                "Rate type",
                ALLOWED_RATE_TYPES,
                index=ALLOWED_RATE_TYPES.index(
                    data.get("rate_type", "N/A")
                    if data.get("rate_type", "N/A") in ALLOWED_RATE_TYPES
                    else "N/A"
                )
            )

        with col_b:
            edit_issue = st.text_input("Issue date (dd/mm/yyyy)", value=data.get("issue", "N/A"))
            edit_maturity = st.text_input("Maturity date (dd/mm/yyyy)", value=data.get("maturity", "N/A"))
            edit_settlement = st.text_input("Settlement date (dd/mm/yyyy)", value=data.get("settlement", "N/A"))
            edit_price = st.text_input("Price", value=data.get("price", "N/A"))
            edit_nominal = st.text_input("Nominal", value=data.get("nominal", "N/A"))
            edit_accrued_interest = st.text_input("Accrued interest", value=data.get("accrued_interest", "N/A"))
            edit_interest_days = st.text_input("Interest days", value=data.get("interest_days", "N/A"))

        submitted = st.form_submit_button("Apply manual corrections")

    if submitted:
        corrected = {
            "name": edit_name,
            "issuer": edit_issuer,
            "isin": edit_isin,
            "coupon": edit_coupon,
            "coupon_frequency": edit_coupon_frequency,
            "issue": edit_issue,
            "maturity": edit_maturity,
            "settlement": edit_settlement,
            "price": edit_price,
            "nominal": edit_nominal,
            "currency": edit_currency,
            "day_count": edit_day_count,
            "rate_type": edit_rate_type,
            "accrued_interest": edit_accrued_interest,
            "interest_days": edit_interest_days
        }

        new_data = validate_and_fix_data(
            corrected,
            raw_text=st.session_state["raw_json_text"],
            source="manual"
        )

        if st.session_state["locked_fields"]:
            new_data = apply_locked_fields(
                new_data,
                st.session_state["locked_snapshot"],
                st.session_state["locked_fields"]
            )

        st.session_state["parsed_data"] = new_data
        data = st.session_state["parsed_data"]

        if st.session_state["locked_fields"]:
            st.session_state["locked_snapshot"] = {
                field: data.get(field, "N/A")
                for field in st.session_state["locked_fields"]
            }
            save_current_profile(
                st.session_state["active_profile"],
                st.session_state["locked_fields"],
                st.session_state["locked_snapshot"]
            )

        st.success("Οι διορθώσεις εφαρμόστηκαν.")

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Extracted JSON")
        st.json(data)

    with col2:
        st.subheader("Quick view")
        st.write(f"**Name:** {data.get('name', 'N/A')}")
        st.write(f"**Issuer:** {data.get('issuer', 'N/A')}")
        st.write(f"**ISIN:** {data.get('isin', 'N/A')}")
        st.write(f"**Coupon:** {data.get('coupon', 'N/A')}")
        st.write(f"**Coupon frequency:** {data.get('coupon_frequency', 'N/A')}")
        st.write(f"**Issue:** {data.get('issue', 'N/A')}")
        st.write(f"**Maturity:** {data.get('maturity', 'N/A')}")
        st.write(f"**Settlement:** {data.get('settlement', 'N/A')}")
        st.write(f"**Price:** {data.get('price', 'N/A')}")
        st.write(f"**Nominal:** {data.get('nominal', 'N/A')}")
        st.write(f"**Currency:** {data.get('currency', 'N/A')}")
        st.write(f"**Day count:** {data.get('day_count', 'N/A')}")
        st.write(f"**Rate type:** {data.get('rate_type', 'N/A')}")
        st.write(f"**Accrued interest:** {data.get('accrued_interest', 'N/A')}")
        st.write(f"**Interest days:** {data.get('interest_days', 'N/A')}")

        if data.get("coupon_dates"):
            st.write("**Coupon dates:**")
            for d in data["coupon_dates"]:
                st.write(f"- {d}")

    st.markdown("---")
    st.subheader("Download results")

    json_bytes = dict_to_json_bytes(data)
    csv_bytes = dict_to_csv_bytes(data)

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        st.download_button(
            label="Download JSON",
            data=json_bytes,
            file_name="bond_result.json",
            mime="application/json"
        )
    with dcol2:
        st.download_button(
            label="Download CSV",
            data=csv_bytes,
            file_name="bond_result.csv",
            mime="text/csv"
        )

    if show_debug:
        st.markdown("---")
        st.subheader("Debug info")
        st.json({
            "page_count": st.session_state["page_count"],
            "debug": st.session_state["debug_data"],
            "raw_json_text": st.session_state["raw_json_text"],
            "active_profile": st.session_state["active_profile"],
            "detected_bank": st.session_state["detected_bank"],
            "suggested_profile": st.session_state["suggested_profile"],
            "profiles": st.session_state["profiles"]
        })