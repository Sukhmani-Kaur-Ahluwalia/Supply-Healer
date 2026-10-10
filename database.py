# SQLite persistence for local MVP. Production AWS deployment needs a durable adapter.
from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
def db_path(): return os.getenv('DATABASE_PATH',str(ROOT/'data'/'supply_chain.db'))
def connect():
    Path(db_path()).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(db_path(),timeout=10); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS suppliers(id TEXT PRIMARY KEY, company_name TEXT NOT NULL, country TEXT NOT NULL, region TEXT, address TEXT, latitude REAL, longitude REAL, contact_email TEXT, website TEXT, business_registration TEXT, tax_id TEXT, certifications TEXT, export_countries TEXT, verified INTEGER DEFAULT 0, source_label TEXT DEFAULT 'Self-registered - unverified', last_updated TEXT DEFAULT CURRENT_TIMESTAMP, public_profile INTEGER DEFAULT 1, UNIQUE(company_name,country));
        CREATE TABLE IF NOT EXISTS catalog_items(id TEXT PRIMARY KEY, supplier_id TEXT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE, item_id TEXT, product_name TEXT NOT NULL, category TEXT, hs_code TEXT, specifications TEXT, min_order_quantity REAL DEFAULT 1, capacity REAL DEFAULT 0, spare_capacity REAL DEFAULT 0, unit_price REAL DEFAULT 0, currency TEXT DEFAULT 'USD', lead_time_days INTEGER DEFAULT 0, incoterms TEXT, certifications TEXT, priority TEXT DEFAULT 'P3', source_label TEXT DEFAULT 'Self-registered - unverified', confidence REAL DEFAULT 1, UNIQUE(supplier_id,product_name,hs_code));
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, event_type TEXT NOT NULL, country TEXT NOT NULL, target_country TEXT, severity REAL DEFAULT 1, tariff_rate REAL DEFAULT 0.25, start_date TEXT, end_date TEXT, source TEXT DEFAULT 'manual', confidence REAL DEFAULT 1, approved INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP, payload TEXT);
        CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_catalog_product ON catalog_items(product_name); CREATE INDEX IF NOT EXISTS idx_catalog_hs ON catalog_items(hs_code); CREATE INDEX IF NOT EXISTS idx_supplier_country ON suppliers(country);
        ''')
def upsert_supplier(payload: dict[str,Any], items:list[dict[str,Any]], source_label='Self-registered - unverified'):
    import uuid
    init_db(); sid='SUP-'+uuid.uuid4().hex[:10].upper()
    with connect() as c:
        row=c.execute('SELECT id FROM suppliers WHERE lower(company_name)=lower(?) AND lower(country)=lower(?)',(payload['company_name'],payload['country'])).fetchone()
        if row: sid=row['id']
        c.execute('''INSERT INTO suppliers(id,company_name,country,region,address,latitude,longitude,contact_email,website,business_registration,tax_id,certifications,export_countries,source_label) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET region=excluded.region,address=excluded.address,latitude=excluded.latitude,longitude=excluded.longitude,contact_email=excluded.contact_email,website=excluded.website,certifications=excluded.certifications,export_countries=excluded.export_countries,last_updated=CURRENT_TIMESTAMP''', (sid,payload['company_name'],payload['country'],payload.get('region'),payload.get('address'),payload.get('latitude'),payload.get('longitude'),payload.get('contact_email'),payload.get('website'),payload.get('business_registration'),payload.get('tax_id'),json.dumps(payload.get('certifications',[])),json.dumps(payload.get('export_countries',[])),source_label))
        count=0
        for item in items:
            iid='CAT-'+uuid.uuid4().hex[:10].upper()
            c.execute('''INSERT INTO catalog_items(id,supplier_id,item_id,product_name,category,hs_code,specifications,min_order_quantity,capacity,spare_capacity,unit_price,currency,lead_time_days,incoterms,certifications,priority,source_label,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(supplier_id,product_name,hs_code) DO UPDATE SET category=excluded.category,specifications=excluded.specifications,capacity=excluded.capacity,spare_capacity=excluded.spare_capacity,unit_price=excluded.unit_price,lead_time_days=excluded.lead_time_days,source_label=excluded.source_label''', (iid,sid,item.get('item_id'),item['product_name'],item.get('category'),item.get('hs_code'),item.get('specifications'),item.get('min_order_quantity',1),item.get('capacity',0),item.get('spare_capacity',0),item.get('unit_price',0),item.get('currency','USD'),item.get('lead_time_days',0),item.get('incoterms'),json.dumps(item.get('certifications',[])),item.get('priority','P3'),source_label,item.get('confidence',1)))
            count+=1
        c.execute('INSERT INTO audit_log(action,details) VALUES(?,?)',('supplier_upsert',json.dumps({'supplier_id':sid,'items':count,'source_label':source_label})))
    return sid,count
def search_catalog(product_name=None,hs_code=None,country=None,category=None,verified_only=False,limit=50,min_capacity=None,max_lead_time_days=None,certification=None):
    init_db(); sql='''SELECT s.id supplier_id,s.company_name,s.country,s.region,s.address,s.latitude,s.longitude,s.contact_email,s.website,s.verified,s.source_label supplier_source,s.certifications supplier_certifications,c.* FROM catalog_items c JOIN suppliers s ON s.id=c.supplier_id WHERE s.public_profile=1'''; args=[]
    for col,val in [('c.product_name',product_name),('c.hs_code',hs_code),('s.country',country),('c.category',category)]:
        if val: sql+=f' AND lower({col}) LIKE lower(?)'; args.append('%'+val.strip()+'%')
    if verified_only: sql+=' AND s.verified=1'
    if min_capacity is not None: sql+=' AND c.spare_capacity >= ?'; args.append(min_capacity)
    if max_lead_time_days is not None: sql+=' AND c.lead_time_days <= ?'; args.append(max_lead_time_days)
    if certification: sql+=' AND lower(c.certifications) LIKE lower(?)'; args.append('%'+certification.strip()+'%')
    sql+=" ORDER BY s.verified DESC, CASE WHEN c.source_label LIKE 'Unverified%' THEN 1 ELSE 0 END, c.unit_price ASC LIMIT ?"; args.append(limit)
    with connect() as c: rows=[dict(r) for r in c.execute(sql,args).fetchall()]
    for r in rows:
        for k in ('certifications','supplier_certifications'): 
            try: r[k]=json.loads(r[k] or '[]')
            except Exception: pass
    return rows
def save_event(e:dict):
    import uuid
    eid='EVT-'+uuid.uuid4().hex[:10].upper(); init_db()
    with connect() as c:
        c.execute('INSERT INTO events(id,event_type,country,target_country,severity,tariff_rate,start_date,end_date,source,confidence,approved,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(eid,e['type'],e['country'],e.get('target_country'),e.get('severity',1),e.get('tariff_rate',.25),e.get('start_date'),e.get('end_date'),e.get('source','manual'),e.get('confidence',1),int(e.get('approved',True)),json.dumps(e)))
    return eid
def list_events():
    init_db()
    with connect() as c: return [dict(r) for r in c.execute('SELECT * FROM events ORDER BY created_at DESC').fetchall()]
def set_verified(supplier_id:str,verified:bool):
    init_db()
    with connect() as c: return c.execute('UPDATE suppliers SET verified=?,source_label=?,last_updated=CURRENT_TIMESTAMP WHERE id=?',(int(verified),'Verified' if verified else 'Self-registered - unverified',supplier_id)).rowcount>0
def list_suppliers():
    init_db()
    with connect() as c: return [dict(r) for r in c.execute('SELECT id,company_name,country,region,verified,source_label,last_updated FROM suppliers ORDER BY company_name').fetchall()]
