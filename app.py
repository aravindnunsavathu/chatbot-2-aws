import json
import os
import re

import boto3
import pandas as pd
import psycopg2
import streamlit as st

# ── Config ─────────────────────────────────────────────────────────────────────
AWS_REGION     = os.environ.get("AWS_REGION", "us-east-1")
LLM_MODEL_ID   = os.environ.get("LLM_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0")
EMBED_MODEL_ID = os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBED_DIM      = 1536  # Titan Embeddings v2 output dimension

DB = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ.get("DB_NAME", "fivebyfiveqa"),
    user=os.environ.get("DB_USER", "postgres"),
    password=os.environ.get("DB_PASSWORD", ""),
    options="-c search_path=fivebyfive",
)

MAX_ROWS       = 200
METADATA_PATH  = "fivebyfive_metadata.json"

VECTOR_TABLES = {"physical_components", "asset_version_notes"}

CATEGORICAL_COLUMNS: list[tuple[str, str]] = [
    ("asset_versions", "packaging_status"),
    ("asset_versions", "processing_status"),
    ("asset_versions", "capture_status"),
    ("asset_versions", "data_source"),
    ("asset_versions", "capture_type"),
    ("assets", "asset_type"),
    ("assets", "structure_type"),
    ("volumes", "volume_type"),
    ("volumes", "installation_status"),
    ("revision_items", "action_type"),
    ("revision_items", "construction_status"),
    ("revisions", "revision_type"),
    ("revisions", "design_state"),
    ("apertures", "aperture_type"),
    ("physical_components", "category"),
    ("physical_components", "primary_type"),
    ("physical_components", "shape"),
    ("grants", "grant_type"),
    ("matched_volumes", "match_type"),
    ("matched_volumes", "confirmation_status"),
    ("measurements", "measurement_type"),
    ("sites", "site_status"),
    ("sites", "terrain_category"),
    ("cad_tasks", "file_format"),
    ("physical_component_models", "format"),
    ("tags", "category"),
]
MAX_DISTINCT_VALUES = 30

FEW_SHOT_EXAMPLES = """
-- Example 1: simple aggregate
Q: How many assets are there per asset type?
SQL:
SELECT asset_type, COUNT(*) AS count
FROM fivebyfive.assets
GROUP BY asset_type
ORDER BY count DESC;

-- Example 2: multi-table JOIN with LEFT JOIN
Q: List all sites with their total asset count and most recent capture date
SQL:
SELECT s.site_name, s.display_id, s.address_state,
       COUNT(DISTINCT a.id) AS asset_count,
       MAX(av.capture_date) AS last_capture_date
FROM fivebyfive.sites s
LEFT JOIN fivebyfive.assets a ON a.site_id = s.id
LEFT JOIN fivebyfive.asset_versions av ON av.asset_id = a.id AND av.active = true
GROUP BY s.id, s.site_name, s.display_id, s.address_state
ORDER BY last_capture_date DESC NULLS LAST
LIMIT 200;

-- Example 3: filter on a categorical status column
Q: Show all asset versions that are currently being processed
SQL:
SELECT a.asset_name, a.display_id, av.processing_status,
       av.created_on, av.last_analyst
FROM fivebyfive.asset_versions av
JOIN fivebyfive.assets a ON av.asset_id = a.id
WHERE av.processing_status = 'processing'
ORDER BY av.created_on DESC
LIMIT 200;

-- Example 4: NOT EXISTS to find missing relationships
Q: Which sites have no assets at all?
SQL:
SELECT s.display_id, s.site_name, s.address_state, s.address_region
FROM fivebyfive.sites s
WHERE NOT EXISTS (
    SELECT 1 FROM fivebyfive.assets a WHERE a.site_id = s.id
)
ORDER BY s.site_name
LIMIT 200;

-- Example 5: access control join across three tables
Q: Which companies have access to the most asset versions?
SQL:
SELECT c.display_name, COUNT(DISTINCT avcr.asset_version_id) AS version_count
FROM fivebyfive.companies c
JOIN fivebyfive.asset_version_company_access_rights avcr ON avcr.company_id = c.id
GROUP BY c.id, c.display_name
ORDER BY version_count DESC
LIMIT 200;
"""

# ── AWS clients ────────────────────────────────────────────────────────────────
@st.cache_resource
def get_bedrock():
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)

# ── Metadata ───────────────────────────────────────────────────────────────────
@st.cache_data
def load_metadata():
    with open(METADATA_PATH) as f:
        return json.load(f)

@st.cache_data
def build_tier1(_metadata):
    lines = ["Available tables:"]
    for table, info in _metadata.items():
        desc = info.get("description", "")
        lines.append(f"- {table}: {desc}")
    return "\n".join(lines)

@st.cache_data
def load_sample_values() -> dict[str, dict[str, list]]:
    result: dict[str, dict[str, list]] = {}
    try:
        conn = psycopg2.connect(**DB)
        cur = conn.cursor()
        for table, column in CATEGORICAL_COLUMNS:
            try:
                cur.execute(
                    f"SELECT DISTINCT {column} FROM fivebyfive.{table} "
                    f"WHERE {column} IS NOT NULL ORDER BY {column} LIMIT {MAX_DISTINCT_VALUES + 1}"
                )
                rows = cur.fetchall()
                if len(rows) <= MAX_DISTINCT_VALUES:
                    result.setdefault(table, {})[column] = [r[0] for r in rows]
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return result

def build_tier2(metadata, tables, sample_values: dict | None = None):
    sv = sample_values or {}
    parts = []
    for table in tables:
        if table not in metadata:
            continue
        info = metadata[table]
        lines = [
            f"TABLE: fivebyfive.{table}",
            f"Description: {info.get('description', '')}",
            "Columns:",
        ]
        for col in info["columns"]:
            nullable = "" if col.get("nullable", True) else " NOT NULL"
            desc = col.get("description", "")
            known = sv.get(table, {}).get(col["name"])
            if known:
                values_hint = f"known values: {', '.join(repr(v) for v in known)}"
                desc = f"{desc}  [{values_hint}]" if desc else f"[{values_hint}]"
            desc_str = f"  -- {desc}" if desc else ""
            lines.append(f"  {col['name']} {col['type']}{nullable}{desc_str}")
        if info.get("foreign_keys"):
            lines.append("Foreign Keys:")
            for fk in info["foreign_keys"]:
                lines.append(
                    f"  {fk['constrained_columns']} → "
                    f"fivebyfive.{fk['referred_table']}.{fk['referred_columns']}"
                )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)

# ── LLM calls ──────────────────────────────────────────────────────────────────
def _llm(prompt: str) -> str:
    bedrock = get_bedrock()
    response = bedrock.converse(
        modelId=LLM_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 2048, "temperature": 0.1},
    )
    return response["output"]["message"]["content"][0]["text"].strip()

def _strip_fences(sql: str) -> str:
    sql = re.sub(r"^```sql\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"^```\s*", "", sql, flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", sql).strip()

def pick_tables(question: str, tier1: str) -> list[str]:
    prompt = f"""You are a database expert selecting tables needed to answer a SQL question.

{tier1}

User question: {question}

Which tables are needed? Include intermediate join tables.
Return ONLY a JSON array of table names, e.g. ["sites", "assets"]
Nothing else — just the JSON array."""
    text = _llm(prompt)
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if m:
        try:
            tables = json.loads(m.group())
            return [t for t in tables if isinstance(t, str)]
        except json.JSONDecodeError:
            pass
    return re.findall(r'"([a-z_]+)"', text)

def generate_sql(question: str, tier2: str, vector_hints: str = "") -> str:
    hints_section = (
        f"\nVector search pre-results (use these IDs in WHERE/JOIN clauses if relevant):\n{vector_hints}\n"
        if vector_hints else ""
    )
    prompt = f"""You are a PostgreSQL expert. Write a SQL query to answer the user's question.

{tier2}
{hints_section}
Reference examples (same schema, same rules):
{FEW_SHOT_EXAMPLES}

Rules:
- Prefix ALL tables with schema: fivebyfive.table_name
- Use JOINs based on the foreign keys shown
- Use the exact column values shown in [known values: ...] hints — do not guess
- Add LIMIT {MAX_ROWS} unless the question asks for a count or aggregate
- Return ONLY the SQL query — no markdown, no backticks, no explanation

User question: {question}

SQL:"""
    return _strip_fences(_llm(prompt))

def fix_sql(question: str, bad_sql: str, error: str, tier2: str) -> str:
    prompt = f"""You are a PostgreSQL expert. Fix this SQL query that produced an error.

{tier2}

Original question: {question}

Bad SQL:
{bad_sql}

Error: {error}

Return ONLY the corrected SQL query, no explanation."""
    return _strip_fences(_llm(prompt))

def format_answer(question: str, sql: str, columns: list, rows: list) -> str:
    if not rows:
        results_text = "Query returned no rows."
    else:
        header = " | ".join(columns)
        sample = "\n".join(" | ".join(str(v) for v in row) for row in rows[:50])
        results_text = f"{header}\n{sample}"
        if len(rows) > 50:
            results_text += f"\n... ({len(rows)} total rows, first 50 shown)"

    prompt = f"""The user asked: {question}

SQL executed:
{sql}

Results:
{results_text}

Give a clear, concise answer to the question based on these results.
Be direct and specific. Highlight key numbers or findings."""
    return _llm(prompt)

# ── Vector search ──────────────────────────────────────────────────────────────
def _vec_str(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"

def embed_text(text: str) -> list[float]:
    bedrock = get_bedrock()
    body = json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True})
    response = bedrock.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())["embedding"]

def vector_search(tables: list[str], question: str) -> str:
    active = [t for t in tables if t in VECTOR_TABLES]
    if not active:
        return ""
    try:
        vec = _vec_str(embed_text(question))
    except Exception:
        return ""

    hints = []

    if "physical_components" in active:
        sql = f"""
            SELECT id::text, manufacturer, model_identifier, component_description,
                   round((1 - (embedding <=> '{vec}'::vector))::numeric, 3) AS similarity
            FROM fivebyfive.physical_components
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> '{vec}'::vector
            LIMIT 5
        """
        cols, rows, err = run_sql(sql)
        if not err and rows:
            lines = ["Semantically similar physical components (by vector search):"]
            for row in rows:
                id_, mfr, model, desc, sim = row
                lines.append(f"  id={id_}  similarity={sim}  {mfr} {model}: {desc}")
            hints.append("\n".join(lines))

    if "asset_version_notes" in active:
        sql = f"""
            SELECT id::text, asset_version_id::text, text_content,
                   round((1 - (embedding <=> '{vec}'::vector))::numeric, 3) AS similarity
            FROM fivebyfive.asset_version_notes
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> '{vec}'::vector
            LIMIT 5
        """
        cols, rows, err = run_sql(sql)
        if not err and rows:
            lines = ["Semantically similar asset version notes (by vector search):"]
            for row in rows:
                id_, av_id, text, sim = row
                lines.append(f"  note_id={id_}  asset_version_id={av_id}  similarity={sim}: {text[:200]}")
            hints.append("\n".join(lines))

    return "\n\n".join(hints)

def vectors_ready() -> bool:
    _, rows, err = run_sql(
        "SELECT COUNT(*) FROM fivebyfive.physical_components WHERE embedding IS NOT NULL"
    )
    return not err and rows and rows[0][0] > 0

# ── Database ───────────────────────────────────────────────────────────────────
def run_sql(sql: str) -> tuple[list, list, str | None]:
    try:
        conn = psycopg2.connect(**DB)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(MAX_ROWS)
            return columns, rows, None
        finally:
            conn.close()
    except Exception as e:
        return [], [], str(e)

def db_ok() -> bool:
    try:
        conn = psycopg2.connect(**DB, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False

def bedrock_ok() -> bool:
    try:
        boto3.client("bedrock-runtime", region_name=AWS_REGION).get_waiter  # noqa
        return True
    except Exception:
        return False

# ── Page setup ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FiveByFive Assistant",
    page_icon="🏗️",
    layout="wide",
)

with st.sidebar:
    st.title("🏗️ FiveByFive")
    st.caption("Infrastructure Data Assistant")
    st.divider()
    db_status = db_ok()
    st.markdown(f"**Database:** {'🟢 Connected' if db_status else '🔴 Unreachable'}")
    st.markdown(f"**Region:** `{AWS_REGION}`")
    st.markdown(f"**LLM:** `{LLM_MODEL_ID}`")
    st.markdown(f"**Embeddings:** `{EMBED_MODEL_ID}`")
    vec_status = vectors_ready()
    st.markdown(
        f"**Vectors:** {'🟢 Ready' if vec_status else '🟡 Not set up'}"
        + ("" if vec_status else " — run `python3 setup_vectors.py`")
    )
    st.divider()
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()
    with st.expander("Table list (58 tables)"):
        meta = load_metadata()
        for t in meta:
            st.markdown(f"- `{t}`")

st.title("FiveByFive Data Assistant")
st.caption("Ask questions about sites, assets, models, components, revisions, and more.")

if not db_status:
    st.error("Cannot connect to the database. Check DB_HOST and credentials in environment variables.")

# ── Chat state ─────────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

metadata = load_metadata()
tier1 = build_tier1(metadata)
sample_values = load_sample_values()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
            with st.expander("SQL"):
                st.code(msg["sql"], language="sql")
        if msg.get("tables"):
            with st.expander(f"Tables used ({len(msg['tables'])})"):
                st.write(", ".join(f"`{t}`" for t in msg["tables"]))
        if msg.get("columns") and msg.get("rows"):
            with st.expander(f"Raw results ({len(msg['rows'])} rows)"):
                st.dataframe(
                    pd.DataFrame(msg["rows"], columns=msg["columns"]),
                    use_container_width=True,
                )

# ── Chat input ─────────────────────────────────────────────────────────────────
if question := st.chat_input("Ask a question about your data..."):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.status("Working...", expanded=True) as status:

            status.write("Identifying relevant tables...")
            tables = pick_tables(question, tier1)
            status.write(f"Selected: {', '.join(f'`{t}`' for t in tables)}")

            tier2 = build_tier2(metadata, tables, sample_values)

            vector_hints = ""
            if any(t in VECTOR_TABLES for t in tables):
                status.write("Running vector search...")
                vector_hints = vector_search(tables, question)

            status.write("Generating SQL...")
            sql = generate_sql(question, tier2, vector_hints)

            status.write("Running query...")
            columns, rows, error = run_sql(sql)

            if error:
                status.write("Fixing SQL error...")
                sql = fix_sql(question, sql, error, tier2)
                columns, rows, error = run_sql(sql)

            if error:
                answer = (
                    f"I wasn't able to run the query. Error: `{error}`\n\n"
                    f"**Generated SQL:**\n```sql\n{sql}\n```"
                )
                status.update(label="Query failed", state="error")
            else:
                status.write("Formatting answer...")
                answer = format_answer(question, sql, columns, rows)
                status.update(label="Done", state="complete")

        st.markdown(answer)

        if sql:
            with st.expander("SQL query"):
                st.code(sql, language="sql")
        if tables:
            with st.expander(f"Tables used ({len(tables)})"):
                st.write(", ".join(f"`{t}`" for t in tables))
        if columns and rows:
            with st.expander(f"Raw results ({len(rows)} rows)"):
                st.dataframe(
                    pd.DataFrame(rows, columns=columns),
                    use_container_width=True,
                )

        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "sql": sql,
            "tables": tables,
            "columns": columns,
            "rows": rows,
        })
