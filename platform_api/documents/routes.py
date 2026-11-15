"""Bounded multipart ingestion and ACL-enforced evidence API."""
from fastapi import Depends,File,Form,UploadFile,Query
from fastapi.responses import JSONResponse,Response
from pydantic import ValidationError
from .schemas import DocumentInput,ReviewInput,ACLInput,DocumentError,MAX_PDF_BYTES,MAX_REQUEST_BYTES
from .service import DocumentService

class UploadLimitMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method')!='POST' or scope.get('path')!='/v1/documents':
            return await self.app(scope,receive,send)
        body=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            body.extend(message.get('body',b''))
            if len(body)>MAX_REQUEST_BYTES:
                response=JSONResponse(status_code=413,content={'detail':'PDF upload request exceeds the limit'})
                return await response(scope,receive,send)
            if not message.get('more_body',False):break
        sent=False
        async def replay():
            nonlocal sent
            if not sent:
                sent=True
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)


def register_document_routes(app,engine,reader,viewer=None):
    service=DocumentService(engine);app.state.documents=service
    viewer=viewer or reader
    app.add_middleware(UploadLimitMiddleware)

    @app.exception_handler(DocumentError)
    async def document_error(request,error):return JSONResponse(status_code=error.status,content={'detail':error.detail})

    @app.get('/v1/document-samples/{sample}')
    def sample(sample:str,actor=Depends(reader)):
        from pathlib import Path
        names={'supplier-s1':'supplier-s1.pdf','supplier-s3':'supplier-s3.pdf',
            'conflicting':'contradictory.pdf','business-days':'business-days.pdf','scanned':'scanned.pdf'}
        if sample not in names:raise DocumentError(404,'Sample not found')
        path=Path(__file__).resolve().parents[2]/'evals/datasets/contracts'/names[sample]
        return Response(path.read_bytes(),media_type='application/pdf',
            headers={'Content-Disposition':f'attachment; filename="{names[sample]}"'})

    @app.get('/v1/documents')
    def list_documents(actor=Depends(viewer)):return service.list(actor)

    @app.get('/v1/document-readers')
    def readers(actor=Depends(reader)):return service.readers(actor)

    @app.get('/v1/documents/search')
    def search(q:str=Query(min_length=2,max_length=200),limit:int=Query(default=10,ge=1,le=20),actor=Depends(viewer)):
        return service.search(actor,q,limit)

    @app.post('/v1/documents',status_code=201)
    async def upload(metadata:str=Form(max_length=10000),file:UploadFile=File(),actor=Depends(reader)):
        try:
            try:parsed=DocumentInput.model_validate_json(metadata)
            except ValidationError:raise DocumentError(422,'Invalid document metadata') from None
            if file.content_type!='application/pdf':raise DocumentError(422,'Upload a PDF using application/pdf')
            data=await file.read(MAX_PDF_BYTES+1)
            return service.upload(actor,parsed,data)
        finally:await file.close()

    @app.get('/v1/documents/{document_id}')
    def get(document_id:str,actor=Depends(viewer)):return service.get(actor,document_id)

    @app.get('/v1/documents/{document_id}/pages/{page}')
    def page(document_id:str,page:int,actor=Depends(viewer)):return service.page(actor,document_id,page)

    @app.get('/v1/documents/{document_id}/file')
    def file(document_id:str,actor=Depends(viewer)):
        data=service.file(actor,document_id)
        return Response(data,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{document_id}.pdf"'})

    @app.put('/v1/documents/{document_id}/acl')
    def share(document_id:str,body:ACLInput,actor=Depends(reader)):return service.share(actor,document_id,body)

    @app.post('/v1/documents/{document_id}/review')
    def review(document_id:str,body:ReviewInput,actor=Depends(reader)):return service.review(actor,document_id,body)
