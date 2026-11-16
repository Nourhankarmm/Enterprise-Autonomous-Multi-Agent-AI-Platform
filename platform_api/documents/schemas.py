from datetime import date
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

MAX_PDF_BYTES=2*1024*1024
MAX_REQUEST_BYTES=MAX_PDF_BYTES+16384
MAX_PAGES=20
MAX_TEXT=200000

class DocumentError(Exception):
    def __init__(self,status,detail):self.status,self.detail=status,detail

class DocumentInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=160)
    supplier_id:str=Field(min_length=1,max_length=80)
    contract_ref:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    version:int=Field(ge=1,le=10000)
    kind:Literal['contract','amendment']='contract'
    order_ids:list[str]=Field(default_factory=list,max_length=20)
    effective_from:date|None=None
    effective_to:date|None=None

    @model_validator(mode='after')
    def dates(self):
        if self.effective_from and self.effective_to and self.effective_from>self.effective_to:
            raise ValueError('Invalid effective date range')
        if len(set(self.order_ids))!=len(self.order_ids):raise ValueError('Duplicate order ID')
        return self

class ReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    checksum:str=Field(pattern=r'^[a-f0-9]{64}$')
    extraction_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    ocr_compared_to_original:bool=False

class OCRInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    version:int=Field(ge=2,le=10000)

class ACLInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    readers:list[str]=Field(max_length=50)
