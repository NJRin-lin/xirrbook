from fastapi import APIRouter
from backend.database import get_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("")
def get_portfolio():
    conn = get_db()
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT date, symbol, business_type, cash_flow, shares, price, market_value FROM transactions ORDER BY date ASC"
    ).fetchall()
    conn.close()

    holdings = {}  # symbol -> {buy_shares, cur_shares, total_cost, dividends, market_value}
    cumulative = []  # [{date, cash_flow, running_total}]
    running = 0.0

    for r in rows:
        sym = r["symbol"]
        if sym not in holdings:
            holdings[sym] = {
                "symbol": sym,
                "buy_shares": 0.0,
                "cur_shares": 0.0,
                "total_cost": 0.0,
                "dividends": 0.0,
                "market_value": None,
            }

        h = holdings[sym]

        if r["business_type"] in ("买入", "股息再投资"):
            if r["shares"]:
                h["buy_shares"] += r["shares"]
                h["cur_shares"] += r["shares"]
            h["total_cost"] += abs(r["cash_flow"])
        elif r["business_type"] == "卖出":
            if r["shares"]:
                h["cur_shares"] -= r["shares"]
        elif r["business_type"] == "分红入账":
            h["dividends"] += r["cash_flow"]

        if r["market_value"] is not None:
            h["market_value"] = r["market_value"]

        running += r["cash_flow"]
        cumulative.append({
            "date": r["date"],
            "cash_flow": r["cash_flow"],
            "running_total": round(running, 2),
        })

    holding_list = []
    for sym, h in holdings.items():
        avg_cost = h["total_cost"] / h["buy_shares"] if h["buy_shares"] > 0 else 0.0
        cost_basis = round(h["cur_shares"] * avg_cost, 2)
        mv = h["market_value"]
        if mv is not None and h["cur_shares"] > 0:
            unrealized_pl = round(mv - cost_basis, 2)
        else:
            unrealized_pl = None

        holding_list.append({
            "symbol": h["symbol"],
            "shares": h["cur_shares"],
            "avg_cost": round(avg_cost, 2),
            "cost_basis": cost_basis,
            "market_value": mv,
            "unrealized_pl": unrealized_pl,
            "dividends": round(h["dividends"], 2),
        })

    total_invested = sum(r["cash_flow"] for r in rows if r["cash_flow"] < 0)
    total_returned = sum(r["cash_flow"] for r in rows if r["cash_flow"] > 0)

    return {
        "holdings": holding_list,
        "cumulative": cumulative,
        "summary": {
            "total_invested": abs(total_invested),
            "total_returned": total_returned,
            "net_cashflow": total_returned + total_invested,
        },
    }
