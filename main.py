"""Self-Healing Supply Chain MVP API."""
from __future__ import annotations
from pathlib import Path
import json
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from app.database import init_db, upsert_supplier, search_catalog, save_event, list_events, list_suppliers, set_verified
from app.engine import hydrate_graph, edge_records, get_upstream_dependencies, calculate_disruption_impact, analyze_event, recommend_healing
from app.seed import build_seed_edges, PRODUCTS, COUNTRIES
from app.scraper import scrape_and_import, import_directory_html

BASE=Path(__file__).resolve().parents[1]
app=FastAPI(title='Self-Healing Supply Chain',version='1.0.0',description='MVP API for multi-tier supply graph, disruptions, healing and supplier discovery')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
EDGES=build_seed_edges()
GRAPH=hydrate_graph(EDGES)
class CatalogItem(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    item_id:str|None=None; product_name:str=Field(min_length=1,max_length=200); category:str|None=None; hs_code:str|None=None; specifications:str|None=None; min_order_quantity:float=Field(default=1,ge=0); capacity:float=Field(default=0,ge=0); spare_capacity:float=Field(default=0,ge=0); unit_price:float=Field(default=0,ge=0); currency:str='USD'; lead_time_days:int=Field(default=0,ge=0); incoterms:str|None=None; certifications:list[str]=[]; priority:str='P3'; confidence:float=Field(default=1,ge=0,le=1)
class SupplierIn(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    company_name:str=Field(min_length=1,max_length=200); country:str=Field(min_length=1,max_length=100); region:str|None=None; address:str|None=None; latitude:float|None=Field(default=None,ge=-90,le=90); longitude:float|None=Field(default=None,ge=-180,le=180); contact_email:str|None=None; website:str|None=None; business_registration:str|None=None; tax_id:str|None=None; certifications:list[str]=[]; export_countries:list[str]=[]; items:list[CatalogItem]=Field(min_length=1,max_length=100)
class EventIn(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    type:str=Field(pattern='^(tariff|sanction|embargo|war|disaster|chokepoint_closure|country_block)$'); country:str=Field(min_length=1); target_country:str|None=None; chokepoint:str|None=None; severity:float=Field(default=1,ge=0,le=1); tariff_rate:float=Field(default=.25,ge=0,le=10); start_date:str|None=None; end_date:str|None=None; source:str='manual'; confidence:float=Field(default=1,ge=0,le=1); approved:bool=True
class ScrapeIn(BaseModel): url:str=Field(min_length=8); timeout:float=Field(default=5,ge=1,le=8)
class VerifyIn(BaseModel): verified:bool
@app.on_event('startup')
def startup(): init_db()
@app.get('/')
def home(): return {'name':'Self-Healing Supply Chain','version':'1.0.0','status':'running','docs':'/docs','seed_edges':len(EDGES),'products':len(PRODUCTS),'countries':len(COUNTRIES)}
@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/seed/summary')
def seed_summary(): return {'products':PRODUCTS,'countries':COUNTRIES,'edge_count':len(EDGES),'node_count':GRAPH.number_of_nodes(),'tier_count':4}
@app.post('/supplier',status_code=201)
def register_supplier(payload:SupplierIn):
    try:
        data=payload.model_dump(); items=data.pop('items'); sid,count=upsert_supplier(data,items)
        return {'message':'Supplier profile and catalog saved','supplier_id':sid,'catalog_items_saved':count,'source_label':'Self-registered - unverified','verification_required':True}
    except Exception as e: raise HTTPException(status_code=400,detail=str(e)) from e
@app.get('/search')
def search(product_name:str|None=None,hs_code:str|None=None,country:str|None=None,category:str|None=None,verified_only:bool=False,min_capacity:float|None=Query(None,ge=0),max_lead_time_days:int|None=Query(None,ge=0),certification:str|None=None,limit:int=Query(50,ge=1,le=100)):
    try: rows=search_catalog(product_name,hs_code,country,category,verified_only,limit,min_capacity,max_lead_time_days,certification)
    except Exception as e: raise HTTPException(status_code=500,detail='Supplier search failed') from e
    return {'count':len(rows),'results':rows}
@app.get('/suppliers')
def suppliers(): return {'count':len(list_suppliers()),'results':list_suppliers()}
@app.patch('/suppliers/{supplier_id}/verification')
def verify_supplier(supplier_id:str,payload:VerifyIn):
    if not set_verified(supplier_id,payload.verified): raise HTTPException(status_code=404,detail='Supplier not found')
    return {'supplier_id':supplier_id,'verified':payload.verified,'source_label':'Verified' if payload.verified else 'Self-registered - unverified'}
@app.get('/graph/edges')
def graph_edges(): return {'count':len(EDGES),'results':EDGES}
@app.get('/graph/dependencies')
def dependencies(item_id:str): return {'item_id':item_id,'count':len(get_upstream_dependencies(GRAPH,item_id)),'dependencies':get_upstream_dependencies(GRAPH,item_id)}
@app.get('/impact')
def impact(country:str,event_type:str='war',target_country:str|None=None,severity:float=Query(1,ge=0,le=1),tariff_rate:float=.25):
    event={'type':event_type,'country':country,'target_country':target_country,'severity':severity,'tariff_rate':tariff_rate}
    try: return analyze_event(EDGES,event)
    except ValueError as e: raise HTTPException(status_code=400,detail=str(e)) from e
@app.post('/events',status_code=201)
def create_event(event:EventIn):
    data=event.model_dump(); eid=save_event(data); return {'event_id':eid,'event':data,'message':'Event saved'}
@app.get('/events')
def events(): return {'results':list_events()}
@app.post('/heal')
def heal(event:EventIn, demand:float|None=Query(default=None,ge=0)):
    try: return recommend_healing(EDGES,event.model_dump(),demand)
    except ValueError as e: raise HTTPException(status_code=400,detail=str(e)) from e
@app.post('/simulate')
def simulate(event:EventIn, demand:float|None=Query(default=None,ge=0)):
    # Simulation is pure: it does not change EDGES or GRAPH.
    return {'simulation_only':True,'impact':analyze_event(EDGES,event.model_dump()),'healing':recommend_healing(EDGES,event.model_dump(),demand)}
@app.post('/scrape')
def scrape(payload:ScrapeIn):
    try: return scrape_and_import(payload.url,payload.timeout)
    except (ValueError,RuntimeError) as e: raise HTTPException(status_code=400,detail=f'Scrape failed: {e}') from e
@app.post('/scrape/sample')
def scrape_sample():
    # Safe, deterministic demo path: imports the bundled fixture without external network access.
    sample_path=BASE/'data'/'sample_directory.html'
    try: return import_directory_html(sample_path.read_text(encoding='utf-8'),'bundled://data/sample_directory.html')
    except Exception as e: raise HTTPException(status_code=500,detail=f'Sample import failed: {e}') from e
@app.get('/products')
def products(): return {'results':PRODUCTS}
@app.get('/countries')
def countries(): return {'results':COUNTRIES}
