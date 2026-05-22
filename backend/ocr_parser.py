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
    if not _ensure_tesseract():
        return ""
    if not _ensure_pillow():
        return ""

    import pytesseract

    img = _Image.open(io.BytesIO(file_bytes))
    # Preprocess: grayscale, enhance contrast, scale up for better accuracy
    img = img.convert("L")  # grayscale
    # Simple contrast stretch
    from PIL import ImageOps
    img = ImageOps.autocontrast(img, cutoff=2)
    # Scale up small images
    w, h = img.size
    if w < 600:
        scale = 1200 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)

    text = pytesseract.image_to_string(img, lang=lang)
    return text


# --- Field extraction from OCR text ---

DATE_PATTERNS = [
    re.compile(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})[日]?"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),  # 20250521 compact
]

AMOUNT_PATTERNS = [
    re.compile(r"[¥￥$]?\s*(-?[\d,]+\.\d{2})"),  # ¥1,000.00  or -500.00
    re.compile(r"(-?[\d,]+\.\d{2})\s*[¥￥元]?"),
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

    result = {
        "date": _extract_date(lines, full_text),
        "symbol": _extract_symbol(lines, full_text),
        "business_type": _extract_type(lines, full_text),
        "cash_flow": _extract_amounts(lines, full_text),
        "shares": _extract_number(SHARES_AFTER, lines, full_text),
        "price": _extract_number(PRICE_AFTER, lines, full_text),
        "raw_lines": lines[:20],  # first 20 lines for debug preview
    }

    # Sign correction: if type is 买入, cash_flow should be negative
    if result["cash_flow"] and result["business_type"] in ("买入", "股息再投资"):
        if result["cash_flow"] > 0:
            result["cash_flow"] = -result["cash_flow"]

    return result


def _extract_date(lines, full_text):
    for line in lines:
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


def _extract_symbol(lines, full_text):
    # Prefer 6-digit stock code
    for line in lines:
        m = STOCK_CODE.search(line)
        if m:
            return m.group(1)
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


def _extract_type(lines, full_text):
    full = full_text.lower()
    for ttype, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in full:
                return ttype
    return None


def _extract_amounts(lines, full_text):
    candidates = []
    for line in lines:
        for pat in AMOUNT_PATTERNS:
            for m in pat.finditer(line):
                val = m.group(1).replace(",", "")
                try:
                    candidates.append(float(val))
                except ValueError:
                    continue
    if not candidates:
        return None
    # Return the amount with largest absolute value (typically the principal)
    return max(candidates, key=abs)


def _extract_number(pattern, lines, full_text):
    for line in lines:
        m = pattern.search(line)
        if m:
            val = m.group(1).replace(",", "")
            try:
                return float(val)
            except ValueError:
                return None
    m = pattern.search(full_text)
    if m:
        val = m.group(1).replace(",", "")
        try:
            return float(val)
        except ValueError:
            return None
    return None
