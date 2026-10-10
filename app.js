const $=id=>document.getElementById(id);const base=()=> $('apiBase').value.trim().replace(/\/$/,'');
async function api(path,options={}){const r=await fetch(base()+path,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});const t=await r.text();let data;try{data=JSON.parse(t)}catch{data={detail:t}}if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);return data}
function show(id,obj){$(id).textContent=JSON.stringify(obj,null,2)}
let seedEdges=[];
async function drawNetwork(changedEdges=[],healedIds=[]){
  if(!seedEdges.length){try{seedEdges=(await api('/graph/edges')).results}catch{return}}
  const baseEdges=seedEdges.filter(e=>e.product_id==='MED-KIT');
  const selected=[];for(let tier=0;tier<4;tier++){selected.push(...baseEdges.filter(e=>e.item_tier===tier).slice(0,6))}
  const status=new Map(changedEdges.map(e=>[e.edge_id,e.status]));const healed=new Set(healedIds);const svg=$('networkSvg');svg.innerHTML='';
  const NS='http://www.w3.org/2000/svg';const make=(tag,attrs={},txt='')=>{const n=document.createElementNS(NS,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(txt)n.textContent=txt;svg.appendChild(n);return n};
  const tierNames=['Raw materials','Sub-parts','Components','Finished goods','Buyer'];
  tierNames.forEach((t,i)=>make('text',{x:85+i*180,y:22,class:'tier-label'},t));
  const nodes=new Map(),ys=new Map();function position(name,col){const key=col+'|'+name;if(!nodes.has(key)){const row=ys.get(col)||0;ys.set(col,row+1);nodes.set(key,{x:85+col*180,y:55+row*39,name,col})}return nodes.get(key)}
  const lines=[];
  selected.forEach(e=>{const tier=Number(e.item_tier);const sc=position(e.supplier,3-tier);const bc=position(e.buyer,tier===0?4:4-tier);lines.push({e,sc,bc})});
  lines.forEach(({e,sc,bc})=>{let cls='network-edge';if(status.get(e.edge_id)==='disrupted'||status.get(e.edge_id)==='degraded')cls+=' disrupted';if(healed.has(e.edge_id))cls='network-edge healed';make('line',{x1:sc.x+40,y1:sc.y,x2:bc.x-40,y2:bc.y,class:cls})});
  nodes.forEach(n=>{make('rect',{x:n.x-42,y:n.y-13,width:84,height:26,rx:7,class:'network-node'});make('text',{x:n.x,y:n.y+3,class:'network-label'},n.name.length>15?n.name.slice(0,14)+'…':n.name)});
}
async function connect(){try{const d=await api('/seed/summary');$('apiState').textContent='API connected';$('apiState').style.background='#e6f7ec';drawNetwork();$('stats').innerHTML=`<article><span>Seed edges</span><strong>${d.edge_count}</strong></article><article><span>Products</span><strong>${d.products.length}</strong></article><article><span>Countries</span><strong>${d.countries.length}</strong></article>`}catch(e){$('apiState').textContent='API offline: '+e.message}}
$('connectBtn').onclick=connect;
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab,.panel').forEach(x=>x.classList.remove('active'));b.classList.add('active');$(b.dataset.tab).classList.add('active')});
$('simulateBtn').onclick=async()=>{const event={type:$('eventType').value,country:$('eventCountry').value,target_country:$('targetCountry').value||null,chokepoint:$('chokepoint').value||null,severity:Number($('severity').value),tariff_rate:Number($('tariffRate').value),source:'manual',approved:true};try{const result=await api('/simulate?demand='+Number($('demand').value),{method:'POST',body:JSON.stringify(event)});show('simulationOutput',result);const ids=(result.healing.recommendations||[]).flatMap(r=>(r.allocations||[]).map(a=>a.edge_id));drawNetwork(result.impact.changed_edges||[],ids)}catch(e){$('simulationOutput').textContent=e.message}};
$('searchBtn').onclick=async()=>{const p=new URLSearchParams();for(const [k,id] of [['product_name','searchProduct'],['hs_code','searchHs'],['country','searchCountry'],['category','searchCategory']])if($(id).value.trim())p.set(k,$(id).value.trim());if($('verifiedOnly').checked)p.set('verified_only','true');try{const d=await api('/search?'+p);$('searchOutput').innerHTML=d.results.length?d.results.map(x=>`<article class="supplier-card"><h3>${esc(x.company_name)}</h3><p>${esc(x.product_name)} · ${esc(x.country)}</p><p>Capacity: ${x.capacity} · Price: ${x.currency} ${x.unit_price} · Lead time: ${x.lead_time_days} days</p><span class="tag">${esc(x.source_label||x.supplier_source||'Unverified')}</span></article>`).join(''):'No matching supplier records. Register a supplier or import a permitted public directory.'}catch(e){$('searchOutput').textContent=e.message}};
$('registerBtn').onclick=async()=>{const body={company_name:$('company').value,country:$('companyCountry').value,region:$('region').value||null,contact_email:$('email').value||null,website:$('website').value||null,certifications:[],export_countries:[],items:[{product_name:$('productName').value,category:$('productCategory').value,hs_code:$('productHs').value,capacity:Number($('capacity').value),spare_capacity:Number($('spare').value),unit_price:Number($('price').value),lead_time_days:Number($('lead').value),priority:'P3'}]};try{show('registerOutput',await api('/supplier',{method:'POST',body:JSON.stringify(body)}))}catch(e){$('registerOutput').textContent=e.message}};
$('eventsBtn').onclick=async()=>{try{show('eventsOutput',await api('/events'))}catch(e){$('eventsOutput').textContent=e.message}};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}connect();drawNetwork();

$('sampleScrapeBtn').onclick=async()=>{try{show('eventsOutput',await api('/scrape/sample',{method:'POST'}))}catch(e){$('eventsOutput').textContent=e.message}};
