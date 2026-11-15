"""Phase 3 document/citation/ACL acceptance on SQLite and PostgreSQL."""
import io,json,os,tempfile,uuid
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch,MagicMock
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from pypdf import PdfWriter
from sqlalchemy import insert,select,update
from platform_api.main import create_app,Identity
from platform_api.database import (engine_for,database_url,tenants,suppliers,projects,orders,contracts,users,
    documents,document_pages,agent_tasks)
from platform_api.agents.planner import DemoPlanner
from platform_api.agents.schemas import DEMO_GOAL
from platform_api.documents.service import DocumentService
from platform_api.documents.schemas import DocumentError,MAX_PDF_BYTES,MAX_REQUEST_BYTES
from platform_api.documents.terms import extract_terms
from platform_api.documents.extract import extract_pages

PDFS=Path(__file__).resolve().parents[1]/'evals/datasets/contracts'
def make_pdf(lines):
    stream=io.BytesIO();pdf=canvas.Canvas(stream,pagesize=(595,842))
    for i,line in enumerate(lines):pdf.drawString(40,780-i*24,line)
    pdf.save();return stream.getvalue()

class PhaseThreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.path=os.getenv('TEST_DATABASE_URL') or str(Path(cls.temp.name)/'evidence.db')
        cls.engine=engine_for(cls.path)
        with patch.dict(os.environ,{'DATABASE_URL':database_url(cls.path)}):
            command.upgrade(Config('alembic.ini'),'head');command.check(Config('alembic.ini'))

    @classmethod
    def tearDownClass(cls):cls.engine.dispose();cls.temp.cleanup()

    def setUp(self):
        suffix=uuid.uuid4().hex[:12];self.alpha='docs-a-'+suffix;self.beta='docs-b-'+suffix
        self.alice='alice-'+suffix;self.bob='bob-'+suffix;self.carol='carol-'+suffix
        fixture=json.loads((PDFS.parent/'procurement.json').read_text(encoding='utf-8'))
        with self.engine.begin() as db:
            for tenant in (self.alpha,self.beta):
                db.execute(insert(tenants).values(id=tenant))
                for supplier in ('S1','S2','S3'):db.execute(insert(suppliers).values(tenant_id=tenant,id=supplier))
                for project in ('P1','P2','P9'):db.execute(insert(projects).values(tenant_id=tenant,id=project))
            for row in fixture['orders']:
                row['tenant_id']=self.alpha if row['tenant_id']=='alpha' else self.beta
                db.execute(insert(orders).values(**row))
            for row in fixture['contracts']:
                row['tenant_id']=self.alpha if row['tenant_id']=='alpha' else self.beta
                db.execute(insert(contracts).values(**row))
            for user,tenant,role in [(self.alice,self.alpha,'manager'),(self.bob,self.alpha,'analyst'),(self.carol,self.beta,'manager')]:
                db.execute(insert(users).values(id=user,username=user,tenant_id=tenant,role=role,password_hash='not-a-login-fixture',active=True))
        self.tokens={'alice':Identity(tenant_id=self.alpha,role='manager',subject=self.alice),
            'bob':Identity(tenant_id=self.alpha,role='analyst',subject=self.bob),
            'carol':Identity(tenant_id=self.beta,role='manager',subject=self.carol),
            'auditor':Identity(tenant_id=self.alpha,role='auditor',subject=self.alice)}
        self.app=create_app(self.path,self.tokens,DemoPlanner());self.client=TestClient(self.app);self.client.__enter__()

    def tearDown(self):self.client.__exit__(None,None,None)
    def headers(self,token='alice'):return {'Authorization':'Bearer '+token}
    def upload(self,name='supplier-s1.pdf',token='alice',data=None,**metadata):
        body={'title':'Test contract','supplier_id':'S1','contract_ref':'PDF-'+uuid.uuid4().hex[:8],'version':1}|metadata
        return self.client.post('/v1/documents',headers=self.headers(token),data={'metadata':json.dumps(body)},
            files={'file':(name,data if data is not None else (PDFS/name).read_bytes(),'application/pdf')})
    def doc(self,**kwargs):
        r=self.upload(**kwargs);self.assertEqual(r.status_code,201,r.text);return r.json()
    def publish(self,doc,token='alice'):
        return self.client.post(f"/v1/documents/{doc['id']}/review",headers=self.headers(token),json={
            'revision':doc['revision'],'checksum':doc['checksum'],'extraction_hash':doc['extraction']['extraction_hash']})
    def share(self,doc,readers):
        return self.client.put(f"/v1/documents/{doc['id']}/acl",headers=self.headers(),json={'revision':doc['revision'],'readers':readers})
    def assessment(self,token='alice'):
        r=self.client.get('/v1/procurement/assessment?as_of=2026-11-14',headers=self.headers(token))
        self.assertEqual(r.status_code,200,r.text);return {o['order_id']:o for o in r.json()['items']}
    def run_operation(self,token='alice'):
        r=self.client.post('/v1/operations',headers=self.headers(token),json={'goal':DEMO_GOAL,'as_of':'2026-11-14'})
        self.assertEqual(r.status_code,201,r.text);op=r.json()
        r=self.client.post(f"/v1/operations/{op['id']}/confirm",headers=self.headers(token),json={'version':op['plan_version'],'plan_hash':op['plan_hash']})
        self.assertEqual(r.status_code,200,r.text);return r.json()

    def test_pdf_pages_and_clause_candidates_need_human_review(self):
        doc=self.doc();self.assertEqual(doc['state'],'EXTRACTED');self.assertEqual(doc['page_count'],2)
        self.assertNotIn('content',doc);self.assertFalse(doc['active_source'])
        self.assertEqual(doc['extraction']['grace_days'],5)
        facts=[f for f in doc['extraction']['facts'] if f['field']=='grace_days']
        self.assertEqual(facts[0]['page'],2)
        page=self.client.get(f"/v1/documents/{doc['id']}/pages/2",headers=self.headers()).json()
        self.assertEqual(page['text'][facts[0]['start']:facts[0]['end']],facts[0]['quote'])

    def test_private_document_is_hidden_from_same_and_other_company(self):
        doc=self.doc()
        for token in ('bob','carol'):
            for suffix in ('','/pages/2','/file'):
                r=self.client.get(f"/v1/documents/{doc['id']}"+suffix,headers=self.headers(token));self.assertEqual(r.status_code,404)
            r=self.client.get('/v1/documents/search?q=Grace',headers=self.headers(token));self.assertEqual(r.json()['items'],[])
            self.assertEqual(self.client.get('/v1/documents',headers=self.headers(token)).json()['items'],[])
        self.assertEqual(self.client.get('/v1/documents',headers=self.headers('auditor')).status_code,403)
        self.assertEqual(self.client.get('/v1/documents').status_code,401)

    def test_reviewed_terms_drive_page_cited_deterministic_assessment(self):
        doc=self.doc();r=self.publish(doc);self.assertEqual(r.status_code,200,r.text)
        finding=self.assessment()['PO-A1'];self.assertEqual(finding['source_mode'],'reviewed_pdf')
        self.assertEqual((finding['late_days'],finding['beyond_grace_days'],finding['risk']),(13,8,'HIGH'))
        citations=[e for e in finding['evidence'] if e['source_type']=='contract_pdf']
        self.assertEqual({c['page'] for c in citations},{2});self.assertEqual({c['checksum'] for c in citations},{doc['checksum']})
        self.assertIn('5 calendar days',str(citations));self.assertIn('critical',finding['explanation'])

    def test_unreviewed_document_does_not_silently_replace_source(self):
        self.doc(data=make_pdf(['Supplier ID: S1','Grace period: 20 calendar days.']))
        finding=self.assessment()['PO-A1'];self.assertEqual(finding['risk'],'HIGH')
        self.assertEqual(finding['evidence'][1]['source_id'],'C-A1')

    def test_denied_active_pdf_never_falls_back_to_mock_contract(self):
        doc=self.doc();self.assertEqual(self.publish(doc).status_code,200)
        finding=self.assessment('bob')['PO-A1']
        self.assertEqual(finding['status'],'INCOMPLETE_EVIDENCE');self.assertIsNone(finding['risk'])
        self.assertNotIn(doc['id'],json.dumps(finding));self.assertEqual(len(finding['evidence']),1)

    def test_acl_grant_revoke_and_no_nonowner_management(self):
        doc=self.doc();doc=self.share(doc,[self.bob]).json()
        self.assertEqual(self.client.get(f"/v1/documents/{doc['id']}",headers=self.headers('bob')).status_code,200)
        self.assertEqual(self.publish(doc,'bob').status_code,404)
        self.assertEqual(self.client.put(f"/v1/documents/{doc['id']}/acl",headers=self.headers('bob'),json={'revision':doc['revision'],'readers':[]}).status_code,404)
        r=self.share(doc,[]);self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get(f"/v1/documents/{doc['id']}/file",headers=self.headers('bob')).status_code,404)

    def test_cross_company_reader_and_order_link_are_rejected(self):
        doc=self.doc();self.assertEqual(self.share(doc,[self.carol]).status_code,422)
        self.assertEqual(self.upload(order_ids=['PO-B1']).status_code,422)
        self.assertEqual(self.upload(order_ids=['unknown']).status_code,422)
        self.assertEqual(self.upload(supplier_id='unknown').status_code,422)

    def test_stale_review_hash_and_acl_revision_are_rejected(self):
        doc=self.doc()
        r=self.client.post(f"/v1/documents/{doc['id']}/review",headers=self.headers(),json={
            'revision':1,'checksum':'0'*64,'extraction_hash':doc['extraction']['extraction_hash']})
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.share(doc,[self.bob]).status_code,200)
        self.assertEqual(self.share(doc,[]).status_code,409)
        self.assertEqual(self.publish(doc).status_code,409)

    def test_conflicting_clauses_and_business_days_cannot_be_approved(self):
        for name,state,reason in [('contradictory.pdf','CONFLICT','CONTRADICTORY_CLAUSES'),
                                  ('business-days.pdf','NEEDS_REVIEW','BUSINESS_CALENDAR_REQUIRED')]:
            doc=self.doc(name=name);self.assertEqual(doc['state'],state);self.assertEqual(doc['extraction']['reason'],reason)
            self.assertEqual(self.publish(doc).status_code,409)

    def test_scanned_pdf_requires_ocr_without_invented_terms(self):
        doc=self.doc(name='scanned.pdf');self.assertEqual(doc['state'],'NEEDS_OCR')
        self.assertIsNone(doc['extraction']['grace_days']);self.assertEqual(self.publish(doc).status_code,409)
        self.assertEqual(self.client.get('/v1/documents/search?q=Grace',headers=self.headers()).json()['items'],[])

    def test_bad_encrypted_and_over_limit_uploads(self):
        self.assertEqual(self.upload(data=b'not a pdf').status_code,422)
        self.assertEqual(self.upload(data=b'%PDF-1.4 malformed').status_code,422)
        self.assertEqual(self.upload(data=b'%PDF-'+b'a'*MAX_PDF_BYTES).status_code,413)
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('password')
        stream=io.BytesIO();writer.write(stream)
        self.assertEqual(self.upload(data=stream.getvalue()).status_code,422)
        self.assertEqual(self.client.get('/v1/documents',headers=self.headers()).json()['items'],[])

    def test_page_and_streaming_request_limits(self):
        writer=PdfWriter()
        for _ in range(21):writer.add_blank_page(width=100,height=100)
        stream=io.BytesIO();writer.write(stream)
        self.assertEqual(self.upload(data=stream.getvalue()).status_code,422)
        def parts():
            yield b'a'*(MAX_REQUEST_BYTES//2)
            yield b'b'*(MAX_REQUEST_BYTES//2+10)
        r=self.client.post('/v1/documents',headers=self.headers()|{'Content-Type':'application/octet-stream'},content=parts())
        self.assertEqual(r.status_code,413)

    def test_ambiguous_missing_and_arabic_clauses_are_conservative(self):
        for text in ['No known grace period.','Grace period: to be agreed.','Late days: 5.']:
            value=extract_terms([text],'S1');self.assertIsNone(value['grace_days']);self.assertEqual(value['state'],'NEEDS_REVIEW')
        value=extract_terms(['فترة السماح: ٥ أيام تقويمية.'],'S1')
        self.assertEqual(value['grace_days'],5);self.assertEqual(value['facts'][0]['quote'],'فترة السماح: ٥ أيام تقويمية.')
        self.assertEqual(extract_terms(['No grace period.'],'S1')['grace_days'],0)

    def test_supplier_text_link_conflict_blocks_review(self):
        doc=self.doc(name='supplier-s3.pdf',supplier_id='S1')
        self.assertEqual(doc['extraction']['reason'],'SUPPLIER_LINK_CONFLICT')
        self.assertEqual(self.publish(doc).status_code,409)

    def test_version_uniqueness_and_family_ownership(self):
        doc=self.doc(contract_ref='same-family')
        self.assertEqual(self.upload(contract_ref='same-family').status_code,409)
        self.assertEqual(self.upload(token='bob',contract_ref='same-family',version=2).status_code,404)
        self.assertEqual(self.upload(contract_ref='same-family',version=2,supplier_id='S3').status_code,404)

    def test_erp_contract_date_conflict_blocks_contractual_risk(self):
        doc=self.doc(data=make_pdf(['Supplier ID: S1','Grace period: 5 calendar days.','Delivery due date: 2026-11-05']))
        self.assertEqual(self.publish(doc).status_code,200)
        finding=self.assessment()['PO-A1'];self.assertEqual(finding['status'],'SOURCE_CONFLICT')
        self.assertEqual(finding['late_days'],13);self.assertIsNone(finding['risk']);self.assertIsNone(finding['beyond_grace_days'])
        operation=self.run_operation();self.assertEqual(operation['state'],'WAITING_CLARIFICATION')
        self.assertEqual(operation['result']['conflicting_order_ids'],['PO-A1'])

    def test_resolving_missing_contract_produces_supported_new_operation(self):
        before=self.run_operation();self.assertEqual(before['state'],'WAITING_DOCUMENT')
        doc=self.doc(name='supplier-s3.pdf',supplier_id='S3');self.assertEqual(self.publish(doc).status_code,200)
        after=self.run_operation();self.assertEqual(after['state'],'COMPLETED')
        result=next(f for f in after['result']['findings'] if f['order_id']=='PO-A3')
        self.assertEqual(result['beyond_grace_days'],7);self.assertEqual(result['evidence'][1]['document_id'],doc['id'])
        original=self.client.get(f"/v1/operations/{before['id']}",headers=self.headers()).json()
        self.assertEqual(original['state'],'WAITING_DOCUMENT')

    def test_revocation_hides_cached_operations_and_traces(self):
        doc=self.doc();doc=self.share(doc,[self.bob]).json();self.assertEqual(self.publish(doc).status_code,200)
        operation=self.run_operation('bob');latest=self.client.get(f"/v1/documents/{doc['id']}",headers=self.headers()).json()
        self.assertEqual(self.share(latest,[]).status_code,200)
        for suffix in ('','/events'):
            r=self.client.get(f"/v1/operations/{operation['id']}"+suffix,headers=self.headers('bob'))
            self.assertEqual(r.status_code,403);self.assertNotIn('5 calendar days',r.text)

    def test_versioned_citations_and_original_download_are_reproducible(self):
        first=self.doc(contract_ref='versioned');self.assertEqual(self.publish(first).status_code,200)
        original=self.run_operation()
        second=self.doc(contract_ref='versioned',version=2,data=make_pdf(['Supplier ID: S1','Grace period: 7 calendar days.','Delivery due date: 2026-11-01']))
        self.assertEqual(self.publish(second).status_code,200)
        finding=self.assessment()['PO-A1'];self.assertEqual(finding['beyond_grace_days'],6)
        self.assertEqual(finding['evidence'][1]['version'],2)
        stored=self.client.get(f"/v1/operations/{original['id']}",headers=self.headers()).json()
        old=next(f for f in stored['result']['findings'] if f['order_id']=='PO-A1')
        self.assertEqual(old['evidence'][1]['version'],1);self.assertEqual(old['beyond_grace_days'],8)
        r=self.client.get(f"/v1/documents/{first['id']}/file",headers=self.headers())
        self.assertEqual(r.content,(PDFS/'supplier-s1.pdf').read_bytes());self.assertIn('attachment',r.headers['content-disposition'])

    def test_extraction_or_page_tampering_is_detected(self):
        doc=self.doc();altered=dict(doc['extraction']);altered['grace_days']=90
        with self.engine.begin() as db:db.execute(update(documents).where(documents.c.id==doc['id']).values(extraction=altered))
        self.assertEqual(self.publish(doc).status_code,409)
        doc=self.doc();self.assertEqual(self.publish(doc).status_code,200)
        with self.engine.begin() as db:db.execute(update(document_pages).where(document_pages.c.document_id==doc['id'],document_pages.c.page==2).values(text='tampered text'))
        self.assertEqual(self.client.get('/v1/procurement/assessment?as_of=2026-11-14',headers=self.headers()).status_code,409)
        doc=self.doc();published=self.publish(doc).json()
        from platform_api.agents.schemas import canonical_hash
        terms=dict(published['approved_terms']);terms['grace_days']=90
        terms['terms_hash']=canonical_hash({k:v for k,v in terms.items() if k!='terms_hash'})
        with self.engine.begin() as db:db.execute(update(documents).where(documents.c.id==doc['id']).values(approved_terms=terms))
        self.assertEqual(self.client.get('/v1/procurement/assessment?as_of=2026-11-14',headers=self.headers()).status_code,409)


    def test_injection_text_cannot_expand_capabilities_or_send(self):
        doc=self.doc(name='injection.pdf');self.assertEqual(self.publish(doc).status_code,200)
        result=self.run_operation()
        self.assertEqual(result['result']['external_actions'],0)
        self.assertNotIn('attacker',json.dumps(result))
        self.assertEqual({t['capability'] for t in result['tasks']},{'purchase_orders.read','contracts.read','procurement.assess','findings.verify','report.compose'})

    def test_effective_dates_and_explicit_order_scope_block_fallback(self):
        doc=self.doc(effective_from='2027-01-01',order_ids=['PO-A1']);self.assertEqual(self.publish(doc).status_code,200)
        self.assertIsNone(self.assessment()['PO-A1']['risk'])

    def test_hidden_documents_do_not_affect_search_scores(self):
        doc=self.doc();doc=self.share(doc,[self.bob]).json()
        before=self.client.get('/v1/documents/search?q=Grace',headers=self.headers('bob')).json()['items']
        self.doc(name='injection.pdf')
        after=self.client.get('/v1/documents/search?q=Grace',headers=self.headers('bob')).json()['items']
        self.assertEqual(before,after);self.assertEqual({r['document_id'] for r in after},{doc['id']})
        page=self.client.get(after[0]['location'],headers=self.headers('bob')).json()['text']
        self.assertEqual(page[after[0]['start']:after[0]['end']],after[0]['quote'])

    def test_parser_child_has_no_provider_credentials_and_is_killed_on_timeout(self):
        import subprocess,psutil
        fake=MagicMock();fake.pid=999999999;fake.communicate.side_effect=[subprocess.TimeoutExpired('parser',8),(b'',b'')]
        with patch.dict(os.environ,{'GEMINI_API_KEY':'must-not-enter-child','OPENAI_API_KEY':'neither-this'}),\
             patch('platform_api.documents.extract.subprocess.Popen',return_value=fake) as spawn,\
             patch('platform_api.documents.extract.psutil.Process',side_effect=psutil.NoSuchProcess(fake.pid)):
            with self.assertRaisesRegex(DocumentError,'eight-second'):extract_pages(b'%PDF-1.4')
        env=spawn.call_args.kwargs['env'];self.assertNotIn('GEMINI_API_KEY',env);self.assertNotIn('OPENAI_API_KEY',env)
        fake.kill.assert_called_once()


    def test_acl_revocation_during_execution_blocks_the_remaining_tasks(self):
        from platform_api.agents.gateway import ToolGateway
        from platform_api.documents.schemas import ACLInput
        doc=self.doc();doc=self.share(doc,[self.bob]).json();self.assertEqual(self.publish(doc).status_code,200)
        source=self.app.state.documents;owner=self.tokens['alice']
        class RevokeAfterRead(ToolGateway):
            def execute(self,task,*args):
                result=super().execute(task,*args)
                if task.capability=='contracts.read':
                    current=source.get(owner,doc['id'])
                    source.share(owner,doc['id'],ACLInput(revision=current['revision'],readers=[]))
                return result
        self.app.state.operations.gateway_factory=RevokeAfterRead
        op=self.client.post('/v1/operations',headers=self.headers('bob'),json={'goal':DEMO_GOAL,'as_of':'2026-11-14'}).json()
        r=self.client.post(f"/v1/operations/{op['id']}/confirm",headers=self.headers('bob'),json={'version':op['plan_version'],'plan_hash':op['plan_hash']})
        self.assertEqual(r.status_code,403);self.assertNotIn('5 calendar days',r.text)
        with self.engine.connect() as db:
            count=db.execute(select(agent_tasks.c.attempts).where(agent_tasks.c.operation_id==op['id'],agent_tasks.c.task_id=='report')).scalar_one()
        self.assertEqual(count,0)

    def test_changing_active_version_during_execution_rejects_stale_report(self):
        from platform_api.agents.gateway import ToolGateway
        from platform_api.documents.schemas import ReviewInput
        for point in ('contracts.read','report.compose'):
            with self.subTest(point=point):
                family='runtime-'+uuid.uuid4().hex[:8]
                first=self.doc(contract_ref=family);self.assertEqual(self.publish(first).status_code,200)
                second=self.doc(contract_ref=family,version=2,data=make_pdf(['Supplier ID: S1','Grace period: 7 calendar days.','Delivery due date: 2026-11-01']))
                source=self.app.state.documents;owner=self.tokens['alice']
                class ReplaceSource(ToolGateway):
                    def execute(self,task,*args):
                        result=super().execute(task,*args)
                        if task.capability==point:source.review(owner,second['id'],ReviewInput(
                            revision=second['revision'],checksum=second['checksum'],extraction_hash=second['extraction']['extraction_hash']))
                        return result
                self.app.state.operations.gateway_factory=ReplaceSource
                result=self.run_operation()
                self.assertEqual(result['state'],'FAILED');self.assertIsNone(result['result'])
                self.assertEqual(result['error_code'],'EVIDENCE_ACCESS_OR_VERSION_CHANGED')
                self.assertEqual(next(t['attempts'] for t in result['tasks'] if t['capability']=='report.compose'),0 if point=='contracts.read' else 1)

    def test_mixed_scanned_page_blocks_approval_and_original_tamper_is_detected(self):
        from pypdf import PdfReader
        writer=PdfWriter()
        for name in ('supplier-s1.pdf','scanned.pdf'):
            for page in PdfReader(PDFS/name).pages:writer.add_page(page)
        stream=io.BytesIO();writer.write(stream)
        doc=self.doc(data=stream.getvalue());self.assertEqual(doc['state'],'NEEDS_OCR')
        self.assertEqual(self.publish(doc).status_code,409)
        good=self.doc()
        with self.engine.begin() as db:db.execute(update(documents).where(documents.c.id==good['id']).values(content=b'tampered'))
        self.assertEqual(self.client.get(f"/v1/documents/{good['id']}/file",headers=self.headers()).status_code,409)


    def test_sample_downloads_are_fixed_authenticated_fixtures(self):
        self.assertEqual(self.client.get('/v1/document-samples/supplier-s1').status_code,401)
        r=self.client.get('/v1/document-samples/supplier-s1',headers=self.headers())
        self.assertEqual(r.content,(PDFS/'supplier-s1.pdf').read_bytes())
        self.assertEqual(self.client.get('/v1/document-samples/unknown',headers=self.headers()).status_code,404)

    def test_document_family_migration_preserves_existing_rows(self):
        from platform_api.database import document_acl,document_families
        from platform_api.store import seed
        from platform_api.documents.terms import text_hash
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'prior-evidence.db');cfg=Config('alembic.ini');cfg.attributes['database_url']=database_url(path)
            command.upgrade(cfg,'0003')
            engine=engine_for(path);seed(engine)
            data=make_pdf(['Supplier ID: S1','Grace period: 5 calendar days.'])
            text='Supplier ID: S1\nGrace period: 5 calendar days.\n'
            with engine.begin() as db:
                db.execute(insert(documents).values(tenant_id='alpha',id='legacy-doc',owner_id='legacy-owner',supplier_id='S1',
                    contract_ref='legacy-family',version=1,title='Prior PDF',checksum=hashlib.sha256(data).hexdigest(),content=data,
                    byte_size=len(data),page_count=1,state='EXTRACTED',revision=1,extraction=extract_terms([text],'S1'),
                    order_ids=[],created_at='2026-11-15T00:00:00Z'))
                db.execute(insert(document_pages).values(tenant_id='alpha',document_id='legacy-doc',page=1,text=text,text_hash=text_hash(text)))
                db.execute(insert(document_acl).values(tenant_id='alpha',document_id='legacy-doc',subject_id='legacy-reader'))
            command.upgrade(cfg,'head');command.check(cfg)
            with engine.connect() as db:
                self.assertEqual(db.execute(select(documents.c.content).where(documents.c.id=='legacy-doc')).scalar_one(),data)
                self.assertEqual(db.execute(select(document_pages.c.text).where(document_pages.c.document_id=='legacy-doc')).scalar_one(),text)
                self.assertEqual(db.execute(select(document_families.c.owner_id)).scalar_one(),'legacy-owner')
                self.assertEqual(db.execute(select(document_acl.c.subject_id)).scalar_one(),'legacy-reader')
            engine.dispose()

if __name__=='__main__':unittest.main()
