import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from Data.Database.database import init_database, get_connection
from Data.Database.repository import create_kingdom, create_world
from Data.Cities.city_generator import generate_city

init_database()
conn = get_connection()

world = create_world(conn, "Test World")
kingdom = create_kingdom(conn, world["id"], "Test Kingdom")

city = generate_city(conn, kingdom["id"], "Village")
conn.commit()

print(city)

conn.close()
