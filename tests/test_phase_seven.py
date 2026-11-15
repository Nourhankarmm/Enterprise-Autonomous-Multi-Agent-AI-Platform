"""OCR isolation, immutable derived evidence, explicit comparison and actual engine acceptance."""
import copy,io,json,os,shutil,subprocess,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from sqlalchemy import select
import test_phase_three as phase3
from platform_api.documents.ocr import extract_ocr,available
from platform_api.documents.ocr_worker import parse_tsv,run
from platform_api.documents.schemas import DocumentError
from platform_api.database import documents

TEXT='Supplier ID: S1\nGrace period: 5 calendar days.\nDelivery due date: 2026-11-01'
def result(confidence=99):return {'pages':[TEXT],'ocr':{'engine':'tesseract','engine_version':'synthetic-test-engine',
    'languages':'ara+eng','dpi':200,'pages':[{'page':1,'word_count':14,'mean_confidence':confidence,'min_confidence':confidence}]}}

class PhaseSevenTests(unittest.TestCase):
    setUpClass=classmethod(phase3.PhaseThreeTests.setUpClass.__func__)
    tearDownClass=classmethod(phase3.PhaseThreeTests.tearDownClass.__func__)
    setUp=phase3.PhaseThreeTests.setUp
    tearDown=phase3.PhaseThreeTests.tearDown
    headers=phase3.PhaseThreeTests.headers
    upload=phase3.PhaseThreeTests.upload
    def source(self):
        r=self.upload(name='scanned.pdf');self.assertEqual(r.status_code,201);return r.json()
    def create(self,source,token='alice',**body):
        return self.client.post('/v1/documents/'+source['id']+'/ocr',headers=self.headers(token),
            json={'revision':source['revision'],'version':2}|body)
    def review(self,doc,**body):
        return self.client.post('/v1/documents/'+doc['id']+'/review',headers=self.headers(),json={
            'revision':doc['revision'],'checksum':doc['checksum'],'extraction_hash':doc['extraction']['extraction_hash']}|body)
    def test_derived_version_preserves_original_and_is_private_unpublished(self):
        source=self.source()
        with patch('platform_api.documents.ocr.extract_ocr',return_value=result()):response=self.create(source)
        self.assertEqual(response.status_code,201);doc=response.json()
        self.assertNotEqual(doc['id'],source['id']);self.assertEqual(doc['version'],2)
        self.assertEqual(doc['checksum'],source['checksum']);self.assertEqual(doc['state'],'EXTRACTED')
        self.assertFalse(doc['active_source']);self.assertEqual(doc['readers'],[])
        self.assertEqual(doc['extraction']['ocr']['source_document_id'],source['id'])
        self.assertEqual(self.client.get('/v1/documents/'+source['id'],headers=self.headers()).json()['state'],'NEEDS_OCR')
        self.assertEqual(self.client.get('/v1/documents/'+doc['id'],headers=self.headers('bob')).status_code,404)
    def test_ocr_requires_owner_not_merely_same_company_or_shared_reader(self):
        source=self.source()
        with patch('platform_api.documents.ocr.extract_ocr') as engine:
            for token in ('bob','carol','auditor'):
                self.assertIn(self.create(source,token).status_code,(403,404))
            engine.assert_not_called()
    def test_stale_revision_old_version_and_duplicate_versions_are_rejected(self):
        source=self.source()
        with patch('platform_api.documents.ocr.extract_ocr',return_value=result()) as engine:
            self.assertEqual(self.create(source,revision=99).status_code,409);engine.assert_not_called()
            self.assertEqual(self.create(source,version=1).status_code,422)
            self.assertEqual(self.create(source).status_code,201)
            self.assertEqual(self.create(source).status_code,409)
    def test_original_pdf_tampering_blocks_ocr_before_engine(self):
        source=self.source()
        with self.engine.begin() as db:db.execute(documents.update().where(documents.c.id==source['id']).values(content=b'%PDF-corrupt'))
        with patch('platform_api.documents.ocr.extract_ocr') as engine:
            self.assertEqual(self.create(source).status_code,409);engine.assert_not_called()
    def test_low_confidence_cannot_be_published_even_with_comparison(self):
        source=self.source()
        with patch('platform_api.documents.ocr.extract_ocr',return_value=result(59)):doc=self.create(source).json()
        self.assertEqual(doc['state'],'NEEDS_REVIEW');self.assertEqual(doc['extraction']['reason'],'OCR_LOW_CONFIDENCE')
        self.assertEqual(self.review(doc,ocr_compared_to_original=True).status_code,409)
    def test_explicit_comparison_required_and_reviewed_ocr_drives_cited_analysis(self):
        source=self.source()
        with patch('platform_api.documents.ocr.extract_ocr',return_value=result()):doc=self.create(source).json()
        self.assertEqual(self.review(doc).status_code,409)
        self.assertEqual(self.review(doc,ocr_compared_to_original=True).status_code,200)
        r=self.client.get('/v1/procurement/assessment?as_of=2026-11-14',headers=self.headers())
        self.assertEqual(r.status_code,200);order=next(x for x in r.json()['items'] if x['order_id']=='PO-A1')
        self.assertEqual(order['beyond_grace_days'],8)
        self.assertIn(doc['id'],json.dumps(order));self.assertNotIn(source['id'],json.dumps(order))
        operation=phase3.PhaseThreeTests.run_operation(self)
        self.assertIn(doc['id'],json.dumps(operation['result']))
        self.assertIn('ocr',json.dumps(operation['result']))
    def test_ocr_jobs_share_the_existing_pdf_processing_budget(self):
        from fastapi.testclient import TestClient
        source=self.source()
        with patch.dict(os.environ,{'RATE_LIMIT_ENABLED':'1','UPLOAD_USER_LIMIT':'1'}),patch('platform_api.documents.ocr.extract_ocr',return_value=result()) as engine:
            with TestClient(phase3.create_app(self.path,self.tokens,phase3.DemoPlanner())) as client:
                url='/v1/documents/'+source['id']+'/ocr';body={'revision':source['revision'],'version':2}
                self.assertEqual(client.post(url,headers=self.headers(),json=body).status_code,201)
                response=client.post(url,headers=self.headers(),json=body)
                self.assertEqual(response.status_code,429);self.assertIn('Retry-After',response.headers)
                self.assertEqual(engine.call_count,1)
    def test_unavailable_engine_fails_safely_without_creating_a_version(self):
        source=self.source()
        with patch('platform_api.documents.ocr.engine_path',return_value=None):
            self.assertEqual(self.create(source).status_code,503)
        with self.engine.connect() as db:self.assertEqual(len(db.execute(select(documents.c.id).where(documents.c.contract_ref==source['contract_ref'])).all()),1)
    def test_tsv_lines_and_uncalibrated_confidence_are_bounded(self):
        header='level\tblock_num\tpar_num\tline_num\tconf\ttext\n'
        text,stats=parse_tsv(header+'5\t1\t1\t1\t99.1\tGrace\n5\t1\t1\t1\t80\tperiod\n')
        self.assertEqual(text,'Grace period');self.assertEqual(stats['word_count'],2);self.assertEqual(stats['min_confidence'],80)
        with self.assertRaises(ValueError):parse_tsv(header+'5\t1\t1\t1\tnan\tbad\n')
    def test_timeout_kills_the_entire_ocr_process_tree(self):
        process=MagicMock();process.pid=99999999;process.communicate.side_effect=[subprocess.TimeoutExpired('ocr',65),(b'',b'')]
        with patch('platform_api.documents.ocr.engine_path',return_value='synthetic-engine'),patch('platform_api.documents.ocr.subprocess.Popen',return_value=process),patch('platform_api.documents.ocr.kill_tree') as kill:
            with self.assertRaises(DocumentError) as caught:extract_ocr(b'%PDF-synthetic')
            self.assertEqual(caught.exception.status,422);kill.assert_called_once_with(process)
            environment=__import__('platform_api.documents.ocr',fromlist=['subprocess']).subprocess.Popen.call_args.kwargs['env']
            self.assertNotIn('DATABASE_URL',environment);self.assertNotIn('GEMINI_API_KEY',environment)
    def test_heavy_ocr_jobs_are_not_queued_when_the_local_slot_is_busy(self):
        from platform_api.documents.ocr import _slot
        self.assertTrue(_slot.acquire(blocking=False))
        try:
            with self.assertRaises(DocumentError) as caught:extract_ocr(b'%PDF-synthetic')
            self.assertEqual(caught.exception.status,503)
        finally:_slot.release()
    def test_oversized_raster_is_rejected_before_rendering(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(width=50000,height=50000);stream=io.BytesIO();writer.write(stream)
        with patch('platform_api.documents.ocr_worker.subprocess.run',return_value=SimpleNamespace(stdout=b'test-engine\n')):
            with self.assertRaisesRegex(ValueError,'OCR_PIXEL_LIMIT'):run(stream.getvalue(),'synthetic-engine')
    def test_actual_rasterizer_feeds_the_engine_and_closes_bitmap_resources(self):
        def engine(command,**kwargs):
            if '--version' in command:return SimpleNamespace(stdout=b'synthetic-engine\n')
            self.assertTrue(Path(command[1]).read_bytes().startswith(b'\x89PNG'))
            Path(command[2]).with_suffix('.tsv').write_text('level\tblock_num\tpar_num\tline_num\tconf\ttext\n5\t1\t1\t1\t99\tsynthetic\n')
            return SimpleNamespace(returncode=0)
        with patch('platform_api.documents.ocr_worker.subprocess.run',side_effect=engine):
            parsed=run((phase3.PDFS/'scanned.pdf').read_bytes(),'synthetic-engine')
        self.assertEqual(parsed['pages'],['synthetic'])
    def test_actual_engine_reads_raster_pdf(self):
        if not available():
            if os.getenv('REQUIRE_OCR')=='1':self.fail('CI requires a real Arabic/English OCR engine')
            self.skipTest('Actual Tesseract not installed locally; required in CI')
        from PIL import Image,ImageDraw,ImageFont
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        candidates=[Path('C:/Windows/Fonts/arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
        font=ImageFont.truetype(str(next(p for p in candidates if p.exists())),58)
        image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
        for index,line in enumerate(TEXT.splitlines()):draw.text((100,180+index*120),line,fill='black',font=font)
        output=io.BytesIO();pdf=canvas.Canvas(output,pagesize=(595,842));pdf.drawImage(ImageReader(image),0,0,width=595,height=842);pdf.save()
        parsed=extract_ocr(output.getvalue());self.assertIn('Grace period: 5 calendar days',parsed['pages'][0])
        source=self.upload(data=output.getvalue()).json();self.assertEqual(source['state'],'NEEDS_OCR')
        response=self.create(source);self.assertEqual(response.status_code,201,response.text);doc=response.json()
        self.assertEqual(doc['state'],'EXTRACTED',doc['extraction'])
        self.assertEqual(self.review(doc,ocr_compared_to_original=True).status_code,200)
