"""Supply-chain graph, disruption analysis, and explainable healing recommendations."""
from __future__ import annotations
from collections import deque, defaultdict
from typing import Any
import networkx as nx

REQUIRED = ("edge_id","supplier","buyer","item_id","supplier_country","buyer_country","unit_cost","lead_time","spare_capacity")

def _number(e, field, i):
    v=e.get(field)
    if isinstance(v,bool) or not isinstance(v,(int,float)) or v < 0:
        raise ValueError(f"Edge #{i}: {field} must be a non-negative number")
    return float(v)

def hydrate_graph(edges_list: list[dict[str, Any]]) -> nx.MultiDiGraph:
    """Validate and hydrate a MultiDiGraph; supports multiple lanes per facility pair."""
    if not isinstance(edges_list,list): raise TypeError("edges_list must be a list")
    g=nx.MultiDiGraph(); g.graph['finished_items']=set()
    ids=set()
    for i,e in enumerate(edges_list,1):
        if not isinstance(e,dict): raise TypeError(f"Edge #{i} must be a dictionary")
        missing=[k for k in REQUIRED if k not in e]
        if missing: raise ValueError(f"Edge #{i} missing: {', '.join(missing)}")
        for k in ("edge_id","supplier","buyer","item_id","supplier_country","buyer_country"):
            if not isinstance(e[k],str) or not e[k].strip(): raise ValueError(f"Edge #{i}: {k} must be non-empty text")
        eid=e['edge_id'].strip()
        if eid in ids: raise ValueError(f"Duplicate edge_id: {eid}")
        ids.add(eid)
        data=dict(e)
        for f in ("unit_cost","lead_time","spare_capacity"):
            data[f]=_number(e,f,i)
        data['capacity']=_number({"capacity":e.get('capacity',e['spare_capacity'])},'capacity',i)
        data['route_countries']=list(e.get('route_countries') or [e['supplier_country'],e['buyer_country']])
        data['risk']=float(e.get('risk',0.2)); data['qualified']=bool(e.get('qualified',True)); data['status']='healthy'
        g.add_node(e['supplier'], country=e['supplier_country'], kind='facility')
        g.add_node(e['buyer'], country=e['buyer_country'], kind='facility')
        g.add_edge(e['supplier'],e['buyer'],key=eid,**data)
        if data.get('is_finished_good') or data.get('item_tier')==0: g.graph['finished_items'].add(data['item_id'])
    # Cycles in facility graph are invalid for this MVP's tier traversal.
    simple=nx.DiGraph(); simple.add_edges_from((u,v) for u,v in g.edges())
    try: cycle=nx.find_cycle(simple)
    except nx.NetworkXNoCycle: cycle=None
    if cycle:
        path=' -> '.join([str(cycle[0][0])] + [str(e[1]) for e in cycle])
        raise ValueError(f"Supply graph contains a directed cycle: {path}")
    return g

def edge_records(g: nx.MultiDiGraph) -> list[dict]:
    out=[]
    for _,_,_,d in g.edges(keys=True,data=True): out.append(dict(d))
    return out

def get_upstream_dependencies(graph: nx.MultiDiGraph, item_id: str) -> list[dict]:
    """Return every lane supplying item_id and recursively traverse upstream facility inputs."""
    if not item_id or not item_id.strip(): raise ValueError("item_id must be non-empty")
    result=[]; seen=set(); queue=deque()
    for u,v,k,d in graph.edges(keys=True,data=True):
        if d.get('item_id','').casefold()==item_id.strip().casefold():
            queue.append((u,d.get('item_id')))
            key=(u,v,k)
            if key not in seen: result.append(dict(d)); seen.add(key)
    visited=set()
    while queue:
        facility,_=queue.popleft()
        if facility in visited: continue
        visited.add(facility)
        for u,v,k,d in graph.in_edges(facility,keys=True,data=True):
            key=(u,v,k)
            if key not in seen: result.append(dict(d)); seen.add(key); queue.append((u,d.get('item_id')))
    return result

def apply_disruption(edges: list[dict], event: dict) -> tuple[list[dict],dict]:
    """Return a changed copy of edge records and an event summary; does not mutate live data."""
    kind=str(event.get('type','war')).lower(); country=str(event.get('country','')).strip()
    if not country: raise ValueError('event country is required')
    severity=float(event.get('severity',1.0)); severity=max(0.0,min(1.0,severity))
    target=str(event.get('target_country') or '')
    changed=[]
    for raw in edges:
        e=dict(raw); sc=e.get('supplier_country',''); bc=e.get('buyer_country',''); route=e.get('route_countries',[])
        hit=(sc.casefold()==country.casefold() or country.casefold() in [str(x).casefold() for x in route])
        bilateral=(sc.casefold()==country.casefold() and (not target or bc.casefold()==target.casefold())) or (target and sc.casefold()==target.casefold() and bc.casefold()==country.casefold())
        if kind in ('sanction','embargo') and bilateral:
            e['status']='disrupted'; e['available_capacity']=0; e['disruption_reason']=f"{kind.title()} between {country} and {target or bc}"
        elif kind=='tariff' and bilateral:
            rate=float(event.get('tariff_rate',0.25)); e['effective_cost']=round(float(e['unit_cost'])*(1+max(0,rate)),2); e['disruption_reason']=f"Tariff {rate:.0%} on trade involving {country}"
        elif kind in ('war','country_block') and hit:
            e['status']='disrupted'; e['available_capacity']=0; e['disruption_reason']=f"War/blocked route involving {country}"
        elif kind=='disaster' and (sc.casefold()==country.casefold() or country.casefold() in [str(x).casefold() for x in route]):
            e['available_capacity']=round(float(e.get('spare_capacity',0))*(1-severity),2); e['status']='degraded' if severity>0 else 'healthy'; e['disruption_reason']=f"Disaster severity {severity:.0%} in {country}"
        elif kind=='chokepoint_closure' and str(event.get('chokepoint','')).casefold() in [str(x).casefold() for x in e.get('route_chokepoints',[])]:
            e['status']='disrupted'; e['available_capacity']=0; e['disruption_reason']=f"Closed chokepoint: {event.get('chokepoint')}"
        else:
            e.setdefault('available_capacity',float(e.get('spare_capacity',0)))
            e.setdefault('effective_cost',float(e.get('unit_cost',0)))
        changed.append(e)
    summary={'type':kind,'country':country,'target_country':target or None,'severity':severity,'affected_edges':sum(1 for e in changed if e.get('status')=='disrupted' or e.get('status')=='degraded' or e.get('effective_cost',e.get('unit_cost'))!=e.get('unit_cost'))}
    return changed,summary

def calculate_disruption_impact(graph: nx.MultiDiGraph, blocked_country: str) -> dict:
    """Backward-compatible war-style impact report for all lanes originating/routing through country."""
    edges=edge_records(graph)
    changed,summary=apply_disruption(edges,{'type':'war','country':blocked_country,'severity':1})
    broken_ids={e['edge_id'] for e in changed if e.get('status')=='disrupted'}
    affected_buyers=set(); at_risk=set(); capacity=0.0
    for e in changed:
        if e['edge_id'] in broken_ids:
            affected_buyers.add(e['buyer']); at_risk.add(e.get('product_id') or e['item_id']); capacity+=float(e.get('spare_capacity',0))
    # Cascade forward from directly affected buyers to downstream customers/products.
    q=deque(affected_buyers); seen=set(affected_buyers)
    while q:
        node=q.popleft()
        for _,v,_,d in graph.out_edges(node,keys=True,data=True):
            at_risk.add(d.get('product_id') or d.get('item_id'))
            if v not in seen: seen.add(v); q.append(v); affected_buyers.add(v)
    return {'blocked_country':blocked_country,'affected_buyers':sorted(affected_buyers),'finished_items_at_risk':sorted(x for x in at_risk if x),'disrupted_capacity':round(capacity,2),'disrupted_edges':sorted(broken_ids)}

def analyze_event(edges: list[dict], event: dict) -> dict:
    changed,summary=apply_disruption(edges,event)
    broken=[e for e in changed if e.get('status') in ('disrupted','degraded') or e.get('effective_cost',e.get('unit_cost'))!=e.get('unit_cost')]
    affected_buyers=sorted({e['buyer'] for e in broken})
    products=sorted({e.get('product_id') or e['item_id'] for e in broken})
    disrupted_capacity=round(sum(max(0,float(e.get('spare_capacity',0))-float(e.get('available_capacity',e.get('spare_capacity',0)))) for e in broken),2)
    return {'event':summary,'affected_buyers':affected_buyers,'products_at_risk':products,'disrupted_capacity':disrupted_capacity,'affected_edges':broken,'changed_edges':changed}

def recommend_healing(edges: list[dict], event: dict, demand: float|None=None) -> dict:
    """Rank feasible replacement lanes and allocate requested quantity by essential priority then score."""
    impact=analyze_event(edges,event); changed=impact['changed_edges']; broken=impact['affected_edges']
    recommendations=[]; remaining_by_edge={e['edge_id']:float(e.get('spare_capacity',0)) for e in changed if e.get('status','healthy')=='healthy'}
    # Process essential items first; partial fulfillment is explicit.
    priority={'P1':0,'P2':1,'P3':2}
    broken=sorted(broken,key=lambda e:(priority.get(e.get('priority','P3'),2),e.get('item_id','')))
    for edge in broken:
        item=edge.get('item_id'); buyer=edge.get('buyer'); needed=float(demand if demand is not None else edge.get('demand',min(float(edge.get('spare_capacity',0)),100)))
        candidates=[]
        for cand in changed:
            if cand.get('item_id')!=item or cand.get('buyer')!=buyer or cand.get('edge_id')==edge.get('edge_id'): continue
            if cand.get('status')!='healthy' or not cand.get('qualified',True): continue
            route=[str(x).casefold() for x in cand.get('route_countries',[])]
            if event.get('type','').lower() in ('war','sanction','embargo') and str(event.get('country','')).casefold() in route: continue
            cap=remaining_by_edge.get(cand['edge_id'],0)
            if cap<=0: continue
            cost=float(cand.get('effective_cost',cand.get('unit_cost',0))); lead=float(cand.get('lead_time',0)); risk=float(cand.get('risk',0.2))
            score=cost*0.45 + lead*1.5 + (1-min(cap,max(1,needed))/max(cap,1))*15 + risk*30
            candidates.append((score,cand,cap,cost))
        candidates.sort(key=lambda x:x[0])
        allocations=[]; left=needed
        for score,cand,cap,cost in candidates:
            qty=min(left,cap)
            if qty<=0: continue
            allocations.append({'supplier':cand['supplier'],'supplier_country':cand['supplier_country'],'edge_id':cand['edge_id'],'quantity':round(qty,2),'unit_cost':cost,'lead_time':cand['lead_time'],'score':round(score,3),'reason':f"Qualified, healthy route; score combines cost, lead time, capacity fit and risk ({cand.get('risk',0.2):.2f})."})
            remaining_by_edge[cand['edge_id']]-=qty; left-=qty
            if left<=0: break
        recommendations.append({'disrupted_edge_id':edge['edge_id'],'item_id':item,'buyer':buyer,'priority':edge.get('priority','P3'),'requested_quantity':round(needed,2),'allocations':allocations,'fulfilled_quantity':round(needed-left,2),'unmet_quantity':round(left,2),'status':'healed' if left<=0 else ('partially_healed' if left<needed else 'no_alternative')})
    return {'impact':{k:v for k,v in impact.items() if k!='changed_edges'},'recommendations':recommendations,'total_requested':round(sum(x['requested_quantity'] for x in recommendations),2),'total_fulfilled':round(sum(x['fulfilled_quantity'] for x in recommendations),2),'total_unmet':round(sum(x['unmet_quantity'] for x in recommendations),2),'note':'Recommendations are simulated and require human approval before execution.'}
