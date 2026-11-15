from typing import Literal
from pydantic import Field
from ..agents.schemas import StrictModel,PlanConfirmation

POLICY='sandbox-mail-v1'
TERMINAL={'COMPLETED','PARTIALLY_COMPLETED','FAILED','REJECTED','CANCELLED'}

class StartInput(PlanConfirmation):
    pass

class CommandInput(StrictModel):
    request_id:str=Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')

class DraftInput(CommandInput):
    supplier_id:str=Field(min_length=1,max_length=80)
    subject:str=Field(min_length=1,max_length=200,pattern=r'^[^\r\n]+$')
    body:str=Field(min_length=1,max_length=10000)

class DecisionInput(CommandInput):
    action_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    decision:Literal['approve','reject']

class WorkflowError(Exception):
    def __init__(self,status,detail):self.status,self.detail=status,detail
