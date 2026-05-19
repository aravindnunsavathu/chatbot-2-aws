"""
Generates Cube.dev YAML data model files from fivebyfive_metadata.json.
Output goes to cube/schema/<TableName>.yaml (one file per table).
Run: python3 generate_cube_schemas.py
"""

import json
import os
import re

OUTPUT_DIR = "cube/schema"

PG_TO_CUBE = {
    "uuid": "string",
    "varchar": "string",
    "text": "string",
    "char": "string",
    "character varying": "string",
    "integer": "number",
    "bigint": "number",
    "smallint": "number",
    "int": "number",
    "int4": "number",
    "int8": "number",
    "double precision": "number",
    "float8": "number",
    "real": "number",
    "numeric": "number",
    "decimal": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "timestamp": "time",
    "timestamp without time zone": "time",
    "timestamp with time zone": "time",
    "timestamptz": "time",
    "date": "time",
    "json": "string",
    "jsonb": "string",
}

SKIP_TYPES = {"vector", "geography", "geometry", "bytea", "tsvector"}

# Extra named measures for specific tables beyond the default count
EXTRA_MEASURES = {
    "asset_versions": [
        ("avg_price_to_customer", "avg", "price_to_customer", "Average price charged to the customer"),
        ("avg_price_to_capture",  "avg", "price_to_capture",  "Average cost to capture an asset version"),
        ("avg_height",            "avg", "height",            "Average structure height in meters"),
        ("total_price_to_customer", "sum", "price_to_customer", "Total revenue from asset versions"),
    ],
    "assets": [
        ("avg_base_elevation", "avg", "asset_base_land_elevation_m", "Average base elevation in meters"),
    ],
    "measurements": [
        ("avg_measurement_count", "avg", "measurement_count", "Average number of segments per measurement"),
    ],
    "volumes": [
        ("avg_height_m", "avg", "height_m", "Average volume height in meters"),
        ("avg_width_m",  "avg", "width_m",  "Average volume width in meters"),
    ],
}


def to_pascal(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_"))


def cube_type(pg_type: str) -> str | None:
    base = pg_type.lower().split("(")[0].strip()
    if base in SKIP_TYPES:
        return None
    return PG_TO_CUBE.get(base, "string")


def yaml_str(s: str) -> str:
    """Wrap a string in double quotes, escaping any internal double quotes."""
    escaped = s.replace('"', '\\"')
    return f'"{escaped}"'


def generate_schema(table: str, info: dict) -> str:
    cube_name = to_pascal(table)
    lines = [
        "cubes:",
        f"  - name: {cube_name}",
        f"    sql_table: fivebyfive.{table}",
    ]
    if info.get("description"):
        lines.append(f"    description: {yaml_str(info['description'])}")
    lines.append("")

    # ── joins ────────────────────────────────────────────────────────────────────
    fks = info.get("foreign_keys", [])
    seen_targets: dict[str, str] = {}  # referred_table → join name used
    join_lines = []
    for fk in fks:
        ref_table = fk["referred_table"]
        ref_col   = fk["referred_columns"][0]
        my_col    = fk["constrained_columns"][0]
        ref_cube  = to_pascal(ref_table)

        if ref_table in seen_targets:
            # multiple FKs to same table — skip extras; SQL alias not supported
            continue
        seen_targets[ref_table] = my_col

        join_lines += [
            f"      - name: {ref_cube}",
            f"        sql: \"{{TABLE}}.{my_col} = {{{ref_cube}}}.{ref_col}\"",
            f"        relationship: many_to_one",
        ]

    if join_lines:
        lines += ["    joins:"] + join_lines
        lines.append("")

    # ── measures ─────────────────────────────────────────────────────────────────
    lines += [
        "    measures:",
        "      - name: count",
        "        type: count",
        f"        description: {yaml_str('Total number of ' + table.replace('_', ' '))}",
    ]
    for name, agg_type, col, desc in EXTRA_MEASURES.get(table, []):
        # only emit if column actually exists
        col_names = {c["name"] for c in info.get("columns", [])}
        if col not in col_names:
            continue
        lines += [
            f"      - name: {name}",
            f"        sql: {col}",
            f"        type: {agg_type}",
            f"        description: {yaml_str(desc)}",
        ]
    lines.append("")

    # ── dimensions ───────────────────────────────────────────────────────────────
    lines.append("    dimensions:")
    for col in info.get("columns", []):
        cname = col["name"]
        ctype = cube_type(col.get("type", "varchar"))
        if ctype is None:
            continue  # skip vector / geometry columns

        is_pk = cname == "id"
        lines += [
            f"      - name: {cname}",
            f"        sql: {cname}",
            f"        type: {ctype}",
        ]
        if is_pk:
            lines.append("        primary_key: true")
        if col.get("description"):
            lines.append(f"        description: {yaml_str(col['description'])}")

    return "\n".join(lines) + "\n"


def main():
    with open("fivebyfive_metadata.json") as f:
        meta = json.load(f)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for table, info in meta.items():
        schema = generate_schema(table, info)
        path = os.path.join(OUTPUT_DIR, f"{to_pascal(table)}.yaml")
        with open(path, "w") as f:
            f.write(schema)
        print(f"  wrote {path}")

    print(f"\nDone — {len(meta)} schemas written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
