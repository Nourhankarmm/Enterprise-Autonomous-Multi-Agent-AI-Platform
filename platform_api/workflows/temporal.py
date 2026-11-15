"""Deterministic authoritative lifecycle. IO and current policy checks live in activities."""
from datetime import timedelta,datetime
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

@workflow.defn
class ProcurementWorkflow:
    @workflow.run
    async def run(self,request:dict)->str:
        tenant,run_id=request['tenant_id'],request['id']
        poll=request.get('poll_seconds',15)
        saved=request.get('continuation',{})
        cursor=saved.get('cursor',0);cycle=saved.get('cycle',0)
        cancelled=saved.get('cancelled',False);decision_id=saved.get('decision_id')
        expiry=datetime.fromisoformat(saved['expiry']) if saved else workflow.now()+timedelta(days=7)
        async def call(method,*args):
            return await workflow.execute_activity('platform.step',{'method':method,'args':[tenant,run_id,*args]},
                start_to_close_timeout=timedelta(seconds=75),heartbeat_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2),maximum_interval=timedelta(seconds=10),maximum_attempts=5 if method=='analyze' else 0))
        async def project(state,**fields):return await call('project',state,fields)
        state=saved.get('state','RUNNING');analysis=saved.get('analysis',{})
        iterations=0
        try:
            if not saved:
                await project(state)
                analysis=await call('analyze',cycle)
                state=analysis.pop('state');await project(state,**analysis)
            while state not in {'COMPLETED','PARTIALLY_COMPLETED','FAILED','REJECTED','CANCELLED'}:
                batch=await call('inbox',cursor)
                refresh=False
                for command in batch:
                    cursor=max(cursor,command['id'])
                    if command['kind']=='cancel':cancelled=True
                    elif command['kind']=='refresh':refresh=True
                    elif command['kind']=='decision':decision_id=command['payload']['decision_id']
                if cancelled and state not in {'RECONCILING','RETRY_SEND'}:
                    state='CANCELLED';await project(state);break
                if state in {'RECONCILING','RETRY_SEND'} or decision_id:
                    # Dispatch performs receipt lookup before any retry or policy evaluation.
                    outcome=await call('dispatch',decision_id)
                    state=outcome['state']
                    if state=='REJECTED':await project(state);break
                    if state=='COMPLETED' and cancelled:state='CANCELLED'
                    state=await project(state,error_code=outcome.get('error_code'))
                    if state=='WAITING_APPROVAL':decision_id=None
                if refresh and state in {'WAITING_DOCUMENT','WAITING_CLARIFICATION','WAITING_APPROVAL'} and not cancelled:
                    cycle+=1;decision_id=None
                    if cycle>10:state='FAILED';await project(state,error_code='REFRESH_BUDGET_EXCEEDED');break
                    await project('RUNNING')
                    analysis=await call('analyze',cycle)
                    state=analysis.pop('state');await project(state,**analysis)
                if state in {'WAITING_DOCUMENT','WAITING_CLARIFICATION'} and workflow.now()>=expiry:
                    state='PARTIALLY_COMPLETED' if any(f['status']=='ASSESSED' for f in (analysis.get('result') or {}).get('findings',[])) else 'FAILED'
                    await project(state,error_code='DOCUMENT_WAIT_EXPIRED');break
                if state not in {'COMPLETED','FAILED','CANCELLED','REJECTED','PARTIALLY_COMPLETED'}:
                    await workflow.sleep(timedelta(seconds=2 if state=='RETRY_SEND' else poll))
                    iterations+=1
                    if iterations>=request.get('rotation_limit',200):
                        workflow.continue_as_new({**request,'continuation':{'cursor':cursor,'cycle':cycle,
                            'cancelled':cancelled,'decision_id':decision_id,'expiry':expiry.isoformat(),
                            'state':state,'analysis':analysis}})
            return state
        except ActivityError:
            await project('FAILED',error_code='ACTIVITY_FAILED')
            return 'FAILED'
