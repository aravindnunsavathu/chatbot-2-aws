# FiveByFive Data Assistant — AWS

AWS deployment of the FiveByFive infrastructure chatbot. Identical behaviour to the local version (`chatbot-2`) but runs entirely on AWS — no Ollama, no local database.

See the [local version](https://github.com/aravindnunsavathu/chatbot-2) for background on the architecture and design decisions.

---

## What changed from the local version

| Component | Local (`chatbot-2`) | AWS (`chatbot-2-aws`) |
|---|---|---|
| LLM | Ollama `llama3.1:8b` | Amazon Bedrock `claude-3-5-haiku` |
| Embeddings | Ollama `nomic-embed-text` (768d) | Bedrock Titan Embeddings v2 (1536d) |
| Database | PostgreSQL on `localhost:5433` | Amazon RDS PostgreSQL |
| UI hosting | `streamlit run` locally | AWS App Runner (containerised) |
| Config | Hardcoded constants | Environment variables |

---

## Architecture

```
Browser
  │
  ▼
AWS App Runner (Streamlit container)
  │
  ├── Amazon Bedrock ──► Claude 3.5 Haiku   (table picking, SQL gen, answer)
  │                 ──► Titan Embeddings v2  (vector search)
  │
  └── Amazon RDS PostgreSQL
        schema: fivebyfive (58 tables)
        extension: pgvector (HNSW indexes on 2 tables)
```

---

## AWS services required

| Service | Purpose |
|---|---|
| Amazon Bedrock | LLM inference (Claude) + embeddings (Titan) |
| Amazon RDS for PostgreSQL | Database with pgvector extension |
| AWS App Runner | Hosts the Streamlit container |
| Amazon ECR | Docker image registry |
| IAM | Role granting App Runner access to Bedrock + RDS |

---

## Prerequisites

- AWS account with Bedrock model access enabled for:
  - `anthropic.claude-3-5-haiku-20241022-v1:0`
  - `amazon.titan-embed-text-v2:0`
- RDS PostgreSQL instance with pgvector available
- AWS CLI configured locally (`aws configure`)
- Docker installed

---

## Setup

### 1. RDS — enable pgvector and populate embeddings

Point env vars at your RDS instance and run the one-time setup script:

```bash
export DB_HOST=your-rds-endpoint.rds.amazonaws.com
export DB_USER=postgres
export DB_PASSWORD=your-password
export AWS_REGION=us-east-1

python3 setup_vectors.py
```

This enables the `vector` extension, adds `embedding vector(1536)` columns to `physical_components` and `asset_version_notes`, embeds all rows using Bedrock Titan Embeddings v2, and creates HNSW indexes.

### 2. Build and push Docker image to ECR

```bash
# Create ECR repo (one-time)
aws ecr create-repository --repository-name fivebyfive-chatbot --region us-east-1

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin \
    <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t fivebyfive-chatbot .
docker tag fivebyfive-chatbot:latest \
  <account-id>.dkr.ecr.us-east-1.amazonaws.com/fivebyfive-chatbot:latest
docker push \
  <account-id>.dkr.ecr.us-east-1.amazonaws.com/fivebyfive-chatbot:latest
```

### 3. Deploy to App Runner

Create an App Runner service via the AWS Console or CLI pointing at the ECR image. Set the following environment variables in the App Runner service configuration:

```
AWS_REGION=us-east-1
LLM_MODEL_ID=anthropic.claude-3-5-haiku-20241022-v1:0
EMBED_MODEL_ID=amazon.titan-embed-text-v2:0
DB_HOST=your-rds-endpoint.rds.amazonaws.com
DB_PORT=5432
DB_NAME=fivebyfiveqa
DB_USER=postgres
DB_PASSWORD=your-password
```

### 4. IAM — grant App Runner access to Bedrock

Attach the following policy to the App Runner instance role:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:Converse"
  ],
  "Resource": [
    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-haiku-20241022-v1:0",
    "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
  ]
}
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock and other services |
| `LLM_MODEL_ID` | `anthropic.claude-3-5-haiku-20241022-v1:0` | Bedrock model for SQL generation and answering |
| `EMBED_MODEL_ID` | `amazon.titan-embed-text-v2:0` | Bedrock model for embeddings |
| `DB_HOST` | `localhost` | RDS endpoint |
| `DB_PORT` | `5432` | Database port |
| `DB_NAME` | `fivebyfiveqa` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | _(empty)_ | Database password |

---

## Running locally against RDS (for testing)

```bash
pip install -r requirements.txt

export DB_HOST=your-rds-endpoint.rds.amazonaws.com
export DB_USER=postgres
export DB_PASSWORD=your-password
export AWS_REGION=us-east-1

streamlit run app.py
```

AWS credentials are picked up from `~/.aws/credentials` or environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`).

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit application (Bedrock + RDS) |
| `setup_vectors.py` | One-time pgvector setup using Bedrock Titan embeddings |
| `Dockerfile` | Container definition for App Runner deployment |
| `requirements.txt` | Python dependencies |
| `.env.example` | Documents all required environment variables |
| `fivebyfive_metadata.json` | Schema metadata: 58 tables with descriptions |
| `extract_metadata.py` | Script used to extract schema metadata from PostgreSQL |
