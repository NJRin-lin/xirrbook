"""Build period cash flows from recorded boundary values, without writing trades.

The transaction interval is inclusive [start, end]. Opening valuation is the
closing value on start - 1 day; closing valuation is on end. A stale positive
valuation is never carried to a boundary as if it were a known market value.
"""
from collections import defaultdict
from datetime import timedelta
import math

from backend.valuation import actual_cash_flow, valuation_amount


def boundary_value(rows, boundary, allow_new=False):
    """Return value plus provenance, or None when a valuation is required."""
    previous = [row for row in rows if row['date'] <= boundary]
    snapshots = [row for row in previous if valuation_amount(row) is not None]
    latest = snapshots[-1] if snapshots else None
    if latest is not None and latest['date'] == boundary:
        value = valuation_amount(latest)
        if math.isfinite(value) and value >= 0:
            return {'date': boundary, 'value': value, 'source': 'exact', 'record_id': latest['id']}
    # An explicitly empty position remains empty until another cash movement.
    # This is conservative: a cash-only sale alone cannot prove full liquidation.
    flat = False
    zero_id = None
    for row in previous:
        if actual_cash_flow(row) != 0:
            flat = False
        value = valuation_amount(row)
        if value is not None:
            flat = value == 0
            zero_id = row['id'] if flat else None
    if flat:
        return {'date': boundary, 'value': 0.0, 'source': 'known_zero', 'record_id': zero_id}
    if allow_new and not previous:
        return {'date': boundary, 'value': 0.0, 'source': 'new_investment', 'record_id': None}
    return None


def build_period(rows, start, end):
    opening_date = (start - timedelta(days=1)).isoformat()
    start_text, end_text = start.isoformat(), end.isoformat()
    groups = defaultdict(list)
    for row in rows:
        if row['date'] <= end_text:
            groups[(row['account_id'], row['symbol'])].append(row)
    flows, actual, missing, components = [], [], [], []
    for (account_id, symbol), history in groups.items():
        history.sort(key=lambda row: (row['date'], row['id']))
        activity = [row for row in history if actual_cash_flow(row) != 0]
        inside = [row for row in activity if start_text <= row['date'] <= end_text]
        # No earlier records plus a first outflow supports a zero initial holding.
        # First observed inflows/valuations alone do not prove a zero opening value.
        new_investment = bool(activity and actual_cash_flow(activity[0]) < 0)
        if new_investment:
            first_key = (activity[0]['date'], activity[0]['id'])
            if any((row['date'], row['id']) < first_key and (valuation_amount(row) or 0) > 0 for row in history):
                new_investment = False
        opening = boundary_value(history, opening_date, allow_new=new_investment)
        closing = boundary_value(history, end_text)
        if opening and closing and opening['value'] == closing['value'] == 0 and not inside:
            continue
        component = {'account_id': account_id, 'symbol': symbol, 'opening': opening, 'closing': closing}
        components.append(component)
        for kind, boundary, value in [('opening', opening_date, opening), ('closing', end_text, closing)]:
            if value is None:
                missing.append({'account_id': account_id, 'symbol': symbol, 'kind': kind, 'date': boundary})
            else:
                flows.append((boundary, -value['value'] if kind == 'opening' else value['value']))
        actual.extend((row['date'], actual_cash_flow(row)) for row in inside)
    flows.extend(actual)
    opening_complete = all(component['opening'] is not None for component in components)
    closing_complete = all(component['closing'] is not None for component in components)
    opening_total = sum(component['opening']['value'] for component in components) if opening_complete else None
    closing_total = sum(component['closing']['value'] for component in components) if closing_complete else None
    invested = -sum(value for _, value in actual if value < 0)
    returned = sum(value for _, value in actual if value > 0)
    return {
        'mode': 'period', 'status': 'missing_valuations' if missing else 'ready',
        'start_date': start_text, 'end_date': end_text, 'opening_date': opening_date,
        'opening_market_value': opening_total, 'market_value': closing_total,
        'total_invested': invested, 'total_returned': returned, 'net_cashflow': returned - invested,
        'period_profit': round(closing_total + returned - invested - opening_total, 2) if not missing else None,
        'cashflow_count': len(flows), 'missing_valuations': missing, 'valuations': components,
        'cashflows': sorted(flows),
    }
