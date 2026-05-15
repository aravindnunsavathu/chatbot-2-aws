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

### Step 4 — populate pgvector embeddings (one-time)

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

```bash
cd terraform
terraform destroy
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
