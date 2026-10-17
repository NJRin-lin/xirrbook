import asyncio
import json
import uuid
import unittest
from unittest.mock import patch

from backend import database
from backend.ocr_batch import review_batch, import_batch
from backend.ocr_parser import parse_trade_text
from fastapi import HTTPException
import test_regressions as regressions


class OCRBatchTests(unittest.TestCase):
    setUp = regressions.FinancialRegressionTests.setUp
    tearDown = regressions.FinancialRegressionTests.tearDown
    add = regressions.FinancialRegressionTests.add
    request = regressions.FinancialRegressionTests.request

    def row(self, **updates):
        row = {'date':'2025-01-01', 'symbol':'易方达蓝筹精选', 'business_type':'买入',
               'cash_flow':1000, 'market_value':None, 'decision':'keep'}
        row.update(updates)
        return row

    def batch(self, rows, request_id=None):
        return {'request_id':request_id or str(uuid.uuid4()), 'records':rows}

    def count(self):
        conn = database.get_db()
        try:
            return conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
        finally:
            conn.close()

    def test_review_marks_database_and_overlapping_images(self):
        self.add('2025-01-01','买入',-1000,symbol='易方达蓝筹精选')
        result = review_batch({'records':[self.row(), self.row(), self.row(cash_flow=2000)]})['records']
        self.assertEqual(result[0]['database_ids'], [1])
        self.assertEqual(result[0]['batch_indexes'], [1])
        self.assertEqual(result[1]['batch_indexes'], [0])
        self.assertEqual(result[2]['database_ids'], [])
        self.assertEqual(self.count(), 1)

    def test_import_refuses_ambiguous_duplicates_atomically(self):
        with self.assertRaises(HTTPException) as caught:
            import_batch(self.batch([self.row(),self.row(),self.row(cash_flow=2000)]))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_explicit_keep_preserves_legitimate_same_day_trades(self):
        result = import_batch(self.batch([self.row(allow_duplicate=True),self.row(allow_duplicate=True)]))
        self.assertEqual(result['imported'], 2)
        self.assertEqual(self.count(), 2)

    def test_skip_overlap_does_not_block_unique_retained_record(self):
        result = import_batch(self.batch([self.row(),self.row(decision='skip')]))
        self.assertEqual(result, {'imported':1,'skipped':1})

    def test_db_duplicate_is_rechecked_when_importing(self):
        row = self.row()
        self.assertEqual(review_batch({'records':[row]})['records'][0]['database_ids'], [])
        self.add('2025-01-01','买入',-1000,symbol='易方达蓝筹精选')
        with self.assertRaises(HTTPException) as caught:
            import_batch(self.batch([row]))
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.count(),1)

    def test_network_retry_has_exactly_once_effect_even_after_reopen(self):
        payload = self.batch([self.row()])
        first = import_batch(payload)
        second = import_batch(json.loads(json.dumps(payload)))
        self.assertEqual(first,second)
        self.assertEqual(self.count(),1)

    def test_reused_batch_id_with_changed_content_rejected(self):
        request_id = str(uuid.uuid4())
        import_batch(self.batch([self.row()],request_id))
        with self.assertRaises(HTTPException) as caught:
            import_batch(self.batch([self.row(cash_flow=2000)], request_id))
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.count(),1)

    def test_bad_row_prevents_partial_import(self):
        with self.assertRaises(HTTPException) as caught:
            import_batch(self.batch([self.row(), self.row(date='2025-02-30')]))
        self.assertEqual(caught.exception.status_code,422)
        self.assertEqual(self.count(),0)

    def test_reject_nonfinite_zero_and_subcent_amounts(self):
        for amount in [None,0,float('inf'),float('nan'),1.001,True]:
            with self.subTest(amount=amount):
                result = review_batch({'records':[self.row(cash_flow=amount)]})
                self.assertTrue(result['records'][0]['errors'])

    def test_zero_market_value_legacy_duplicate_and_no_cashflow(self):
        self.add('2025-01-01','市值更新',1100,1100,symbol='易方达蓝筹精选')
        row = self.row(business_type='当前市值',market_value=1100)
        self.assertEqual(review_batch({'records':[row]})['records'][0]['database_ids'],[1])
        zero = self.row(business_type='当前市值',market_value=0)
        import_batch(self.batch([zero]))
        conn = database.get_db()
        try:
            record = conn.execute('SELECT * FROM transactions WHERE id=2').fetchone()
            self.assertEqual(record['cash_flow'],0)
            self.assertEqual(record['market_value'],0)
        finally:
            conn.close()

    def test_skipping_invalid_rows_is_explicit_and_allowed(self):
        result = import_batch(self.batch([self.row(), self.row(date=None, decision='skip')]))
        self.assertEqual(result,{'imported':1,'skipped':1})

    def test_http_review_and_import_statuses(self):
        status, result = asyncio.run(self.request('POST','/api/ocr/review',{'records':[self.row()]}))
        self.assertEqual(status,200)
        self.assertEqual(result['records'][0]['errors'],[])
        status, result = asyncio.run(self.request('POST','/api/ocr/import',self.batch([self.row()])))
        self.assertEqual(status,200)
        self.assertEqual(result['imported'],1)
        status, _ = asyncio.run(self.request('POST','/api/ocr/import',self.batch([self.row()])))
        self.assertEqual(status,409)

    def test_incomplete_ocr_candidates_stay_available_for_review(self):
        text = '易方达蓝筹精选\n买入\n2025-01-01\n卖出 500.00元\n2025-02-01'
        records = parse_trade_text(text, include_incomplete=True)['records']
        self.assertEqual(len(records),2)
        self.assertIsNone(records[0]['cash_flow'])
        self.assertEqual(records[1]['date'],'2025-02-01')
        missing_date = parse_trade_text('易方达蓝筹精选\n定投\n1000.00', include_incomplete=True)['records']
        self.assertIsNone(missing_date[0]['date'])
        self.assertEqual(missing_date[0]['cash_flow'],1000)

    def test_alipay_reinvestment_uses_specific_type(self):
        row = parse_trade_text('易方达蓝筹精选\n红利再投资 10.00元\n2025-01-01',include_incomplete=True)['records'][0]
        self.assertEqual(row['business_type'],'股息再投资')

    def test_cancelled_records_not_presented_as_incomplete(self):
        records = parse_trade_text('易方达蓝筹精选\n定投 已撤回\n2025-01-01',include_incomplete=True)['records']
        self.assertEqual(records,[])

    def test_blank_image_returns_actionable_error(self):
        from io import BytesIO
        from starlette.datastructures import UploadFile
        from backend.routes.ocr import ocr_upload
        with patch('backend.routes.ocr.ocr_image', return_value={'text':'','engine':'','error':'未识别出文字'}):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(ocr_upload(UploadFile(file=BytesIO(b'image'), filename='blank.png')))
        self.assertEqual(caught.exception.status_code,422)
        self.assertIn('有效文字', caught.exception.detail)
        self.assertNotIn('未安装', caught.exception.detail)

    def test_record_limit(self):
        with self.assertRaises(HTTPException):
            review_batch({'records':[self.row()] * 3001})
