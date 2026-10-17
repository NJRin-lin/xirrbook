"""Local OCR review and atomic import; screenshots themselves are not stored."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import uuid

from fastapi import HTTPException

from backend.database import get_db
from backend.valuation import actual_cash_flow, normalize_valuation, valuation_amount

TYPES = {"买入", "卖出", "分红入账", "股息再投资", "当前市值"}


def amount(value):
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError("金额缺失或格式不正确")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal('1000000000000'):
            raise ValueError("金额超出有效范围")
        if number != number.quantize(Decimal('.01')):
            raise ValueError("金额最多保留两位小数")
        return float(number)
    except (InvalidOperation, TypeError):
        raise ValueError("金额格式不正确")


def validate_record(raw):
    errors = []
    normalized = {}
    try:
        text = raw.get('date')
        if not isinstance(text, str) or date.fromisoformat(text).isoformat() != text:
            raise ValueError()
        normalized['date'] = text
    except (ValueError, TypeError):
        errors.append('请填写有效日期（YYYY-MM-DD）')
    symbol = raw.get('symbol')
    if not isinstance(symbol, str) or not symbol.strip() or len(symbol.strip()) > 200:
        errors.append('请填写标的（最多 200 字）')
    else:
        normalized['symbol'] = symbol.strip()
    kind = raw.get('business_type')
    if not isinstance(kind, str) or kind not in TYPES:
        errors.append('请选择业务类型')
    else:
        normalized['business_type'] = kind
    try:
        if kind == '当前市值':
            value = amount(raw.get('market_value'))
            if value < 0:
                raise ValueError('市值不能为负数')
            normalized.update(cash_flow=0.0, market_value=value)
        else:
            value = amount(raw.get('cash_flow'))
            if value == 0:
                raise ValueError('交易金额必须大于 0')
            normalized.update(cash_flow=-abs(value) if kind in ('买入', '股息再投资') else abs(value), market_value=None)
    except ValueError as exc:
        errors.append(str(exc))
    notes = raw.get('notes')
    if notes is not None and (not isinstance(notes, str) or len(notes) > 2000):
        errors.append('备注最多 2000 字')
    else:
        normalized['notes'] = notes
    normalized['account_id'] = 1
    return normalized, errors


def signature(record):
    # Cents avoid binary floating-point differences in duplicate comparisons.
    kind = record['business_type']
    value = record['market_value'] if kind == '当前市值' else record['cash_flow']
    return (record.get('account_id', 1), record['date'], record['symbol'], kind,
            Decimal(str(value)).quantize(Decimal('.01')))


def read_records(payload):
    records = payload.get('records')
    if not isinstance(records, list) or not 1 <= len(records) <= 3000 or any(not isinstance(r, dict) for r in records):
        raise HTTPException(422, '每批请提交 1 至 3000 条记录')
    return records


def review(records, conn):
    normalized = []
    groups = defaultdict(list)
    existing = defaultdict(list)
    for row in conn.execute('SELECT * FROM transactions'):
        item = dict(row)
        item['cash_flow'] = actual_cash_flow(row)
        item['market_value'] = valuation_amount(row)
        if item['business_type'] == '市值更新':
            item['business_type'] = '当前市值'
        try:
            existing[signature(item)].append(item['id'])
        except (ValueError, TypeError, InvalidOperation):
            continue
    results = []
    for index, raw in enumerate(records):
        item, errors = validate_record(raw)
        normalized.append(item)
        result = {'index': index, 'errors': errors, 'database_ids': [], 'batch_indexes': []}
        if not errors:
            key = signature(item)
            groups[key].append(index)
            result['database_ids'] = existing.get(key, [])
        results.append(result)
    for indexes in groups.values():
        if len(indexes) > 1:
            for index in indexes:
                results[index]['batch_indexes'] = [other for other in indexes[:11] if other != index][:10]
                results[index]['batch_count'] = len(indexes)
    return results, normalized


def review_batch(payload):
    records = read_records(payload)
    conn = get_db()
    try:
        results, _ = review(records, conn)
        return {'records': results}
    finally:
        conn.close()


def import_batch(payload):
    records = read_records(payload)
    request_id = payload.get('request_id')
    try:
        uuid.UUID(request_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, '导入请求缺少有效批次编号')
    digest = hashlib.sha256(json.dumps(records, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    conn = get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute('SELECT payload_hash, result FROM ocr_import_batches WHERE request_id=?', (request_id,)).fetchone()
        if previous:
            if previous['payload_hash'] != digest:
                raise HTTPException(409, '批次内容已变化，请重新核对后导入')
            return json.loads(previous['result'])
        # Explicitly skipped rows must not create duplicate conflicts for retained rows.
        kept = [r for r in records if r.get('decision') == 'keep']
        if any(r.get('decision') not in ('keep', 'skip') for r in records):
            raise HTTPException(422, '请为每条记录选择保留或跳过')
        if not kept:
            raise HTTPException(422, '没有选择要导入的记录')
        results, normalized = review(kept, conn)
        conflicts = []
        for raw, result in zip(kept, results):
            if result['errors']:
                raise HTTPException(422, {'message': '仍有字段需要修正，尚未写入任何记录', 'records': results})
            if (result['database_ids'] or result['batch_indexes']) and raw.get('allow_duplicate') is not True:
                conflicts.append(result)
        if conflicts:
            raise HTTPException(409, {'message': '发现疑似重复，请重新核对并明确选择保留或跳过，尚未写入任何记录', 'records': conflicts})
        for item in normalized:
            item = normalize_valuation(item)
            conn.execute(
                'INSERT INTO transactions(account_id,date,symbol,business_type,cash_flow,market_value,notes) VALUES(?,?,?,?,?,?,?)',
                tuple(item.get(field) for field in ('account_id','date','symbol','business_type','cash_flow','market_value','notes')),
            )
        result = {'imported': len(kept), 'skipped': len(records) - len(kept)}
        conn.execute('INSERT INTO ocr_import_batches(request_id,payload_hash,result) VALUES(?,?,?)',
                     (request_id, digest, json.dumps(result)))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
