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

    result = ocr_image(contents)
    raw_text = result["text"]
    engine = result["engine"]

    if not raw_text:
        raise HTTPException(
            status_code=500,
            detail=f"OCR 识别失败（{engine or '无可用引擎'}）：{result['error']}。"
                   "Vision 为 macOS 内置能力，请确认系统正常；Tesseract 可另行安装作为兜底。",
        )

    parsed = parse_trade_text(raw_text)

    return {
        "success": True,
        "engine": engine,
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
