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
        return _clean_ocr_text(text)

    # 2. Fallback to Tesseract
    text = _ocr_tesseract(file_bytes, lang)
    return _clean_ocr_text(text) if text else ""


def _clean_ocr_text(text: str) -> str:
    """Preprocess OCR output: remove noise lines, arrow symbols, etc."""
    import re
    date_like = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")

    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # Remove leading/trailing arrow symbols and whitespace
        line = line.strip().lstrip(">＜〈《").rstrip(">＜〉《")
        if not line:
            continue
        # Always keep date lines (critical for parsing)
        if date_like.search(line):
            cleaned.append(line)
            continue
        # Skip status bar / noise lines (no Chinese, no date, no transaction keywords)
        alpha_count = sum(1 for c in line if c.isalpha() or ('一' <= c <= '鿿'))
        if alpha_count == 0 and len(line) > 3:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


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


def _compute_otsu_threshold(img) -> int:
    """Compute optimal binary threshold using OTSU's method on a grayscale image."""
    hist = img.histogram()
    total = sum(hist)
    if total == 0:
        return 128

    sum_b = 0
    w_b = 0
    max_var = 0
    threshold = 128

    sum_total = sum(i * h for i, h in enumerate(hist))

    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            threshold = t

    return threshold


def _ocr_tesseract(file_bytes: bytes, lang: str = "chi_sim+eng") -> str:
    """Fallback OCR using Tesseract with adaptive preprocessing.

    Key improvements over a naive tesseract call:
    - OEM 1 (LSTM-only) — best for Chinese, no legacy-mode fallback
    - NO character whitelist — whitelists force legacy mode and kill LSTM accuracy
    - OTSU adaptive thresholding — handles screenshots with varying brightness
    - Median denoising — removes salt-and-pepper noise from JPEG compression
    - RGBA → RGB flattening — some apps produce screenshots with alpha channels
    - Upscale to ~300 DPI equivalent — Tesseract works best at print resolution
    """
    if not _ensure_tesseract():
        return ""
    if not _ensure_pillow():
        return ""

    import pytesseract
    from PIL import ImageFilter
    import sys

    # Check that Chinese language data is installed
    try:
        installed = pytesseract.get_languages()
        if "chi_sim" not in installed:
            print("⚠ Tesseract 中文语言包未安装，OCR 准确率将大幅下降。安装方法：", file=sys.stderr)
            print("  macOS: brew install tesseract-lang", file=sys.stderr)
            print("  Windows: 重新运行安装程序并勾选中文语言包", file=sys.stderr)
    except Exception:
        pass  # get_languages() may not be available in old pytesseract versions

    img = _Image.open(io.BytesIO(file_bytes))

    # Flatten RGBA onto white background
    if img.mode == "RGBA":
        background = _Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Upscale to ~300 DPI equivalent for better character separation
    w, h = img.size
    if max(w, h) < 2400:
        scale = 2400 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)

    # Grayscale
    img = img.convert("L")

    # Denoise — median filter removes salt-and-pepper noise without blurring edges
    img = img.filter(ImageFilter.MedianFilter(size=3))

    # OTSU adaptive binarisation
    threshold = _compute_otsu_threshold(img)
    img = img.point(lambda x: 0 if x < threshold else 255)

    # Light sharpen to crisp up character edges
    img = img.filter(ImageFilter.SHARPEN)

    # OEM 1 = LSTM neural net only (best for Chinese)
    # PSM 6 = assume uniform block of text, read rows left-to-right
    text = pytesseract.image_to_string(img, lang=lang, config="--oem 1 --psm 6")
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
FUND_NAME = re.compile(r"([一-鿿]{4,30}(?:精选|蓝筹|成长|混合|债券|指数|货币|股票|联接|LOF|FOF|ETF)?[A-C]?)")

TYPE_KEYWORDS = {
    "买入": ["买入", "申购", "买", "认购", "buy", "定投"],
    "卖出": ["卖出", "赎回", "卖", "sell"],
    "分红入账": ["分红", "红利", "股利", "dividend", "派息"],
    "股息再投资": ["红利再投", "红利转投", "drip", "再投资"],
}

CANCEL_KEYWORDS = ["已撤回", "已撤销", "撤单", "已取消", "失败", "已退款", "交易关闭", "交易失败"]

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
    """Type-keyword-anchored extraction — more robust than date-anchored."""
    # Find type keyword positions
    type_spots = []
    for i, line in enumerate(lines):
        t = _extract_type_from_lines([line])
        if t:
            type_spots.append((i, t, line))

    records = []
    used_dates = set()

    for ti, raw_type, type_line in type_spots:
        # Find nearest date within ±4 lines of this type
        date_val = None
        date_pos = None
        for offset in range(0, 5):
            for d in [ti - offset, ti + offset]:
                if 0 <= d < len(lines) and d not in used_dates:
                    dv = _extract_date_from_line(lines[d])
                    if dv:
                        date_val = dv
                        date_pos = d
                        break
            if date_val:
                break

        if not date_val:
            continue

        # Build search window: type line through date line + 2 extra
        lo = min(ti, date_pos)
        hi = min(len(lines), max(ti, date_pos) + 2)
        window = lines[lo:hi]
        window_text = " ".join(window)

        # Cancel check
        if any(kw in window_text for kw in CANCEL_KEYWORDS):
            used_dates.add(date_pos)
            continue

        # Amount from window
        amts = _extract_all_amounts(window)
        amount_val = amts[0] if amts else None
        if amount_val is None:
            continue

        display_type = "买入" if raw_type == "定投" else raw_type

        if display_type in ("买入", "股息再投资"):
            amount_val = abs(amount_val)

        local_symbol = _extract_symbol(window, window_text) or global_symbol

        records.append({
            "date": date_val,
            "symbol": local_symbol,
            "business_type": display_type,
            "cash_flow": amount_val,
            "shares": _extract_number(SHARES_AFTER, window, window_text),
            "price": _extract_number(PRICE_AFTER, window, window_text),
        })

        used_dates.add(date_pos)

    # Fallback: date-anchored if no type keywords found
    if not records:
        return _extract_records_fallback(lines, full_text, global_symbol)

    return records


def _extract_records_fallback(lines, full_text, global_symbol):
    """Fallback when no type keywords found — date-anchored extraction."""
    records = []
    for i, line in enumerate(lines):
        date_val = _extract_date_from_line(line)
        if not date_val:
            continue
        lo = max(0, i - 3)
        hi = min(len(lines), i + 4)
        window = lines[lo:hi]
        window_text = " ".join(window)
        if any(kw in window_text for kw in CANCEL_KEYWORDS):
            continue
        type_val = _extract_type_from_lines(window)
        amts = _extract_all_amounts(window)
        amount_val = amts[0] if amts else None
        if not type_val or amount_val is None:
            continue
        if type_val in ("买入", "股息再投资"):
            amount_val = abs(amount_val)
        records.append({
            "date": date_val,
            "symbol": _extract_symbol(window, window_text) or global_symbol,
            "business_type": type_val,
            "cash_flow": amount_val,
            "shares": _extract_number(SHARES_AFTER, window, window_text),
            "price": _extract_number(PRICE_AFTER, window, window_text),
        })
    return records


def _build_record_at(lines, start_idx, global_symbol=None):
    """Build a single transaction record starting from a date line.
    Searches up to the next date line, and also looks backward for type info."""
    current = lines[start_idx]

    date_val = _extract_date_from_line(current)
    if not date_val:
        return None

    # Find the end: next line that contains a date
    end_idx = start_idx + 1
    while end_idx < len(lines):
        if _extract_date_from_line(lines[end_idx]):
            break
        end_idx += 1

    # Alipay format: type → amount → date (3 lines). Look back up to 3 lines.
    lookback = max(0, start_idx - 3)
    record_lines = lines[lookback:end_idx]
    window_text = " ".join(record_lines)

    # Skip cancelled: check date line + lines above (where type/amount usually are)
    cancel_check_text = " ".join(lines[max(0, start_idx-3):start_idx+1])
    for kw in CANCEL_KEYWORDS:
        if kw in cancel_check_text:
            return None

    # Type: search lines above date (up to 3 lines back), then all record lines
    ahead_lines = lines[max(0, start_idx-3):start_idx]
    type_val = _extract_type_from_lines(ahead_lines) or _extract_type_from_lines(record_lines)
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
        # Skip timestamp-only lines (colon but no 元/¥ after it)
        if ":" in line:
            # Unless there's a currency indicator after the timestamp
            if not ("元" in line.split(":")[-1] or "¥" in line.split(":")[-1]):
                continue
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
    # Fallback: Chinese fund name (prefer first few lines where fund name appears)
    # Collect exclusion words (type keys + type keywords + header words)
    exclude_words = set(TYPE_KEYWORDS.keys())
    for keywords in TYPE_KEYWORDS.values():
        for kw in keywords:
            exclude_words.add(kw)
    exclude_words.update(["交易记录", "交易明细", "基金详情", "历史交易", "确认信息", "基金名称"])

    search_lines = lines[:5] + lines  # top lines first, then all
    for line in search_lines:
        m = FUND_NAME.search(line)
        if m:
            name = m.group(1)
            if name not in exclude_words:
                return name
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
