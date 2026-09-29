"""Bank statement parsing: CSV / PDF bytes -> clean pandas DataFrame.

Output columns: date (datetime64), description (str), amount (float > 0),
direction ("debit" | "credit"), merchant (str).
"""
import io
import re

import pandas as pd

DATE_COLS = ["txn date", "transaction date", "value date", "date", "posting date", "tran date"]
DESC_COLS = ["description", "narration", "particulars", "remarks", "details", "transaction details", "transaction remarks"]
DEBIT_COLS = ["debit", "withdrawal", "withdrawal amt", "withdrawal amount", "debit amount", "dr", "withdrawals"]
CREDIT_COLS = ["credit", "deposit", "deposit amt", "deposit amount", "credit amount", "cr", "deposits"]
AMOUNT_COLS = ["amount", "transaction amount", "amount (inr)", "amt"]
TYPE_COLS = ["type", "dr/cr", "cr/dr", "debit/credit", "transaction type"]

SKIP_DESC = re.compile(r"opening balance|closing balance|balance b/f|balance c/f|^total", re.I)


class StatementParseError(ValueError):
    pass


def _norm(col) -> str:
    return re.sub(r"[^a-z/() ]", "", str(col).lower().replace("\n", " ")).strip()


def _find(columns, candidates):
    normalized = {_norm(c): c for c in columns}
    for cand in candidates:
        if cand in normalized:
            return normalized[cand]
    for cand in candidates:  # fuzzy: header contains the candidate word
        for norm, original in normalized.items():
            if cand in norm and len(cand) > 2:
                return original
    return None


def to_number(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace(r"[₹,\s]|INR|Rs\.?", "", regex=True)
        .str.replace(r"\((.*)\)", r"-\1", regex=True)  # (123.00) -> -123.00
        .str.replace(r"(?i)(cr|dr)$", "", regex=True)
        .replace({"": None, "-": None, "nan": None, "None": None})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Map an arbitrary bank layout to the standard columns and clean it."""
    raw = raw.dropna(how="all").copy()
    raw.columns = [str(c).strip() for c in raw.columns]

    date_col = _find(raw.columns, DATE_COLS)
    desc_col = _find(raw.columns, DESC_COLS)
    if not date_col or not desc_col:
        raise StatementParseError(
            f"Couldn't find date/description columns. Found: {', '.join(map(str, raw.columns))}"
        )

    df = pd.DataFrame({
        "date": pd.to_datetime(raw[date_col].astype(str).str.strip(), errors="coerce", dayfirst=True, format="mixed"),
        "description": raw[desc_col].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip(),
    })

    debit_col, credit_col = _find(raw.columns, DEBIT_COLS), _find(raw.columns, CREDIT_COLS)
    amount_col, type_col = _find(raw.columns, AMOUNT_COLS), _find(raw.columns, TYPE_COLS)

    if debit_col and credit_col:
        debit, credit = to_number(raw[debit_col]).fillna(0), to_number(raw[credit_col]).fillna(0)
        df["amount"] = debit.where(debit > 0, credit).abs()
        df["direction"] = ["debit" if d > 0 else "credit" for d in debit]
    elif amount_col:
        amount = to_number(raw[amount_col])
        if type_col:
            is_credit = raw[type_col].astype(str).str.strip().str.lower().str.startswith("cr")
        else:  # signed amounts: negative = money out
            is_credit = amount > 0
        df["amount"] = amount.abs()
        df["direction"] = ["credit" if c else "debit" for c in is_credit]
    else:
        raise StatementParseError("Couldn't find debit/credit or amount columns.")

    df = df.dropna(subset=["date", "amount"])
    df = df[(df["amount"] > 0) & (df["description"] != "") & ~df["description"].str.contains(SKIP_DESC)]
    df = df.drop_duplicates()
    if df.empty:
        raise StatementParseError("No transactions found in the file.")
    df["merchant"] = df["description"].map(extract_merchant)
    return df.sort_values("date").reset_index(drop=True)


def parse_csv(data: bytes) -> pd.DataFrame:
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    # Bank CSVs often have account info above the header; start at the first header-like line.
    start = next((i for i, line in enumerate(lines[:40])
                  if re.search(r"date", line, re.I) and re.search(r"narration|description|particulars|details|remarks", line, re.I)), 0)
    raw = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str, skip_blank_lines=True, on_bad_lines="skip")
    return normalize(raw)


def parse_pdf(data: bytes) -> pd.DataFrame:
    import pdfplumber

    rows, header = [], None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    cells = [(c or "").strip() for c in row]
                    joined = " ".join(cells).lower()
                    if header is None and "date" in joined and any(k in joined for k in ("narration", "description", "particulars", "details", "remarks")):
                        header = cells
                        continue
                    if header and len(cells) == len(header) and not " ".join(cells).lower().startswith("date"):
                        rows.append(cells)
    if header and rows:
        return normalize(pd.DataFrame(rows, columns=header))
    return _parse_pdf_text(data)


LINE_RE = re.compile(
    r"^(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2} \w{3} \d{2,4})\s+(?P<desc>.+?)\s+"
    r"(?P<amount>[\d,]+\.\d{2})\s*(?P<type>Cr|Dr|CR|DR)?\s+(?:[\d,]+\.\d{2})?\s*(?:Cr|Dr|CR|DR)?$"
)


def _parse_pdf_text(data: bytes) -> pd.DataFrame:
    """Fallback for PDFs without ruling lines: regex over text lines."""
    import pdfplumber

    records = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                m = LINE_RE.match(line.strip())
                if m:
                    records.append({"date": m["date"], "description": m["desc"], "amount": m["amount"],
                                    "type": (m["type"] or "dr")})
    if not records:
        raise StatementParseError("Couldn't read transactions from this PDF. Try the CSV export from net banking.")
    return normalize(pd.DataFrame(records))


def parse_statement(data: bytes, file_type: str) -> pd.DataFrame:
    if file_type == "csv":
        return parse_csv(data)
    if file_type == "pdf":
        return parse_pdf(data)
    raise StatementParseError("Only CSV and PDF files are supported.")


# --- merchant extraction ----------------------------------------------------

NOISE = re.compile(
    r"^(upi|imps|neft|rtgs|pos|ecom|nach|ach|mmt|bil|bbps|onl|inb|nwd|awd|dr|cr|d|c|p2m|p2a|payment|txn|ref|paid|"
    r"to|from|via|netbank|yesb|hdfc|icic|sbin|utib|kkbk|punb|axis|ibkl|barb|ybl|okaxis|oksbi|okhdfcbank|okicici|"
    r"paytm|apl|axl|ibl)$",
    re.I,
)
IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$", re.I)


def _is_noise_word(word: str) -> bool:
    return bool(NOISE.match(word) or IFSC.match(word) or re.fullmatch(r"[\dX]+", word, re.I) or len(word) < 2)


def extract_merchant(description: str) -> str:
    """'UPI/DR/412345678901/SWIGGY/YESB/swiggy@yesbank/...' -> 'Swiggy'."""
    text = description.strip()
    vpa = re.search(r"([a-z0-9.\-_]+)@[a-z]+", text, re.I)
    tokens = [t.strip() for t in re.split(r"[/|:*\-]+", text) if t.strip()]
    for token in tokens:
        if "@" in token:
            continue
        clean = re.sub(r"[^A-Za-z0-9 &.']", " ", token).strip()
        words = [w for w in clean.split() if not _is_noise_word(w)]
        if len(re.sub(r"[^A-Za-z]", "", "".join(words))) >= 3:
            return " ".join(words)[:60].title()
    if vpa:
        return vpa.group(1).split(".")[0][:60].title()
    return text[:60].title() or "Unknown"
