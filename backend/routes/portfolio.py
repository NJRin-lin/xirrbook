from fastapi import APIRouter
from backend.database import get_db
from backend.valuation import actual_cash_flow, valuation_amount

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("")
def get_portfolio():
    conn = get_db()
    rows = conn.execute("SELECT * FROM transactions ORDER BY date ASC, id ASC").fetchall()
    conn.close()

    holdings = {}
    cumulative = []
    running = 0.0
    latest_values = {}
    for r in rows:
        sym = r["symbol"]
        h = holdings.setdefault(sym, {
            "symbol": sym, "shares": 0.0, "cost_basis": 0.0,
            "shares_known": True, "dividends": 0.0,
            "invested": 0.0, "sale_proceeds": 0.0, "net_cashflow": 0.0,
        })
        cash = actual_cash_flow(r)
        if r["business_type"] in ("买入", "股息再投资"):
            h["invested"] += abs(cash)
            h["cost_basis"] += abs(cash)
            if r["shares"] is None:
                h["shares_known"] = False
            else:
                h["shares"] += r["shares"]
        elif r["business_type"] == "卖出":
            h["sale_proceeds"] += cash
            if r["shares"] is None:
                h["shares_known"] = False
            elif h["shares"] > 0:
                # Moving average cost: a sale removes the sold shares' cost.
                sold = min(r["shares"], h["shares"])
                h["cost_basis"] *= 1 - sold / h["shares"]
                h["shares"] -= sold
        elif r["business_type"] == "分红入账":
            h["dividends"] += cash

        value = valuation_amount(r)
        if value is not None:
            latest_values[(r["account_id"], sym)] = value
        h["net_cashflow"] += cash
        running += cash
        cumulative.append({"date": r["date"], "cash_flow": cash, "running_total": round(running, 2)})

    holding_list = []
    for sym, h in holdings.items():
        values = [value for (_, symbol), value in latest_values.items() if symbol == sym]
        mv = sum(values) if values else None
        known = h["shares_known"]
        shares = h["shares"] if known else None
        cost = round(h["cost_basis"], 2) if known else None
        holding_list.append({
            "symbol": sym,
            "shares": shares,
            "avg_cost": round(cost / shares, 2) if known and shares > 0 else None,
            "cost_basis": cost,
            "net_invested": round(h["invested"] - h["sale_proceeds"], 2),
            "market_value": mv,
            "unrealized_pl": round(mv - cost, 2) if mv is not None and cost is not None else None,
            "total_pl": round(mv + h["net_cashflow"], 2) if mv is not None else None,
            "dividends": round(h["dividends"], 2),
            "is_active": (shares > 0.001 if known else (mv > 0 if mv is not None else h["invested"] > 0)),
        })

    invested = -sum(actual_cash_flow(r) for r in rows if actual_cash_flow(r) < 0)
    returned = sum(actual_cash_flow(r) for r in rows if actual_cash_flow(r) > 0)
    return {
        "holdings": holding_list,
        "cumulative": cumulative,
        "summary": {"total_invested": invested, "total_returned": returned, "net_cashflow": returned - invested},
    }
