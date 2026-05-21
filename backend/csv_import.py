import csv
import io
from typing import Optional

COLUMN_ALIASES = {
    "date": ["日期", "date", "交易日期", "trade date", "时间"],
    "symbol": ["标的", "symbol", "代码", "code", "ticker", "stock code", "名称", "name", "证券名称", "证券代码"],
    "business_type": ["业务类型", "business_type", "类型", "type", "交易类型", "trade type", "操作", "operation"],
    "cash_flow": ["现金流", "cash_flow", "金额", "amount", "发生金额", "资金", "成交金额"],
    "shares": ["股数", "shares", "数量", "quantity", "qty", "成交数量"],
    "price": ["成交价", "price", "价格", "trade price", "成交价格"],
    "market_value": ["市值", "market_value", "market value"],
    "notes": ["备注", "notes", "note", "说明", "remark", "摘要"],
}

BUSINESS_TYPE_MAP = {
    "买入": "买入", "buy": "买入", "买": "买入",
    "卖出": "卖出", "sell": "卖出", "卖": "卖出",
    "分红入账": "分红入账", "dividend": "分红入账", "分红": "分红入账", "股利": "分红入账",
    "股息再投资": "股息再投资", "drip": "股息再投资", "再投资": "股息再投资",
}


def auto_map_columns(headers: list[str]) -> dict[str, Optional[str]]:
    """Auto-detect column mapping from CSV headers. Returns {field: header_name}."""
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for h in headers:
            if h.strip().lower() in [a.lower() for a in aliases]:
                mapping[field] = h
                break
    return mapping


def parse_and_validate(
    content: str,
    column_mapping: Optional[dict] = None,
) -> dict:
    """Parse CSV content, apply column mapping, validate rows, return results.

    Returns:
        {
            "headers": [...],
            "rows": [{...}, ...],          # parsed rows
            "errors": [{"row": N, "msg": "..."}],
            "mapping": {field: header},     # applied mapping
        }
    """
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []

    if column_mapping:
        mapping = {k: v for k, v in column_mapping.items() if v in headers}
    else:
        mapping = auto_map_columns(headers)

    rows = []
    errors = []

    for i, raw_row in enumerate(reader):
        row_num = i + 2  # 1-indexed + header row
        parsed = {}
        row_errors = []

        # Map and validate date
        date_hdr = mapping.get("date")
        if date_hdr and raw_row.get(date_hdr):
            val = raw_row[date_hdr].strip()
            # Normalize date formats
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d"]:
                try:
                    from datetime import datetime
                    parsed["date"] = datetime.strptime(val, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if "date" not in parsed:
                row_errors.append(f"无法解析日期: {val}")
        else:
            row_errors.append("缺少日期")

        # Map symbol
        sym_hdr = mapping.get("symbol")
        if sym_hdr and raw_row.get(sym_hdr):
            parsed["symbol"] = raw_row[sym_hdr].strip()
        else:
            row_errors.append("缺少标的")

        # Map business_type
        bt_hdr = mapping.get("business_type")
        if bt_hdr and raw_row.get(bt_hdr):
            raw_type = raw_row[bt_hdr].strip()
            parsed["business_type"] = BUSINESS_TYPE_MAP.get(raw_type.lower(), raw_type)
        else:
            row_errors.append("缺少业务类型")

        # Map cash_flow (required)
        cf_hdr = mapping.get("cash_flow")
        if cf_hdr and raw_row.get(cf_hdr):
            try:
                val = raw_row[cf_hdr].strip().replace(",", "").replace("￥", "").replace("$", "")
                parsed["cash_flow"] = float(val)
            except ValueError:
                row_errors.append(f"现金流格式错误: {raw_row[cf_hdr]}")
        else:
            row_errors.append("缺少现金流")

        # Optional fields
        for opt_field in ["shares", "price", "market_value"]:
            hdr = mapping.get(opt_field)
            if hdr and raw_row.get(hdr):
                try:
                    val = raw_row[hdr].strip().replace(",", "")
                    if val:
                        parsed[opt_field] = float(val)
                except ValueError:
                    pass

        # Map notes
        notes_hdr = mapping.get("notes")
        if notes_hdr and raw_row.get(notes_hdr):
            parsed["notes"] = raw_row[notes_hdr].strip()

        if row_errors:
            errors.append({"row": row_num, "msgs": row_errors})
        else:
            rows.append(parsed)

    return {
        "headers": headers,
        "rows": rows,
        "errors": errors,
        "mapping": mapping,
    }
