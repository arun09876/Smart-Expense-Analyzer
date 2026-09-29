"""Generate a realistic 3-month HDFC-style statement CSV for demos.

    python sample_data/generate_sample.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(7)
START = date(2026, 6, 1)
DAYS = 92
rows = []


def upi(merchant, vpa, bank="YESB"):
    ref = random.randint(400000000000, 499999999999)
    return f"UPI-{merchant}-{vpa}-{bank}0000001-{ref}-Payment"


def add(d, narration, debit=0.0, credit=0.0):
    rows.append((d, narration, round(debit, 2), round(credit, 2)))


for i in range(DAYS):
    d = START + timedelta(days=i)
    if d.day == 1:
        add(d, "SALARY CREDIT ACME TECHNOLOGIES PVT LTD JUN-SEP", credit=85000)
        add(d, "NEFT DR-HDFC0001234-SURESH REDDY RENT-NETBANK", debit=15000)
    if d.day == 3:
        add(d, "ACH D- ZERODHA BROKING SIP-MF", debit=5000)
    if d.day == 5:
        add(d, upi("NETFLIX", "netflix@hdfcbank", "HDFC"), debit=649)
    if d.day == 7:
        add(d, upi("JIO PREPAID", "jiorecharge@paytm", "PYTM"), debit=349)
    if d.day == 10:
        add(d, "BBPS APSPDCL ELECTRICITY BILL", debit=random.randint(1100, 1900))
    if d.day == 12:
        add(d, "EMI 5543210 BAJAJ FINANCE LTD", debit=3250)
    if d.day == 15:
        add(d, upi("SPOTIFY", "spotify@icici", "ICIC"), debit=119)
    if d.weekday() == 6:
        add(d, upi("RATNADEEP SUPERMARKET", "ratnadeep@ybl"), debit=random.randint(900, 2600))
        add(d, upi("SRI LAKSHMI TIFFINS", "q123456789@ybl"), debit=random.choice([80, 120, 150]))
    if random.random() < 0.45:
        add(d, upi("SWIGGY", "swiggy@yesbank"), debit=random.randint(180, 650))
    if random.random() < 0.25:
        add(d, upi("ZOMATO", "zomato@kotak", "KKBK"), debit=random.randint(200, 700))
    if random.random() < 0.3:
        add(d, upi("UBER INDIA", "uber@axisbank", "UTIB"), debit=random.randint(90, 420))
    if random.random() < 0.15:
        add(d, upi("RAPIDO", "rapido@ybl"), debit=random.randint(40, 160))
    if random.random() < 0.12:
        add(d, f"POS 4591XXXXXXXX1234 AMAZON PAY INDIA", debit=random.randint(300, 4200))
    if random.random() < 0.06:
        add(d, upi("FLIPKART", "flipkart@axisbank", "UTIB"), debit=random.randint(500, 3500))
    if random.random() < 0.1:
        add(d, upi("VENKATESWARA TRADERS", "vtraders@oksbi", "SBIN"), debit=random.randint(150, 900))
    if random.random() < 0.08:
        add(d, upi("RAMESH KUMAR", "9848012345@ybl"), debit=random.choice([500, 1000, 2000]))
    if random.random() < 0.06:
        add(d, upi("APOLLO PHARMACY", "apollopharmacy@icici", "ICIC"), debit=random.randint(120, 900))
    if random.random() < 0.05:
        add(d, "POS 4591XXXXXXXX1234 HPCL PETROL PUMP KAKINADA", debit=random.randint(500, 1500))
    if random.random() < 0.04:
        add(d, "NWD-4591XXXXXXXX1234-ATM VIJAYAWADA", debit=random.choice([2000, 5000]))
    if random.random() < 0.05:
        add(d, upi("KRISHNA MESS", "krishnamess@ibl", "IBKL"), debit=random.randint(90, 250))
    if random.random() < 0.03:
        add(d, upi("MOM", "8977001122@ybl"), credit=random.choice([1000, 2000]))

out = Path(__file__).with_name("sample_statement.csv")
balance = 42000.0
with out.open("w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["HDFC BANK LTD - Statement of account"])
    w.writerow(["Account No: XXXXXXXX4321", "Period: 01/06/2026 to 31/08/2026"])
    w.writerow([])
    w.writerow(["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"])
    for d, narration, debit, credit in rows:
        balance += credit - debit
        w.writerow([d.strftime("%d/%m/%y"), narration, f"{random.randint(10**11, 10**12 - 1)}",
                    d.strftime("%d/%m/%y"), f"{debit:.2f}" if debit else "", f"{credit:.2f}" if credit else "",
                    f"{balance:.2f}"])
print(f"Wrote {len(rows)} transactions to {out}")
