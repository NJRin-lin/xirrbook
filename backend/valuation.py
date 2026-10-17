"""Interpret valuation snapshots without rewriting historical records."""
import math

VALUATION_TYPES = {"当前市值", "市值更新"}


def is_valuation(row):
    return row["business_type"] in VALUATION_TYPES


def actual_cash_flow(row):
    return 0.0 if is_valuation(row) else row["cash_flow"]


def valuation_amount(row):
    value = row["market_value"]
    # Older clients stored snapshots as positive cash flows.
    if value is None and is_valuation(row):
        value = row["cash_flow"]
    return value


def normalize_valuation(data):
    data = dict(data)
    if not data.get("business_type") and data.get("market_value") is not None:
        data["business_type"] = "当前市值"
    if data.get("business_type") in VALUATION_TYPES:
        value = data.get("market_value")
        if value is None or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError("当前市值必须填写大于或等于 0 的有效金额")
        data.update(business_type="当前市值", cash_flow=0.0)
    return data
