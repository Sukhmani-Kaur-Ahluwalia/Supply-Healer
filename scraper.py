"""Bounded static HTML scraper. Use only sources whose terms/robots rules permit access."""
from __future__ import annotations
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from ipaddress import ip_address
import socket, re, time
from bs4 import BeautifulSoup
from app.database import upsert_supplier

MAX_BYTES=1_000_000
USER_AGENT='SelfHealingSupplyChainResearchBot/1.0 (contact: admin@example.org)'
def _validate_url(url:str):
    p=urlparse(url)
    if p.scheme not in ('http','https') or not p.hostname: raise ValueError('URL must be an absolute http(s) URL')
    host=p.hostname.lower()
    if host in ('localhost','metadata.google.internal') or host.endswith('.local') or host.endswith('.example') or host.endswith('.invalid') or host.endswith('.test'):
        raise ValueError(f"Refusing placeholder or local hostname: {host}")
    try:
        infos=socket.getaddrinfo(host,None)
        for info in infos:
            ip=ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast: raise ValueError('Refusing private or non-public destination')
    except socket.gaierror as e: raise ValueError(f"Could not resolve scraper host '{host}'. Check that the URL is a real public website.") from e
    return url

def fetch_html(url:str,timeout:float=5.0)->str:
    _validate_url(url)
    req=Request(url,headers={'User-Agent':USER_AGENT,'Accept':'text/html'})
    try:
        with urlopen(req,timeout=timeout) as r:
            ctype=r.headers.get('Content-Type','')
            if 'text/html' not in ctype and 'application/xhtml+xml' not in ctype: raise ValueError('URL did not return HTML')
            data=r.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES: raise ValueError('HTML exceeds 1 MB safety limit')
            return data.decode(r.headers.get_content_charset() or 'utf-8',errors='replace')
    except HTTPError as e: raise RuntimeError(f'Scraper HTTP error: {e.code}') from e
    except URLError as e: raise RuntimeError(f'Scraper network error: {e.reason}') from e

def parse_directory(html:str, source_url:str)->list[dict]:
    soup=BeautifulSoup(html,'html.parser'); records=[]
    # Preferred structural format: each .supplier card has company/country and one or more product fields.
    cards=soup.select('[data-supplier], .supplier-card, .supplier')
    for card in cards:
        name=(card.get('data-supplier') or _text(card,['.company-name','[itemprop="name"]','h2','h3'])).strip()
        country=(card.get('data-country') or _text(card,['.country','[itemprop="addressCountry"]'])).strip()
        product=(card.get('data-product') or _text(card,['.product-name','.product','[data-product-name]'])).strip()
        hs=(card.get('data-hs-code') or _text(card,['.hs-code','[data-hs-code]'])).strip()
        if name and country and product:
            records.append({'supplier':{'company_name':name,'country':country,'website':source_url},'items':[{'product_name':product,'hs_code':hs or None,'category':_text(card,['.category']) or None,'capacity':_num(_text(card,['.capacity'])), 'unit_price':_num(_text(card,['.price'])), 'lead_time_days':_int(_text(card,['.lead-time'])), 'confidence':0.55}]})
    # Simple table adapter: columns Company, Country, Product, HS Code, Capacity, Price, Lead Time.
    if not records:
        for tr in soup.select('table tr'):
            cells=[c.get_text(' ',strip=True) for c in tr.select('th,td')]
            if len(cells)>=3 and not any(x.casefold() in ('company','company name','supplier') for x in cells[:1]):
                records.append({'supplier':{'company_name':cells[0],'country':cells[1],'website':source_url},'items':[{'product_name':cells[2],'hs_code':cells[3] if len(cells)>3 else None,'capacity':_num(cells[4]) if len(cells)>4 else 0,'unit_price':_num(cells[5]) if len(cells)>5 else 0,'lead_time_days':_int(cells[6]) if len(cells)>6 else 0,'confidence':0.45}]})
    # Keep records conservative and never infer a company from generic paragraphs.
    return records[:100]
def _text(el,selectors):
    for sel in selectors:
        node=el.select_one(sel)
        if node:
            value=node.get('data-product-name') or node.get_text(' ',strip=True)
            if value: return value
    return ''
def _num(s):
    m=re.search(r'\d+(?:\.\d+)?',str(s or '').replace(',',''))
    return float(m.group()) if m else 0

def _int(s): return int(_num(s))
def import_directory_html(html:str, source_url:str, max_runtime:float=12.0)->dict:
    start=time.monotonic(); records=parse_directory(html,source_url); imported=[]
    for rec in records:
        if time.monotonic()-start>max_runtime: break
        sid,count=upsert_supplier(rec['supplier'],rec['items'],source_label='Unverified - public data')
        imported.append({'supplier_id':sid,'company_name':rec['supplier']['company_name'],'country':rec['supplier']['country'],'items_saved':count,'source_url':source_url,'source_label':'Unverified - public data'})
    return {'source_url':source_url,'records_found':len(records),'records_imported':len(imported),'results':imported,'note':'Public directory data is unverified and must be reviewed before use.'}

def scrape_and_import(url:str, timeout:float=5.0, max_runtime:float=12.0)->dict:
    start=time.monotonic(); html=fetch_html(url,timeout)
    if time.monotonic()-start>max_runtime: raise RuntimeError('Scrape time budget exceeded')
    return import_directory_html(html,url,max_runtime=max_runtime)
