"""Evidence-backed delivery exposure, without invented inventory or schedule arithmetic."""
def project_exposure(findings):
    groups={}
    for finding in findings:
        project=groups.setdefault(finding['project_id'],{'project_id':finding['project_id'],'orders':[],
            'overdue_order_ids':[],'high_risk_order_ids':[],'unresolved_order_ids':[],
            'inventory_shortage':None,'project_delay_days':None,'quantity_total':None})
        project['orders'].append(finding)
        if finding['remaining_quantity']>0 and finding['late_days']>0:project['overdue_order_ids'].append(finding['order_id'])
        if finding['risk']=='HIGH':project['high_risk_order_ids'].append(finding['order_id'])
        if finding['status']!='ASSESSED':project['unresolved_order_ids'].append(finding['order_id'])
    for project in groups.values():
        project['delivery_exposure']='OVERDUE_OUTSTANDING' if project['overdue_order_ids'] else 'NO_OVERDUE_OUTSTANDING_IN_SCOPE'
        project['shared_overdue_dependencies']=len(project['overdue_order_ids'])>1
        project['basis']='Scoped purchase-order delivery evidence; no inventory or project schedule supplied'
    return [groups[key] for key in sorted(groups)]
