"""Tenant-scoped allowlisted tools. No arbitrary callables or model-supplied identities."""
from sqlalchemy.exc import OperationalError
from .schemas import canonical_hash, PlanRejected
from ..store import MockERP
from ..documents.intelligence import assess_with_evidence as assess_order
from ..documents.service import DocumentService
from ..database import engine_for
from types import SimpleNamespace

class TransientToolError(Exception):
    pass

class ToolGateway:
    def __init__(self,path,tenant_id):
        self.erp=MockERP(path,tenant_id)
        self.tenant_id=tenant_id
        self.actor=SimpleNamespace(tenant_id=tenant_id,subject=None)

    def execute(self,task,outputs,as_of):
        try:
            return self._execute(task,outputs,as_of)
        except OperationalError:
            raise TransientToolError('Read adapter temporarily unavailable') from None

    def _execute(self,task,outputs,as_of):
        # Inputs come exclusively from validated filters or successful predecessor snapshots.
        capability=task.capability
        if capability=='purchase_orders.read':
            f=task.filters
            items=[o for o in self.erp.orders() if
                (not f.outstanding_only or o['quantity']>o['delivered_quantity']) and
                (f.supplier_id is None or o['supplier_id']==f.supplier_id) and
                (f.project_id is None or o['project_id']==f.project_id) and
                (not f.order_ids or o['id'] in f.order_ids)]
            return {'items':items,'snapshot_hash':canonical_hash(items)}
        if capability=='contracts.read':
            orders=outputs['purchase_orders.read']['items']
            engine=engine_for(self.erp.path)
            try:
                source=DocumentService(engine)
                by_order={o['id']:source.resolve(self.actor,o['supplier_id'],o['id'],as_of,
                    self.erp.contract(o['supplier_id'])) for o in orders}
            finally:engine.dispose()
            values={o['supplier_id']:by_order[o['id']] for o in orders}
            return {'items':values,'by_order':by_order,'snapshot_hash':canonical_hash(by_order)}
        if capability=='procurement.assess':
            orders=outputs['purchase_orders.read']['items'];contracts=outputs['contracts.read']['items']
            return {'findings':[assess_order(o,outputs['contracts.read'].get('by_order',{}).get(o['id'],contracts[o['supplier_id']]),as_of) for o in orders]}
        if capability=='findings.verify':
            orders=outputs['purchase_orders.read']['items'];contracts=outputs['contracts.read']['items']
            findings=outputs['procurement.assess']['findings']
            by_order=outputs['contracts.read'].get('by_order',{})
            expected=[assess_order(o,by_order.get(o['id'],contracts[o['supplier_id']]),as_of) for o in orders]
            if findings!=expected:
                raise PlanRejected('Finding verification failed')
            if any(o['tenant_id']!=self.tenant_id for o in orders):
                raise PlanRejected('Source tenant mismatch')
            if any(c and c['tenant_id']!=self.tenant_id for c in contracts.values()):
                raise PlanRejected('Contract tenant mismatch')
            unresolved=[r['order_id'] for r in findings if r['status']!='ASSESSED']
            conflicts=[r['order_id'] for r in findings if r['status']=='SOURCE_CONFLICT']
            return {'valid':True,'checked_findings':len(findings),'unresolved_order_ids':unresolved,'conflicting_order_ids':conflicts,
                    'evidence_snapshot_hash':canonical_hash({'orders':orders,'contracts':contracts,'by_order':by_order})}
        if capability=='report.compose':
            verified=outputs['findings.verify']
            if not verified['valid']:
                raise PlanRejected('Unverified findings cannot be reported')
            return {'findings':outputs['procurement.assess']['findings'],'verification':verified,
                    'unresolved_order_ids':verified['unresolved_order_ids'],'conflicting_order_ids':verified.get('conflicting_order_ids',[]),'external_actions':0}
        raise PlanRejected('Capability is not allowlisted')
