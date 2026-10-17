from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from scipy.optimize import newton

from backend.database import get_db
from backend.period_returns import build_period
from backend.valuation import actual_cash_flow, valuation_amount

router = APIRouter(prefix="/xirr", tags=["xirr"])


def xirr(cashflows: list) -> Optional[float]:
    """Calculate XIRR given a list of (date_str, amount) tuples.

    Returns the annualized rate as a decimal (e.g. 0.15 = 15%), or None if
    the calculation does not converge or there are insufficient cash flows.
    """
    if len(cashflows) < 2 or len({date for date, _ in cashflows}) < 2:
        return None

    amounts = [cf[1] for cf in cashflows]
    if all(a <= 0 for a in amounts) or all(a >= 0 for a in amounts):
        return None

    ref_date = datetime.strptime(cashflows[0][0], "%Y-%m-%d")
    times = []
    for date_str, _ in cashflows:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        times.append((d - ref_date).days / 365.0)

    def npv(rate):
        base = 1 + rate
        if base <= 0:
            return float('inf')
        return sum(amt / base ** t for t, amt in zip(times, amounts))

    # Try bounded brentq first (most robust), then newton secant fallback
    try:
        from scipy.optimize import brentq
        rate = brentq(npv, -0.9999, 10.0, maxiter=200, xtol=1e-8)
        if -0.999 < rate < 100:
            return rate
    except (ValueError, RuntimeError, OverflowError, ZeroDivisionError):
        pass

    for guess in [0.1, -0.3, -0.6, -0.8, -0.9, -0.95, 0.3, 0.5, 0.0, -0.98, 2.0]:
        try:
            rate = newton(npv, guess, maxiter=200, tol=1e-8)
            if -0.999 < rate < 100:
                return rate
        except (RuntimeError, OverflowError, ZeroDivisionError):
            continue

    return None


@router.get("")
def get_xirr(
    start_date: str = Query(None, description="YYYY-MM-DD"),
    end_date: str = Query(None, description="YYYY-MM-DD"),
    symbol: str = Query(None),
):
    period = bool(start_date or end_date)
    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else date.today()
        if start and (start > end or start == date.min):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, detail="请填写有效日期，起始日期不能晚于结束日期")

    conn = get_db()
    where = ["1=1"]
    params = []
    if symbol:
        where.append("symbol LIKE ?")
        params.append(f"%{symbol}%")
    if period:
        where.append("date <= ?")
        params.append(end.isoformat())
    try:
        rows = conn.execute(
            f"SELECT * FROM transactions WHERE {' AND '.join(where)} ORDER BY date ASC, id ASC", params,
        ).fetchall()
    finally:
        conn.close()

    if period:
        if start is None:
            start = date.fromisoformat(rows[0]['date']) if rows else end
            if start == date.min:
                raise HTTPException(422, detail="起始日期超出支持范围")
        result = build_period(rows, start, end)
        flows = result.pop('cashflows')
        result['xirr'] = xirr(flows) if result['status'] == 'ready' else None
        if result['status'] == 'ready' and result['xirr'] is None:
            result['status'] = 'unavailable'
        return result

    # Unfiltered lifetime calculation keeps the recorded date of each latest value.
    actual = [(r["date"], actual_cash_flow(r)) for r in rows if actual_cash_flow(r) != 0]
    latest = {}
    for row in rows:
        value = valuation_amount(row)
        if value is not None:
            latest[(row["account_id"], row["symbol"])] = (row["date"], value)
    cashflows = sorted(actual + list(latest.values()))
    rate = xirr(cashflows)
    total_invested = sum(cf for _, cf in actual if cf < 0)
    total_returned = sum(cf for _, cf in actual if cf > 0)
    return {
        "mode": "lifetime", "xirr": rate,
        "total_invested": abs(total_invested), "total_returned": total_returned,
        "net_cashflow": total_returned + total_invested,
        "cashflow_count": len(cashflows), "market_value": sum(value for _, value in latest.values()),
    }
