"""Deterministic connected supply network: 5 products, 4 tiers, 15 countries, 500+ edges."""
from __future__ import annotations
import random

COUNTRIES = ["India", "China", "United States", "Germany", "Japan", "South Korea", "Taiwan", "Vietnam", "Thailand", "Malaysia", "Singapore", "Indonesia", "Mexico", "Brazil", "Netherlands"]
PRODUCTS = [
    {"item_id":"MED-KIT", "name":"Emergency Medical Kit", "category":"Medical", "priority":"P1", "demand":500},
    {"item_id":"FUEL-PUMP", "name":"Fuel Pump", "category":"Energy", "priority":"P2", "demand":500},
    {"item_id":"WATER-PUR", "name":"Water Purifier", "category":"Water", "priority":"P2", "demand":300},
    {"item_id":"SOLAR-INV", "name":"Solar Inverter", "category":"Energy", "priority":"P3", "demand":250},
    {"item_id":"FOOD-PACK", "name":"Staple Food Pack", "category":"Food", "priority":"P2", "demand":800},
]

def build_seed_edges() -> list[dict]:
    """Build a connected four-tier DAG with alternate lanes for every tier."""
    rng = random.Random(17)
    edges=[]
    for p_idx, product in enumerate(PRODUCTS):
        facilities = {}
        # Six facilities at each tier, each in a different country sequence.
        for tier in range(4):
            facilities[tier] = []
            for lane in range(6):
                country = COUNTRIES[(p_idx*2 + tier*3 + lane) % len(COUNTRIES)]
                facilities[tier].append({
                    "name": f"{country.replace(' ','')}-P{p_idx}-T{tier}-F{lane}",
                    "country": country,
                })
        item_by_tier = {
            0: product["item_id"],
            1: f"{product['item_id']}-PART1",
            2: f"{product['item_id']}-PART2",
            3: f"{product['item_id']}-RAW",
        }
        # Tier 0 finished-goods lanes all serve one downstream buyer.
        final_buyer = {"name":f"{product['item_id']}-Market-Buyer", "country":"India"}
        for lane, supplier in enumerate(facilities[0]):
            edges.append(_edge(rng, product, 0, lane, supplier, final_buyer, item_by_tier[0]))
        # Each upstream tier can supply each facility in the next downstream tier.
        # This creates real transitive dependencies and alternate candidates per buyer.
        for tier in (1,2,3):
            downstream = facilities[tier-1]
            upstream = facilities[tier]
            lane_no = 0
            for buyer in downstream:
                for supplier in upstream:
                    edges.append(_edge(rng, product, tier, lane_no, supplier, buyer, item_by_tier[tier]))
                    lane_no += 1
    # Explicit X/Y/A/B tariff/war demo lane with a qualified alternative.
    edges.extend([
        {"edge_id":"DEMO-X-B", "supplier":"X-Parts-Co", "buyer":"Y-Assembly", "item_id":"MED-KIT-PART1", "product_id":"MED-KIT", "item_tier":1, "priority":"P1", "demand":500, "supplier_country":"China", "buyer_country":"India", "route_countries":["China","Singapore","India"], "unit_cost":12.0, "lead_time":8, "capacity":1000, "spare_capacity":700, "qualified":True, "risk":0.7, "transport_mode":"sea", "is_finished_good":False, "status":"healthy"},
        {"edge_id":"DEMO-ALT-B", "supplier":"Alt-Med-Parts", "buyer":"Y-Assembly", "item_id":"MED-KIT-PART1", "product_id":"MED-KIT", "item_tier":1, "priority":"P1", "demand":500, "supplier_country":"Germany", "buyer_country":"India", "route_countries":["Germany","Netherlands","India"], "unit_cost":15.0, "lead_time":6, "capacity":600, "spare_capacity":500, "qualified":True, "risk":0.1, "transport_mode":"air", "is_finished_good":False, "status":"healthy"},
        {"edge_id":"DEMO-Y-A", "supplier":"Y-Assembly", "buyer":"India-Medical-Distributor", "item_id":"MED-KIT", "product_id":"MED-KIT", "item_tier":0, "priority":"P1", "demand":500, "supplier_country":"India", "buyer_country":"India", "route_countries":["India"], "unit_cost":40.0, "lead_time":2, "capacity":900, "spare_capacity":300, "qualified":True, "risk":0.1, "transport_mode":"road", "is_finished_good":True, "status":"healthy"},
    ])
    return edges

def _edge(rng, product, tier, lane, supplier, buyer, item_id):
    route=[supplier["country"], COUNTRIES[(lane + tier + 4) % len(COUNTRIES)], buyer["country"]]
    return {
        "edge_id":f"P{product['item_id']}-T{tier}-L{lane:03d}",
        "supplier":supplier["name"], "buyer":buyer["name"], "item_id":item_id,
        "product_id":product["item_id"], "item_tier":tier,
        "priority":product["priority"], "demand":product["demand"],
        "supplier_country":supplier["country"], "buyer_country":buyer["country"],
        "route_countries":route, "route_chokepoints":[["Suez Canal","Strait of Malacca","Panama Canal","Strait of Hormuz"][lane%4]], "unit_cost":round(rng.uniform(2,120),2),
        "lead_time":rng.randint(2,35), "capacity":rng.randint(300,1600),
        "spare_capacity":rng.randint(100,900), "qualified":lane != 5,
        "risk":round(rng.uniform(.05,.75),2), "transport_mode":["sea","air","road","rail"][lane%4],
        "is_finished_good":tier==0, "status":"healthy"
    }
