import sys
sys.path.insert(0, "scripts/research")
import sqlite3
from thai_data_layer import DB_PATH, init_db, update_set_index_history

conn = sqlite3.connect(DB_PATH)
init_db(conn)
n = update_set_index_history(conn, backfill=False)
print(f"[SET] update_set_index_history wrote {n} rows")
conn.close()
