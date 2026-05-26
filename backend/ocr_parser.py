import re
import io
from datetime import datetime
from typing import Optional

# Lazy imports — only when OCR is actually used (Tesseract/Pillow may not be installed)
_tesseract_available = None
_Image = None


def _ensure_tesseract():
    global _tesseract_available
    if _tesseract_available is None:
        try:
            import pytesseract
            _tesseract_available = True
        except ImportError:
            _tesseract_available = False
    return _tesseract_available


def _ensure_pillow():
    global _Image
    if _Image is None:
        try:
            from PIL import Image as _PILImage
            _Image = _PILImage
        except ImportError:
            pass
    return _Image


def ocr_image(file_bytes: bytes, lang: str = "chi_sim+eng") -> str:
    # 1. Try Apple Vision (much better Chinese accuracy, built into macOS)
    text = _ocr_vision(file_bytes)
    if text and len(text.strip()) > 10:
        return text

    # 2. Fallback to Tesseract
    return _ocr_tesseract(file_bytes, lang)


def _ocr_vision(file_bytes: bytes) -> str:
    """Use macOS Vision framework via Swift helper for OCR."""
    import subprocess
    import tempfile
    import os

    swift_script = os.path.join(os.path.dirname(__file__), "vision_ocr.swift")
    if not os.path.exists(swift_script):
        return ""

    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        result = subprocess.run(
            ["swift", swift_script, tmp_path],
            capture_output=True, text=True, timeout=60
        )
        os.unlink(tmp_path)
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _ocr_tesseract(file_bytes: bytes, lang: str) -> str:
    """Fallback OCR using Tesseract."""
    if not _ensure_tesseract():
        return ""
    if not _ensure_pillow():
        return ""

    import pytesseract
    from PIL import ImageFilter

    img = _Image.open(io.BytesIO(file_bytes))

    w, h = img.size
    if max(w, h) < 1200:
        scale = 1800 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)

    img = img.convert("L")
    img = img.point(lambda x: 0 if x < 140 else 255)
    img = img.filter(ImageFilter.SHARPEN)

    text = pytesseract.image_to_string(
        img, lang=lang,
        config="--psm 6 -c tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ一-鿿,.+-¥￥()（）/年月日买买入卖出申购赎回认购分红红利股利派息股息再投数量份额股成交价价格金额元"
    )
    return text


# --- Field extraction from OCR text ---

DATE_PATTERNS = [
    re.compile(r"(\d{4})[:：年/-](\d{1,2})[:：月/-](\d{1,2})[日]?"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),  # 20250521 compact
    re.compile(r"(\d{4})(\d{2})[-:/](\d{2})"),  # 202402-20 (Vision format)
]

AMOUNT_PATTERNS = [
    re.compile(r"[+\-]?\s*[¥￥$]?\s*([\d,]+\.\d{2})"),           # +1,000.00, ¥500.00, -300.00
    re.compile(r"([\d]+(?:\.[\d]{3})*\.\d{2})"),                 # 12.000.00 (dots as thousand sep)
    re.compile(r"([\d,]+\.\d{2})\s*[¥￥元]?"),                    # 1,000.00元
    re.compile(r"[+\-]?\s*[¥￥$]?\s*([\d,]+)(?:\s|$)"),          # +1000, ¥5000 (integer amounts)
]

STOCK_CODE = re.compile(r"\b(\d{6})\b")  # A-share 6-digit code
STOCK_TICKER = re.compile(r"\b([A-Z]{1,5})\b")  # US stock ticker
FUND_NAME = re.compile(r"([一-鿿]{2,8}(?:精选|蓝筹|成长|混合|债券|指数|ETF|联接|LOF|FOF)?[A-C]?)")

TYPE_KEYWORDS = {
    "买入": ["买入", "申购", "买", "认购", "buy"],
    "卖出": ["卖出", "赎回", "卖", "sell"],
    "分红入账": ["分红", "红利", "股利", "dividend", "派息"],
    "股息再投资": ["红利再投", "红利转投", "drip", "再投资"],
}

SHARES_AFTER = re.compile(r"(?:数量|成交数量|股数|份额|shares|qty)\s*[：:]?\s*([\d,]+\.?\d*)", re.IGNORECASE)
PRICE_AFTER = re.compile(r"(?:价格|成交价|成交价格|均价|price)\s*[：:]?\s*([\d,]+\.?\d*)", re.IGNORECASE)


def parse_trade_text(raw_text: str) -> dict:
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    full_text = " ".join(lines)

    # Extract global symbol from overall text
    global_symbol = _extract_symbol(lines, full_text)

    # Try multi-record extraction: each date starts a new record
    records = _extract_records(lines, full_text, global_symbol)

    result = {
        "records": records,
        "date": records[0]["date"] if records else None,
        "symbol": records[0]["symbol"] if records else global_symbol,
        "business_type": records[0]["business_type"] if records else None,
        "cash_flow": records[0]["cash_flow"] if records else None,
        "shares": records[0]["shares"] if records else None,
        "price": records[0]["price"] if records else None,
        "raw_lines": lines[:30],
    }

    return result


def _extract_records(lines, full_text, global_symbol=None):
    """Scan all lines for transaction patterns and return list of records."""
    records = []
    i = 0
    while i < len(lines):
        date_val = _extract_date_from_line(lines[i])
        if date_val:
            record = _build_record_at(lines, i, global_symbol)
            if record and record.get("cash_flow") is not None:
                records.append(record)
        i += 1

    # Fallback: if no records found, try the single-record approach
    if not records:
        single = {
            "date": _extract_date_from_lines(lines),
            "symbol": global_symbol,
            "business_type": _extract_type_from_lines(lines),
            "cash_flow": _extract_amounts_from_lines(lines),
            "shares": _extract_number(SHARES_AFTER, lines, full_text),
            "price": _extract_number(PRICE_AFTER, lines, full_text),
        }
        if single["date"] and single["cash_flow"] is not None:
            records.append(single)

    return records


def _build_record_at(lines, start_idx, global_symbol=None):
    """Build a single transaction record starting from a date line.
    Only searches up to the next date line (non-overlapping records)."""
    current = lines[start_idx]

    date_val = _extract_date_from_line(current)
    if not date_val:
        return None

    # Find the end of this record: the next line that contains a date
    end_idx = start_idx + 1
    while end_idx < len(lines):
        if _extract_date_from_line(lines[end_idx]):
            break
        end_idx += 1

    record_lines = lines[start_idx:end_idx]
    window_text = " ".join(record_lines)

    type_val = _extract_type_from_lines(record_lines)
    all_amts = _extract_all_amounts(record_lines)
    amount_val = max(all_amts, key=abs) if all_amts else None
    local_symbol = _extract_symbol(record_lines, window_text) or global_symbol

    # Sign correction
    if amount_val is not None and type_val in ("买入", "股息再投资"):
        if amount_val > 0:
            amount_val = -amount_val

    if date_val and amount_val is not None:
        return {
            "date": date_val,
            "symbol": local_symbol,
            "business_type": type_val,
            "cash_flow": amount_val,
            "shares": _extract_number(SHARES_AFTER, record_lines, window_text),
            "price": _extract_number(PRICE_AFTER, record_lines, window_text),
        }

    return None


def _extract_date_from_line(line):
    for pat in DATE_PATTERNS:
        m = pat.search(line)
        if m:
            try:
                y, mth, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2020 <= y <= 2030 and 1 <= mth <= 12 and 1 <= d <= 31:
                    return f"{y:04d}-{mth:02d}-{d:02d}"
            except (ValueError, IndexError):
                continue
    return None


def _extract_date_from_lines(lines):
    for line in lines:
        d = _extract_date_from_line(line)
        if d:
            return d
    return None


def _extract_type_from_lines(lines):
    text = " ".join(lines).lower()
    for ttype, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return ttype
    return None


def _normalize_amount(raw_str):
    """Parse an amount string, handling various OCR quirks."""
    val = raw_str.replace(",", "")
    # Handle dot as thousand separator: "12.000.00" → "12000.00"
    dots = val.count(".")
    if dots > 1:
        parts = val.split(".")
        val = "".join(parts[:-1]) + "." + parts[-1]
    try:
        num = float(val)
        if 0.01 <= abs(num) <= 100000000:
            return num
    except ValueError:
        pass
    return None


def _extract_all_amounts(lines):
    """Extract all valid amount candidates from lines, return list."""
    candidates = []
    for line in lines:
        chinese_chars = sum(1 for c in line if '一' <= c <= '鿿')
        if chinese_chars > len(line) * 0.4:
            continue
        for pat in AMOUNT_PATTERNS:
            for m in pat.finditer(line):
                num = _normalize_amount(m.group(1))
                if num is not None:
                    candidates.append(num)
    return candidates


def _extract_amounts_from_lines(lines):
    return _extract_all_amounts(lines)  # delegate to unified extractor


def _extract_symbol(lines, full_text):
    # Prefer 6-digit stock code (exclude date-like patterns: 2020xx-2030xx)
    for line in lines:
        m = STOCK_CODE.search(line)
        if m:
            code = m.group(1)
            if not (code.startswith("202") or code.startswith("203")):
                return code
    # Fallback: ticker
    exclude = {"ETF", "LOF", "FOF", "A", "B", "C", "E"}
    for line in lines:
        for m in STOCK_TICKER.finditer(line):
            t = m.group(1)
            if t not in exclude:
                return t
    # Fallback: Chinese fund name
    for line in lines:
        m = FUND_NAME.search(line)
        if m:
            return m.group(1)
    return None


def _extract_number(pattern, lines, full_text):
    for line in lines:
        m = pattern.search(line)
        if m:
            val = m.group(1).replace(",", "")
            try:
                num = float(val)
                if 0.001 < abs(num) < 100000000:
                    return num
            except ValueError:
                continue
    m = pattern.search(full_text)
    if m:
        val = m.group(1).replace(",", "")
        try:
            num = float(val)
            if 0.001 < abs(num) < 100000000:
                return num
        except ValueError:
            pass
    return None
