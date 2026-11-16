"""Evidence-aware assessment: factual lateness is separated from contractual certainty."""
from ..domain import assess_order


def assess_with_evidence(order,contract,as_of):
    if contract is None:return assess_order(order,None,as_of)
    if not contract.get('document_id'):return assess_order(order,contract,as_of)
    source_due=contract.get('due_date')
    citations=contract['citations']
    if contract.get('amendment'):
        amendment=contract['amendment'];effective=order|{'due_date':amendment['due_date']}
        grace=amendment['grace_days'] if amendment['grace_days'] is not None else contract['grace_days']
        result=assess_order(effective,contract|{'grace_days':grace},as_of)
        result['evidence']=[result['evidence'][0],*citations,*amendment['citations']]
        return result|{'source_mode':'reviewed_amendment','due_date_basis':'independent_human_reviewed_amendment',
            'erp_due_date':order['due_date'],'effective_due_date':amendment['due_date'],
            'erp_late_days':assess_order(order,None,as_of)['late_days'],'amendment':amendment,
            'explanation':'Uses the independently attested amendment for this order; ERP date remains unchanged and visible.'}
    if source_due and source_due!=order['due_date']:
        result=assess_order(order,None,as_of)
        return result|{'status':'SOURCE_CONFLICT','evidence':result['evidence']+citations,
            'reason_code':'ERP_CONTRACT_DATE_CONFLICT','erp_due_date':order['due_date'],
            'contract_due_date':source_due,'explanation':'ERP and reviewed contract dates conflict; contractual priority is unknown.'}
    result=assess_order(order,contract,as_of)
    result['evidence']=[result['evidence'][0],*citations]
    result['source_mode']='reviewed_pdf'
    result['explanation']=(
        'Outstanding critical material exceeds the reviewed calendar-day grace clause.' if result['risk']=='HIGH' else
        'Outstanding noncritical material exceeds the reviewed calendar-day grace clause.' if result['risk']=='MEDIUM' else
        'No current exposure beyond the reviewed calendar-day grace clause.')
    return result
