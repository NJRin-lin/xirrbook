import os
import re
import io
from datetime import datetime
from typing import Optional

# Lazy imports — only when OCR is actually used (Tesseract/Pillow may not be installed)
_tesseract_available = None
_Image = None

# Precompiled Vision binary — compile once, reuse across calls (much faster than
# running `swift vision_ocr.swift` every time, which recompiles on each call).
_vision_bin = None


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


def ocr_image(file_bytes: bytes, lang: str = "chi_sim+eng") -> dict:
    """Run OCR, trying Apple Vision first (built into macOS, best Chinese accuracy),
    then falling back to Tesseract.

    Returns a dict: {"text": str, "engine": str, "error": str}
    - engine: "vision" | "tesseract" | ""
    - error:  human-readable reason when no text was produced
    """
    # 1. Try Apple Vision (much better Chinese accuracy, built into macOS)
    text, err = _ocr_vision(file_bytes)
    if text and len(text.strip()) > 10:
        return {"text": _clean_ocr_text(text), "engine": "vision", "error": ""}

    vision_error = err or "Vision 未识别出有效文字"

    # 2. Fallback to Tesseract
    text = _ocr_tesseract(file_bytes, lang)
    if text and text.strip():
        return {"text": _clean_ocr_text(text), "engine": "tesseract", "error": ""}

    return {"text": "", "engine": "", "error": vision_error}


def _clean_ocr_text(text: str) -> str:
    """Preprocess OCR output: remove noise lines, arrow symbols, etc."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # Remove leading/trailing arrow symbols and whitespace
        line = line.strip().lstrip(">＜〈《").rstrip(">＜〉《")
        if not line:
            continue
        # Always keep date lines (critical for parsing)
        if _extract_date_from_line(line) or re.fullmatch(r"[+\-]?\s*[¥￥$]?\s*\d[\d,.]*\s*[元]?", line):
            cleaned.append(line)
            continue
        # Skip status bar / noise lines (no Chinese, no date, no transaction keywords)
        alpha_count = sum(1 for c in line if c.isalpha() or ('一' <= c <= '鿿'))
        if alpha_count == 0 and len(line) > 3:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _ocr_vision(file_bytes: bytes) -> tuple:
    """Use macOS Vision framework via a precompiled Swift helper for OCR.

    Returns (text, error). Compiles the Swift script to a binary once and reuses
    it — running swift vision_ocr.swift every time recompiles and is very slow.
    Tall scrolling screenshots are sliced into chunks because Vision fails on
    extremely tall images (returns "no text recognized").
    """
    import subprocess
    import tempfile
    import shutil

    global _vision_bin

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vision_ocr.swift")
    if not os.path.exists(script):
        return "", "Vision 脚本缺失: vision_ocr.swift"

    # Compile once to a temp binary; fall back to running the script directly
    if _vision_bin is None or not os.path.exists(_vision_bin):
        try:
            bin_fd, bin_path = tempfile.mkstemp(suffix="_vision_ocr", prefix="xirr_")
            os.close(bin_fd)
            os.unlink(bin_path)
            # -Xcc -fmodules-cache-path redirects clang module cache to a writable
            # temp dir — avoids "Operation not permitted" on restricted systems.
            module_cache = os.path.join(tempfile.gettempdir(), "xirr_swift_modules")
            os.makedirs(module_cache, exist_ok=True)
            compile_result = subprocess.run(
                ["swiftc", "-O", "-Xcc", "-fmodules-cache-path=" + module_cache, "-o", bin_path, script],
                capture_output=True, text=True, timeout=180
            )
            if compile_result.returncode == 0:
                _vision_bin = bin_path
            else:
                os.unlink(bin_path)
                _vision_bin = None
        except Exception:
            _vision_bin = None

    def _run_vision_once(image_bytes: bytes) -> tuple:
        """Run the Vision helper on one image; returns (text, error)."""
        suffix = os.path.splitext(_guess_image_ext(image_bytes))[1] or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name
        try:
            if _vision_bin:
                result = subprocess.run([_vision_bin, tmp_path], capture_output=True, text=True, timeout=90)
            else:
                result = subprocess.run(["swift", script, tmp_path], capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                text = result.stdout.strip()
                return (text, "" if text else "Vision 未识别出文字")
            return "", "Vision 识别失败: " + (result.stderr.strip()[-200:] or "未知错误")
        except subprocess.TimeoutExpired:
            return "", "Vision 识别超时（图片过大？）"
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # Slice tall images (e.g. long scrolling screenshots) into chunks — Vision
    # returns "no text recognized" on very tall images. Chunk height 4000px.
    CHUNK_H = 4000
    try:
        from PIL import Image as _PILImg
        probe = _PILImg.open(io.BytesIO(file_bytes))
        w, h = probe.size
    except Exception:
        probe = None
        w = h = 0

    if probe is not None and h > CHUNK_H:
        chunks = []
        y = 0
        idx = 0
        while y < h:
            y2 = min(y + CHUNK_H, h)
            # Overlap by 100px so a text line at the boundary is not split
            y_start = max(0, y - 100) if idx > 0 else y
            y_end = y2
            crop = probe.crop((0, y_start, w, y_end))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            text, err = _run_vision_once(buf.getvalue())
            if text:
                chunks.append(text)
            y = y2
            idx += 1
        if chunks:
            return ("\n".join(chunks), "")
        return ("", "Vision 对长图切片识别均失败")

    try:
        return _run_vision_once(file_bytes)
    except Exception as e:
        return "", "Vision 识别异常: " + str(e)


def _guess_image_ext(data: bytes) -> str:
    """Guess image extension from magic bytes so Vision can decode it properly."""
    if data[:8] == bytes.fromhex("89504e470d0a1a0a"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"\x00\x00\x01\x00":
        return ".ico"
    return ".png"


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

    # Upscale small images to ~300 DPI equivalent; but cap the total size —
    # Tesseract fails with "Image too large" on very tall scrolling screenshots.
    w, h = img.size
    if max(w, h) < 2400:
        scale = 2400 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), _Image.LANCZOS)
    # Cap longest edge at 8000px (long scroll screenshots are sliced by rows later)
    w, h = img.size
    if max(w, h) > 8000:
        scale = 8000 / max(w, h)
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
    text = pytesseract.image_to_string(img, lang=lang, config="--oem 1 --psm 6", timeout=45)
    return text


# --- Field extraction from OCR text ---

DATE_PATTERNS = [
    re.compile(r"(\d{4})[:：年/-](\d{1,2})[:：月/-](\d{1,2})[日]?"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),  # 20250521 compact
    re.compile(r"(\d{4})(\d{2})[-:/](\d{2})"),  # 202402-20 (Vision format)
]

# Match a whole amount token before normalizing separators. Never take a
# partial decimal from a dot-separated thousands amount (12.000.00).
AMOUNT_TOKEN = re.compile(
    r"(?<![\d.,])([+\-]?\s*[¥￥$]?\s*\d[\d,.]*)(?![\d.,])"
)

STOCK_CODE = re.compile(r"\b(\d{6})\b")  # A-share 6-digit code
STOCK_TICKER = re.compile(r"\b([A-Z]{1,5})\b")  # US stock ticker
# A fund name can mix Chinese, acronyms, index digits and a share class.
# Keep the full token: e.g. 博时黄金ETF联接C or 南方中证500ETF联接A.
FUND_NAME = re.compile(r"([一-鿿]{4,40}(?:[A-Za-z0-9（(][一-鿿A-Za-z0-9（）)·-]{0,60})?)")


def _fund_title(line):
    """Recognize a standalone fund heading, never a transaction/amount line."""
    line = re.sub(r"[（(]\d{6}[）)]", "", line).strip()
    line = re.sub(r"\s*(ETF|LOF|FOF|QDII|REITs)\s*", r"\1", line)
    line = re.sub(r"(?<=[一-鿿）)])\s+([A-E])(?=\s|$)", r"\1", line)
    if not any(word in line for word in ('基金', 'ETF', 'LOF', 'FOF', 'QDII', '联接', '指数', '混合', '债券')):
        return None
    if line in ('基金名称', '基金详情', '基金代码'):
        return None
    return line if FUND_NAME.fullmatch(line) else None

TYPE_KEYWORDS = {
    "买入": ["买入", "申购", "买", "认购", "buy", "定投"],
    "卖出": ["卖出", "赎回", "卖", "sell"],
    "分红入账": ["分红", "红利", "股利", "dividend", "派息"],
    "股息再投资": ["红利再投", "红利转投", "drip", "再投资"],
}

CANCEL_KEYWORDS = ["已撤回", "已撤销", "撤单", "已取消", "失败", "已退款", "交易关闭", "交易失败"]

SHARES_AFTER = re.compile(r"(?:数量|成交数量|股数|份额|shares|qty)\s*[：:]?\s*([\d,]+\.?\d*)", re.IGNORECASE)
PRICE_AFTER = re.compile(r"(?:价格|成交价|成交价格|均价|price)\s*[：:]?\s*([\d,]+\.?\d*)", re.IGNORECASE)


def parse_trade_text(raw_text: str, include_incomplete: bool = False) -> dict:
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    full_text = " ".join(lines)

    # Extract global symbol from overall text
    global_symbol = _extract_symbol(lines, full_text)

    # Try multi-record extraction: each date starts a new record
    records = _extract_records(lines, full_text, global_symbol, include_incomplete)

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


def _extract_records(lines, full_text, global_symbol=None, include_incomplete=False):
    """Type-keyword-anchored extraction — more robust than date-anchored."""
    # Find type keyword positions
    type_spots = []
    for i, line in enumerate(lines):
        if _fund_title(line):
            continue  # e.g. 红利低波动ETF is a heading, not a dividend.
        t = _extract_type_from_lines([line])
        if t:
            type_spots.append((i, t, line))

    records = []
    used_dates = set()

    for spot_index, (ti, raw_type, type_line) in enumerate(type_spots):
        previous_type = type_spots[spot_index - 1][0] if spot_index else -1
        next_type = type_spots[spot_index + 1][0] if spot_index + 1 < len(type_spots) else len(lines)
        # Find nearest date within ±4 lines of this type
        date_val = None
        date_pos = None
        for offset in range(0, 5):
            for d in [ti + offset, ti - offset]:
                if previous_type < d < next_type and d not in used_dates:
                    dv = _extract_date_from_line(lines[d])
                    if dv:
                        date_val = dv
                        date_pos = d
                        break
            if date_val:
                break

        if not date_val and not include_incomplete:
            continue
        anchor = date_pos if date_pos is not None else ti

        # Build search window: type line through date line + 2 extra
        lo = min(ti, anchor)
        hi = min(next_type, max(ti, anchor) + (2 if date_val else 5))
        window = lines[lo:hi]
        window_text = " ".join(window)

        # Cancel check: only inspect this record's own lines (type → date),
        # NOT lines after the date — otherwise the next record's "交易关闭"
        # text gets swept into this window and this record is wrongly dropped.
        cancel_scope = lines[lo:max(ti, anchor) + 1]
        if any(kw in " ".join(cancel_scope) for kw in CANCEL_KEYWORDS):
            used_dates.add(date_pos)
            continue

        # Amount from window
        amts = _extract_all_amounts(window)
        amount_val = amts[0] if amts else None
        share_dividend = re.search(r"(?:分红|红利)\s*([\d,.]+)\s*份", type_line)
        if share_dividend:
            amount_val = None  # Units cannot be converted to money without a price.
        # Reserve this record's date even when its amount is missing.
        used_dates.add(date_pos)
        if amount_val is None and not include_incomplete:
            continue

        display_type = "买入" if raw_type == "定投" else raw_type

        if amount_val is not None and display_type in ("买入", "股息再投资"):
            amount_val = abs(amount_val)

        local_symbol = global_symbol or _extract_symbol(window, window_text)

        trade_time = None
        if date_pos is not None:
            clock_line = lines[date_pos]
            for pattern in DATE_PATTERNS:
                clock_line = pattern.sub(" ", clock_line)
            clock = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?:[:：]([0-5]\d))?(?!\d)", clock_line)
            if clock:
                trade_time = f"{int(clock.group(1)):02d}:{clock.group(2)}" + (f":{clock.group(3)}" if clock.group(3) else '')
        records.append({
            "date": date_val,
            "trade_time": trade_time,
            "symbol": local_symbol,
            "business_type": display_type,
            "cash_flow": amount_val,
            "shares": _extract_number(SHARES_AFTER, window, window_text),
            "price": _extract_number(PRICE_AFTER, window, window_text),
            **({'ocr_warning': '分红以份额显示，请核对分红方式并补充现金金额或跳过；不能将份额当作金额'} if share_dividend else {}),
        })

        used_dates.add(date_pos)

    # Fallback: date-anchored if no type keywords found
    if not type_spots:
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
                if 1900 <= y <= 2100:
                    return datetime(y, mth, d).strftime("%Y-%m-%d")
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
    matches = [(len(keyword), kind) for kind, keywords in TYPE_KEYWORDS.items()
               for keyword in keywords if keyword in text]
    if matches:
        return max(matches, key=lambda match: match[0])[1]
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
    """Read amounts without treating dates, times or fund codes as money."""
    candidates = []
    for original in lines:
        if _fund_title(original):
            continue
        line = original
        for pattern in DATE_PATTERNS:
            line = pattern.sub(" ", line)
        line = re.sub(r"\b\d{1,2}[:：]\d{2}(?:[:：]\d{2})?\b", " ", line)
        line = re.sub(r"(?:代码|证券代码|基金代码)\s*[:：]?\s*\d{6}", " ", line)
        if any(label in line for label in ("份额", "股数", "成交数量", "成交价", "价格")):
            continue
        line = re.sub(r"[+\-]?\d[\d,.]*\s*(?:份|股)", " ", line)
        # Accept standalone amounts and currency/type/amount-labelled lines.
        if re.search(r"[一-鿿]", line) and not (
            re.search(r"[¥￥$元]|金额|现金流", line) or _extract_type_from_lines([line])
        ):
            continue
        for match in AMOUNT_TOKEN.finditer(line):
            token = re.sub(r"[¥￥$\s]", "", match.group(1))
            if not re.fullmatch(r"[+\-]?(?:\d+|\d+\.\d{2}|\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{1,3}(?:\.\d{3})+\.\d{2})", token):
                continue
            value = _normalize_amount(token)
            if value is not None:
                candidates.append(abs(value))
    return candidates


def _extract_amounts_from_lines(lines):
    return _extract_all_amounts(lines)  # delegate to unified extractor


def _extract_symbol(lines, full_text):
    for line in lines:
        title = _fund_title(line)
        if title:
            return title
    # Prefer 6-digit stock code (exclude date-like patterns: 2020xx-2030xx)
    for line in lines:
        m = STOCK_CODE.search(line)
        if m:
            code = m.group(1)
            if not (code.startswith("202") or code.startswith("203")):
                return code
    # Fallback: ticker
    exclude = {"ETF", "LOF", "FOF", "QDII", "REITS", "A", "B", "C", "D", "E"}
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

    for line in lines:
        # Vision sometimes inserts spaces around acronyms or the share class.
        line = re.sub(r"\s*(ETF|LOF|FOF|QDII|REITs)\s*", r"\1", line)
        line = re.sub(r"(?<=[一-鿿）)])\s+([A-E])(?=\s|$)", r"\1", line)
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
