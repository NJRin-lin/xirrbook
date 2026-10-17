import asyncio
from datetime import date
import unittest
from unittest.mock import patch

from backend import database
from backend.routes.xirr import get_xirr
import test_regressions as regressions


class PeriodXIRRTests(unittest.TestCase):
    setUp = regressions.FinancialRegressionTests.setUp
    tearDown = regressions.FinancialRegressionTests.tearDown
    add = regressions.FinancialRegressionTests.add
    request = regressions.FinancialRegressionTests.request

    def period(self, start='2026-01-01', end='2026-12-31', symbol=None):
        return get_xirr(start_date=start, end_date=end, symbol=symbol)

    def holdings(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2025-12-31', '当前市值', 1200, 1200)
        self.add('2026-12-31', '当前市值', 1320, 1320)

    def test_full_year_uses_opening_value_not_original_cost(self):
        self.holdings()
        result = self.period()
        self.assertAlmostEqual(result['xirr'], .1, places=7)
        self.assertEqual(result['opening_date'], '2025-12-31')
        self.assertEqual(result['opening_market_value'], 1200)
        self.assertEqual(result['market_value'], 1320)
        self.assertEqual(result['period_profit'], 120)
        self.assertEqual(result['total_invested'], 0)
        self.assertEqual(result['total_returned'], 0)
        conn = database.get_db()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 3)
        conn.close()

    def test_internal_snapshots_do_not_count_as_income(self):
        self.holdings()
        self.add('2026-06-01', '当前市值', 1250, 1250)
        result = self.period()
        self.assertAlmostEqual(result['xirr'], .1, places=7)
        self.assertEqual(result['total_returned'], 0)

    def test_start_and_end_day_trades_are_included(self):
        self.holdings()
        self.add('2025-12-31', '分红入账', 5)
        self.add('2026-01-01', '买入', -100)
        self.add('2026-12-31', '分红入账', 10)
        result = self.period()
        # Opening 1200 + first-day buy 100, end value 1320 + dividend 10.
        # The opening is invested one day longer than the additional buy.
        rate = result['xirr']
        residual = -1200 - 100 / (1 + rate) ** (1 / 365) + 1330 / (1 + rate)
        self.assertAlmostEqual(residual, 0, places=5)
        self.assertEqual(result['total_invested'], 100)
        self.assertEqual(result['total_returned'], 10)
        self.assertEqual(result['period_profit'], 30)

    def test_additional_investment_partial_sale_and_dividend(self):
        self.holdings()
        self.add('2026-04-01', '买入', -100)
        self.add('2026-07-01', '卖出', 300)
        self.add('2026-07-01', '分红入账', 20)
        result = self.period()
        rate = result['xirr']
        opening = date(2025, 12, 31)
        flows = [('2025-12-31', -1200), ('2026-04-01', -100), ('2026-07-01', 320), ('2026-12-31', 1320)]
        self.assertAlmostEqual(sum(value / (1 + rate) ** ((date.fromisoformat(day) - opening).days / 365) for day, value in flows), 0, places=5)
        self.assertEqual(result['period_profit'], 340)

    def test_missing_opening_value_blocks_computation(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2026-12-31', '当前市值', 1320, 1320)
        result = self.period()
        self.assertIsNone(result['xirr'])
        self.assertEqual(result['status'], 'missing_valuations')
        self.assertEqual(result['missing_valuations'][0]['date'], '2025-12-31')
        self.assertIsNone(result['opening_market_value'])
        self.assertIsNone(result['period_profit'])

    def test_stale_positive_values_are_not_carried_to_boundaries(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2025-12-30', '当前市值', 1200, 1200)
        self.add('2026-12-30', '当前市值', 1320, 1320)
        result = self.period()
        self.assertIsNone(result['xirr'])
        self.assertEqual([r['kind'] for r in result['missing_valuations']], ['opening', 'closing'])

    def test_new_investment_in_period_has_zero_opening(self):
        self.add('2026-01-01', '买入', -1000)
        self.add('2026-12-31', '当前市值', 1100, 1100)
        result = self.period()
        self.assertEqual(result['opening_market_value'], 0)
        self.assertEqual(result['valuations'][0]['opening']['source'], 'new_investment')
        self.assertAlmostEqual(result['xirr'], 1.1 ** (365 / 364) - 1, places=7)
        self.assertEqual(result['total_invested'], 1000)

    def test_only_inflow_or_valuation_cannot_prove_zero_opening(self):
        for kind, cash, value in [('卖出', 100, None), ('分红入账', 10, None), ('当前市值', 0, 100)]:
            with self.subTest(kind=kind):
                self.add('2026-12-31', kind, cash, value, symbol=kind)
                result = self.period(symbol=kind)
                self.assertTrue(any(r['kind'] == 'opening' for r in result['missing_valuations']))

    def test_initial_snapshot_before_first_buy_is_not_new_holding(self):
        self.add('2026-01-01', '当前市值', 100, 100)
        self.add('2026-02-01', '买入', -100)
        self.add('2026-12-31', '当前市值', 220, 220)
        self.assertTrue(any(r['kind'] == 'opening' for r in self.period()['missing_valuations']))

    def test_cash_only_sale_does_not_assume_position_closed(self):
        self.add('2025-12-31', '当前市值', 1200, 1200)
        self.add('2026-12-31', '卖出', 1320)
        result = self.period()
        self.assertIsNone(result['xirr'])
        self.assertEqual(result['missing_valuations'][0]['kind'], 'closing')
        self.add('2026-12-31', '当前市值', 0, 0)
        result = self.period()
        self.assertAlmostEqual(result['xirr'], .1, places=7)
        self.assertEqual(result['market_value'], 0)
        self.assertEqual(result['total_returned'], 1320)

    def test_known_empty_position_remains_empty_without_new_trade(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2025-06-01', '当前市值', 0, 0)
        result = self.period()
        self.assertEqual(result['missing_valuations'], [])
        self.assertEqual(result['period_profit'], 0)
        self.assertEqual(result['market_value'], 0)
        self.assertEqual(result['status'], 'unavailable')

    def test_new_trade_invalidates_old_zero_value(self):
        self.add('2025-01-01', '当前市值', 0, 0)
        self.add('2025-12-01', '买入', -1000)
        self.add('2026-12-31', '当前市值', 1100, 1100)
        self.assertTrue(any(r['kind'] == 'opening' for r in self.period()['missing_valuations']))

    def test_missing_one_instrument_blocks_aggregate(self):
        self.holdings()
        self.add('2025-01-01', '买入', -1000, symbol='OTHER')
        result = self.period()
        self.assertIsNone(result['xirr'])
        self.assertTrue(all(item['symbol'] == 'OTHER' for item in result['missing_valuations']))
        self.assertAlmostEqual(self.period(symbol='TEST')['xirr'], .1, places=7)

    def test_accounts_with_same_symbol_are_separate(self):
        self.holdings()
        conn = database.get_db()
        conn.execute("INSERT INTO accounts(id,name) VALUES(2,'second')")
        for day, value in [('2025-12-31', 200), ('2026-12-31', 220)]:
            conn.execute("INSERT INTO transactions(account_id,date,symbol,business_type,cash_flow,market_value) VALUES(2,?,'TEST','当前市值',0,?)", (day, value))
        conn.commit(); conn.close()
        result = self.period()
        self.assertEqual(result['opening_market_value'], 1400)
        self.assertEqual(result['market_value'], 1540)
        self.assertAlmostEqual(result['xirr'], .1, places=7)
        self.assertEqual(len(result['valuations']), 2)

    def test_same_day_latest_snapshot_wins(self):
        self.holdings()
        self.add('2025-12-31', '当前市值', 0, 1300)
        self.add('2026-12-31', '当前市值', 0, 1430)
        self.assertAlmostEqual(self.period()['xirr'], .1, places=7)

    def test_future_rows_do_not_leak_into_period(self):
        self.holdings()
        self.add('2027-01-01', '买入', -99999)
        self.add('2027-02-01', '当前市值', 999999, 999999)
        self.assertAlmostEqual(self.period()['xirr'], .1, places=7)

    def test_start_only_ends_today(self):
        self.add('2026-01-01', '买入', -1000)
        self.add('2026-10-17', '当前市值', 1100, 1100)
        class Today(date):
            @classmethod
            def today(cls):
                return cls(2026, 10, 17)
        with patch('backend.routes.xirr.date', Today):
            result = self.period(end=None)
        self.assertEqual(result['end_date'], '2026-10-17')
        self.assertIsNotNone(result['xirr'])

    def test_end_only_starts_with_first_record(self):
        self.add('2025-01-01', '买入', -1000)
        self.add('2026-01-01', '当前市值', 1100, 1100)
        result = self.period(start=None, end='2026-01-01')
        self.assertEqual(result['start_date'], '2025-01-01')
        self.assertAlmostEqual(result['xirr'], .1, places=7)

    def test_http_invalid_dates_and_reversed_range(self):
        for query in ['start_date=2026-02-30', 'start_date=2027-01-01&end_date=2026-01-01']:
            status, result = asyncio.run(self.request('GET', '/api/xirr?' + query, {}))
            self.assertEqual(status, 422)
            self.assertIn('日期', result['detail'])

    def test_http_missing_values_and_ready_result(self):
        self.add('2025-01-01', '买入', -1000)
        route = '/api/xirr?start_date=2026-01-01&end_date=2026-12-31'
        status, result = asyncio.run(self.request('GET', route, {}))
        self.assertEqual(status, 200)
        self.assertEqual(len(result['missing_valuations']), 2)
        self.add('2025-12-31', '当前市值', 0, 1200)
        self.add('2026-12-31', '当前市值', 0, 1320)
        status, result = asyncio.run(self.request('GET', route, {}))
        self.assertEqual(status, 200)
        self.assertAlmostEqual(result['xirr'], .1, places=7)

    def test_no_filter_keeps_lifetime_behavior(self):
        self.holdings()
        result = get_xirr(start_date=None, end_date=None, symbol=None)
        self.assertEqual(result['mode'], 'lifetime')
        self.assertAlmostEqual(result['xirr'], 1.32 ** (365 / 729) - 1, places=7)

    def test_empty_period_does_not_require_fictitious_values(self):
        result = self.period(symbol='unknown')
        self.assertEqual(result['missing_valuations'], [])
        self.assertIsNone(result['xirr'])
        self.assertEqual(result['period_profit'], 0)


if __name__ == '__main__':
    unittest.main()
