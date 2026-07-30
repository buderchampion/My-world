import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from Data.Database.database import init_database, get_connection
from Data.Database.repository import create_world, create_kingdom, create_city
from Data.NPCS.npc_generator import generate_npc

init_database()
conn = get_connection()

world = create_world(conn, "Test World")
kingdom = create_kingdom(conn, world["id"], "Test Kingdom")
city = create_city(conn, kingdom["id"], "Test Village", "Village", 100)

npc = generate_npc(conn, city["id"], "Village")
conn.commit()

print("NPC Generated")
print("-------------")

for key, value in npc.items():
    print(f"{key}: {value}")

conn.close()
