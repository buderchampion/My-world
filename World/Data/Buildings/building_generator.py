from pathlib import Path
import json
import random
import math
from Data.Database.repository import create_building

DATA_DIR = Path(__file__).resolve().parent.parent

BUILDING_TYPES = DATA_DIR / "Buildings" / "BuildingTypes.json"
CITY_BUILDING_KITS = DATA_DIR / "Buildings" / "CityBuildingKits.json"


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def generate_buildings(conn, city_id, city_type, population):

    building_types_data = load_json(BUILDING_TYPES)
    kits = load_json(CITY_BUILDING_KITS)

    buildings = []

    kit = kits.get(city_type, {})

    for building_type, bounds in kit.items():

        min_count, max_count = bounds

        count = random.randint(min_count, max_count)

        for _ in range(count):

            type_data = building_types_data[building_type]

            building = create_building(
                conn, city_id, f"{building_type} #_",
                building_type, type_data["category"], type_data["capacity"]
            )

            # name includes the DB-assigned id, so it's set in a second step
            building["name"] = f"{building_type} #{building['id']}"
            conn.execute("UPDATE buildings SET name = ? WHERE id = ?", (building["name"], building["id"]))
            conn.commit()

            buildings.append(building)

    house_capacity = building_types_data["House"]["capacity"]

    house_count = max(1, math.ceil(population / house_capacity))

    for _ in range(house_count):

        type_data = building_types_data["House"]

        building = create_building(
            conn, city_id, "House #_", "House", type_data["category"], type_data["capacity"]
        )

        building["name"] = f"House #{building['id']}"
        conn.execute("UPDATE buildings SET name = ? WHERE id = ?", (building["name"], building["id"]))
        conn.commit()

        buildings.append(building)
        
    from Data.Cities.districts import ensure_districts_for_city
    ensure_districts_for_city(conn, city_id)
    conn.commit()

    return buildings

def ensure_production_chain_buildings(conn, city_id, city_type):
    from Data.Database.repository import get_city_buildings, get_city_npcs, save_npc
    
    existing_types = {b["type"] for b in get_city_buildings(conn, city_id)}
    
    if "Mill" in existing_types or "Bakery" in existing_types:
        return[]
    
    building_types_data = load_json(BUILDING_TYPES)
    kits = load_json(CITY_BUILDING_KITS)
    kit = kits.get(city_type, {})
    
    created = []
    
    for building_type in ("Mill", "Bakery"):
        
        bounds = kit.get(building_type)
        if not bounds:
            continue
        
        min_count, max_count = bounds
        count = random.randint(min_count, max_count)
        
        for _ in range(count):
            
            type_data = building_types_data[building_type]
            
            building = create_building(
                conn, city_id, f"{building_type} #_",
                building_type, type_data["category"], type_data["capacity"]
            )
            
            building["name"] = f"{building_type} #{building['id']}"
            conn.execute("UPDATE buildings SET name = ? WHERE id = ?", (building["name"], building["id"]))
            
            created.append(building)
            
        conn.commit()
        
        if not created:
            return[]
        
        from Data.Cities.districts import ensure_districts_for_city 
        ensure_districts_for_city(conn, city_id)
        conn.commit()
        
        npcs = get_city_npcs(conn, city_id)
        
        new_bakeries = [b for b in created if b["type"] == "Bakery"]
        new_mills = [b for b in created if b["type"] == "Mill"]
        
        if new_bakeries:
            
            bakery_capacity = building_types_data["Bakery"]["capacity"]
            bakery_slots = []
            for bakery in new_bakeries:
                bakery_slots.extend([bakery["id"]] * bakery_capacity)
                
            existing_bakers = [n for n in npcs if n["alive"] and n["profession"] == "Baker"]
            
            for npc, slot in zip(existing_bakers, bakery_slots):
                npc["workplace"] = slot
                save_npc(conn, npc)
                
        if new_mills:
            
            mill_capacity = building_types_data["Mill"]["capacity"]
            mill_slots = []
            for mill in new_mills:
                mill_slots.extend([mill["id"]] * mill_capacity)
                
            unemployed = [n for n in npcs if n["alive"] and n["profession"] == "Unemployed"]
            
            for npc, slot in zip(unemployed, mill_slots):
                npc["profession"] = "Miller"
                npc["workplace"] = slot
                save_npc(conn, npc)
                
        conn.commit()
        
        return created