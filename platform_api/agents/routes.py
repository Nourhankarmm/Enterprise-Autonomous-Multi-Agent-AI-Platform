"""Authenticated operation API; identity is never accepted in planner input."""
import os
from fastapi import Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from .schemas import GoalInput, PlanConfirmation, Revision, PlanRejected, TOOLS, DEMO_GOAL
from .planner import PlannerUnavailable
from .service import OperationService, OperationError


def register_agent_routes(app,engine,path,reader,planner=None):
    service=OperationService(engine,path,planner)
    app.state.operations=service

    @app.exception_handler(OperationError)
    async def operation_error(request,error):return JSONResponse(status_code=error.status,content={'detail':error.detail})

    @app.exception_handler(PlanRejected)
    async def invalid_plan(request,error):return JSONResponse(status_code=422,content={'detail':str(error)})

    @app.exception_handler(PlannerUnavailable)
    async def planner_error(request,error):return JSONResponse(status_code=503,content={'detail':str(error)})

    @app.exception_handler(ValidationError)
    async def malformed_proposal(request,error):
        return JSONResponse(status_code=422,content={'detail':'Planner output failed schema validation; no plan was executed'})

    @app.get('/v1/agent-capabilities')
    def capabilities(actor=Depends(reader)):
        mode=getattr(planner,'name',None) or os.getenv('PLANNER_PROVIDER','demo')
        return {'planner_provider':mode,'live_model_configured':bool(os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL')),
            'tools':list(TOOLS),'sample_goal':DEMO_GOAL,'max_replans':2,'max_total_attempts':12,
            'runtime_seconds':30,'external_actions':False}

    @app.post('/v1/operations',status_code=201)
    def create(body:GoalInput,actor=Depends(reader)):return service.create(actor,body)

    @app.get('/v1/operations')
    def list_operations(actor=Depends(reader)):return service.list(actor)

    @app.get('/v1/operations/{operation_id}')
    def get(operation_id:str,actor=Depends(reader)):return service.get(actor,operation_id)

    @app.get('/v1/operations/{operation_id}/events')
    def events(operation_id:str,after:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=200),actor=Depends(reader)):
        return service.events(actor,operation_id,after,limit)

    @app.post('/v1/operations/{operation_id}/revise')
    def revise(operation_id:str,body:Revision,actor=Depends(reader)):return service.revise(actor,operation_id,body)

    @app.post('/v1/operations/{operation_id}/confirm')
    def confirm(operation_id:str,body:PlanConfirmation,actor=Depends(reader)):
        return service.execute(actor,operation_id,body)

    @app.post('/v1/operations/{operation_id}/resume')
    def resume(operation_id:str,actor=Depends(reader)):return service.execute(actor,operation_id,resume=True)

    @app.post('/v1/operations/{operation_id}/cancel')
    def cancel(operation_id:str,actor=Depends(reader)):return service.cancel(actor,operation_id)
