"""Server-identity scoped amendment review and project exposure endpoints."""
from datetime import date
from fastapi import Depends,Query
from .amendments import AmendmentService,ProposalInput,AmendmentDecision
from .projects import project_exposure
from .store import MockERP
from .documents.intelligence import assess_with_evidence
from .documents.schemas import DocumentError

def register(app,engine,path,reader,viewer):
    service=AmendmentService(engine)
    @app.post('/v1/amendments',status_code=201)
    def create(body:ProposalInput,actor=Depends(reader)):return service.create(actor,body)
    @app.get('/v1/amendments')
    def list_amendments(actor=Depends(viewer)):return service.list(actor)
    @app.get('/v1/amendments/{proposal_id}')
    def get(proposal_id:str,actor=Depends(viewer)):return service.get(actor,proposal_id)
    @app.post('/v1/amendments/{proposal_id}/decision')
    def decide(proposal_id:str,body:AmendmentDecision,actor=Depends(viewer)):return service.decide(actor,proposal_id,body)
    @app.get('/v1/projects/exposure')
    def projects(as_of:date,project_id:str|None=Query(default=None,max_length=80),actor=Depends(reader)):
        erp=MockERP(path,actor.tenant_id);orders=[o for o in erp.orders() if project_id is None or o['project_id']==project_id]
        if project_id and not orders:raise DocumentError(404,'Project not found in the available order scope')
        findings=[assess_with_evidence(o,service.docs.resolve(actor,o['supplier_id'],o['id'],as_of,erp.contract(o['supplier_id'])),as_of) for o in orders]
        return {'as_of':as_of.isoformat(),'items':project_exposure(findings),'inventory_available':False,
            'schedule_available':False,'scope':'tenant purchase-order delivery evidence only'}
