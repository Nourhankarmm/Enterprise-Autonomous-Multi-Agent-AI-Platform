"""Opt-in live acceptance against a local live-model server; consumes one model request/quota.

No API key is read here. The server owns it. Local login uses a hidden prompt.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime,timezone


def verify(base_url,username,password):
    parsed=urlsplit(base_url)
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.username or parsed.password or parsed.path not in {'','/'} or parsed.query or parsed.fragment:
        raise ValueError('Use only a plain local loopback server URL')
    if parsed.port is None:raise ValueError('Specify the local server port')
    os.environ['SMOKE_BASE_URL']=base_url.rstrip('/')
    import compose_smoke
    compose_smoke.BASE_URL=base_url.rstrip('/')
    request=compose_smoke.request
    session=request('/v1/auth/login',payload={'username':username,'password':password})
    token=session['access_token']
    op=None
    try:
        mode=request('/v1/agent-capabilities',token)
        if mode['planner_provider'] not in {'openai','gemini'} or not mode['live_model_configured']:
            raise AssertionError('This server is not configured for a live model planner')
        op=request('/v1/operations',token,payload={
            'goal':'راجع تأخير طلبات الشراء المفتوحة للمورد S1 فقط، واحسب تجاوز السماح وتحقق من الأدلة.',
            'as_of':'2026-11-14'},expected=201)
        assert op['planner_provider']==mode['planner_provider'] and op['state']=='PLANNED', 'Live planner did not produce a ready plan'
        read=next(t for t in op['plan']['proposal']['tasks'] if t['capability']=='purchase_orders.read')
        assert read['filters']['supplier_id']=='S1', 'Planner did not preserve the requested supplier filter'
        result=request('/v1/operations/'+op['id']+'/confirm',token,payload={
            'version':op['plan_version'],'plan_hash':op['plan_hash']})
        assert result['state']=='COMPLETED', 'Use an alpha account and check the operation trace'
        findings=result['result']['findings']
        assert len(findings)==1 and findings[0]['order_id']=='PO-A1', 'Expected alpha supplier S1 fixture'
        assert (findings[0]['late_days'],findings[0]['beyond_grace_days'],findings[0]['risk'])==(13,8,'HIGH')
        assert op['planner_usage'].get('input_tokens',0)>0
        evidence={'verified_at':datetime.now(timezone.utc).isoformat(),'provider':op['planner_provider'],'model':op['planner_model'],'operation_id':op['id'],
            'state':result['state'],'analysis_date':'2026-11-14','findings_count':len(findings),
            'usage':op['planner_usage'],'supplier_filter_verified':True}
        target=Path(__file__).resolve().parents[1]/'.data/live-planner-verification.json'
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        print(json.dumps(evidence,ensure_ascii=True))
        return evidence
    finally:
        if op:
            current=request('/v1/operations/'+op['id'],token)
            if current['state']=='PLANNED':request('/v1/operations/'+op['id']+'/cancel',token,method='POST')
        request('/v1/auth/logout',token,method='POST',expected=204)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8001')
    parser.add_argument('--username',default='demo')
    args=parser.parse_args()
    verify(args.base_url,args.username,getpass.getpass('Local account password (hidden): '))

if __name__=='__main__':main()
