"""Rule-based categories first; Claude for whatever the rules can't recognise."""
import hashlib
import json
import logging
import re

import anthropic
from django.conf import settings
from django.core.cache import cache

from .models import CATEGORIES

log = logging.getLogger(__name__)

RULES: dict[str, list[str]] = {
    "Food & Dining": ["swiggy", "zomato", "domino", "mcdonald", "kfc", "pizza", "restaurant", "cafe", "bakery",
                      "biryani", "hotel ", "starbucks", "burger", "chai", "eatfit", "dunzo daily"],
    "Groceries": ["bigbasket", "blinkit", "zepto", "dmart", "jiomart", "instamart", "grofers", "ratnadeep",
                  "more retail", "reliance fresh", "kirana", "supermarket", "vegetable", "milk"],
    "Shopping": ["amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa", "tata cliq", "lifestyle", "trends",
                 "decathlon", "croma", "reliance digital", "ikea"],
    "Transport": ["uber", "ola", "rapido", "metro", "irctc", "redbus", "apsrtc", "tsrtc", "fastag", "parking", "namma yatri"],
    "Fuel": ["petrol", "fuel", "hpcl", "bpcl", "iocl", "indian oil", "bharat petroleum", "hp pay", "shell"],
    "Bills & Utilities": ["electricity", "apspdcl", "apepdcl", "apcpdcl", "tgspdcl", "tsspdcl", "bescom", "airtel",
                          "jio", "vodafone", "vi prepaid", "bsnl", "act fibernet", "broadband", "recharge", "dth",
                          "tata play", "water bill", "gas bill", "bill payment", "bbps", "indane", "hp gas", "bharat gas"],
    "Rent & Housing": ["rent", "nobroker", "maintenance", "society", "housing"],
    "Entertainment": ["netflix", "hotstar", "spotify", "prime video", "bookmyshow", "pvr", "inox", "youtube",
                      "sonyliv", "zee5", "gaana", "steam", "playstation"],
    "Health": ["apollo", "pharmacy", "medplus", "1mg", "pharmeasy", "hospital", "clinic", "netmeds", "diagnostic",
               "lab", "dental", "practo", "cult.fit", "cultfit"],
    "Education": ["udemy", "coursera", "school", "college", "tuition", "byju", "unacademy", "fees", "leetcode"],
    "Travel": ["makemytrip", "goibibo", "indigo", "air india", "akasa", "vistara", "oyo", "airbnb", "cleartrip", "ixigo", "yatra"],
    "EMI & Loans": ["emi", "loan", "bajaj finance", "home credit", "lazypay", "simpl", "credit card payment", "cc payment"],
    "Investments": ["zerodha", "groww", "sip", "mutual fund", "upstox", "angel one", "kuvera", "ppf", "nps", "coin by"],
    "Insurance": ["lic", "insurance", "policybazaar", "acko", "hdfc life", "icici pru", "star health"],
    "Cash Withdrawal": ["atm", "cash withdrawal", "cash wdl", "nwd", "awd"],
    "Income": ["salary", "sal cr", "interest credit", "int.pd", "dividend", "refund", "cashback"],
}

LLM_BATCH = 40
CACHE_TTL = 60 * 60 * 24 * 30

# Word-boundary patterns so "rent" doesn't match "current" and "lic" doesn't match "public".
_PATTERNS = {
    category: re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(k.strip()) for k in keywords) + r")(?![a-z0-9])")
    for category, keywords in RULES.items()
}
_TRANSFER = re.compile(r"(?<![a-z])(upi|imps|neft|rtgs)(?![a-z])")


def rule_category(description: str, merchant: str, direction: str) -> str | None:
    text = f"{description} {merchant}".lower()
    for category, pattern in _PATTERNS.items():
        if pattern.search(text):
            if category == "Income" and direction == "debit":
                continue
            return category
    if direction == "credit":
        return "Transfers" if _TRANSFER.search(text) else "Income"
    return None


def _merchant_key(merchant: str) -> str:
    return "cat:merchant:" + hashlib.sha1(merchant.lower().encode()).hexdigest()


SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "category": {"type": "string", "enum": CATEGORIES},
                },
                "required": ["id", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

SYSTEM = (
    "You categorise Indian bank transactions (UPI, card, NEFT). For each item pick exactly one "
    "category from the allowed list, using the merchant name and raw bank narration. Person-to-person "
    "UPI payments to an individual's name are 'Transfers'. Small local shops with unclear names that "
    "sell daily needs are 'Groceries'. Use 'Other' only if nothing fits."
)


def llm_categories(items: list[dict]) -> dict[str, str]:
    """items: [{"merchant": ..., "description": ...}] -> {merchant: category}.

    Checks the Redis cache per merchant first; only unseen merchants go to Claude.
    """
    result, todo = {}, []
    for item in items:
        cached = cache.get(_merchant_key(item["merchant"]))
        if cached:
            result[item["merchant"]] = cached
        else:
            todo.append(item)
    if not todo or not settings.LLM_ENABLED:
        return result

    client = anthropic.Anthropic()
    for start in range(0, len(todo), LLM_BATCH):
        batch = todo[start:start + LLM_BATCH]
        payload = [{"id": i, "merchant": it["merchant"], "narration": it["description"][:160]}
                   for i, it in enumerate(batch)]
        try:
            response = client.beta.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            )
        except anthropic.RateLimitError as e:
            log.warning("LLM rate limited, leaving %d merchants uncategorised: %s", len(batch), e)
            break
        except anthropic.APIStatusError as e:
            log.error("LLM API error %s: %s", e.status_code, e.message)
            break
        except anthropic.APIConnectionError as e:
            log.error("LLM connection error: %s", e)
            break
        except TypeError as e:  # no credentials configured
            log.warning("LLM not configured: %s", e)
            break

        if response.stop_reason in ("refusal", "max_tokens"):
            log.warning("LLM stopped with %s; skipping batch", response.stop_reason)
            continue
        text = next((b.text for b in response.content if b.type == "text"), "{}")
        for row in json.loads(text).get("results", []):
            if 0 <= row["id"] < len(batch) and row["category"] in CATEGORIES:
                merchant = batch[row["id"]]["merchant"]
                result[merchant] = row["category"]
                cache.set(_merchant_key(merchant), row["category"], CACHE_TTL)
    return result
