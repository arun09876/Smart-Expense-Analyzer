# Smart Expense Analyzer

> People can't keep track of where their UPI and card money goes. Upload a bank statement to get categorised spending, monthly charts, top merchants, and your recurring payments. The file itself is never stored.

**Stack:** Django · pandas · Matplotlib · pdfplumber · Celery · Redis · Claude API (LLM) · Bootstrap 5

## Features

- **Upload a CSV or PDF statement** (max 5 MB). The parser finds the header row even with bank preambles, and maps many layouts (HDFC, SBI, ICICI-style debit/credit columns, a signed `Amount`, or `Amount` + `Dr/Cr`) onto one schema.
- **pandas cleaning**: parses `₹`, commas, and `(123.00)` negatives; handles dd/mm dates; drops opening/closing balance rows; dedupes; extracts the merchant from narrations like `UPI/DR/4123…/SWIGGY/YESB/swiggy@yesbank`.
- **Rule-based categories** (Swiggy → Food & Dining, APSPDCL → Bills, Zerodha → Investments, …) using word-boundary regexes, so "rent" doesn't match "current".
- **LLM fallback**: merchants the rules don't recognise (e.g. *Sri Lakshmi Tiffins*, *Venkateswara Traders*) go to Claude in batches of 40. **Structured outputs** (a JSON schema with an enum of categories) guarantee valid JSON, and each merchant's answer is cached in Redis for 30 days, so a merchant is only ever sent once.
- **Celery background processing**, with a "Processing…" page that polls the status (`Reading → Categorising → Asking AI → Finding recurring`).
- **Dashboard**: stacked monthly spend by category, a category donut, top-10 merchants (all Matplotlib), plus totals.
- **Recurring payment detection**: finds the same merchant with a similar amount (±10%) at regular gaps (weekly, monthly, or quarterly), and predicts the next due date. It catches Netflix, rent, SIPs, EMIs, and recharges.
- **Fix categories**: change one transaction, or all transactions from that merchant. The source is shown as rule / AI / you.
- **Privacy — files are processed and deleted, never stored:**
  - `FILE_UPLOAD_HANDLERS` is memory-only, so Django never spools uploads to a temp file.
  - The bytes go to the Celery task (base64 in the task message), are parsed, and are dropped.
  - Only the extracted transactions are saved. Deleting a statement removes them.

## Run locally

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # add ANTHROPIC_API_KEY; clear REDIS_URL to run tasks inline
python manage.py migrate
python manage.py runserver
```

Sign up, then upload [sample_data/sample_statement.csv](sample_data/sample_statement.csv), a generated three-month HDFC-style statement. To regenerate it, run `python sample_data/generate_sample.py`.

With Redis available (to see real background processing):

```bash
celery -A config worker -l info --pool=solo   # Windows needs --pool=solo
```

Or use `docker compose up --build`.

Without an API key, unknown merchants fall back to "Other" and everything else still works.

## Tests

```bash
python manage.py test
```

The tests cover four CSV layouts, the PDF text fallback, merchant extraction, word-boundary rules, recurring detection, LLM batching/caching/failure handling (mocked client), the full upload → task → dashboard → charts flow, bad files, bulk recategorisation, and per-user isolation.

## Design notes

- **Rules first, LLM second:** about 90% of transactions are known merchants. The LLM only sees unique unknown merchants, and a Redis cache means it's paid for once per merchant, not once per transaction.
- **Structured outputs over prompt-and-pray:** the response is constrained by a JSON schema, so no regex parsing of model output is needed.
- **Per-user cache versioning:** charts and the summary are cached under `exp:{user}:v{n}:…`. Any change bumps `n` instead of hunting down individual keys.
