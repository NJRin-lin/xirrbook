from fastapi import APIRouter, File, HTTPException, UploadFile

from starlette.concurrency import run_in_threadpool
from backend.ocr_parser import ocr_image, parse_trade_text
from backend.ocr_batch import review_batch, import_batch

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

    try:
        result = await run_in_threadpool(ocr_image, contents)
    except Exception as exc:
        raise HTTPException(422, detail="图片识别失败，请检查图片格式或重试：" + str(exc)[:200])
    raw_text = result["text"]
    engine = result["engine"]

    if not raw_text:
        raise HTTPException(
            status_code=422,
            detail=f"OCR 未识别到有效文字：{result['error']}。请使用清晰、包含完整交易记录的截图后重试。",
        )

    parsed = parse_trade_text(raw_text, include_incomplete=True)

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


@router.post("/review")
def review_records(payload: dict):
    return review_batch(payload)


@router.post("/import")
def import_records(payload: dict):
    return import_batch(payload)
