import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from Data.Database.database import init_database, get_connection
from Data.Database.repository import create_world
from Data.Kingdoms.kingdom_generator import generate_kingdom

init_database()
conn = get_connection()

world = create_world(conn, "Test World")

kingdom = generate_kingdom(conn, world["id"])
conn.commit()

print(kingdom)

conn.close()
