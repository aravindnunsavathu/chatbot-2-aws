#!/usr/bin/env python3
"""
One-time setup: enables pgvector, adds embedding columns to physical_components
and asset_version_notes, populates them using Amazon Bedrock Titan Embeddings v2,
and creates HNSW indexes.

Run against your RDS instance with env vars set:
    DB_HOST=... DB_USER=... DB_PASSWORD=... python3 setup_vectors.py
"""
import json
import os

import boto3
import psycopg2

AWS_REGION     = os.environ.get("AWS_REGION", "us-east-1")
EMBED_MODEL_ID = os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBED_DIM      = 1536  # Titan Embeddings v2 output dimension

DB = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ.get("DB_NAME", "fivebyfiveqa"),
    user=os.environ.get("DB_USER", "postgres"),
    password=os.environ.get("DB_PASSWORD", ""),
)

BATCH_SIZE = 20

bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def vec_str(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


def embed(text: str) -> list[float]:
    body = json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True})
    response = bedrock.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())["embedding"]


def setup():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    # ── 1. Enable extension ────────────────────────────────────────────────────
    print("Enabling pgvector extension...")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    conn.commit()
    print("  Done")

    # ── 2. Add embedding columns ───────────────────────────────────────────────
    print("\nAdding embedding columns...")
    cur.execute(f"""
        ALTER TABLE fivebyfive.physical_components
        ADD COLUMN IF NOT EXISTS embedding vector({EMBED_DIM})
    """)
    cur.execute(f"""
        ALTER TABLE fivebyfive.asset_version_notes
        ADD COLUMN IF NOT EXISTS embedding vector({EMBED_DIM})
    """)
    conn.commit()
    print("  Done")

    # ── 3. Embed physical_components ───────────────────────────────────────────
    print("\nEmbedding physical_components (manufacturer + model + description)...")
    cur.execute("""
        SELECT id, manufacturer, model_identifier, component_description
        FROM fivebyfive.physical_components
        WHERE component_description IS NOT NULL
          AND embedding IS NULL
    """)
    rows = cur.fetchall()
    print(f"  {len(rows)} rows to process")

    for i, (id_, manufacturer, model_id, desc) in enumerate(rows):
        text = " ".join(filter(None, [manufacturer, model_id, desc])).strip()
        if not text:
            continue
        cur.execute(
            "UPDATE fivebyfive.physical_components SET embedding = %s::vector WHERE id = %s",
            (vec_str(embed(text)), str(id_)),
        )
        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()
            print(f"  {i + 1}/{len(rows)}")

    conn.commit()
    print(f"  Done: {len(rows)} components embedded")

    # ── 4. Embed asset_version_notes ───────────────────────────────────────────
    print("\nEmbedding asset_version_notes (text_content)...")
    cur.execute("""
        SELECT id, text_content
        FROM fivebyfive.asset_version_notes
        WHERE text_content IS NOT NULL
          AND text_content <> ''
          AND embedding IS NULL
    """)
    rows = cur.fetchall()
    print(f"  {len(rows)} rows to process")

    for i, (id_, text) in enumerate(rows):
        if not text or not text.strip():
            continue
        cur.execute(
            "UPDATE fivebyfive.asset_version_notes SET embedding = %s::vector WHERE id = %s",
            (vec_str(embed(text)), str(id_)),
        )
        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()
            print(f"  {i + 1}/{len(rows)}")

    conn.commit()
    print(f"  Done: {len(rows)} notes embedded")

    # ── 5. Create HNSW indexes ─────────────────────────────────────────────────
    print("\nCreating HNSW indexes (cosine distance)...")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS physical_components_embedding_idx
        ON fivebyfive.physical_components
        USING hnsw (embedding vector_cosine_ops)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS asset_version_notes_embedding_idx
        ON fivebyfive.asset_version_notes
        USING hnsw (embedding vector_cosine_ops)
    """)
    conn.commit()
    print("  Done")

    cur.close()
    conn.close()
    print("\nSetup complete. pgvector is ready.")


if __name__ == "__main__":
    setup()
