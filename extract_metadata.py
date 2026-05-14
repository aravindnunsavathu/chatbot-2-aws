import json
from sqlalchemy import create_engine, MetaData, inspect

# 1. Establish the connection to your running PostgreSQL 17 server
# Formatting: postgresql://[user]@localhost:[port]/[database]
DATABASE_URL = "postgresql://aravindnunsavathu@localhost:5433/fivebyfiveqa"
SCHEMA_NAME = "fivebyfive"

engine = create_engine(DATABASE_URL)
inspector = inspect(engine)

# Container for our structured metadata
schema_inventory = {}

print(f"Analyzing schema '{SCHEMA_NAME}'...")

# 2. Extract all table names within the targeted schema
table_names = inspector.get_table_names(schema=SCHEMA_NAME)
print(f"Found {len(table_names)} tables. Extracting columns and constraints...")

for table in table_names:
    schema_inventory[table] = {
        "columns": [],
        "primary_keys": [],
        "foreign_keys": []
    }
    
    # A. Extract Column Names and Data Types
    columns = inspector.get_columns(table_name=table, schema=SCHEMA_NAME)
    for col in columns:
        schema_inventory[table]["columns"].append({
            "name": col["name"],
            "type": str(col["type"]),
            "nullable": col["nullable"]
        })
        
   # B. Extract Primary Keys (Updated for modern SQLAlchemy versions)
    pk_info = inspector.get_pk_constraint(table_name=table, schema=SCHEMA_NAME)
    schema_inventory[table]["primary_keys"] = pk_info.get("constrained_columns", [])
 
    # C. Extract Foreign Key Relationships (Critical for Multi-table joins)
    fk_constraints = inspector.get_foreign_keys(table_name=table, schema=SCHEMA_NAME)
    for fk in fk_constraints:
        schema_inventory[table]["foreign_keys"].append({
            "constrained_columns": fk["constrained_columns"],
            "referred_schema": fk["referred_schema"] or SCHEMA_NAME,
            "referred_table": fk["referred_table"],
            "referred_columns": fk["referred_columns"]
        })

# 3. Save the formatted output to a JSON file for your vector store
output_file = "fivebyfive_metadata.json"
with open(output_file, "w") as f:
    json.dump(schema_inventory, f, indent=4)

print(f"Success! Metadata inventory saved to {output_file}")

