"""Order-scoped date amendments with independent, evidence-bound human authorization."""
import uuid
from datetime import date,datetime,timezone,timedelta
from types import SimpleNamespace
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from sqlalchemy import select,insert,update,and_,or_,func
from sqlalchemy.exc import IntegrityError
from .database import amendment_proposals as proposals,active_amendments,orders,users,contract_sources
from .agents.schemas import canonical_hash
from .documents.schemas import DocumentError
from .documents.service import DocumentService

class ProposalInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    order_id:str=Field(min_length=1,max_length=80)
    base_document_id:str=Field(min_length=1,max_length=80)
    amendment_document_id:str=Field(min_length=1,max_length=80)
    approver_id:str=Field(min_length=1,max_length=80)
    effective_from:date

class AmendmentDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    decision:Literal['approve','reject']
    signed_copy_compared:StrictBool=False

def timestamp():return datetime.now(timezone.utc).isoformat()

class AmendmentService:
    def __init__(self,engine):self.engine=engine;self.docs=DocumentService(engine)
    def _row(self,db,actor,proposal_id):
        row=db.execute(select(proposals).where(proposals.c.tenant_id==actor.tenant_id,proposals.c.id==proposal_id,
            or_(proposals.c.requester_id==actor.subject,proposals.c.approver_id==actor.subject))).mappings().first()
        if not row:raise DocumentError(404,'Amendment not found')
        row=dict(row)
        self.docs._row(db,actor,row['base_document_id']);self.docs._row(db,actor,row['amendment_document_id'])
        return row
    def _current(self,db,tenant,order_id):
        return db.execute(select(active_amendments.c.amendment_id).where(active_amendments.c.tenant_id==tenant,
            active_amendments.c.order_id==order_id)).scalar_one_or_none()
    def _person(self,db,tenant,subject,roles):
        return db.execute(select(users.c.id).where(users.c.tenant_id==tenant,users.c.id==subject,
            users.c.active.is_(True),users.c.role.in_(roles))).scalar_one_or_none() is not None
    def _snapshot(self,db,actor,body):
        order=db.execute(select(orders).where(orders.c.tenant_id==actor.tenant_id,orders.c.id==body.order_id)).mappings().first()
        if not order:raise DocumentError(404,'Order not found')
        base=self.docs.reviewed_document(db,actor,body.base_document_id)
        amendment=self.docs.reviewed_document(db,actor,body.amendment_document_id)
        if (base['kind']!='contract' or amendment['kind']!='amendment' or base['supplier_id']!=order['supplier_id'] or
                amendment['supplier_id']!=order['supplier_id'] or body.order_id not in amendment['order_ids'] or
                (base['order_ids'] and body.order_id not in base['order_ids'])):
            raise DocumentError(422,'Amendment must explicitly cover this order and match the reviewed base supplier')
        binding=db.execute(select(contract_sources.c.document_id).where(contract_sources.c.tenant_id==actor.tenant_id,
            contract_sources.c.supplier_id==order['supplier_id'])).scalar_one_or_none()
        if binding!=base['id']:raise DocumentError(409,'Base contract source changed')
        for doc in (base,amendment):
            if (doc['effective_from'] and body.effective_from.isoformat()<doc['effective_from']) or (doc['effective_to'] and body.effective_from.isoformat()>doc['effective_to']):
                raise DocumentError(422,'Amendment effective date is outside reviewed document scope')
        if not amendment['approved_terms']['due_date']:raise DocumentError(422,'Explicit amended delivery date required')
        return {'order':dict(order),'base':self._document_snapshot(base),'amendment':self._document_snapshot(amendment)}
    def _document_snapshot(self,doc):
        return {k:doc[k] for k in ('id','revision','checksum','version','kind','order_ids','effective_from','effective_to','approved_terms')}
    def create(self,actor,body):
        try:
            with self.engine.begin() as db:
                if not self._person(db,actor.tenant_id,actor.subject,['manager','analyst']):raise DocumentError(403,'Current procurement permission required')
                if body.approver_id==actor.subject or not self._person(db,actor.tenant_id,body.approver_id,['approver']):
                    raise DocumentError(422,'Assign an active independent company approver')
                input_hash=canonical_hash(body.model_dump(mode='json'))
                prior=db.execute(select(proposals).where(proposals.c.tenant_id==actor.tenant_id,
                    proposals.c.requester_id==actor.subject,proposals.c.request_id==body.request_id)).mappings().first()
                if prior:
                    if prior['input_hash']!=input_hash:raise DocumentError(409,'Request ID already used for a different amendment')
                    return self._row(db,actor,prior['id'])
                snapshot=self._snapshot(db,actor,body)
                reviewer=SimpleNamespace(tenant_id=actor.tenant_id,subject=body.approver_id)
                self.docs._row(db,reviewer,body.base_document_id);self.docs._row(db,reviewer,body.amendment_document_id)
                previous=self._current(db,actor.tenant_id,body.order_id)
                if previous:
                    prior=db.execute(select(proposals).where(proposals.c.tenant_id==actor.tenant_id,proposals.c.id==previous)).mappings().one()
                    if body.effective_from.isoformat()<prior['effective_from']:raise DocumentError(422,'A replacement cannot become effective before the amendment it supersedes')
                if db.execute(select(func.count()).select_from(proposals).where(proposals.c.tenant_id==actor.tenant_id)).scalar_one()>=100:
                    raise DocumentError(409,'Demo company amendment limit reached')
                payload={'tenant_id':actor.tenant_id,'requester_id':actor.subject,'approver_id':body.approver_id,
                    'effective_from':body.effective_from.isoformat(),'supersedes_id':previous,**snapshot}
                row={'tenant_id':actor.tenant_id,'id':str(uuid.uuid4()),'requester_id':actor.subject,'approver_id':body.approver_id,
                    'request_id':body.request_id,'input_hash':input_hash,'order_id':body.order_id,'base_document_id':body.base_document_id,
                    'amendment_document_id':body.amendment_document_id,'effective_from':body.effective_from.isoformat(),
                    'supersedes_id':previous,'state':'WAITING_APPROVAL','payload':payload,'action_hash':canonical_hash(payload),
                    'created_at':timestamp(),'decided_at':None,'signed_copy_compared':False}
                db.execute(insert(proposals).values(**row));self.docs._audit(db,actor,'amendment.proposed',row['id'],{'action_hash':row['action_hash']})
                return row
        except IntegrityError:raise DocumentError(409,'Amendment request conflicted; reload and retry') from None
    def list(self,actor):
        with self.engine.connect() as db:
            ids=db.execute(select(proposals.c.id).where(proposals.c.tenant_id==actor.tenant_id,
                or_(proposals.c.requester_id==actor.subject,proposals.c.approver_id==actor.subject))
                .order_by(proposals.c.created_at.desc()).limit(100)).scalars().all()
            visible=[]
            for proposal_id in ids:
                try:visible.append(self._row(db,actor,proposal_id))
                except DocumentError:continue
            return {'items':visible,'scope':'owner_or_assigned_and_current_document_acl','limited_to':100}
    def get(self,actor,proposal_id):
        with self.engine.connect() as db:return self._row(db,actor,proposal_id)
    def _validate(self,db,row):
        self._integrity(row)
        if not self._person(db,row['tenant_id'],row['requester_id'],['manager','analyst']) or not self._person(db,row['tenant_id'],row['approver_id'],['approver']):
            raise DocumentError(409,'Amendment authority changed; obtain fresh independent review')
        body=SimpleNamespace(order_id=row['order_id'],base_document_id=row['base_document_id'],
            amendment_document_id=row['amendment_document_id'],effective_from=date.fromisoformat(row['effective_from']))
        for subject in (row['requester_id'],row['approver_id']):
            actor=SimpleNamespace(tenant_id=row['tenant_id'],subject=subject)
            snapshot=self._snapshot(db,actor,body)
            if snapshot!={k:row['payload'][k] for k in ('order','base','amendment')}:
                raise DocumentError(409,'Order or reviewed evidence changed; propose a fresh amendment')
    def _integrity(self,row):
        if (canonical_hash(row['payload'])!=row['action_hash'] or any(row[k]!=row['payload'].get(k)
                for k in ('tenant_id','requester_id','approver_id','effective_from','supersedes_id'))):
            raise DocumentError(409,'Amendment payload integrity check failed')
    def decide(self,actor,proposal_id,body):
        try:
            with self.engine.begin() as db:
                row=self._row(db,actor,proposal_id)
                if actor.subject!=row['approver_id'] or not self._person(db,actor.tenant_id,actor.subject,['approver']):raise DocumentError(403,'Only the assigned independent approver may decide')
                if body.action_hash!=row['action_hash']:raise DocumentError(409,'Amendment hash changed; reload')
                desired='APPROVED' if body.decision=='approve' else 'REJECTED'
                if desired=='APPROVED' and not body.signed_copy_compared:raise DocumentError(409,'Explicit signed-copy and authority comparison is required')
                if row['state']==desired:return row
                if row['state']!='WAITING_APPROVAL':raise DocumentError(409,'Amendment already decided')
                if datetime.now(timezone.utc)-datetime.fromisoformat(row['created_at'])>=timedelta(hours=24):raise DocumentError(409,'Proposal expired; obtain a fresh review')
                self._validate(db,row)
                if desired=='APPROVED':
                    if self._current(db,actor.tenant_id,row['order_id'])!=row['supersedes_id']:raise DocumentError(409,'Another amendment was approved; propose against the latest source')
                    if row['supersedes_id']:
                        changed=db.execute(update(active_amendments).where(active_amendments.c.tenant_id==actor.tenant_id,
                            active_amendments.c.order_id==row['order_id'],active_amendments.c.amendment_id==row['supersedes_id']).values(amendment_id=row['id']))
                        if changed.rowcount!=1:raise DocumentError(409,'Active amendment changed')
                    else:db.execute(insert(active_amendments).values(tenant_id=actor.tenant_id,order_id=row['order_id'],amendment_id=row['id']))
                decided_at=timestamp()
                changed=db.execute(update(proposals).where(proposals.c.tenant_id==actor.tenant_id,proposals.c.id==row['id'],
                    proposals.c.state=='WAITING_APPROVAL').values(state=desired,decided_at=decided_at,signed_copy_compared=body.signed_copy_compared))
                if changed.rowcount!=1:raise DocumentError(409,'Amendment decision changed')
                self.docs._audit(db,actor,'amendment.'+body.decision,row['id'],{'action_hash':row['action_hash'],'signed_copy_compared':body.signed_copy_compared})
                return row|{'state':desired,'decided_at':decided_at,'signed_copy_compared':body.signed_copy_compared}
        except IntegrityError:raise DocumentError(409,'Concurrent amendment approval; reload the current source') from None
    def apply(self,actor,order_id,as_of,base):
        with self.engine.connect() as db:
            current=self._current(db,actor.tenant_id,order_id);visited=set()
            while current:
                if current in visited or len(visited)>=100:raise DocumentError(409,'Amendment chain integrity check failed')
                visited.add(current)
                row=db.execute(select(proposals).where(proposals.c.tenant_id==actor.tenant_id,proposals.c.id==current,proposals.c.order_id==order_id)).mappings().first()
                if not row or row['state']!='APPROVED' or not row['signed_copy_compared']:raise DocumentError(409,'Approved amendment integrity check failed')
                self._integrity(row)
                if row['effective_from']>as_of.isoformat():current=row['supersedes_id'];continue
                try:
                    self._validate(db,row)
                    amended=self.docs.reviewed_document(db,actor,row['amendment_document_id'])
                    if not base or base.get('document_id')!=row['base_document_id']:return None
                    if amended['effective_to'] and as_of.isoformat()>amended['effective_to']:return None
                except DocumentError:return None # Never expose denied amendment evidence or fall back to obsolete dates.
                terms=amended['approved_terms']
                amendment={'amendment_id':row['id'],'action_hash':row['action_hash'],'order_id':order_id,
                    'as_of':as_of.isoformat(),'effective_from':row['effective_from'],'document_id':amended['id'],
                    'checksum':amended['checksum'],'version':amended['version'],'source_id':amended['contract_ref'],
                    **{k:terms[k] for k in ('terms_hash','due_date','grace_days','day_basis','citations')},
                    'authority':'independent_human_signed_copy_attestation','signature_cryptographically_verified':False}
                return base|{'amendment':amendment}
            return base
    def assert_current(self,actor,amendment):
        # Re-resolve through the same current source/ACL/role checks; snapshots cannot authorize old dates.
        with self.engine.connect() as db:
            order=db.execute(select(orders).where(orders.c.tenant_id==actor.tenant_id,orders.c.id==amendment['order_id'])).mappings().first()
        if not order:raise DocumentError(409,'Amended order is unavailable')
        current=self.docs.resolve(actor,order['supplier_id'],order['id'],date.fromisoformat(amendment['as_of']),None)
        if not current or canonical_hash(current.get('amendment'))!=canonical_hash(amendment):raise DocumentError(409,'Active amendment changed; review a new plan')
    def stamp(self,actor,order_id,as_of):
        with self.engine.connect() as db:
            current=self._current(db,actor.tenant_id,order_id);visited=set();chosen=None
            while current:
                if current in visited or len(visited)>=100:raise DocumentError(409,'Amendment chain integrity check failed')
                visited.add(current)
                row=db.execute(select(proposals).where(proposals.c.tenant_id==actor.tenant_id,proposals.c.id==current,
                    proposals.c.order_id==order_id)).mappings().first()
                if not row or row['state']!='APPROVED':raise DocumentError(409,'Amendment authority integrity check failed')
                self._integrity(row)
                if row['effective_from']<=as_of.isoformat():chosen={'id':row['id'],'hash':row['action_hash']};break
                current=row['supersedes_id']
            return {'order_id':order_id,'as_of':as_of.isoformat(),'amendment_authority_fingerprint':canonical_hash(chosen)}
