import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from backend import database
from backend.csv_import import parse_and_validate
from backend.ocr_parser import _clean_ocr_text, _extract_all_amounts, parse_trade_text, ocr_image
from backend.routes import portfolio, transactions, xirr, ocr


class FinancialRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = [patch.object(database, 'DB_DIR', self.tmp.name),
                      patch.object(database, 'DB_PATH', os.path.join(self.tmp.name, 'test.db'))]
        for item in self.paths:
            item.start()
        database.init_db()

    def tearDown(self):
        for item in reversed(self.paths):
            item.stop()
        self.tmp.cleanup()

    def add(self, date, kind, cash, value=None, shares=None, symbol='TEST'):
        conn = database.get_db()
        conn.execute('INSERT INTO transactions(date,symbol,business_type,cash_flow,market_value,shares) VALUES(?,?,?,?,?,?)',
                     (date, symbol, kind, cash, value, shares))
        conn.commit()
        conn.close()

    def metrics(self, **kwargs):
        return xirr.get_xirr(**dict(start_date=None, end_date=None, symbol=None, **kwargs))

    def test_repeated_legacy_snapshots_are_not_receipts(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2025-07-01', '当前市值', 1050, 1050)
        self.add('2026-01-01', '当前市值', 1100, 1100)
        result = self.metrics()
        self.assertAlmostEqual(result['xirr'], .1, places=6)
        self.assertEqual(result['total_returned'], 0)
        self.assertEqual(result['market_value'], 1100)
        self.assertEqual(portfolio.get_portfolio()['summary']['total_returned'], 0)

    def test_multiple_symbols_each_use_latest_snapshot(self):
        for symbol in ['A', 'B']:
            self.add('2025-01-01', '买入', -1000, symbol=symbol)
            self.add('2025-07-01', '市值更新', 0, 1050, symbol=symbol)
            self.add('2026-01-01', '当前市值', 0, 1100, symbol=symbol)
        self.assertAlmostEqual(self.metrics()['xirr'], .1, places=6)
        self.assertEqual(self.metrics()['market_value'], 2200)

    def test_actual_sales_and_dividends_remain_receipts(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2026-01-01', '分红入账', 50)
        self.add('2026-01-01', '卖出', 1050)
        self.assertAlmostEqual(self.metrics()['xirr'], .1, places=6)
        self.assertEqual(self.metrics()['total_returned'], 1100)

    def test_date_and_symbol_filters(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2025-07-01', '当前市值', 1050, 1050)
        self.add('2026-01-01', '当前市值', 1100, 1100)
        self.add('2025-01-01', '买入', -9999, symbol='OTHER')
        result = xirr.get_xirr(start_date=None, end_date='2025-07-01', symbol='TEST')
        expected = 1.05 ** (365 / 181) - 1
        self.assertAlmostEqual(result['xirr'], expected, places=6)
        self.assertEqual(result['market_value'], 1050)

    def test_same_day_latest_id_wins_and_zero_is_valid(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2026-01-01', '当前市值', 1100, 1100)
        self.add('2026-01-01', '当前市值', 0, 0)
        self.assertEqual(self.metrics()['market_value'], 0)
        holding = portfolio.get_portfolio()['holdings'][0]
        self.assertEqual(holding['total_pl'], -1000)
        self.assertFalse(holding['is_active'])

    def test_no_shares_holdings_have_honest_metrics(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2025-06-01', '卖出', 200)
        self.add('2025-07-01', '分红入账', 50)
        self.add('2026-01-01', '当前市值', 900, 900)
        h = portfolio.get_portfolio()['holdings'][0]
        self.assertTrue(h['is_active'])
        self.assertIsNone(h['shares'])
        self.assertIsNone(h['cost_basis'])
        self.assertIsNone(h['unrealized_pl'])
        self.assertEqual(h['net_invested'], 800)
        self.assertEqual(h['total_pl'], 150)
        self.assertEqual(portfolio.get_portfolio()['cumulative'][-1]['running_total'], -750)

    def test_known_shares_partial_sale_reduces_cost(self):
        self.add('2025-01-01', '买入', -1000, shares=100)
        self.add('2025-06-01', '卖出', 600, shares=50)
        self.add('2026-01-01', '当前市值', 550, 550)
        h = portfolio.get_portfolio()['holdings'][0]
        self.assertEqual(h['shares'], 50)
        self.assertEqual(h['cost_basis'], 500)
        self.assertEqual(h['unrealized_pl'], 50)

    async def request(self, method, path, body):
        app = FastAPI()
        app.include_router(transactions.router, prefix='/api')
        app.include_router(ocr.router, prefix='/api')
        messages = []
        sent = False
        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}
            return {'type': 'http.disconnect'}
        async def send(message):
            messages.append(message)
        await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                   'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                   'query_string': b'', 'root_path': '', 'headers': [(b'content-type', b'application/json')],
                   'client': ('127.0.0.1', 1), 'server': ('test', 80)}, receive, send)
        return messages[0]['status'], json.loads(b''.join(m.get('body', b'') for m in messages[1:]))

    def test_api_create_and_update_normalize_snapshots(self):
        status, row = asyncio.run(self.request('POST', '/api/transactions', {
            'date': '2026-01-01', 'symbol': 'TEST', 'business_type': '当前市值',
            'cash_flow': 1100, 'market_value': 1100}))
        self.assertEqual(status, 201)
        self.assertEqual(row['cash_flow'], 0)
        status, row = asyncio.run(self.request('PUT', '/api/transactions/' + str(row['id']), {'market_value': 1200}))
        self.assertEqual(status, 200)
        self.assertEqual(row['cash_flow'], 0)
        self.assertEqual(row['market_value'], 1200)

    def test_api_rejects_missing_or_negative_valuation(self):
        for value in [None, -1]:
            status, _ = asyncio.run(self.request('POST', '/api/transactions', {
                'date': '2026-01-01', 'symbol': 'TEST', 'business_type': '当前市值', 'market_value': value}))
            self.assertEqual(status, 422)

    def test_edit_can_clear_old_snapshot(self):
        self.add('2026-01-01', '当前市值', 1100, 1100)
        row = transactions.update_transaction(1, {'business_type': '卖出', 'cash_flow': 1100, 'market_value': None})
        self.assertIsNone(row['market_value'])
        self.assertEqual(self.metrics()['market_value'], 0)

    def test_csv_normalizes_snapshot_with_blank_cash(self):
        data = parse_and_validate('日期,标的,业务类型,现金流,市值\n2026-01-01,TEST,当前市值,,1100\n')
        self.assertEqual(data['errors'], [])
        self.assertEqual(data['rows'][0]['cash_flow'], 0)

    def test_same_day_only_has_no_annualized_return(self):
        self.add('2026-01-01', '买入', -1000)
        self.add('2026-01-01', '当前市值', 1000, 1000)
        self.assertIsNone(self.metrics()['xirr'])

    def test_reimport_legacy_snapshot_is_deduplicated(self):
        from starlette.datastructures import UploadFile
        from io import BytesIO
        self.add('2026-01-01', '市值更新', 1100, 1100)
        content = '日期,标的,业务类型,现金流,市值\n2026-01-01,TEST,市值更新,1100,1100\n'
        result = asyncio.run(transactions.import_csv(UploadFile(file=BytesIO(content.encode())), mapping=None))
        self.assertEqual(result['imported'], 0)
        self.assertEqual(result['skipped'], 1)

    def test_csv_same_day_different_values_not_lost(self):
        from starlette.datastructures import UploadFile
        from io import BytesIO
        content = '日期,标的,业务类型,现金流,市值\n2026-01-01,TEST,当前市值,1100,1100\n2026-01-01,TEST,当前市值,1200,1200\n'
        async def upload():
            return await transactions.import_csv(UploadFile(file=BytesIO(content.encode())), mapping=None)
        self.assertEqual(asyncio.run(upload())['imported'], 2)
        self.assertEqual(asyncio.run(upload())['skipped'], 2)
        self.assertEqual(self.metrics()['market_value'], 1200)


class OCRRegressionTests(unittest.TestCase):
    def test_clean_and_parse_standalone_amounts(self):
        for raw, expected in [('1000.00', 1000), ('1,000.00', 1000), ('12.000.00', 12000), ('1000', 1000), ('￥1000.00元', 1000), ('-1000.00', 1000)]:
            with self.subTest(raw=raw):
                text = _clean_ocr_text('易方达蓝筹精选\n买入\n' + raw + '\n2025-01-01')
                self.assertIn(raw, text)
                self.assertEqual(parse_trade_text(text)['records'][0]['cash_flow'], expected)

    def test_date_and_time_are_not_amounts(self):
        self.assertEqual(_extract_all_amounts(['2025-01-01', '2025年1月1日', '20250101', '2025:01-01', '12:30', '基金代码：000001']), [])

    def test_missing_amount_does_not_turn_date_into_trade(self):
        text = _clean_ocr_text('易方达蓝筹精选\n买入\n2025-01-01')
        self.assertEqual(parse_trade_text(text)['records'], [])

    def test_inline_amount_after_date_or_label(self):
        self.assertEqual(_extract_all_amounts(['金额：1000.00']), [1000])
        self.assertEqual(_extract_all_amounts(['2025-01-01 买入 1000.00']), [1000])

    def test_realistic_multiple_trades(self):
        text = '易方达蓝筹精选\n买入 1000.00元\n2025-01-01\n卖出 500.00元\n2025-02-01\n分红 10.00元\n2025-03-01'
        records = parse_trade_text(_clean_ocr_text(text))['records']
        self.assertEqual([r['cash_flow'] for r in records], [1000, 500, 10])

    def test_missing_amount_does_not_borrow_next_trade(self):
        text = '易方达蓝筹精选\n买入\n2025-01-01\n卖出 500.00元\n2025-02-01'
        records = parse_trade_text(_clean_ocr_text(text))['records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['date'], '2025-02-01')
        self.assertEqual(records[0]['cash_flow'], 500)

    def test_mixed_fund_names_keep_suffix_and_share_class(self):
        names = ['博时黄金ETF联接C', '博时黄金ETF联接A', '南方中证500ETF联接A',
                 '摩根标普500指数（QDII）A', '易方达蓝筹精选混合C']
        for name in names:
            with self.subTest(name=name):
                text = f'交易记录\n买入 {name} 100.00元\n2025-01-01 确认成功'
                record = parse_trade_text(text, include_incomplete=True)['records'][0]
                self.assertEqual(record['symbol'], name)

    def test_fund_name_handles_spaces_inserted_by_vision(self):
        text = '交易记录\n买入 博时黄金 ETF 联接 C 100.00元\n2025-01-01 确认成功'
        self.assertEqual(parse_trade_text(text)['symbol'], '博时黄金ETF联接C')

    def test_full_fund_name_does_not_absorb_metadata(self):
        text = ('交易记录\n买入 博时黄金ETF联接C 2025-01-01 09:30 100.00元 确认成功\n'
                '买入 博时黄金ETF联接C 100.00元\n2025-02-01 09:30 交易关闭\n'
                '卖出 博时黄金ETF联接C 110.00元\n2025-03-01 09:30 确认成功')
        records = parse_trade_text(text, include_incomplete=True)['records']
        self.assertEqual(len(records), 2)
        self.assertEqual([r['symbol'] for r in records], ['博时黄金ETF联接C'] * 2)
        self.assertEqual([r['cash_flow'] for r in records], [100, 110])

    def test_stock_code_and_ticker_still_supported(self):
        self.assertEqual(parse_trade_text('AAPL\nbuy 100.00\n2025-01-01')['symbol'], 'AAPL')
        self.assertEqual(parse_trade_text('000001\n买入 100.00元\n2025-01-01')['symbol'], '000001')

    def test_engine_pipeline_preserves_amount(self):
        with patch('backend.ocr_parser._ocr_vision', return_value=('易方达蓝筹精选\n买入\n1000.00\n2025-01-01', '')):
            result = ocr_image(b'fake image')
        self.assertEqual(result['engine'], 'vision')
        self.assertEqual(parse_trade_text(result['text'])['records'][0]['cash_flow'], 1000)


if __name__ == '__main__':
    unittest.main()
