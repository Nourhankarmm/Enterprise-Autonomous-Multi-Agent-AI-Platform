"""Tenant/document ACL boundary and immutable evidence-backed contract publication."""
import hashlib,math,re,uuid
from collections import Counter
from datetime import datetime,timezone
from sqlalchemy import select,insert,update,delete,exists,and_,or_,func
from sqlalchemy.exc import IntegrityError
from ..database import (document_families,documents,document_acl,document_pages,document_chunks,contract_sources,
                        suppliers,orders,users,audit_events)
from ..agents.schemas import canonical_hash
from .schemas import DocumentError,DocumentInput
from .extract import extract_pages
from .terms import extract_terms,chunks_for_page,text_hash

DOC_COLUMNS=[c for c in documents.c if c.name!='content']

class DocumentService:
    def __init__(self,engine):self.engine=engine

    def allowed(self,actor):
        return and_(documents.c.tenant_id==actor.tenant_id,or_(documents.c.owner_id==actor.subject,
            exists(select(document_acl.c.document_id).where(document_acl.c.tenant_id==documents.c.tenant_id,
                document_acl.c.document_id==documents.c.id,document_acl.c.subject_id==actor.subject))))

    def _row(self,db,actor,doc_id,owner=False,content=False):
        condition=and_(documents.c.tenant_id==actor.tenant_id,documents.c.owner_id==actor.subject) if owner else self.allowed(actor)
        row=db.execute(select(*(list(documents.c) if content else DOC_COLUMNS)).where(condition,documents.c.id==doc_id)).mappings().first()
        if row is None:raise DocumentError(404,'Document not found')
        return dict(row)

    def _audit(self,db,actor,action,doc_id,details=None):
        db.execute(insert(audit_events).values(id=str(uuid.uuid4()),tenant_id=actor.tenant_id,actor_id=actor.subject,
            action=action,resource=doc_id,occurred_at=datetime.now(timezone.utc).isoformat(),details=details or {}))

    def get(self,actor,doc_id):
        with self.engine.connect() as db:
            row=self._row(db,actor,doc_id)
            row['can_manage']=row['owner_id']==actor.subject
            if row['can_manage']:
                row['readers']=list(db.execute(select(document_acl.c.subject_id).where(
                    document_acl.c.tenant_id==actor.tenant_id,document_acl.c.document_id==doc_id)).scalars())
            binding=db.execute(select(contract_sources.c.document_id).where(contract_sources.c.tenant_id==actor.tenant_id,
                contract_sources.c.supplier_id==row['supplier_id'])).scalar_one_or_none()
            row['active_source']=binding==doc_id
            return row

    def list(self,actor):
        with self.engine.connect() as db:
            rows=db.execute(select(documents.c.id,documents.c.title,documents.c.supplier_id,documents.c.contract_ref,
                documents.c.version,documents.c.state,documents.c.page_count,documents.c.created_at).where(self.allowed(actor))
                .order_by(documents.c.created_at.desc()).limit(100)).mappings()
            return {'items':[dict(row) for row in rows]}

    def readers(self,actor):
        with self.engine.connect() as db:
            return {'items':[dict(r) for r in db.execute(select(users.c.id,users.c.username).where(
                users.c.tenant_id==actor.tenant_id,users.c.active.is_(True),users.c.role.in_(['manager','analyst','approver']),
                users.c.id!=actor.subject).limit(100)).mappings()]}

    def upload(self,actor,metadata,data,*,ocr_result=None):
        with self.engine.connect() as db:
            if not db.execute(select(suppliers.c.id).where(suppliers.c.tenant_id==actor.tenant_id,
                    suppliers.c.id==metadata.supplier_id)).first():raise DocumentError(422,'Supplier must belong to your company')
            linked=db.execute(select(orders.c.id).where(orders.c.tenant_id==actor.tenant_id,
                orders.c.supplier_id==metadata.supplier_id,orders.c.id.in_(metadata.order_ids))).scalars().all() if metadata.order_ids else []
            if set(linked)!=set(metadata.order_ids):raise DocumentError(422,'Order references must belong to this supplier and company')
            previous=db.execute(select(document_families).where(document_families.c.tenant_id==actor.tenant_id,
                document_families.c.contract_ref==metadata.contract_ref)).mappings().first()
            if previous and (previous['owner_id']!=actor.subject or previous['supplier_id']!=metadata.supplier_id):
                raise DocumentError(404,'Contract reference is unavailable')
            if db.execute(select(func.count()).select_from(documents).where(documents.c.tenant_id==actor.tenant_id)).scalar_one()>=100:
                raise DocumentError(409,'Demo company document limit reached')
        pages=ocr_result['pages'] if ocr_result else extract_pages(data)
        extraction=extract_terms(pages,metadata.supplier_id,ocr_result['ocr'] if ocr_result else None)
        doc_id=str(uuid.uuid4());checksum=hashlib.sha256(data).hexdigest()
        try:
            with self.engine.begin() as db:
                if previous is None:
                    db.execute(insert(document_families).values(tenant_id=actor.tenant_id,contract_ref=metadata.contract_ref,
                        owner_id=actor.subject,supplier_id=metadata.supplier_id))
                db.execute(insert(documents).values(tenant_id=actor.tenant_id,id=doc_id,owner_id=actor.subject,
                    supplier_id=metadata.supplier_id,contract_ref=metadata.contract_ref,version=metadata.version,
                    title=metadata.title,checksum=checksum,content=data,byte_size=len(data),page_count=len(pages),
                    state=extraction['state'],revision=1,extraction=extraction,order_ids=metadata.order_ids,
                    effective_from=metadata.effective_from.isoformat() if metadata.effective_from else None,
                    effective_to=metadata.effective_to.isoformat() if metadata.effective_to else None,
                    created_at=datetime.now(timezone.utc).isoformat()))
                for number,text in enumerate(pages,1):
                    db.execute(insert(document_pages).values(tenant_id=actor.tenant_id,document_id=doc_id,
                        page=number,text=text,text_hash=text_hash(text)))
                    for chunk in chunks_for_page(text,number):
                        db.execute(insert(document_chunks).values(tenant_id=actor.tenant_id,document_id=doc_id,**chunk))
                self._audit(db,actor,'document.uploaded',doc_id,{'version':metadata.version,'state':extraction['state'],'checksum':checksum})
        except IntegrityError:raise DocumentError(409,'Document version already exists or metadata changed') from None
        return self.get(actor,doc_id)

    def ocr(self,actor,doc_id,body):
        from .ocr import extract_ocr
        with self.engine.connect() as db:
            source=self._row(db,actor,doc_id,owner=True,content=True)
            if source['revision']!=body.revision:raise DocumentError(409,'Document revision changed')
            if source['state']!='NEEDS_OCR':raise DocumentError(409,'OCR requires an original document awaiting OCR')
            if body.version<=source['version']:raise DocumentError(422,'OCR must create a newer document version')
            if hashlib.sha256(source['content']).hexdigest()!=source['checksum']:raise DocumentError(409,'Original PDF integrity check failed')
        result=extract_ocr(source['content'])
        result['ocr'].update(source_document_id=doc_id,source_checksum=source['checksum'])
        with self.engine.connect() as db:
            current=self._row(db,actor,doc_id,owner=True)
            active=db.execute(select(users.c.active).where(users.c.tenant_id==actor.tenant_id,users.c.id==actor.subject,
                users.c.role.in_(['manager','analyst']))).scalar_one_or_none()
            if not active:raise DocumentError(403,'Current document processing permission required')
            if current['revision']!=body.revision:raise DocumentError(409,'Document changed during OCR')
        metadata=DocumentInput(title=source['title'],supplier_id=source['supplier_id'],contract_ref=source['contract_ref'],
            version=body.version,order_ids=source['order_ids'],effective_from=source['effective_from'],effective_to=source['effective_to'])
        # New versions are private by default; source ACL and active publication are never silently copied.
        return self.upload(actor,metadata,source['content'],ocr_result=result)

    def page(self,actor,doc_id,page):
        with self.engine.connect() as db:
            doc=self._row(db,actor,doc_id)
            row=db.execute(select(document_pages).where(document_pages.c.tenant_id==actor.tenant_id,
                document_pages.c.document_id==doc_id,document_pages.c.page==page)).mappings().first()
            if row is None:raise DocumentError(404,'Page not found')
            if row['text_hash']!=text_hash(row['text']):raise DocumentError(409,'Page text integrity check failed')
            return dict(row)|{'title':doc['title'],'version':doc['version'],'checksum':doc['checksum'],
                'text_origin':'ocr' if doc['extraction'].get('ocr') else 'pdf_text'}

    def file(self,actor,doc_id):
        with self.engine.connect() as db:
            doc=self._row(db,actor,doc_id,content=True)
            if hashlib.sha256(doc['content']).hexdigest()!=doc['checksum']:raise DocumentError(409,'Original PDF integrity check failed')
            return doc['content']

    def share(self,actor,doc_id,body):
        if len(set(body.readers))!=len(body.readers):raise DocumentError(422,'Duplicate reader')
        with self.engine.begin() as db:
            doc=self._row(db,actor,doc_id,owner=True)
            if doc['revision']!=body.revision:raise DocumentError(409,'Document revision changed')
            if body.readers:
                valid=set(db.execute(select(users.c.id).where(users.c.tenant_id==actor.tenant_id,
                    users.c.id.in_(body.readers),users.c.active.is_(True),users.c.role.in_(['manager','analyst','approver']))).scalars())
                if valid!=set(body.readers):raise DocumentError(422,'Readers must be active procurement users in your company')
            changed=db.execute(update(documents).where(documents.c.tenant_id==actor.tenant_id,documents.c.id==doc_id,
                documents.c.owner_id==actor.subject,documents.c.revision==body.revision).values(revision=body.revision+1))
            if changed.rowcount!=1:raise DocumentError(409,'Document revision changed')
            db.execute(delete(document_acl).where(document_acl.c.tenant_id==actor.tenant_id,document_acl.c.document_id==doc_id))
            for subject in body.readers:
                if subject!=actor.subject:db.execute(insert(document_acl).values(tenant_id=actor.tenant_id,document_id=doc_id,subject_id=subject))
            self._audit(db,actor,'document.acl_changed',doc_id,{'revision':body.revision+1,'reader_count':len(body.readers)})
        return self.get(actor,doc_id)

    def _citation(self,doc,fact):
        return {'source_type':'contract_pdf','source_id':doc['contract_ref'],'document_id':doc['id'],
            'version':doc['version'],'checksum':doc['checksum'],'page':fact['page'],
            'start':fact['start'],'end':fact['end'],'quote':fact['quote'],'text_hash':fact['text_hash'],
            'location':f"/v1/documents/{doc['id']}/pages/{fact['page']}",
            **({'text_origin':'ocr','ocr_engine':doc['extraction']['ocr']['engine']} if doc['extraction'].get('ocr') else {})}

    def _check_citation(self,db,doc,citation):
        page=db.execute(select(document_pages).where(document_pages.c.tenant_id==doc['tenant_id'],
            document_pages.c.document_id==doc['id'],document_pages.c.page==citation['page'])).mappings().first()
        if not page or page['text_hash']!=text_hash(page['text']) or citation['text_hash']!=page['text_hash']:
            raise DocumentError(409,'Evidence text integrity check failed')
        if citation['start']<0 or citation['end']>len(page['text']) or page['text'][citation['start']:citation['end']]!=citation['quote']:
            raise DocumentError(409,'Citation does not match the source page')
        if citation.get('document_id',doc['id'])!=doc['id'] or citation.get('checksum',doc['checksum'])!=doc['checksum'] or citation.get('version',doc['version'])!=doc['version']:
            raise DocumentError(409,'Citation version integrity check failed')

    def review(self,actor,doc_id,body):
        try:
            with self.engine.begin() as db:
                doc=self._row(db,actor,doc_id,owner=True)
                extraction=doc['extraction']
                if extraction.get('ocr') and not body.ocr_compared_to_original:
                    raise DocumentError(409,'Compare OCR text with the original PDF and explicitly confirm the comparison')
                if (doc['revision']!=body.revision or doc['checksum']!=body.checksum or
                        extraction['extraction_hash']!=body.extraction_hash):raise DocumentError(409,'Document/extraction revision changed')
                if doc['state']!='EXTRACTED':raise DocumentError(409,'Only unambiguous calendar-day clauses can be approved; clarify or upload a corrected version')
                # Recompute extraction from immutable stored pages before trusting persisted candidates.
                pages=db.execute(select(document_pages.c.text).where(document_pages.c.tenant_id==actor.tenant_id,
                    document_pages.c.document_id==doc_id).order_by(document_pages.c.page)).scalars().all()
                if extract_terms(pages,doc['supplier_id'],extraction.get('ocr'))!=extraction:raise DocumentError(409,'Extraction integrity check failed')
                citations=[self._citation(doc,f) for f in extraction['facts'] if f['field'] in {'grace_days','due_date'}]
                for citation in citations:self._check_citation(db,doc,citation)
                old=db.execute(select(contract_sources).where(contract_sources.c.tenant_id==actor.tenant_id,
                    contract_sources.c.supplier_id==doc['supplier_id'])).mappings().first()
                if old:
                    if old['owner_id']!=actor.subject:raise DocumentError(409,'Only the active source owner can replace it')
                    prior=self._row(db,actor,old['document_id'],owner=True)
                    if prior['contract_ref']==doc['contract_ref'] and prior['version']>=doc['version']:
                        raise DocumentError(409,'Publish a newer document version')
                terms={'grace_days':extraction['grace_days'],'day_basis':extraction['day_basis'],
                    'due_date':extraction['due_date'],'citations':citations}
                terms['terms_hash']=canonical_hash(terms)
                changed=db.execute(update(documents).where(documents.c.tenant_id==actor.tenant_id,documents.c.id==doc_id,
                    documents.c.revision==body.revision,documents.c.state=='EXTRACTED').values(
                        approved_terms=terms,state='READY',revision=body.revision+1))
                if changed.rowcount!=1:raise DocumentError(409,'Document changed while reviewing')
                if old:
                    changed=db.execute(update(contract_sources).where(contract_sources.c.tenant_id==actor.tenant_id,
                        contract_sources.c.supplier_id==doc['supplier_id'],contract_sources.c.document_id==old['document_id'])
                        .values(document_id=doc_id))
                    if changed.rowcount!=1:raise DocumentError(409,'Active contract source changed')
                else:db.execute(insert(contract_sources).values(tenant_id=actor.tenant_id,supplier_id=doc['supplier_id'],document_id=doc_id,owner_id=actor.subject))
                self._audit(db,actor,'document.reviewed_and_published',doc_id,{'version':doc['version'],'checksum':doc['checksum'],
                    'terms_hash':terms['terms_hash'],'ocr_compared_to_original':bool(extraction.get('ocr') and body.ocr_compared_to_original)})
        except IntegrityError:raise DocumentError(409,'Active source changed while reviewing') from None
        return self.get(actor,doc_id)

    def resolve(self,actor,supplier_id,order_id,as_of,fixture):
        with self.engine.connect() as db:
            binding=db.execute(select(contract_sources.c.document_id).where(contract_sources.c.tenant_id==actor.tenant_id,
                contract_sources.c.supplier_id==supplier_id)).scalar_one_or_none()
            if binding is None:return fixture
            try:doc=self._row(db,actor,binding)
            except DocumentError:return None # Never fall back to a mock/older source behind a denied active source.
            if doc['state']!='READY' or (doc['order_ids'] and order_id not in doc['order_ids']):return None
            if doc['effective_from'] and as_of.isoformat()<doc['effective_from']:return None
            if doc['effective_to'] and as_of.isoformat()>doc['effective_to']:return None
            terms=doc['approved_terms']
            if not terms or terms['terms_hash']!=canonical_hash({k:v for k,v in terms.items() if k!='terms_hash'}):
                raise DocumentError(409,'Reviewed terms integrity check failed')
            for citation in terms['citations']:self._check_citation(db,doc,citation)
            pages=db.execute(select(document_pages.c.text).where(document_pages.c.tenant_id==actor.tenant_id,
                document_pages.c.document_id==doc['id']).order_by(document_pages.c.page)).scalars().all()
            fresh=extract_terms(pages,supplier_id,doc['extraction'].get('ocr'))
            if fresh!=doc['extraction'] or any(terms[key]!=fresh[key] for key in ('grace_days','due_date','day_basis')):
                raise DocumentError(409,'Reviewed values do not match the cited clauses')
            return {'id':doc['contract_ref'],'tenant_id':actor.tenant_id,'supplier_id':supplier_id,
                'grace_days':terms['grace_days'],'due_date':terms['due_date'],'day_basis':terms['day_basis'],
                'document_id':doc['id'],'checksum':doc['checksum'],'terms_hash':terms['terms_hash'],
                'version':doc['version'],'location':terms['citations'][0]['location'],'citations':terms['citations']}

    def assert_snapshot_access(self,actor,value,current=False):
        refs={};citations=[];contracts=[]
        def walk(item):
            if isinstance(item,dict):
                if item.get('document_id') and item.get('checksum'):
                    refs[item['document_id']]=item['checksum']
                    if all(key in item for key in ('page','start','end','quote','text_hash')):citations.append(item)
                    if 'terms_hash' in item:contracts.append(item)
                for child in item.values():walk(child)
            elif isinstance(item,list):
                for child in item:walk(child)
        walk(value)
        if not refs:return
        with self.engine.connect() as db:
            for doc_id,checksum in refs.items():
                try:doc=self._row(db,actor,doc_id)
                except DocumentError:raise DocumentError(403,'Evidence access changed; this result is unavailable') from None
                if doc['checksum']!=checksum:raise DocumentError(409,'Evidence version changed')
                for contract in contracts:
                    if contract['document_id']==doc_id and (not doc['approved_terms'] or any(contract.get(key)!=doc['approved_terms'].get(key) for key in ('terms_hash','grace_days','due_date','day_basis'))):
                        raise DocumentError(409,'Cached contract values differ from reviewed evidence')
                for citation in citations:
                    if citation['document_id']==doc_id:self._check_citation(db,doc,citation)
                if current:
                    binding=db.execute(select(contract_sources.c.document_id).where(contract_sources.c.tenant_id==actor.tenant_id,
                        contract_sources.c.supplier_id==doc['supplier_id'])).scalar_one_or_none()
                    if binding!=doc_id:raise DocumentError(409,'Active evidence source changed; review a new plan')

    def search(self,actor,query,limit=10):
        terms=re.findall(r'[^\W_]+',query.casefold(),re.UNICODE)[:30]
        if not terms:return {'items':[],'retrieval':'lexical_bm25','corpus_limited':False}
        with self.engine.connect() as db:
            rows=[dict(r) for r in db.execute(select(document_chunks,documents.c.title,documents.c.version,documents.c.checksum)
                .join(documents,and_(documents.c.tenant_id==document_chunks.c.tenant_id,documents.c.id==document_chunks.c.document_id))
                .where(self.allowed(actor)).order_by(documents.c.id,document_chunks.c.page,document_chunks.c.start_offset).limit(2001)).mappings()]
        limited=len(rows)>2000;rows=rows[:2000]
        counters=[Counter(re.findall(r'[^\W_]+',row['text'].casefold(),re.UNICODE)) for row in rows]
        frequencies={term:sum(term in c for c in counters) for term in set(terms)}
        hits=[]
        average_length=sum(sum(c.values()) for c in counters)/max(1,len(counters)) or 1
        for row,counter in zip(rows,counters):
            score=sum(math.log(1+(len(rows)-frequencies[t]+.5)/(frequencies[t]+.5))*(counter[t]*2.2)/(counter[t]+1.2*(.25+.75*sum(counter.values())/average_length)) for t in set(terms) if counter[t])
            if score:
                hits.append({'document_id':row['document_id'],'title':row['title'],'version':row['version'],
                    'checksum':row['checksum'],'page':row['page'],'chunk_id':row['chunk_id'],
                    'start':row['start_offset'],'end':row['end_offset'],'quote':row['text'],'text_hash':row['text_hash'],
                    'score':round(score,6),'location':f"/v1/documents/{row['document_id']}/pages/{row['page']}"})
        hits.sort(key=lambda r:(-r['score'],r['document_id'],r['page'],r['start']))
        visible=[]
        with self.engine.connect() as db:
            for hit in hits[:limit]:
                try:doc=self._row(db,actor,hit['document_id'])
                except DocumentError:continue
                # Retrieval chunks cite their exact page span; validate against the page, not a model snippet.
                page=db.execute(select(document_pages).where(document_pages.c.tenant_id==actor.tenant_id,
                    document_pages.c.document_id==doc['id'],document_pages.c.page==hit['page'])).mappings().first()
                if not page or page['text_hash']!=text_hash(page['text']) or page['text'][hit['start']:hit['end']]!=hit['quote'] or text_hash(hit['quote'])!=hit['text_hash']:
                    raise DocumentError(409,'Retrieved passage integrity check failed')
                visible.append(hit)
        return {'items':visible,'retrieval':'lexical_bm25','corpus_limited':limited}
