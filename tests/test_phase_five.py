"""Dashboard scoping, safe trace metadata, honest measured usage and captured pricing."""
import json,os,uuid,unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import insert,select,update
import test_phase_four as phase4
from platform_api.main import Identity
from platform_api.database import users,planner_calls,workflow_actions,audit_events
from platform_api.agents.planner import DemoPlanner,PlanningResult,PlannerUnavailable
from platform_api.agents.schemas import DEMO_GOAL
from platform_api.telemetry import estimate,record_call

class PhaseFiveTests(unittest.TestCase):
    setUpClass=classmethod(phase4.PhaseFourTests.setUpClass.__func__)
    tearDownClass=classmethod(phase4.PhaseFourTests.tearDownClass.__func__)
    headers=phase4.PhaseFourTests.headers
    upload=phase4.PhaseFourTests.upload
    doc=phase4.PhaseFourTests.doc
    publish=phase4.PhaseFourTests.publish
    share=phase4.PhaseFourTests.share
    draft=phase4.PhaseFourTests.draft
    decision=phase4.PhaseFourTests.decision

    def setUp(self):
        phase4.PhaseFourTests.setUp(self)
        self.dashboard=self.app.state.dashboard
    def tearDown(self):phase4.PhaseFourTests.tearDown(self)
    def get(self,path,token='alice'):
        return self.client.get(path,headers=self.headers(token))
    def usage(self):
        r=self.get('/v1/telemetry/planner');self.assertEqual(r.status_code,200,r.text);return r.json()
    def create(self):return self.client.post('/v1/operations',headers=self.headers(),json={'goal':DEMO_GOAL,'as_of':'2026-11-14'})
    def live_planner(self,usage=None):
        base=DemoPlanner().propose(DEMO_GOAL,date(2026,11,14))
        self.app.state.operations.planner=SimpleNamespace(name='gemini',model='fixture-model',
            propose=lambda *args:PlanningResult(base.proposal,'gemini','fixture-model',usage or {'input_tokens':100,'output_tokens':25}))
    def pricing(self,input_rate='2',output_rate='4'):
        return json.dumps({'gemini/fixture-model':{'input_per_million':input_rate,'output_per_million':output_rate,
            'currency':'USD','source':'https://example.invalid/pricing','as_of':'2026-01-01'}})

    def test_dashboard_auth_and_live_role_revocation(self):
        self.assertEqual(self.client.get('/v1/dashboard').status_code,401)
        with self.engine.begin() as db:db.execute(update(users).where(users.c.id==self.alice).values(active=False))
        self.assertEqual(self.get('/v1/dashboard').status_code,403)

    def test_dashboard_is_owner_and_tenant_scoped(self):
        alice=self.get('/v1/dashboard').json();bob=self.get('/v1/dashboard','bob').json();carol=self.get('/v1/dashboard','carol').json()
        self.assertEqual([o['id'] for o in alice['analyses']],[self.op['id']])
        self.assertEqual([w['id'] for w in alice['workflows']],[self.run])
        self.assertEqual(bob['analyses'],[]);self.assertEqual(bob['workflows'],[])
        self.assertNotIn(self.op['id'],json.dumps(carol));self.assertNotIn(self.run,json.dumps(carol))

    def test_assigned_approver_has_only_own_case_and_no_costs(self):
        self.assertEqual(self.get('/v1/dashboard','approver').json()['workflows'],[])
        self.draft();row=self.get('/v1/dashboard','approver').json()
        self.assertEqual(row['analyses'],[]);self.assertEqual([w['id'] for w in row['workflows']],[self.run])
        self.assertEqual(self.get('/v1/telemetry/planner','approver').status_code,403)
        self.assertEqual(self.get('/v1/operations/'+self.op['id']+'/trace','approver').status_code,403)

    def test_unassigned_approver_cannot_get_case_timeline(self):
        action=self.draft();other='other-'+uuid.uuid4().hex
        with self.engine.begin() as db:db.execute(insert(users).values(id=other,username=other,tenant_id=self.alpha,
            role='approver',active=True,password_hash='not-a-login'))
        actor=Identity(tenant_id=self.alpha,subject=other,role='approver')
        self.assertEqual(self.dashboard.overview(actor)['workflows'],[])
        with self.assertRaises(Exception):self.dashboard.workflow_timeline(actor,self.run)

    def test_trace_contains_measured_attempts_without_outputs_or_thoughts(self):
        r=self.get('/v1/operations/'+self.op['id']+'/trace');self.assertEqual(r.status_code,200,r.text)
        trace=r.json();self.assertEqual(len(trace['tasks']),5);self.assertFalse(trace['private_reasoning_included'])
        for task in trace['tasks']:
            self.assertEqual(task['state'],'SUCCEEDED');self.assertEqual(len(task['timings']),1)
            self.assertGreaterEqual(task['timings'][0]['duration_ms'],0)
            self.assertNotIn('output',task)
        self.assertNotIn('purchase_orders',json.dumps(trace['tasks'][0]['output_counts']))

    def test_other_owner_and_tenant_cannot_read_trace_or_usage(self):
        for token in ('bob','carol'):
            self.assertEqual(self.get('/v1/operations/'+self.op['id']+'/trace',token).status_code,404)
            self.assertEqual(self.get('/v1/telemetry/planner?operation_id='+self.op['id'],token).status_code,404)

    def test_workflow_timeline_has_decision_state_and_receipt_events(self):
        decision=self.decision(self.draft());outcome=self.service.dispatch(self.alpha,self.run,decision['id'])
        self.service.project(self.alpha,self.run,outcome['state'])
        r=self.get('/v1/workflows/'+self.run+'/timeline');self.assertEqual(r.status_code,200,r.text)
        events=r.json()['items'];kinds={e['event_type'] for e in events}
        self.assertTrue({'action.proposed','approval.approve','action.receipt_recorded','workflow.state'}<=kinds)
        self.assertFalse(r.json()['audit_is_immutable'])
        self.assertEqual([e['occurred_at'] for e in events],sorted(e['occurred_at'] for e in events))

    def test_timeline_strips_untrusted_audit_details(self):
        with self.engine.begin() as db:db.execute(insert(audit_events).values(id=str(uuid.uuid4()),tenant_id=self.alpha,
            actor_id=self.alice,action='workflow.state',resource=self.run,occurred_at='2026-11-15T00:00:00+00:00',
            details={'state':'WAITING_APPROVAL','prompt':'private goal','api_key':'do-not-expose','chain_of_thought':'hidden'}))
        text=self.get('/v1/workflows/'+self.run+'/timeline').text
        self.assertNotIn('do-not-expose',text);self.assertNotIn('private goal',text);self.assertNotIn('chain_of_thought',text)

    def test_approver_timeline_hides_previous_other_assignee_proposals(self):
        old=self.draft();other='other-'+uuid.uuid4().hex
        with self.engine.begin() as db:db.execute(insert(users).values(id=other,username=other,tenant_id=self.alpha,
            role='approver',active=True,password_hash='not-a-login'))
        from platform_api.workflows.schemas import DraftInput
        new=self.service.draft(self.owner,self.run,DraftInput(request_id=uuid.uuid4().hex,supplier_id='S1',
            subject='New follow-up',body='Please confirm.',approver_id=other))
        actor=Identity(tenant_id=self.alpha,subject=other,role='approver')
        text=json.dumps(self.dashboard.workflow_timeline(actor,self.run))
        self.assertNotIn(old['action_hash'],text);self.assertIn(new['action_hash'],text)

    def test_recorded_demo_usage_is_explicit_zero_not_paid_model_claim(self):
        data=self.usage();self.assertEqual(data['summary']['calls'],1);self.assertEqual(data['summary']['total_estimate_usd'],'0')
        self.assertEqual(data['items'][0]['pricing']['source'],'offline-demo');self.assertFalse(data['is_invoice'])

    def test_live_tokens_without_rates_do_not_become_zero_cost(self):
        self.live_planner()
        with patch.dict(os.environ,{'MODEL_PRICING_JSON':'{}'}):self.assertEqual(self.create().status_code,201)
        data=self.usage();self.assertEqual(data['summary']['input_tokens'],100);self.assertEqual(data['summary']['output_tokens'],25)
        self.assertIsNone(data['summary']['total_estimate_usd']);self.assertEqual(data['summary']['unpriced_calls'],1)

    def test_decimal_estimate_and_captured_rates_survive_config_change(self):
        self.live_planner()
        with patch.dict(os.environ,{'MODEL_PRICING_JSON':self.pricing()}):self.assertEqual(self.create().status_code,201)
        with patch.dict(os.environ,{'MODEL_PRICING_JSON':self.pricing('200','400')}):data=self.usage()
        paid=next(c for c in data['items'] if c['provider']=='gemini')
        self.assertEqual(paid['estimated_cost_usd'],'0.0003');self.assertEqual(paid['pricing']['input_per_million'],'2')
        self.assertEqual(data['summary']['total_estimate_usd'],'0.0003')

    def test_invalid_or_future_price_config_is_unpriced(self):
        for value in ('NaN','Infinity','-1','1000001'):
            with patch.dict(os.environ,{'MODEL_PRICING_JSON':self.pricing(value)}):
                self.assertEqual(estimate('gemini','fixture-model',{'input_tokens':100,'output_tokens':2},True),(None,None))
        config=json.loads(self.pricing());config['gemini/fixture-model']['as_of']='2999-01-01'
        with patch.dict(os.environ,{'MODEL_PRICING_JSON':json.dumps(config)}):
            self.assertEqual(estimate('gemini','fixture-model',{'input_tokens':100,'output_tokens':2},True),(None,None))

    def test_malformed_and_sensitive_rate_sources_are_not_exposed(self):
        for raw in ('not-json','[]',json.dumps({'gemini/fixture-model':{'currency':'USD'}})):
            with patch.dict(os.environ,{'MODEL_PRICING_JSON':raw}):self.assertEqual(estimate('gemini','fixture-model',{},True),(None,None))
        config=json.loads(self.pricing());config['gemini/fixture-model']['source']='https://user:password@example.invalid/?key=secret'
        with patch.dict(os.environ,{'MODEL_PRICING_JSON':json.dumps(config)}):self.assertEqual(estimate('gemini','fixture-model',{},True),(None,None))

    def test_failed_provider_call_records_unknown_usage_without_error_body(self):
        self.app.state.operations.planner=SimpleNamespace(name='gemini',model='fixture-model',
            propose=lambda *args:(_ for _ in ()).throw(PlannerUnavailable('provider-private-message')))
        self.assertEqual(self.create().status_code,503)
        data=self.usage();failed=next(c for c in data['items'] if c['status']=='FAILED')
        self.assertFalse(failed['usage_known']);self.assertIsNone(failed['estimated_cost_usd'])
        self.assertEqual(failed['error_code'],'PLANNER_UNAVAILABLE');self.assertNotIn('provider-private-message',json.dumps(data))

    def test_rejected_plan_still_records_reported_model_usage(self):
        bad=DemoPlanner().propose(DEMO_GOAL,date(2026,11,14)).proposal.model_dump();bad['tasks'][0]['max_attempts']=99
        self.app.state.operations.planner=SimpleNamespace(name='gemini',model='fixture-model',
            propose=lambda *args:PlanningResult(bad,'gemini','fixture-model',{'input_tokens':12,'output_tokens':3}))
        self.assertEqual(self.create().status_code,422)
        row=next(c for c in self.usage()['items'] if c['status']=='REJECTED');self.assertEqual(row['usage']['input_tokens'],12)

    def test_replanning_records_separate_calls_and_refreshes_do_not_duplicate_usage(self):
        op=self.create().json()
        response=self.client.post('/v1/operations/'+op['id']+'/revise',headers=self.headers(),json={
            'goal':DEMO_GOAL,'as_of':'2026-11-14','version':op['plan_version'],'plan_hash':op['plan_hash']})
        self.assertEqual(response.status_code,200,response.text)
        before=self.usage()['summary']['calls'];self.service.analyze(self.alpha,self.run,1)
        self.assertEqual(self.usage()['summary']['calls'],before)
        linked=self.get('/v1/telemetry/planner?operation_id='+op['id']).json()
        self.assertEqual(len(linked['items']),2)

    def test_planner_telemetry_stores_only_bounded_usage_fields(self):
        record_call(self.engine,self.owner,'gemini','fixture-model','SUCCESS',
            {'input_tokens':4,'output_tokens':2,'secret':'never-save','reasoning':'hidden'},1)
        row=next(c for c in self.usage()['items'] if c['provider']=='gemini')
        self.assertEqual(row['usage'],{'input_tokens':4,'output_tokens':2});self.assertNotIn('never-save',json.dumps(row))
        record_call(self.engine,self.owner,'gemini','fixture-model','SUCCESS',{'input_tokens':-1,'output_tokens':True},1)
        self.assertEqual(self.usage()['summary']['unknown_usage_calls'],1)

    def test_document_revocation_hides_dashboard_cards_trace_cost_and_workflow(self):
        doc=self.doc();self.publish(doc);doc=self.service.docs.get(self.owner,doc['id'])
        self.share(doc,[self.bob,self.approver])
        op=self.client.post('/v1/operations',headers=self.headers('bob'),json={'goal':DEMO_GOAL,'as_of':'2026-11-14'}).json()
        self.client.post('/v1/operations/'+op['id']+'/confirm',headers=self.headers('bob'),json={'version':op['plan_version'],'plan_hash':op['plan_hash']})
        self.assertTrue(self.get('/v1/dashboard','bob').json()['analyses'])
        doc=self.service.docs.get(self.owner,doc['id']);self.share(doc,[self.approver])
        self.assertEqual(self.get('/v1/dashboard','bob').json()['analyses'],[])
        self.assertEqual(self.get('/v1/operations/'+op['id']+'/trace','bob').status_code,403)
        self.assertEqual(self.get('/v1/telemetry/planner','bob').json()['items'],[])

    def test_dashboard_window_is_explicit_and_state_counts_match_visible_rows(self):
        row=self.get('/v1/dashboard').json()
        self.assertEqual(row['window_limit'],100);self.assertEqual(sum(row['analysis_states'].values()),len(row['analyses']))
        self.assertEqual(sum(row['workflow_states'].values()),len(row['workflows']))
        self.assertEqual(row['scope'],'own_analyses_and_assigned_workflows')

    def test_pre_telemetry_operations_are_not_fabricated_as_measured_calls(self):
        with self.engine.begin() as db:db.execute(planner_calls.delete().where(planner_calls.c.tenant_id==self.alpha))
        data=self.usage();self.assertEqual(data['items'],[]);self.assertIsNone(data['summary']['total_estimate_usd'])
        self.assertFalse(data['legacy_calls_included'])

    def test_linked_operation_exposes_workflow_controls_reference(self):
        self.assertEqual(self.get('/v1/operations/'+self.op['id']).json()['workflow_id'],self.run)

    def test_shell_contains_review_trace_cost_and_accessible_navigation_surfaces(self):
        page=self.client.get('/').text
        for marker in ('workspace-nav','overview-panel','analysis-date','agent-trace','workflow-timeline','usage-panel','dashboard.js'):
            self.assertIn(marker,page)
        self.assertIn('aria-pressed',page);self.assertIn('role="status"',page)
