from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from scipy.optimize import newton

from backend.database import get_db

router = APIRouter(prefix="/xirr", tags=["xirr"])


def xirr(cashflows: list) -> Optional[float]:
    """Calculate XIRR given a list of (date_str, amount) tuples.

    Returns the annualized rate as a decimal (e.g. 0.15 = 15%), or None if
    the calculation does not converge or there are insufficient cash flows.
    """
    if len(cashflows) < 2:
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
        # Guard: (1+rate) must stay positive for meaningful power calculations
        base = max(1e-10, 1 + rate)
        return sum(amt / base ** t for t, amt in zip(times, amounts))

    # Secant method (no fprime) — more robust near singularities.
    # Try diverse initial guesses from deeply negative to high positive.
    for guess in [0.1, -0.3, -0.6, -0.8, -0.9, -0.95, 0.3, 0.5, 0.0, -0.98]:
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
    conn = get_db()
    cursor = conn.cursor()

    where = ["1=1"]
    params = []
    if symbol:
        where.append("symbol LIKE ?")
        params.append(f"%{symbol}%")
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)

    rows = cursor.execute(
        f"SELECT date, cash_flow FROM transactions WHERE {' AND '.join(where)} ORDER BY date ASC",
        params,
    ).fetchall()
    conn.close()

    cashflows = [(r["date"], r["cash_flow"]) for r in rows]
    rate = xirr(cashflows)

    total_invested = sum(cf for _, cf in cashflows if cf < 0)
    total_returned = sum(cf for _, cf in cashflows if cf > 0)

    return {
        "xirr": rate,
        "total_invested": abs(total_invested),
        "total_returned": total_returned,
        "net_cashflow": total_returned + total_invested,
        "cashflow_count": len(cashflows),
    }
