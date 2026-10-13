from Data.Database.repository import get_city_buildings

def ensure_districts_for_city(conn, city_id):
    
    existing = conn.execute("SELECT 1 FROM districts WHERE city_id = ? LIMIT 1", (city_id,)).fetchone()
    
    if existing:
        return []
    
    buildings = get_city_buildings(conn, city_id)
    
    by_category = {}
    for building in buildings:
        by_category.setdefault(building["category"], []).append(building["id"])
        
    created = []
    
    for category, building_ids in by_category.items():
        
        cursor = conn.execute(
            "INSERT INTO districts (city_id, name, category) VALUES (?, ?, ?)",
            (city_id, f"{category} District", category)
        )
        district_id = cursor.lastrowid
        
        placeholders = ",".join("?" * len(building_ids))
        conn.execute(
            f"UPDATE buildings SET district_id = ? WHERE id IN ({placeholders})",
            [district_id] + building_ids
        )
        
        created.append({"id": district_id, "city_id": city_id, "name": f"{category} District", "category": category})
        
    return created