import asyncio
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from starlette.datastructures import UploadFile

from backend import video_import as video
from backend.ocr_parser import parse_trade_text

TEXT = '录屏测试基金\n买入 100.00元\n2025-01-01 10:30 确认成功'
OCR = {'text': TEXT, 'engine': 'vision', 'error': ''}


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='video-unit-')
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(video.shutdown_jobs)

    def record(self, **updates):
        row = {'date':'2025-01-01','symbol':'录屏测试基金','business_type':'买入','cash_flow':100,'trade_time':'10:30'}
        row.update(updates)
        return row

    def make_job(self):
        root = self.root / 'job'
        root.mkdir()
        source = root / 'recording.mp4'
        source.write_bytes(b'fake')
        job = video.VideoJob(id='test-job', root=root, source=source)
        with video._lock:
            video._jobs[job.id] = job
        return job

    def samples(self, job):
        samples = []
        for index, color in enumerate(['red','blue','green']):
            path = job.root / f'frame-{index:04d}.png'
            Image.new('RGB',(320,480),color).save(path)
            samples.append((index,index / 2,path))
        return samples

    def test_adjacent_repeated_observations_require_explicit_group_review(self):
        groups = []
        previous = video.add_observations(groups,[self.record()],0,0,{})
        video.add_observations(groups,[self.record()],1,.5,previous)
        self.assertEqual(len(groups),1)
        self.assertEqual(len(groups[0]['observations']),2)
        self.assertEqual(groups[0]['observations'][1]['timestamp'],.5)

    def test_two_identical_trades_visible_together_keep_two_candidates(self):
        groups = []
        previous = video.add_observations(groups,[self.record(),self.record()],0,0,{})
        video.add_observations(groups,[self.record(),self.record()],1,.5,previous)
        self.assertEqual(len(groups),2)
        self.assertEqual([len(row['observations']) for row in groups],[2,2])

    def test_different_trade_times_are_not_linked(self):
        groups=[]
        previous=video.add_observations(groups,[self.record()],0,0,{})
        video.add_observations(groups,[self.record(trade_time='11:00')],1,.5,previous)
        self.assertEqual(len(groups),2)

    def test_missing_time_or_amount_never_auto_links(self):
        for updates in [{'trade_time':None},{'cash_flow':None}]:
            groups=[]
            previous=video.add_observations(groups,[self.record(**updates)],0,0,{})
            video.add_observations(groups,[self.record(**updates)],1,.5,previous)
            self.assertEqual(len(groups),2)

    def test_no_link_across_unreadable_gap(self):
        groups=[]
        video.add_observations(groups,[self.record()],0,0,{})
        video.add_observations(groups,[self.record()],2,1,{})
        self.assertEqual(len(groups),2)

    def test_parser_extracts_trade_time_from_own_date_not_status_bar(self):
        records=parse_trade_text('17:22\n'+TEXT,include_incomplete=True)['records']
        self.assertEqual(records[0]['trade_time'],'10:30')
        records=parse_trade_text('录屏测试基金\n买入 100.00元\n2025:01:02',include_incomplete=True)['records']
        self.assertIsNone(records[0]['trade_time'])

    def test_pixel_filter_does_not_discard_small_image_changes(self):
        first=self.root/'a.png'; second=self.root/'b.png'
        image=Image.new('RGB',(320,480),'white'); image.save(first)
        image.putpixel((100,100),(0,0,0)); image.save(second)
        self.assertNotEqual(video.pixel_fingerprint(first),video.pixel_fingerprint(second))

    def test_fund_heading_is_not_a_dividend_or_cash_amount(self):
        text = '示例中证红利低波动ETF联接A（001234）\n2025-01-03 09:00:01\n定投 200.00元\n2025-01-02 10:30:09\n分红 8.83元\n2025-01-01 11:30:03'
        rows = parse_trade_text(text, include_incomplete=True)['records']
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['cash_flow'] for r in rows], [200, 8.83])
        self.assertEqual([r['date'] for r in rows], ['2025-01-02', '2025-01-01'])
        self.assertEqual(rows[0]['symbol'], '示例中证红利低波动ETF联接A')
        self.assertEqual(rows[0]['trade_time'], '10:30:09')

    def test_dividend_units_never_become_cash_or_borrow_next_amount(self):
        text = '示例红利ETF联接A\n分红 9.50份\n2025-01-01 10:00\n买入 200.00元\n2025-01-02 10:00'
        rows = parse_trade_text(text, include_incomplete=True)['records']
        self.assertIsNone(rows[0]['cash_flow'])
        self.assertIn('份额', rows[0]['ocr_warning'])
        self.assertEqual(rows[1]['cash_flow'], 200)

    def test_conflicting_amounts_require_review_without_modifying_values(self):
        rows = [self.record(cash_flow=8.83), self.record(cash_flow=88.83), self.record(trade_time='11:00')]
        video.flag_amount_conflicts(rows)
        self.assertTrue(rows[0]['video_conflict'])
        self.assertTrue(rows[1]['video_conflict'])
        self.assertNotIn('video_conflict', rows[2])
        self.assertEqual(rows[1]['cash_flow'], 88.83)

    def test_malformed_and_too_long_video_metadata(self):
        for data in [{}, {'streams':[{'width':320,'height':480}],'format':{'duration':'181'}},
                     {'streams':[{'width':320,'height':480}],'format':{'duration':'nan'}}]:
            with patch.object(video,'run_command',return_value=json.dumps(data).encode()):
                with self.assertRaises(ValueError):
                    video.probe_video(Path('file.mp4'),'ffprobe',threading.Event())

    def test_unsupported_empty_or_oversized_upload(self):
        with patch.object(video,'require_tools',return_value=('ffmpeg','ffprobe')):
            for filename, content, status in [('file.txt',b'a',400),('file.MOV',b'',400),('file.mp4',b'12345',413)]:
                with self.subTest(filename=filename),patch.object(video,'MAX_VIDEO_BYTES',4):
                    with self.assertRaises(HTTPException) as caught:
                        asyncio.run(video.receive_video(UploadFile(filename=filename,file=BytesIO(content))))
                    self.assertEqual(caught.exception.status_code,status)

    def test_missing_decoder_is_explicit(self):
        with patch.object(video.shutil,'which',return_value=None):
            with self.assertRaises(HTTPException) as caught:
                video.require_tools()
        self.assertEqual(caught.exception.status_code,503)

    def test_full_job_keeps_partial_failures_visible(self):
        job=self.make_job(); samples=self.samples(job)
        with patch.object(video,'require_tools',return_value=('ffmpeg','ffprobe')),patch.object(video,'probe_video',return_value=2),patch.object(video,'extract_frames',return_value=samples),patch.object(video,'ocr_image',side_effect=[OCR,RuntimeError('引擎失败'),OCR]):
            video.process_job(job)
        state=video.job_snapshot(job.id)
        self.assertEqual(state['status'],'done')
        self.assertEqual(state['result']['failed_frames'],1)
        self.assertEqual(len(state['result']['records']),2)
        self.assertEqual(len(state['result']['frames']),3)
        self.assertEqual(state['result']['warnings'][0]['frame_id'],1)
        self.assertFalse(job.source.exists())
        self.assertTrue(video.frame_path(job.id,0).exists())

    def test_job_result_flags_multi_frame_groups_and_cleanup(self):
        job=self.make_job(); samples=self.samples(job)
        with patch.object(video,'require_tools',return_value=('ffmpeg','ffprobe')),patch.object(video,'probe_video',return_value=2),patch.object(video,'extract_frames',return_value=samples),patch.object(video,'ocr_image',return_value=OCR):
            video.process_job(job)
        self.assertEqual(job.result['records'][0]['video_repeats'],3)
        self.assertEqual(job.result['observation_count'],3)
        video.delete_job(job.id)
        self.assertFalse(job.root.exists())
        with self.assertRaises(HTTPException) as caught:
            video.job_snapshot(job.id)
        self.assertEqual(caught.exception.status_code,410)
        video.delete_job(job.id)  # Idempotent close.

    def test_cancelled_job_removes_temporary_frames(self):
        job=self.make_job(); samples=self.samples(job); job.cancel.set()
        with patch.object(video,'require_tools',return_value=('ffmpeg','ffprobe')),patch.object(video,'probe_video',return_value=2),patch.object(video,'extract_frames',return_value=samples):
            video.process_job(job)
        self.assertFalse(job.root.exists())

    def test_job_candidate_limit_is_explicit_not_silent_truncation(self):
        job=self.make_job(); samples=self.samples(job)
        with patch.object(video,'require_tools',return_value=('ffmpeg','ffprobe')),patch.object(video,'probe_video',return_value=2),patch.object(video,'extract_frames',return_value=samples),patch.object(video,'ocr_image',return_value=OCR),patch.object(video,'MAX_RECORDS',0):
            video.process_job(job)
        self.assertEqual(job.status,'failed')
        self.assertIsNone(job.result)
        self.assertIn('分段',job.error)

    def test_frame_path_cannot_select_arbitrary_file(self):
        job=self.make_job()
        with self.assertRaises(HTTPException) as caught:
            video.frame_path(job.id,999)
        self.assertEqual(caught.exception.status_code,404)

    def test_command_cancellation_stops_decoder(self):
        cancel=threading.Event(); cancel.set()
        started=time.monotonic()
        with self.assertRaises(ValueError):
            video.run_command([sys.executable,'-c','import time; time.sleep(5)'],10,cancel)
        self.assertLess(time.monotonic()-started,2)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg not installed')
    def test_real_video_decoder_first_last_and_exact_pixel_skip(self):
        path=self.root/'test.mp4'
        subprocess.run([shutil.which('ffmpeg'),'-nostdin','-v','error','-f','lavfi','-i','color=c=red:s=320x480:r=10:d=1',
                        '-f','lavfi','-i','color=c=blue:s=320x480:r=10:d=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0',
                        '-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,timeout=20,capture_output=True)
        root=self.root/'real'; root.mkdir(); source=root/'recording.mp4'; shutil.copyfile(path,source)
        job=video.VideoJob(id='real-job',root=root,source=source)
        with video._lock: video._jobs[job.id]=job
        with patch.object(video,'ocr_image',return_value=OCR): video.process_job(job)
        self.assertEqual(job.status,'done',job.error)
        self.assertEqual(job.duration,2)
        self.assertEqual(job.result['sampled_frames'],5)
        self.assertGreater(job.result['identical_frames_skipped'],0)
        self.assertEqual(len(job.result['records']),1)
        self.assertGreater(job.result['records'][0]['video_repeats'],1)
        self.assertEqual(job.result['frames'][0]['timestamp'],0)
        self.assertGreater(job.processed,0)


if __name__ == '__main__': unittest.main()
