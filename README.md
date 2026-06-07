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
| UI hosting | `streamlit run` locally | EC2 t2.micro (containerised, free tier) |
| Config | Hardcoded constants | Environment variables |

---

## Architecture

```
Browser
  │
  ▼
EC2 t2.micro / Elastic IP (Docker — Streamlit on port 8501)
  │
  ├── Amazon Bedrock ──► Claude 3.5 Haiku   (table picking, SQL gen, answer)
  │                 ──► Titan Embeddings v2  (vector search)
  │
  └── Amazon RDS PostgreSQL  (private subnet, VPC-only access)
        schema: fivebyfive (58 tables)
        extension: pgvector (HNSW indexes on 2 tables)
```

---

## AWS services required

| Service | Purpose |
|---|---|
| Amazon Bedrock | LLM inference (Claude) + embeddings (Titan) |
| Amazon RDS for PostgreSQL | Database with pgvector extension |
| EC2 t2.micro | Hosts the Streamlit Docker container |
| Amazon ECR | Docker image registry |
| IAM | Instance profile granting EC2 access to Bedrock + ECR |

---

## Prerequisites

- AWS account with Bedrock model access enabled for:
  - `anthropic.claude-3-5-haiku-20241022-v1:0`
  - `amazon.titan-embed-text-v2:0`
- AWS CLI configured locally (`aws configure`)
- Terraform >= 1.5 installed
- Docker installed

---

## Deployment

All infrastructure is managed by Terraform. The deployment has four steps.

### Step 1 — provision infrastructure

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars if you want to change region, db name, etc.

terraform init
terraform apply
```

This creates: VPC, private/public subnets, security groups, RDS PostgreSQL (`db.t3.micro`), ECR repository, EC2 t2.micro instance with Elastic IP, and IAM roles.

### Step 2 — build and push the Docker image

Terraform outputs the exact commands to run. Copy and execute them:

```bash
terraform output -raw step_1_push_image
```

This authenticates Docker to ECR, builds the image, and pushes it. Run the printed commands from the `chatbot-2-aws` directory.

### Step 3 — start the app on EC2

```bash
terraform output -raw step_2_deploy_ec2 | bash
```

This uses AWS SSM to run `/opt/start_chatbot.sh` on the EC2 instance, which pulls the image from ECR and starts the container. The public URL is:

```bash
terraform output app_url
```

> **Note:** SSM agent takes ~2 minutes to become available after the instance starts. If the command fails, wait a moment and try again.

### Step 4 — populate pgvector embeddings (once per fresh RDS instance)

Run `setup_vectors.py` inside the container on EC2 via SSM (no SSH or VPN needed):

```bash
terraform output -raw step_3_setup_vectors | bash
```

This enables the `vector` extension, adds `embedding vector(1536)` columns to `physical_components` and `asset_version_notes`, embeds all rows using Bedrock Titan Embeddings v2, and creates HNSW indexes.

---

## Cost estimate (free tier account)

| Resource | Cost |
|---|---|
| EC2 `t2.micro` | Free (750 hrs/month for 12 months) |
| Elastic IP (associated) | Free |
| RDS `db.t3.micro` | Free (750 hrs/month for 12 months) |
| ECR | Free (500 MB/month) |
| Bedrock usage | Pay per token |

---

## Updating the app

To deploy a new version of the app:

```bash
# From the chatbot-2-aws directory — rebuild, push, restart
terraform -chdir=terraform output -raw step_1_push_image | bash
terraform -chdir=terraform output -raw step_2_deploy_ec2 | bash
```

---

## Tearing down

**Before destroying**, create a fresh dump from your local PostgreSQL so you can restore RDS on the next deploy:

```bash
pg_dump -h localhost -p 5433 -U aravindnunsavathu \
  -n fivebyfive --no-owner -Fc fivebyfiveqa \
  > ~/Downloads/fivebyfive_dump.dump
```

Then destroy:

```bash
cd terraform
terraform destroy
```

> **Warning:** `terraform destroy` permanently deletes all AWS resources — including the RDS database and all its data. The ECR image is also removed. To bring the app back, follow the steps below.

---

## Redeploying after `terraform destroy`

`terraform destroy` wipes RDS (all data gone), ECR, and EC2. Run these steps in order from the `chatbot-2-aws` project root.

### Redeploy step 1 — provision infrastructure

```bash
cd terraform && terraform apply && cd ..
```

Wait ~5 minutes for RDS to start and ~2 minutes for the SSM agent on EC2.

### Redeploy step 2 — build and push Docker image

```bash
terraform -chdir=terraform output -raw step_1_push_image | bash
```

### Redeploy step 3 — start the app on EC2

```bash
terraform -chdir=terraform output -raw step_2_deploy_ec2 | bash
```

> If this returns an SSM error, the agent is still starting. Wait 30 seconds and retry.

### Redeploy step 4 — enable pgvector on RDS (must happen before restore)

SSH into EC2, then run as root:

```bash
ssh -i ~/Downloads/fivebyfive-key.pem \
  ec2-user@$(terraform -chdir=terraform output -raw ec2_public_ip)
```

Inside the SSH session:

```bash
sudo su -
PGPASSWORD=$(grep DB_PASSWORD /opt/chatbot.env | cut -d= -f2) \
PGSSLMODE=require \
psql -h $(grep DB_HOST /opt/chatbot.env | cut -d= -f2) \
  -U fivebyfive_admin -d fivebyfiveqa \
  -c "CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;"
```

### Redeploy step 5 — restore the database from dump

You need the dump file at `~/Downloads/fivebyfive_dump.dump`. If you don't have it, see the **Tearing down** section above for the `pg_dump` command — run it against your local PostgreSQL before destroying.

Open **two terminals** from the `chatbot-2-aws` directory:

**Terminal 1 — SSH tunnel (keep running):**

```bash
ssh -i ~/Downloads/fivebyfive-key.pem \
  -L 5434:$(terraform -chdir=terraform output -raw db_endpoint):5432 \
  ec2-user@$(terraform -chdir=terraform output -raw ec2_public_ip) \
  -N
```

**Terminal 2 — restore:**

```bash
export PGPASSWORD=$(terraform -chdir=terraform output -raw db_password)
PGSSLMODE=require pg_restore \
  -h localhost -p 5434 \
  -U fivebyfive_admin \
  -d fivebyfiveqa \
  --no-owner \
  ~/Downloads/fivebyfive_dump.dump
```

### Redeploy step 6 — set up Bedrock vector embeddings

The restored dump may contain stale 768d embedding columns from the local setup. Drop them first, then re-embed with Titan v2 (1024d):

SSH into EC2 as root, then:

```bash
# Drop any old embedding columns from the local setup
PGPASSWORD=$(grep DB_PASSWORD /opt/chatbot.env | cut -d= -f2) \
PGSSLMODE=require \
psql -h $(grep DB_HOST /opt/chatbot.env | cut -d= -f2) \
  -U fivebyfive_admin -d fivebyfiveqa -c "
ALTER TABLE fivebyfive.physical_components DROP COLUMN IF EXISTS embedding;
ALTER TABLE fivebyfive.asset_version_notes DROP COLUMN IF EXISTS embedding;
"

# Re-embed with Titan v2 (1024d) and build HNSW indexes
docker run --rm --env-file /opt/chatbot.env \
  --entrypoint python3 \
  $(cat /opt/ecr_url):latest setup_vectors.py
```

### Redeploy step 7 — open the app

```bash
terraform -chdir=terraform output app_url
```

---

## SSH access (optional)

By default, EC2 is managed via SSM Session Manager (no key pair needed). To enable SSH, create a key pair in the AWS Console and set `key_name` in `terraform.tfvars`:

```hcl
key_name = "your-key-pair-name"
```

Then SSH using:

```bash
ssh -i your-key.pem ec2-user@$(terraform output -raw ec2_public_ip)
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
export DB_USER=fivebyfive_admin
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
| `Dockerfile` | Container definition for EC2 deployment |
| `requirements.txt` | Python dependencies |
| `.env.example` | Documents all required environment variables |
| `fivebyfive_metadata.json` | Schema metadata: 58 tables with descriptions |
| `extract_metadata.py` | Script used to extract schema metadata from PostgreSQL |
