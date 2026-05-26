from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.ocr_parser import ocr_image, parse_trade_text

router = APIRouter(prefix="/ocr", tags=["ocr"])


@router.post("")
async def ocr_upload(file: UploadFile = File(...)):
    if not file.filename or not any(file.filename.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
        raise HTTPException(status_code=400, detail="仅支持 PNG/JPG/GIF/BMP/WEBP 格式的图片")

    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 20MB")

    raw_text = ocr_image(contents)

    if not raw_text:
        raise HTTPException(status_code=500, detail="OCR 识别失败，请确认 Tesseract 已安装")

    parsed = parse_trade_text(raw_text)

    return {
        "success": True,
        "raw_text": raw_text.strip(),
        "parsed": {
            "date": parsed.get("date"),
            "symbol": parsed.get("symbol"),
            "business_type": parsed.get("business_type"),
            "cash_flow": parsed.get("cash_flow"),
            "shares": parsed.get("shares"),
            "price": parsed.get("price"),
        },
        "records": parsed.get("records", []),
        "raw_lines": parsed.get("raw_lines", []),
    }
