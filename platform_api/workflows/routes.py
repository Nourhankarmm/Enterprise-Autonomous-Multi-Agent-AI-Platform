import asyncio,os
from fastapi import Depends
from fastapi.responses import JSONResponse
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from .schemas import StartInput,CommandInput,DraftInput,DecisionInput,WorkflowError
from .service import WorkflowService
from .temporal import ProcurementWorkflow

def register_workflow_routes(app,engine,path,identity,reader):
    service=WorkflowService(engine,path);app.state.workflows=service

    @app.exception_handler(WorkflowError)
    async def error(request,exc):return JSONResponse(status_code=exc.status,content={'detail':exc.detail})

    @app.get('/v1/workflow-capabilities')
    def capabilities(actor=Depends(identity)):
        return {'enabled':bool(os.getenv('TEMPORAL_ADDRESS')),'connector':'sandbox-mail','real_email':False}

    @app.post('/v1/operations/{operation_id}/durable',status_code=202)
    async def start(operation_id:str,body:StartInput,actor=Depends(reader)):
        if not os.getenv('TEMPORAL_ADDRESS'):raise WorkflowError(503,'Start the Temporal development stack first')
        run_id=await asyncio.to_thread(service.reserve,actor,operation_id,body)
        try:
            client=await asyncio.wait_for(Client.connect(os.environ['TEMPORAL_ADDRESS'],
                namespace=os.getenv('TEMPORAL_NAMESPACE','default')),timeout=5)
            await asyncio.wait_for(client.start_workflow(ProcurementWorkflow.run,
                {'tenant_id':actor.tenant_id,'id':run_id,'poll_seconds':int(os.getenv('WORKFLOW_POLL_SECONDS','15'))},
                id=run_id,task_queue=os.getenv('TEMPORAL_TASK_QUEUE','procurement-v1'),id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE),timeout=5)
        except WorkflowAlreadyStartedError:pass
        except Exception:raise WorkflowError(503,'Temporal unavailable; retry the same operation to recover the pending start') from None
        return await asyncio.to_thread(service.get,actor,run_id)

    @app.get('/v1/workflows')
    def list_workflows(actor=Depends(identity)):return service.list(actor)

    @app.get('/v1/workflow-approvers')
    def approvers(actor=Depends(reader)):return service.approvers(actor)

    @app.get('/v1/workflows/{run_id}')
    def get(run_id:str,actor=Depends(identity)):return service.get(actor,run_id)

    @app.post('/v1/workflows/{run_id}/draft',status_code=201)
    def draft(run_id:str,body:DraftInput,actor=Depends(reader)):return service.draft(actor,run_id,body)

    @app.post('/v1/workflows/{run_id}/refresh',status_code=202)
    def refresh(run_id:str,body:CommandInput,actor=Depends(reader)):
        service.command(actor,run_id,body,'refresh');return {'accepted':True}

    @app.post('/v1/workflows/{run_id}/cancel',status_code=202)
    def cancel(run_id:str,body:CommandInput,actor=Depends(reader)):
        service.command(actor,run_id,body,'cancel');return {'accepted':True}

    @app.get('/v1/approvals')
    def inbox(actor=Depends(identity)):
        if actor.role!='approver':raise WorkflowError(403,'Approver permission required')
        return service.list(actor)

    @app.post('/v1/approvals/{action_id}/decision')
    def decide(action_id:str,body:DecisionInput,actor=Depends(identity)):return service.decision(actor,action_id,body)
