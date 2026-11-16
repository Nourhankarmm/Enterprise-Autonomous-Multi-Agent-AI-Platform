"""Independent amendment authority, immutable evidence and conservative project exposure."""
import copy,json,uuid,unittest
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import insert,select,update
import test_phase_three as phase3
from platform_api.database import users,orders,amendment_proposals,active_amendments,documents
from platform_api.main import Identity,create_app
from platform_api.agents.planner import DemoPlanner
from platform_api.documents.schemas import DocumentError
from platform_api.documents.service import DocumentService

class PhaseEightTests(unittest.TestCase):
    setUpClass=classmethod(phase3.PhaseThreeTests.setUpClass.__func__)
    tearDownClass=classmethod(phase3.PhaseThreeTests.tearDownClass.__func__)
    tearDown=phase3.PhaseThreeTests.tearDown
    headers=phase3.PhaseThreeTests.headers
    upload=phase3.PhaseThreeTests.upload
    publish=phase3.PhaseThreeTests.publish
    share=phase3.PhaseThreeTests.share
    assessment=phase3.PhaseThreeTests.assessment
    run_operation=phase3.PhaseThreeTests.run_operation
    def setUp(self):
        phase3.PhaseThreeTests.setUp(self);self.client.__exit__(None,None,None)
        self.reviewer='reviewer-'+uuid.uuid4().hex;self.other_reviewer='other-'+uuid.uuid4().hex
        with self.engine.begin() as db:
            for subject in (self.reviewer,self.other_reviewer):
                db.execute(insert(users).values(id=subject,username=subject,tenant_id=self.alpha,role='approver',active=True,password_hash='not-a-login'))
        self.tokens.update(reviewer=Identity(tenant_id=self.alpha,subject=self.reviewer,role='approver'),other=Identity(tenant_id=self.alpha,subject=self.other_reviewer,role='approver'))
        self.app=create_app(self.path,self.tokens,DemoPlanner());self.client=TestClient(self.app);self.client.__enter__()
    def prepared(self,due='2026-11-20',effective_from=None):
        base=self.upload(data=phase3.make_pdf(['Supplier ID: S1','Grace period: 5 calendar days.','Delivery due date: 2026-11-01'])).json()
        self.assertEqual(self.publish(base).status_code,200)
        amendment=self.upload(kind='amendment',order_ids=['PO-A1'],effective_from=effective_from,
            data=phase3.make_pdf(['Supplier ID: S1','Delivery due date: '+due])).json()
        self.assertEqual(amendment['state'],'EXTRACTED',amendment);self.assertEqual(self.publish(amendment).status_code,200)
        for doc in (base,amendment):
            current=self.client.get('/v1/documents/'+doc['id'],headers=self.headers()).json()
            self.assertEqual(self.share(current,[self.reviewer]).status_code,200)
        return base,amendment
    def propose(self,base,amendment,**changes):
        body={'request_id':uuid.uuid4().hex,'order_id':'PO-A1','base_document_id':base['id'],
            'amendment_document_id':amendment['id'],'approver_id':self.reviewer,'effective_from':'2026-11-10'}|changes
        return self.client.post('/v1/amendments',headers=self.headers(),json=body)
    def decision(self,row,token='reviewer',**changes):
        return self.client.post('/v1/amendments/'+row['id']+'/decision',headers=self.headers(token),json={
            'action_hash':row['action_hash'],'decision':'approve','signed_copy_compared':True}|changes)
    def test_reviewed_amendment_document_does_not_replace_the_base_or_change_calculation(self):
        base,amendment=self.prepared()
        self.assertTrue(self.client.get('/v1/documents/'+base['id'],headers=self.headers()).json()['active_source'])
        self.assertFalse(self.client.get('/v1/documents/'+amendment['id'],headers=self.headers()).json()['active_source'])
        self.assertEqual(self.assessment()['PO-A1']['beyond_grace_days'],8)
    def test_approved_effective_amendment_preserves_erp_and_yields_cited_zero_delay(self):
        base,amendment=self.prepared();r=self.propose(base,amendment);self.assertEqual(r.status_code,201,r.text);row=r.json()
        self.assertEqual(self.decision(row).status_code,200)
        finding=self.assessment()['PO-A1'];self.assertEqual((finding['late_days'],finding['beyond_grace_days'],finding['risk']),(0,0,'LOW'))
        self.assertEqual(finding['erp_late_days'],13);self.assertEqual(finding['erp_due_date'],'2026-11-01')
        self.assertEqual(finding['effective_due_date'],'2026-11-20');self.assertFalse(finding['amendment']['signature_cryptographically_verified'])
        self.assertEqual({e['document_id'] for e in finding['evidence'] if e['source_type']=='contract_pdf'},{base['id'],amendment['id']})
        operation=self.run_operation();self.assertIn(row['id'],json.dumps(operation['result']))
        self.assertEqual(next(p for p in operation['result']['projects'] if p['project_id']=='P1')['overdue_order_ids'],['PO-A3'])
        with self.engine.connect() as db:self.assertEqual(db.execute(select(orders.c.due_date).where(orders.c.tenant_id==self.alpha,orders.c.id=='PO-A1')).scalar_one(),'2026-11-01')
    def test_unapproved_rejected_and_future_amendments_never_override_dates(self):
        base,amendment=self.prepared();row=self.propose(base,amendment).json()
        self.assertEqual(self.assessment()['PO-A1']['late_days'],13)
        self.assertEqual(self.decision(row,decision='reject',signed_copy_compared=False).status_code,200)
        self.assertEqual(self.assessment()['PO-A1']['late_days'],13)
        row=self.propose(base,amendment,effective_from='2026-11-15').json();self.assertEqual(self.decision(row).status_code,200)
        self.assertEqual(self.assessment()['PO-A1']['late_days'],13)
    def test_only_assigned_independent_reviewer_can_decide_with_explicit_attestation(self):
        base,amendment=self.prepared();row=self.propose(base,amendment).json()
        for token in ('alice','bob','carol','other'):self.assertIn(self.decision(row,token).status_code,(403,404))
        self.assertEqual(self.decision(row,signed_copy_compared=False).status_code,409)
        self.assertEqual(self.decision(row,action_hash='0'*64).status_code,409)
        self.assertEqual(self.decision(row,signed_copy_compared='true').status_code,422)
        self.assertEqual(self.propose(base,amendment,approver_id=self.alice).status_code,422)
    def test_unshared_document_blocks_proposal_and_revoked_acl_hides_inbox(self):
        base,amendment=self.prepared();doc=self.client.get('/v1/documents/'+amendment['id'],headers=self.headers()).json()
        self.assertEqual(self.share(doc,[]).status_code,200);self.assertEqual(self.propose(base,amendment).status_code,404)
        doc=self.client.get('/v1/documents/'+amendment['id'],headers=self.headers()).json();self.share(doc,[self.reviewer])
        row=self.propose(base,amendment).json();doc=self.client.get('/v1/documents/'+amendment['id'],headers=self.headers()).json();self.share(doc,[])
        self.assertEqual(self.client.get('/v1/amendments',headers=self.headers('reviewer')).json()['items'],[])
        self.assertEqual(self.decision(row).status_code,404)
    def test_changed_order_acl_revision_or_approval_role_requires_fresh_review(self):
        base,amendment=self.prepared();row=self.propose(base,amendment).json()
        with self.engine.begin() as db:db.execute(update(orders).where(orders.c.tenant_id==self.alpha,orders.c.id=='PO-A1').values(delivered_quantity=21))
        self.assertEqual(self.decision(row).status_code,409)
        row=self.propose(base,amendment).json()
        with self.engine.begin() as db:db.execute(update(users).where(users.c.id==self.reviewer).values(active=False))
        self.assertEqual(self.decision(row).status_code,403)
    def test_revoked_authority_after_approval_blocks_old_date_fallback(self):
        base,amendment=self.prepared();row=self.propose(base,amendment).json();self.assertEqual(self.decision(row).status_code,200)
        with self.engine.begin() as db:db.execute(update(users).where(users.c.id==self.reviewer).values(active=False))
        finding=self.assessment()['PO-A1'];self.assertEqual(finding['status'],'INCOMPLETE_EVIDENCE');self.assertIsNone(finding['risk'])
        self.assertNotIn(amendment['id'],json.dumps(finding))
    def test_request_and_decision_replays_are_idempotent_and_changed_request_rejected(self):
        base,amendment=self.prepared();key=uuid.uuid4().hex
        one=self.propose(base,amendment,request_id=key).json();two=self.propose(base,amendment,request_id=key).json();self.assertEqual(one['id'],two['id'])
        self.assertEqual(self.propose(base,amendment,request_id=key,effective_from='2026-11-11').status_code,409)
        self.assertEqual(self.decision(one).json(),self.decision(one).json())
    def test_expired_proposal_and_payload_tampering_are_blocked(self):
        base,amendment=self.prepared();row=self.propose(base,amendment).json()
        with self.engine.begin() as db:db.execute(update(amendment_proposals).where(amendment_proposals.c.id==row['id']).values(created_at=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()))
        self.assertEqual(self.decision(row).status_code,409)
        row=self.propose(base,amendment).json();payload=copy.deepcopy(row['payload']);payload['amendment']['approved_terms']['due_date']='2099-01-01'
        with self.engine.begin() as db:db.execute(update(amendment_proposals).where(amendment_proposals.c.id==row['id']).values(payload=payload))
        self.assertEqual(self.decision(row).status_code,409)
    def test_order_supplier_scope_and_effective_bounds_are_enforced(self):
        base,amendment=self.prepared(effective_from='2026-11-12')
        self.assertEqual(self.propose(base,amendment).status_code,422)
        self.assertEqual(self.propose(base,amendment,order_id='PO-A2',effective_from='2026-11-12').status_code,422)
        self.assertEqual(self.propose(base,amendment,order_id='PO-B1').status_code,404)
    def test_project_exposure_preserves_missing_data_and_never_sums_incompatible_quantities(self):
        r=self.client.get('/v1/projects/exposure?as_of=2026-11-14&project_id=P1',headers=self.headers())
        self.assertEqual(r.status_code,200,r.text);data=r.json();self.assertFalse(data['inventory_available'])
        project=data['items'][0];self.assertEqual(project['overdue_order_ids'],['PO-A1','PO-A3'])
        self.assertTrue(project['shared_overdue_dependencies']);self.assertEqual(project['unresolved_order_ids'],['PO-A3'])
        for key in ('project_delay_days','inventory_shortage','quantity_total'):self.assertIsNone(project[key])
        self.assertEqual(self.client.get('/v1/projects/exposure?as_of=2026-11-14&project_id=P9',headers=self.headers()).status_code,404)
        self.assertEqual(self.client.get('/v1/projects/exposure?as_of=2026-11-14',headers=self.headers('reviewer')).status_code,403)
    def test_nonparticipant_and_cross_tenant_cannot_read_proposals_or_counts(self):
        base,amendment=self.prepared();row=self.propose(base,amendment).json()
        for token in ('bob','carol','other'):
            self.assertEqual(self.client.get('/v1/amendments/'+row['id'],headers=self.headers(token)).status_code,404)
            self.assertEqual(self.client.get('/v1/amendments',headers=self.headers(token)).json()['items'],[])
    def test_later_approved_amendment_requires_current_pointer_and_preserves_earlier_as_of(self):
        base,amendment=self.prepared();one=self.propose(base,amendment).json()
        stale=self.propose(base,amendment,effective_from='2026-11-13').json()
        self.assertEqual(self.decision(one).status_code,200);self.assertEqual(self.decision(stale).status_code,409)
        second=self.propose(base,amendment,effective_from='2026-11-15').json();self.assertEqual(self.decision(second).status_code,200)
        finding=self.assessment()['PO-A1'];self.assertEqual(finding['amendment']['amendment_id'],one['id'])
    def test_new_approval_invalidates_old_execution_authority_but_not_historical_report(self):
        base,amendment=self.prepared();old=self.run_operation();row=self.propose(base,amendment).json()
        self.assertEqual(self.decision(row).status_code,200)
        with self.assertRaises(DocumentError):DocumentService(self.engine).assert_snapshot_access(self.tokens['alice'],old,current=True)
        historical=self.client.get('/v1/operations/'+old['id'],headers=self.headers())
        self.assertEqual(historical.status_code,200)
        finding=next(f for f in historical.json()['result']['findings'] if f['order_id']=='PO-A1')
        self.assertEqual(finding['late_days'],13)
    def test_competing_approvals_converge_to_one_current_authority(self):
        from platform_api.amendments import AmendmentService,AmendmentDecision
        base,amendment=self.prepared();rows=[self.propose(base,amendment).json() for _ in range(2)]
        def approve(row):
            try:return AmendmentService(self.engine).decide(self.tokens['reviewer'],row['id'],
                AmendmentDecision(action_hash=row['action_hash'],decision='approve',signed_copy_compared=True))['state']
            except DocumentError as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(approve,rows))
        self.assertEqual(results.count('APPROVED'),1);self.assertEqual(results.count(409),1)
        with self.engine.connect() as db:
            current=db.execute(select(active_amendments).where(active_amendments.c.tenant_id==self.alpha)).mappings().all()
            self.assertEqual(len(current),1)
