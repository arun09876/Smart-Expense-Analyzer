"""pandas analytics + Matplotlib charts over a user's transactions."""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import pandas as pd  # noqa: E402

from .models import Transaction  # noqa: E402

PALETTE = ["#4361ee", "#f72585", "#4cc9f0", "#7209b7", "#fb8500", "#2a9d8f", "#e63946",
           "#8ac926", "#ffb703", "#6c757d", "#3a0ca3", "#90be6d"]
SPEND_EXCLUDE = {"Transfers", "Investments", "Income"}


def detect_recurring(df: pd.DataFrame) -> pd.DataFrame:
    """Find subscriptions/EMIs/rent: same merchant, similar amount, regular gaps.

    df needs: date (datetime), merchant, amount, direction.
    Returns one row per recurring merchant with frequency, typical amount and next due date.
    """
    debits = df[df["direction"] == "debit"].sort_values("date")
    if "category" in debits.columns:  # ATM withdrawals repeat but aren't subscriptions
        debits = debits[debits["category"] != "Cash Withdrawal"]
    found = []
    for merchant, g in debits.groupby("merchant"):
        if len(g) < 3:
            continue
        median_amt = g["amount"].median()
        similar = g[(g["amount"] - median_amt).abs() <= max(0.1 * median_amt, 1)]
        if len(similar) < 3:
            continue
        gaps = similar["date"].diff().dt.days.dropna()
        gap = gaps.median()
        if 25 <= gap <= 35 and gaps.between(20, 40).mean() >= 0.75:
            frequency = "Monthly"
        elif 6 <= gap <= 8 and gaps.between(5, 9).mean() >= 0.75:
            frequency = "Weekly"
        elif 85 <= gap <= 95:
            frequency = "Quarterly"
        else:
            continue
        found.append({
            "merchant": merchant,
            "frequency": frequency,
            "amount": round(float(median_amt), 2),
            "count": len(similar),
            "last_date": similar["date"].max().date(),
            "next_expected": (similar["date"].max() + pd.Timedelta(days=int(gap))).date(),
        })
    return pd.DataFrame(found, columns=["merchant", "frequency", "amount", "count", "last_date", "next_expected"])


def user_frame(user) -> pd.DataFrame:
    rows = Transaction.objects.filter(statement__user=user, statement__status="done").values(
        "id", "date", "merchant", "amount", "direction", "category", "is_recurring"
    )
    df = pd.DataFrame.from_records(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    df["month"] = df["date"].dt.to_period("M")
    return df


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    spend = df[(df["direction"] == "debit") & ~df["category"].isin(SPEND_EXCLUDE)]
    income = df[df["direction"] == "credit"]
    months = max(df["month"].nunique(), 1)
    by_cat = spend.groupby("category")["amount"].sum().sort_values(ascending=False)
    top = (spend.groupby("merchant")["amount"].agg(["sum", "count"])
           .sort_values("sum", ascending=False).head(10).reset_index())
    monthly = spend.groupby("month")["amount"].sum()
    return {
        "total_spend": spend["amount"].sum(),
        "total_income": income["amount"].sum(),
        "avg_monthly_spend": spend["amount"].sum() / months,
        "months": months,
        "txn_count": len(df),
        "top_category": by_cat.index[0] if not by_cat.empty else None,
        "by_category": [{"category": c, "amount": a, "pct": a / by_cat.sum() * 100} for c, a in by_cat.items()],
        "top_merchants": top.to_dict("records"),
        "monthly": [{"month": str(m), "amount": a} for m, a in monthly.items()],
        "recurring": detect_recurring(df).to_dict("records"),
    }


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


def _empty(msg="Upload a statement to see charts"):
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.text(0.5, 0.5, msg, ha="center", va="center", color="#6c757d")
    ax.axis("off")
    return _png(fig)


def _rupees(ax, axis="y"):
    fmt = mticker.FuncFormatter(lambda v, _: f"₹{v/1000:.0f}k" if v >= 1000 else f"₹{v:.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def monthly_chart(df: pd.DataFrame) -> bytes:
    spend = df[(df["direction"] == "debit") & ~df["category"].isin(SPEND_EXCLUDE)] if not df.empty else df
    if spend.empty:
        return _empty()
    pivot = spend.pivot_table(index="month", columns="category", values="amount", aggfunc="sum", fill_value=0)
    pivot = pivot[pivot.sum().sort_values(ascending=False).index]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    bottom = None
    for i, cat in enumerate(pivot.columns):
        ax.bar(pivot.index.astype(str), pivot[cat], bottom=bottom, label=cat, color=PALETTE[i % len(PALETTE)])
        bottom = pivot[cat] if bottom is None else bottom + pivot[cat]
    ax.set_title("Monthly spending by category")
    _rupees(ax)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8, ncol=2, bbox_to_anchor=(1.02, 1), loc="upper left", frameon=False)
    return _png(fig)


def category_chart(df: pd.DataFrame) -> bytes:
    spend = df[(df["direction"] == "debit") & ~df["category"].isin(SPEND_EXCLUDE)] if not df.empty else df
    if spend.empty:
        return _empty()
    by_cat = spend.groupby("category")["amount"].sum().sort_values(ascending=False)
    if len(by_cat) > 8:
        by_cat = pd.concat([by_cat.head(7), pd.Series({"Other (grouped)": by_cat.iloc[7:].sum()})])
    fig, ax = plt.subplots(figsize=(5.5, 4.2))
    ax.pie(by_cat, labels=by_cat.index, colors=PALETTE[:len(by_cat)], autopct="%1.0f%%", pctdistance=0.8,
           startangle=90, wedgeprops={"width": 0.4, "edgecolor": "white"}, textprops={"fontsize": 8})
    ax.set_title("Where the money went")
    return _png(fig)


def merchants_chart(df: pd.DataFrame) -> bytes:
    spend = df[(df["direction"] == "debit") & ~df["category"].isin(SPEND_EXCLUDE)] if not df.empty else df
    if spend.empty:
        return _empty()
    top = spend.groupby("merchant")["amount"].sum().sort_values().tail(10)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.barh(top.index, top.values, color="#4361ee")
    ax.set_title("Top 10 merchants")
    _rupees(ax, "x")
    ax.spines[["top", "right"]].set_visible(False)
    return _png(fig)
