import csv
import io
import json

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from backend.csv_import import parse_and_validate
from backend.database import get_db

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("")
def list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    symbol: str = Query(None),
    business_type: str = Query(None),
    start_date: str = Query(None, description="YYYY-MM-DD"),
    end_date: str = Query(None, description="YYYY-MM-DD"),
):
    conn = get_db()
    cursor = conn.cursor()

    where = ["1=1"]
    params = []

    if symbol:
        where.append("symbol LIKE ?")
        params.append(f"%{symbol}%")
    if business_type:
        where.append("business_type = ?")
        params.append(business_type)
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)

    count_sql = f"SELECT COUNT(*) FROM transactions WHERE {' AND '.join(where)}"
    total = cursor.execute(count_sql, params).fetchone()[0]

    offset = (page - 1) * page_size
    sql = f"SELECT * FROM transactions WHERE {' AND '.join(where)} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?"
    rows = cursor.execute(sql, params + [page_size, offset]).fetchall()

    conn.close()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows],
    }


@router.get("/export")
def export_csv(
    symbol: str = Query(None),
    business_type: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    conn = get_db()
    cursor = conn.cursor()

    where = ["1=1"]
    params = []
    if symbol:
        where.append("symbol LIKE ?")
        params.append(f"%{symbol}%")
    if business_type:
        where.append("business_type = ?")
        params.append(business_type)
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)

    rows = cursor.execute(
        f"SELECT date, symbol, business_type, cash_flow, shares, price, market_value, notes FROM transactions WHERE {' AND '.join(where)} ORDER BY date ASC",
        params,
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日期", "标的", "业务类型", "现金流", "股数", "成交价", "市值", "备注"])
    for r in rows:
        writer.writerow(list(r))

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@router.get("/{transaction_id}")
def get_transaction(transaction_id: int):
    conn = get_db()
    cursor = conn.cursor()
    row = cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    return dict(row)


@router.post("", status_code=201)
def create_transaction(data: dict):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO transactions (account_id, date, symbol, business_type, cash_flow, shares, price, market_value, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("account_id", 1),
            data["date"],
            data["symbol"],
            data["business_type"],
            data["cash_flow"],
            data.get("shares"),
            data.get("price"),
            data.get("market_value"),
            data.get("notes"),
        ),
    )
    conn.commit()
    new_id = cursor.lastrowid
    row = cursor.execute("SELECT * FROM transactions WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    return dict(row)


@router.post("/import")
async def import_csv(
    file: UploadFile = File(...),
    mapping: str = Form(None),
):
    content = (await file.read()).decode("utf-8-sig")
    col_mapping = json.loads(mapping) if mapping else None

    result = parse_and_validate(content, col_mapping)

    if not result["rows"]:
        return {
            "imported": 0,
            "skipped": 0,
            "errors": result["errors"],
            "mapping": result["mapping"],
            "headers": result["headers"],
        }

    conn = get_db()
    cursor = conn.cursor()
    imported = 0
    skipped = 0

    for row in result["rows"]:
        # Dedup: same date + symbol + business_type + cash_flow
        existing = cursor.execute(
            "SELECT id FROM transactions WHERE date=? AND symbol=? AND business_type=? AND cash_flow=?",
            (row["date"], row["symbol"], row["business_type"], row["cash_flow"]),
        ).fetchone()

        if existing:
            skipped += 1
            continue

        cursor.execute(
            """INSERT INTO transactions (date, symbol, business_type, cash_flow, shares, price, market_value, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["date"],
                row["symbol"],
                row["business_type"],
                row["cash_flow"],
                row.get("shares"),
                row.get("price"),
                row.get("market_value"),
                row.get("notes"),
            ),
        )
        imported += 1

    conn.commit()
    conn.close()

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": result["errors"],
        "mapping": result["mapping"],
        "headers": result["headers"],
    }


@router.put("/{transaction_id}")
def update_transaction(transaction_id: int, data: dict):
    conn = get_db()
    cursor = conn.cursor()
    existing = cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="记录不存在")

    allowed = ["date", "symbol", "business_type", "cash_flow", "shares", "price", "market_value", "notes", "account_id"]
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        conn.close()
        return dict(existing)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [transaction_id]
    cursor.execute(f"UPDATE transactions SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/{transaction_id}")
def delete_transaction(transaction_id: int):
    conn = get_db()
    cursor = conn.cursor()
    existing = cursor.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="记录不存在")
    cursor.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    conn.commit()
    conn.close()
    return {"deleted": transaction_id}
