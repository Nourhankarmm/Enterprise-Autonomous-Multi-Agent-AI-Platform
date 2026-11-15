"""Offline frozen-data evaluation in disposable SQLite; never reads the user's database/key."""
import argparse,copy,hashlib,json,math,os,platform,statistics,subprocess,sys,tempfile,time
from contextlib import ExitStack
from datetime import date,datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from sqlalchemy import insert,select,func
from platform_api.main import create_app,Identity
from platform_api.database import engine_for,users,sandbox_messages
from platform_api.store import initialize
from platform_api.domain import assess_order
from platform_api.agents.planner import DemoPlanner,SYSTEM
from platform_api.agents.schemas import DEMO_GOAL,validate_proposal

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def distribution(samples):
    if not samples:return {'n':0,'p50_ms':None,'p95_ms':None}
    ordered=sorted(samples)
    return {'n':len(samples),'p50_ms':round(statistics.median(samples),6),
        'p95_ms':round(ordered[math.ceil(.95*len(ordered))-1],6),
        'mean_ms':round(statistics.mean(samples),6),'stdev_ms':round(statistics.stdev(samples),6) if len(samples)>1 else 0,
        'estimator':'median / nearest-rank P95; descriptive, no SLA'}

def run(repetitions=5):
    report={'schema_version':1,'evaluated_at':datetime.now(timezone.utc).isoformat(),'status':'RUNNING',
        'provider':'demo','live_model_evaluated':False,'repetitions':repetitions,
        'environment':{'python':platform.python_version(),'platform':platform.platform(),'cpu_count':os.cpu_count(),
            'transport':'in-process ASGI TestClient','database':'temporary SQLite','rate_limiting':'disabled through explicit test identity injection'},
        'source':{},'datasets':{},'checks':[],'metrics':{},'latency':{},
        'limitations':['Curated synthetic deterministic regression, not independent external/LLM evaluation.',
            'Latency includes only the named local workloads; no network/provider latency, infrastructure cost or production SLA.',
            'Citation spans and explicit fixture facts are checked; legal interpretation and semantic RAG are not evaluated.',
            'Temporal crash/replay and dispatch safety are measured by the separate regression/Compose suites, not this synchronous benchmark.']}
    for name in ('calculation-cases.json','phase6-regression.json','business-scenarios.json','procurement.json'):
        report['datasets'][name]=digest(ROOT/'evals/datasets'/name)
    report['source']['planner_prompt_sha256']=hashlib.sha256(SYSTEM.encode()).hexdigest()
    try:
        report['source']['commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        report['source']['dirty']=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
    except (OSError,subprocess.CalledProcessError):report['source'].update(commit=None,dirty=None)
    timings={};checks=report['checks']
    def check(name,passed,**details):checks.append({'name':name,'passed':bool(passed),**details})
    def timed(name,fn):
        started=time.perf_counter();value=fn();timings.setdefault(name,[]).append((time.perf_counter()-started)*1000);return value
    try:
        dev=json.loads((ROOT/'evals/datasets/calculation-cases.json').read_text(encoding='utf-8'))
        frozen=json.loads((ROOT/'evals/datasets/phase6-regression.json').read_text(encoding='utf-8'))
        vectors=[{'id':c['name'],'split':'development','as_of':'2026-11-14','quantity':100,**c,
            'expected':{'late_days':c['late'],'beyond_grace_days':c['beyond'],'risk':c['risk']}} for c in dev]+frozen['cases']
        for case in vectors:
            order={'id':case['id'],'tenant_id':'benchmark','supplier_id':'S1','project_id':'P1',
                'quantity':case['quantity'],'delivered_quantity':case['delivered'],'due_date':case['due'],'critical':case['critical']}
            contract={'id':'C1','grace_days':case['grace'],'location':'frozen-oracle','version':1} if case['grace'] is not None else None
            assess_order(order,contract,date.fromisoformat(case['as_of'])) # unmeasured warm-up
            correct=True
            for _ in range(repetitions):
                result=timed('calculator_'+case['split'],lambda:assess_order(order,contract,date.fromisoformat(case['as_of'])))
                correct=correct and all(result[k]==v for k,v in case['expected'].items())
            check('calculator/'+case['id'],correct,split=case['split'])
        report['metrics']['calculator']={'unique_cases':len(vectors),'passed':sum(c['passed'] for c in checks),
            'confidence_interval':None,'interval_note':'Fixed curated cases are not an IID population sample.'}
        planner=DemoPlanner();valid=0;ready=0
        for goal,expected in [(DEMO_GOAL,'ready'),('analyze overdue purchase orders','ready'),
            ('review overdue purchase orders','ready'),('change bank accounts and send everything','needs_clarification')]:
            for _ in range(repetitions):
                proposal=timed('offline_planner',lambda:planner.propose(goal,date(2026,11,14)).proposal)
                validate_proposal(proposal);valid+=1;ready+=proposal.disposition=='ready'
                check('offline_plan/'+str(valid),proposal.disposition==expected)
        report['metrics']['offline_plan_schema']={'generated':valid,'valid':valid,'ready':ready,
            'safe_clarification':valid-ready,'general_language_understanding_claimed':False}
        with tempfile.TemporaryDirectory() as directory,ExitStack() as cleanup:
            path=str(Path(directory)/'benchmark.db');initialize(path);engine=engine_for(path);cleanup.callback(engine.dispose)
            identities={}
            with engine.begin() as db:
                for name,tenant in [('owner','alpha'),('private-ingestion','alpha'),('other-company','beta')]:
                    db.execute(insert(users).values(id=name,username=name,tenant_id=tenant,role='manager',password_hash='not-a-login',active=True))
                    identities[name]=Identity(tenant_id=tenant,subject=name,role='manager')
            app=create_app(path,identities,DemoPlanner());client=cleanup.enter_context(TestClient(app))
            headers=lambda name='owner':{'Authorization':'Bearer '+name}
            def request(method,url,expected=200,**kwargs):
                response=client.request(method,url,headers=kwargs.pop('headers',headers()),**kwargs)
                if response.status_code!=expected:raise AssertionError('HTTP acceptance failed: '+method+' '+url)
                return response.json() if response.headers.get('content-type','').startswith('application/json') else response.content
            def execute():
                op=request('POST','/v1/operations',201,json={'goal':DEMO_GOAL,'as_of':'2026-11-14'})
                return request('POST','/v1/operations/'+op['id']+'/confirm',json={'version':op['plan_version'],'plan_hash':op['plan_hash']})
            initial=timed('graph_missing_evidence',execute)
            check('missing_evidence_abstention',initial['state']=='WAITING_DOCUMENT' and initial['result']['unresolved_order_ids']==['PO-A3'] and next(f for f in initial['result']['findings'] if f['order_id']=='PO-A3')['risk'] is None)
            pdf_dir=ROOT/'evals/datasets/contracts'
            def upload(filename,supplier,reference,actor='owner'):
                return request('POST','/v1/documents',201,headers=headers(actor),
                    data={'metadata':json.dumps({'title':'Synthetic evaluation contract','supplier_id':supplier,'contract_ref':reference,'version':1})},
                    files={'file':(filename,(pdf_dir/filename).read_bytes(),'application/pdf')})
            for index in range(repetitions):timed('bounded_pdf_ingestion',lambda:upload('supplier-s1.pdf','S1','PERF-'+str(index),'private-ingestion'))
            docs=[]
            for filename,supplier in [('supplier-s1.pdf','S1'),('supplier-s3.pdf','S3')]:
                doc=upload(filename,supplier,'CORPUS-'+supplier);docs.append(doc)
                request('POST','/v1/documents/'+doc['id']+'/review',json={'revision':doc['revision'],'checksum':doc['checksum'],'extraction_hash':doc['extraction']['extraction_hash']})
            report['datasets']['retrieval_pdf_checksums']={d['supplier_id']:d['checksum'] for d in docs}
            citations=0;supported=0;success=0
            for index in range(repetitions):
                op=timed('synchronous_reviewed_graph',execute)
                expected={'PO-A1':(13,8,'HIGH'),'PO-A2':(2,0,'LOW'),'PO-A3':(9,7,'HIGH')}
                correct=op['state']=='COMPLETED' and all((f['late_days'],f['beyond_grace_days'],f['risk'])==expected[f['order_id']] for f in op['result']['findings'])
                check('reviewed_graph/'+str(index),correct);success+=correct
                for finding in op['result']['findings']:
                    for citation in finding['evidence']:
                        if not citation.get('document_id'):continue
                        page=request('GET',citation['location']);original=request('GET','/v1/documents/'+citation['document_id']+'/file')
                        citations+=1;supported+=bool(page['text'][citation['start']:citation['end']]==citation['quote'] and digest_bytes(original)==citation['checksum'] and page['version']==citation['version'])
            report['metrics']['reviewed_graph']={'runs':repetitions,'correct':success,'planner':'fixed demo','runtime':'bounded synchronous LangGraph, not Temporal benchmark'}
            report['metrics']['citation_span_integrity']={'checked':citations,'matching':supported,'legal_semantic_precision_claimed':False}
            check('citation_span_integrity',citations>0 and supported==citations)
            expected_pages={(docs[0]['id'],2),(docs[1]['id'],1)};recalls=[]
            for query in ('calendar','Delivery due date'):
                result=timed('lexical_retrieval',lambda:request('GET','/v1/documents/search',params={'q':query,'limit':5}))
                found={(h['document_id'],h['page']) for h in result['items']}
                recalls.append(len(found&expected_pages)/len(expected_pages));check('curated_retrieval/'+query,expected_pages<=found)
            negative=request('GET','/v1/documents/search',params={'q':'xylophonefoobar987','limit':5})
            check('curated_retrieval_negative',negative['items']==[])
            report['metrics']['curated_page_recall_at_5']={'positive_queries':2,'mean':statistics.mean(recalls),
                'negative_queries':1,'negative_hits':len(negative['items']),'scope':'two permitted synthetic documents, exact labelled terms; not semantic RAG'}
            check('cross_tenant_order_denied',client.get('/v1/purchase-orders/PO-B1',headers=headers()).status_code==404)
            check('same_tenant_private_pdf_denied',client.get('/v1/documents/'+docs[0]['id'],headers=headers('private-ingestion')).status_code==404)
            check('cross_tenant_pdf_denied',client.get('/v1/documents/'+docs[0]['id'],headers=headers('other-company')).status_code==404)
            forged=request('GET','/v1/purchase-orders?tenant_id=beta',headers=headers()|{'X-Tenant-ID':'beta'})
            check('forged_tenant_ignored',all(o['tenant_id']=='alpha' for o in forged['items']))
            with engine.connect() as db:messages=db.execute(select(func.count()).select_from(sandbox_messages)).scalar_one()
            report['metrics']['sandbox_side_effects_in_readonly_workload']=messages;check('readonly_side_effects',messages==0)
            usage=request('GET','/v1/telemetry/planner')
            report['metrics']['offline_model_cost']={'provider':'demo','calls':usage['summary']['calls'],'model_estimate_usd':usage['summary']['total_estimate_usd'],'infrastructure_cost':None,'is_invoice':False}
        report['status']='PASS' if all(c['passed'] for c in checks) else 'FAIL'
    except Exception as error:
        report['status']='FAIL';report['fatal_error_type']=type(error).__name__
    report['latency']={k:distribution(v) for k,v in timings.items()}
    report['summary']={'checks':len(checks),'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks)}
    return report

def digest_bytes(data):return hashlib.sha256(data).hexdigest()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--repetitions',type=int,choices=range(3,21),default=5)
    parser.add_argument('--output',default='.data/phase6-evaluation.json');args=parser.parse_args()
    report=run(args.repetitions);target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'summary':report['summary'],'output':str(target)}))
    raise SystemExit(0 if report['status']=='PASS' else 1)
if __name__=='__main__':main()
