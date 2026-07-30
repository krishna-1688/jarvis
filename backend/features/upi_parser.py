"""
features/upi_parser.py — parses UPI/bank transaction notification text
into {"amount", "merchant", "spent_at"}. One function per known bank
template, falling back to a generic "Rs./₹ N ... to/at MERCHANT" regex.

These templates are built from common Indian bank SMS/notification
formats, not the user's own real messages — accuracy will improve once
real examples replace the assumed formats here. Returns None when
nothing could be parsed; the caller logs the raw text for later
template additions (see server.py's /expense/ingest_upi).
"""

import re
from datetime import datetime

_MONTH_ABBR = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def _to_year(y: str) -> int:
    y = int(y)
    return y + 2000 if y < 100 else y


def _date_from_abbr(day, mon, year) -> datetime:
    return datetime(_to_year(year), _MONTH_ABBR[mon.lower()[:3]], int(day))


def _parse_hdfc(text: str):
    # "Rs.180.00 has been debited from a/c XX1234 to Zomato on 20-Jul-26 via UPI"
    m = re.search(
        r'Rs\.?\s*([\d,]+(?:\.\d+)?)\s+has been debited.*?to\s+([A-Za-z0-9 &._-]+?)\s+on\s+(\d{1,2})-(\w{3})-(\d{2,4})',
        text, re.IGNORECASE,
    )
    if not m:
        return None
    return {
        "amount": float(m.group(1).replace(',', '')),
        "merchant": m.group(2).strip(),
        "spent_at": _date_from_abbr(m.group(3), m.group(4), m.group(5)).isoformat(),
    }


def _parse_sbi(text: str):
    # "Rs.150 debited from A/c XX5678 on 20Jul26 transfer to ZOMATO Ref No 123456789012"
    m = re.search(
        r'Rs\.?\s*([\d,]+(?:\.\d+)?)\s+debited.*?on\s+(\d{1,2})(\w{3})(\d{2,4})\s+transfer to\s+([A-Za-z0-9 &._-]+?)\s+Ref',
        text, re.IGNORECASE,
    )
    if not m:
        return None
    return {
        "amount": float(m.group(1).replace(',', '')),
        "merchant": m.group(5).strip(),
        "spent_at": _date_from_abbr(m.group(2), m.group(3), m.group(4)).isoformat(),
    }


def _parse_icici(text: str):
    # "ICICI Bank Acct XX789 debited with Rs 340.00 on 20-Jul-26; SWIGGY credited. UPI:123456789012"
    m = re.search(
        r'debited with\s+Rs\.?\s*([\d,]+(?:\.\d+)?)\s+on\s+(\d{1,2})-(\w{3})-(\d{2,4});\s*([A-Za-z0-9 &._-]+?)\s+credited',
        text, re.IGNORECASE,
    )
    if not m:
        return None
    return {
        "amount": float(m.group(1).replace(',', '')),
        "merchant": m.group(5).strip(),
        "spent_at": _date_from_abbr(m.group(2), m.group(3), m.group(4)).isoformat(),
    }


def _parse_axis(text: str):
    # "Rs.500.00 debited from A/c no. XX1234 on 20-07-26 to VPA merchant@ybl (SWIGGY). UPI Ref 123456789012"
    m = re.search(
        r'Rs\.?\s*([\d,]+(?:\.\d+)?)\s+debited.*?on\s+(\d{1,2})-(\d{1,2})-(\d{2,4})\s+to\s+VPA\s+[\w.@-]+\s*\(([A-Za-z0-9 &._-]+?)\)',
        text, re.IGNORECASE,
    )
    if not m:
        return None
    year = _to_year(m.group(4))
    return {
        "amount": float(m.group(1).replace(',', '')),
        "merchant": m.group(5).strip(),
        "spent_at": datetime(year, int(m.group(3)), int(m.group(2))).isoformat(),
    }


def _parse_paytm(text: str):
    # "Paid Rs.150 to Ola via Paytm UPI. UPI Ref No. 123456789012"
    m = re.search(r'Paid\s+Rs\.?\s*([\d,]+(?:\.\d+)?)\s+to\s+([A-Za-z0-9 &._-]+?)\s+via\s+Paytm', text, re.IGNORECASE)
    if not m:
        return None
    return {"amount": float(m.group(1).replace(',', '')), "merchant": m.group(2).strip(),
            "spent_at": datetime.now().isoformat()}


def _parse_phonepe(text: str):
    # "You paid Rs.200 to Zomato via PhonePe."
    m = re.search(r'paid\s+Rs\.?\s*([\d,]+(?:\.\d+)?)\s+to\s+([A-Za-z0-9 &._@-]+?)\s+via\s+PhonePe', text, re.IGNORECASE)
    if not m:
        return None
    return {"amount": float(m.group(1).replace(',', '')), "merchant": m.group(2).strip(),
            "spent_at": datetime.now().isoformat()}


def _parse_gpay(text: str):
    # "You paid ₹150.00 to Swiggy using Google Pay UPI" — a P2P payment
    # to a raw VPA looks like "You paid Rs.30 to teaguy-p2a@ybl using
    # Google Pay UPI", so the merchant class allows '@' too (V.3c: this
    # is exactly the "opaque VPA" case the alias_merchant intent names).
    m = re.search(r'paid\s+(?:rs\.?|₹)\s*([\d,]+(?:\.\d+)?)\s+to\s+([A-Za-z0-9 &._@-]+?)\s+using\s+Google Pay', text, re.IGNORECASE)
    if not m:
        return None
    return {"amount": float(m.group(1).replace(',', '')), "merchant": m.group(2).strip(),
            "spent_at": datetime.now().isoformat()}


def _parse_generic(text: str):
    """Fallback: any 'Rs./₹ N ... to/at MERCHANT' shape not matched above.
    Merchant class includes '@' — a P2P UPI payment to someone with no
    registered business name shows a raw VPA like 'p2a-abc123@ybl'
    instead of a readable name (V.3c's "opaque VPA" case)."""
    amount_m = re.search(r'(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)', text, re.IGNORECASE)
    if not amount_m:
        return None
    merchant_m = re.search(
        r'(?:to|at)\s+([A-Za-z][A-Za-z0-9 &._@-]{1,40}?)(?:[.,;]|\s+(?:on|via|using|Ref|UPI)\b|$)',
        text, re.IGNORECASE,
    )
    return {
        "amount": float(amount_m.group(1).replace(',', '')),
        "merchant": merchant_m.group(1).strip() if merchant_m else None,
        "spent_at": datetime.now().isoformat(),
    }


_PARSERS = [_parse_hdfc, _parse_sbi, _parse_icici, _parse_axis, _parse_paytm, _parse_phonepe, _parse_gpay, _parse_generic]


def parse_upi_message(text: str) -> dict | None:
    for parser in _PARSERS:
        result = parser(text)
        if result:
            return result
    return None
