"""Minimal local planner telemetry. No prompts, response bodies, credentials or thoughts."""
import json,os,re,uuid
from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation
from urllib.parse import urlsplit
from sqlalchemy import insert
from .database import planner_calls

def estimate(provider,model,usage,known):
    if provider=='demo':return '0',{'source':'offline-demo','currency':'USD','as_of':date.today().isoformat()}
    if not known:return None,None
    try:
        raw=os.getenv('MODEL_PRICING_JSON','{}')
        if len(raw)>16384:return None,None
        config=json.loads(raw);entry=config.get(provider+'/'+str(model))
        if not isinstance(entry,dict) or entry.get('currency')!='USD':return None,None
        source=urlsplit(entry['source'])
        if source.scheme!='https' or not source.netloc or source.username or source.query or source.fragment:return None,None
        if len(entry['source'])>300 or date.fromisoformat(entry['as_of'])>date.today():return None,None
        rates=[Decimal(str(entry[k])) for k in ('input_per_million','output_per_million')]
        if any(not r.is_finite() or r<0 or r>1000000 for r in rates):return None,None
        cost=(rates[0]*usage['input_tokens']+rates[1]*usage['output_tokens'])/Decimal(1000000)
        snapshot={'source':entry['source'],'as_of':entry['as_of'],'currency':'USD',
            'input_per_million':str(rates[0]),'output_per_million':str(rates[1])}
        return format(cost,'f'),snapshot
    except (ValueError,TypeError,KeyError,AttributeError,InvalidOperation):return None,None

def record_call(engine,actor,provider,model,status,usage,duration_ms,error_code=None,operation_id=None):
    provider=provider if provider in {'demo','openai','gemini'} else 'unknown'
    model=model if isinstance(model,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}',model) else None
    known=provider=='demo' or (isinstance(usage,dict) and all(type(usage.get(k)) is int and 0<=usage[k]<=10**9 for k in ('input_tokens','output_tokens')))
    clean={'input_tokens':0,'output_tokens':0} if provider=='demo' else {k:usage[k] for k in ('input_tokens','output_tokens')} if known else {}
    cost,pricing=estimate(provider,model,clean,known)
    call_id=str(uuid.uuid4())
    with engine.begin() as db:
        db.execute(insert(planner_calls).values(id=call_id,tenant_id=actor.tenant_id,requester_id=actor.subject,
            operation_id=operation_id,provider=provider,model=model,status=status,usage=clean,usage_known=known,
            duration_ms=max(0,int(duration_ms)),estimated_cost_usd=cost,pricing=pricing,error_code=error_code,
            occurred_at=datetime.now(timezone.utc).isoformat()))
    return call_id
