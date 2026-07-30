"""
features/expenses.py — Expense tracker: voice entry now, WhatsApp UPI
auto-parse in features/upi_parser.py (Phase 5.2) feeds the same table.
Categories come from data/expense_rules.json — a keyword LIKE match,
fallback 'other'.
"""

import json
import os
import re
from datetime import datetime, timedelta

from features.base import FeatureResult
from core.memory import (
    add_expense, get_expenses_between, get_expenses_by_category, search_expenses,
)

_RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "expense_rules.json")
with open(_RULES_PATH, encoding="utf-8") as _f:
    CATEGORY_RULES = json.load(_f)

_MERCHANT_NAMES = [
    "zomato", "swiggy", "ola", "uber", "rapido", "netflix", "spotify",
    "irctc", "amazon", "pvr", "inox", "steam", "chatgpt", "claude",
    "kfc", "mcdonalds", "mcdonald's", "dominos", "domino's", "pizza hut",
    "burger king", "subway", "starbucks", "chai point", "dunkin",
]

_MERCHANT_DISPLAY_OVERRIDES = {
    "kfc": "KFC", "irctc": "IRCTC", "pvr": "PVR", "chatgpt": "ChatGPT",
}

# V.3b: when no category keyword matched but a known merchant did,
# fall back to that merchant's usual category instead of "other" —
# "180 kfc" has no food *keyword* in it, but KFC obviously is food.
_MERCHANT_CATEGORY = {
    "zomato": "food", "swiggy": "food", "kfc": "food", "mcdonalds": "food",
    "mcdonald's": "food", "dominos": "food", "domino's": "food",
    "pizza hut": "food", "burger king": "food", "subway": "food",
    "starbucks": "food", "chai point": "food", "dunkin": "food",
    "ola": "travel", "uber": "travel", "rapido": "travel", "irctc": "travel",
    "netflix": "subscriptions", "spotify": "subscriptions", "chatgpt": "subscriptions",
    "claude": "subscriptions",
    "pvr": "entertainment", "inox": "entertainment", "steam": "entertainment",
}

_AMOUNT_RE = re.compile(r'(?:(?:rs\.?|₹|inr)\s*)?(\d+(?:\.\d+)?)(?:\s*(?:rupees?|rs\.?|inr))?', re.IGNORECASE)
_LEAD_FILLER = re.compile(r'^\s*(spent|paid|spend)\b', re.IGNORECASE)
_MIN_AMOUNT_RE = re.compile(r'over\s+(\d+(?:\.\d+)?)', re.IGNORECASE)


def classify(text: str) -> str:
    t = text.lower()
    for category, keywords in CATEGORY_RULES.items():
        if any(k in t for k in keywords):
            return category
    return "other"


def _detect_merchant(text: str):
    """Returns (display_name, matched_token) or (None, None)."""
    t = text.lower()
    for name in _MERCHANT_NAMES:
        if name in t:
            return _MERCHANT_DISPLAY_OVERRIDES.get(name, name.capitalize()), name
    return None, None


def _extract_amount(text: str):
    """Returns (amount|None, remaining_text)."""
    m = _AMOUNT_RE.search(text)
    if not m:
        return None, text
    return float(m.group(1)), text[:m.start()] + text[m.end():]


def _log_uncategorized(raw_text: str):
    """V.3f: anything that lands in 'other' gets flagged here instead of
    silently swallowed — a running list of real spending patterns worth
    turning into new expense_rules.json entries."""
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "uncategorized_merchants.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()}\t{raw_text}\n")
    except Exception:
        pass


def add(raw_text: str, on_progress=None) -> FeatureResult:
    text = raw_text.strip()
    amount, remaining = _extract_amount(text)
    if amount is None:
        msg = "I couldn't find an amount in that."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_amount")

    remaining = _LEAD_FILLER.sub('', remaining)
    for word in ["on", "for", "to", "at"]:
        remaining = re.sub(rf'\b{word}\b', ' ', remaining, count=1, flags=re.IGNORECASE)
    note = re.sub(r'\s+', ' ', remaining).strip(' ,.')

    merchant, matched_token = _detect_merchant(note or text)
    # Strip the merchant name back out of the note — "swiggy biryani"
    # should leave a note of just "biryani", not repeat what the
    # merchant field already says.
    if matched_token:
        note = re.sub(re.escape(matched_token), '', note, flags=re.IGNORECASE)
        note = re.sub(r'\s+', ' ', note).strip(' ,.')

    category = classify(note or text)
    if category == "other" and merchant:
        # No category keyword matched, but a known merchant did — "180
        # kfc" has no food *keyword*, but KFC obviously is food.
        category = _MERCHANT_CATEGORY.get(matched_token, category)
    if category == "other":
        _log_uncategorized(raw_text)

    expense_id = add_expense(amount, category=category, merchant=merchant, note=note or None,
                             source="voice", raw_text=raw_text)

    # V.3e: echo back exactly what was parsed, so a mis-parse is obvious
    # immediately rather than discovered later buried in a summary.
    parts = [f"₹{amount:.0f}"]
    if merchant:
        parts.append(merchant)
    if note:
        parts.append(note)
    msg = ", ".join(parts) + f". Logged under {category}."

    from core.ws_hub import broadcast
    broadcast({"type": "expense_added", "amount": amount, "category": category, "merchant": merchant, "auto": False})

    return FeatureResult(
        ok=True, data={"expense_id": expense_id, "amount": amount, "category": category, "merchant": merchant},
        display=msg, spoken=msg,
    )


def _resolve_range(text: str):
    t = text.lower()
    now = datetime.now()
    if "today" in t:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now + timedelta(days=1)
    if "month" in t:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now
    return now - timedelta(days=7), now  # default: week


def _resolve_category(text: str) -> str | None:
    t = text.lower()
    for cat in CATEGORY_RULES:
        if cat in t or cat.rstrip('s') in t:
            return cat
    return None


def _resolve_merchant_filter(text: str) -> str | None:
    """V.3c: 'how much at zomato this month' names a merchant, not a
    category — matched separately since a merchant can span multiple
    categories over time (rare, but the query should still work)."""
    merchant, _ = _detect_merchant(text)
    return merchant


def parse_summary_query(text: str):
    start, end = _resolve_range(text)
    category = _resolve_category(text)
    merchant = _resolve_merchant_filter(text)
    m = _MIN_AMOUNT_RE.search(text)
    min_amount = float(m.group(1)) if m else None
    return start, end, category, merchant, min_amount


def summary(raw_text: str, on_progress=None) -> FeatureResult:
    start, end, category, merchant, min_amount = parse_summary_query(raw_text)
    expenses = get_expenses_between(start, end)
    if category:
        expenses = [e for e in expenses if (e["category"] or "other") == category]
    if merchant:
        expenses = [e for e in expenses if (e["merchant"] or "").lower() == merchant.lower()]
    if min_amount is not None:
        expenses = [e for e in expenses if e["amount"] > min_amount]
    total = sum(e["amount"] for e in expenses)

    if category or merchant or min_amount is not None:
        lines = [f"₹{e['amount']:.0f} — {e['merchant'] or e['note'] or 'expense'}" for e in expenses]
        display = f"Total: ₹{total:.0f}\n" + "\n".join(lines) if lines else "No matching expenses."
        label = merchant or category
        spoken = f"₹{total:.0f}" + (f" at {label}" if merchant else f" on {label}" if label else "") + "."
        return FeatureResult(ok=True, data={"total": total, "expenses": expenses}, display=display, spoken=spoken)

    by_cat = get_expenses_by_category(start, end)
    lines = [f"{c['category']}: ₹{c['total']:.0f}" for c in by_cat]
    display = f"Total: ₹{total:.0f}\n" + "\n".join(lines) if lines else "No expenses."
    spoken = f"You spent ₹{total:.0f}" + (f", mostly on {by_cat[0]['category']}" if by_cat else "") + " in that period."
    return FeatureResult(ok=True, data={"total": total, "by_category": by_cat, "expenses": expenses}, display=display, spoken=spoken)


def search(query: str, on_progress=None) -> FeatureResult:
    results = search_expenses(query)
    if not results:
        msg = f"No expenses matching '{query}'."
        return FeatureResult(ok=True, data={"expenses": []}, display=msg, spoken=msg)
    total = sum(e["amount"] for e in results)
    lines = [f"₹{e['amount']:.0f} — {e['merchant'] or e['note'] or 'expense'}" for e in results]
    spoken = f"Found {len(results)} matching, ₹{total:.0f} total."
    return FeatureResult(ok=True, data={"expenses": results, "total": total}, display="\n".join(lines), spoken=spoken)


def alias_merchant(friendly_name: str) -> FeatureResult:
    """V.3c: 'that opaque vpa is the tea guy' — names the most recent
    unaliased opaque-VPA expense so future UPI ingests of that same VPA
    substitute the friendly name (see server.py's /expense/ingest_upi)."""
    from core.memory import get_last_opaque_expense, add_merchant_alias

    friendly_name = (friendly_name or "").strip()
    if not friendly_name:
        msg = 'Tell me what to call it — like "that vpa is the tea guy".'
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="missing_entity")

    expense = get_last_opaque_expense()
    if not expense:
        msg = "I don't have any unrecognized UPI handles to name right now."
        return FeatureResult(ok=False, data={}, display=msg, spoken=msg, error="no_data")

    add_merchant_alias(expense["merchant"], friendly_name)
    msg = f'Got it — I\'ll call that "{friendly_name}" from now on.'
    return FeatureResult(
        ok=True, data={"vpa": expense["merchant"], "friendly_name": friendly_name},
        display=msg, spoken=msg,
    )


# ══════════════════════════════════════════
#   FeatureResult wrappers for server.py's INTENT_HANDLERS
# ══════════════════════════════════════════

def get_expense_add_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    raw_text = (entities or {}).get("raw_text") or user_input
    return add(raw_text, on_progress=on_progress)


def get_expense_summary_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    raw_text = (entities or {}).get("raw_text") or user_input
    return summary(raw_text, on_progress=on_progress)


def get_expense_search_result(user_input: str, entities: dict = None, on_progress=None) -> FeatureResult:
    query = (entities or {}).get("query") or user_input
    return search(query, on_progress=on_progress)


def get_alias_merchant_result(user_input: str, entities: dict = None) -> FeatureResult:
    friendly_name = (entities or {}).get("friendly_name") or ""
    return alias_merchant(friendly_name)
