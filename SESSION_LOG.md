# FiveByFive AWS Chatbot — Build Session Log

Full record of the conversation, decisions, errors, and fixes from building and deploying the AWS version of the FiveByFive chatbot.

---

## Phase 1 — Planning the AWS version

**Q: Instead of running locally, I want to deploy in AWS. I want the AWS chatbot code in a folder called 'chatbot-2-aws'. First tell me how you will do this.**

Proposed approach:
- Copy `app.py` and replace Ollama with Amazon Bedrock (Converse API for LLM, Titan Embeddings v2 for vectors)
- Replace hardcoded DB config with environment variables pointing to RDS
- Write Terraform to provision: VPC, RDS PostgreSQL, ECR, App Runner, IAM roles
- Use AWS App Runner to host the Streamlit container

Decided to proceed.

---

## Phase 2 — Building chatbot-2-aws

Created the following files in `/chatbot-2-aws`:
- `app.py` — Streamlit app using Bedrock instead of Ollama
- `setup_vectors.py` — pgvector setup using Bedrock Titan Embeddings v2 (1536d)
- `Dockerfile` — container for deployment
- `requirements.txt` — streamlit, psycopg2-binary, boto3, pandas
- `fivebyfive_metadata.json` — copied from chatbot-2 (58 tables with descriptions)
- `.env.example` — documents all environment variables
- `README.md` — architecture, deployment steps, cost estimate

Key changes from local version:

| Component | Local | AWS |
|---|---|---|
| LLM | Ollama llama3.1:8b | Bedrock Claude |
| Embeddings | nomic-embed-text 768d | Titan Embeddings v2 1536d |
| Database | localhost:5433 | Amazon RDS |
| Hosting | streamlit run | EC2 t3.micro (Docker) |
| Config | Hardcoded | Environment variables |

---

## Phase 3 — Terraform

**Q: Will you be able to create Terraform for this deployment? I am using a free version of AWS account.**

Created Terraform module structure:
```
terraform/
  main.tf
  variables.tf
  outputs.tf
  terraform.tfvars
  modules/
    vpc/
    rds/
    ecr/
    app_runner/   ← later replaced with ec2/
```

Free-tier choices:
- RDS: `db.t3.micro` (750 hrs/month free for 12 months)
- No NAT Gateway (too expensive)
- Bedrock VPC endpoint to allow App Runner → Bedrock without NAT

---

## Phase 4 — App Runner errors and switch to EC2

### Error 1: App Runner VPC Connector subscription required
```
SubscriptionRequiredException: The AWS Access Key Id needs a subscription for the service
```
**Cause:** App Runner VPC connector requires a paid subscription not available on free-tier accounts.

**Q: Can I use App Runner on free plan?**
App Runner is ~$5–10/month, not in free tier. VPC connector requires additional subscription.

**Decision: Switch to EC2 t2.micro.**

### Error 2: t2.micro not free tier eligible
```
InvalidParameterCombination: The specified instance type is not eligible for Free Tier.
```
**Fix:** Changed to `t3.micro` — newer AWS accounts use t3.micro for free tier.

### Architecture after switch to EC2:
- EC2 t3.micro in public subnet with Elastic IP
- Docker container runs Streamlit on port 8501
- EC2 is inside VPC so RDS stays private (no public RDS needed)
- Deployments triggered via AWS SSM (no SSH key required by default)
- Removed Bedrock VPC endpoint (~$14/month saved)
- RDS security group: allow port 5432 from EC2 security group only

---

## Phase 5 — EC2 connectivity issues

### Error: SSM send-command — instance not in valid state
```
InvalidInstanceId: Instances not in a valid state for account
```
**Cause:** SSM agent not yet registered (takes 2–3 minutes after launch).
**Also:** `ec2-instance-connect` package not installed in user_data.

**Fix:** Added `ec2-instance-connect` to user_data.sh, ran `terraform apply` to replace instance.

### Error: EC2 Instance Connect failed
```
Failed to connect to your instance — Error establishing SSH connection
```
**Fix:** Created EC2 key pair (`fivebyfive-key`) in AWS Console, added `key_name` variable to Terraform, ran `terraform apply`.

SSH command:
```bash
ssh -i ~/Downloads/fivebyfive-key.pem ec2-user@<ec2-public-ip>
```

---

## Phase 6 — Docker image issues

### Error: No matching manifest for linux/amd64
```
no matching manifest for linux/amd64 in the manifest list entries
```
**Cause:** Mac is Apple Silicon (ARM64). Image was built for arm64, EC2 is x86_64.

**Fix:** Rebuild with buildx for the correct platform:
```bash
docker buildx create --use --name multiplatform-builder
docker buildx build \
  --platform linux/amd64 \
  --push \
  -t <ecr-url>:latest \
  .
```

### Error: No space left on device
```
write /var/lib/docker/tmp/GetImageBlob...: no space left on device
```
**Cause:** Default EC2 root volume is 8GB, Docker image is larger.

**Fix:** Added `root_block_device { volume_size = 20, volume_type = "gp3" }` to EC2 Terraform module.

Terraform resized EBS in place without replacing instance. Filesystem extension needed:
```bash
sudo growpart /dev/xvda 1
sudo xfs_growfs /
```

---

## Phase 7 — Bedrock model issues

### Error 1: On-demand throughput not supported
```
ValidationException: Invocation of model ID anthropic.claude-3-5-haiku-20241022-v1:0
with on-demand throughput isn't supported. Use an inference profile.
```
**Fix:** Changed model ID to use `us.` prefix (cross-region inference profile):
```
us.anthropic.claude-3-5-haiku-20241022-v1:0
```

**Important:** `docker restart` does NOT re-read the env file. Must run `/opt/start_chatbot.sh` to recreate the container with updated env vars.

### Error 2: Marketplace permissions denied
```
AccessDeniedException: IAM role is not authorized to perform aws-marketplace:ViewSubscriptions, Subscribe
```
**Fix:** Added Marketplace permissions to EC2 IAM role:
```hcl
{
  Effect   = "Allow"
  Action   = ["aws-marketplace:ViewSubscriptions", "aws-marketplace:Subscribe", "aws-marketplace:Unsubscribe"]
  Resource = "*"
}
```

### Error 3: Anthropic use case form not submitted
```
ResourceNotFoundException: Model use case details have not been submitted for this account.
```
**Fix:** Went to AWS Console → Bedrock → Model catalog → Claude 3.5 Haiku → Open in playground → filled out Anthropic use case form.

### Error 4: Model is LEGACY and account has no prior usage
```
ResourceNotFoundException: This Model is marked by provider as Legacy and you have
not been actively using the model in the last 30 days.
```
**Cause:** `claude-3-5-haiku-20241022-v1:0` is LEGACY. New accounts without prior usage are blocked.

**Fix:** Checked active models:
```bash
aws bedrock list-foundation-models \
  --by-provider Anthropic \
  --region us-east-1 \
  --query 'modelSummaries[?modelLifecycle.status==`ACTIVE`].{id:modelId,name:modelName}' \
  --output table
```

Active models available:
- `anthropic.claude-haiku-4-5-20251001-v1:0` ← chosen (cheapest)
- `anthropic.claude-sonnet-4-6`
- `anthropic.claude-opus-4-7`

Changed to: `us.anthropic.claude-haiku-4-5-20251001-v1:0`

---

## Phase 8 — Data migration (local PostgreSQL → RDS)

**Q: How do I copy tables from local PostgreSQL to RDS?**

RDS is in a private subnet — can't connect directly from Mac. Use SSH tunnel through EC2.

### SSH tunnel:
```bash
# Terminal 1 — keep running
ssh -i ~/Downloads/fivebyfive-key.pem \
  -L 5434:fivebyfive-postgres.cozsgk6satoj.us-east-1.rds.amazonaws.com:5432 \
  ec2-user@<ec2-public-ip> \
  -N
```

### pg_dump issues and fixes:

| Error | Fix |
|---|---|
| `role "postgres" does not exist` | Use `-U aravindnunsavathu` (macOS username) |
| `role "aravindnunsavathu" does not exist` on RDS | Add `--no-owner` to pg_dump |
| `backslash commands are restricted` | Use `-Fc` custom format + `pg_restore` |
| Password auth failed | Use `PGPASSWORD` env var |
| SSL error | Use `PGSSLMODE=require` |

### Working commands:
```bash
# Dump
pg_dump -h localhost -p 5433 -U aravindnunsavathu \
  -n fivebyfive --no-owner -Fc fivebyfiveqa \
  > /Users/aravindnunsavathu/Downloads/fivebyfive_dump.dump

# Restore (tunnel must be open)
export PGPASSWORD=$(cd terraform && terraform output -raw db_password)
PGSSLMODE=require pg_restore \
  -h localhost -p 5434 \
  -U fivebyfive_admin \
  -d fivebyfiveqa \
  --no-owner \
  /Users/aravindnunsavathu/Downloads/fivebyfive_dump.dump
```

---

## Phase 9 — Vector setup issues

### Issue: setup_vectors.py started Streamlit instead of running the script
```
Uvicorn server started on 0.0.0.0:8501
```
**Cause:** The Dockerfile uses `ENTRYPOINT` (not `CMD`), so any command passed to `docker run` is appended to the entrypoint rather than replacing it.

**Fix:** Use `--entrypoint` to override:
```bash
sudo docker run --rm --env-file /opt/chatbot.env \
  --entrypoint python3 \
  $(cat /opt/ecr_url):latest setup_vectors.py
```

### Issue: Local embeddings (768d) restored to RDS — incompatible with AWS app (1024d)
**Cause:** The local setup_vectors.py used Ollama `nomic-embed-text` which outputs 768d vectors. The pg_restore copied these into RDS. The AWS app uses Bedrock Titan v2 which outputs 1024d. Querying with a 1024d vector against 768d embeddings causes a dimension mismatch error.

**Fix:** Drop the old embedding columns before running setup_vectors.py:
```bash
PGPASSWORD=$(grep DB_PASSWORD /opt/chatbot.env | cut -d= -f2) \
PGSSLMODE=require \
psql -h $(grep DB_HOST /opt/chatbot.env | cut -d= -f2) \
  -U fivebyfive_admin -d fivebyfiveqa -c "
ALTER TABLE fivebyfive.physical_components DROP COLUMN IF EXISTS embedding;
ALTER TABLE fivebyfive.asset_version_notes DROP COLUMN IF EXISTS embedding;
"
```

### Error: Titan Embeddings v2 dimension 1536 is invalid
```
ValidationException: Malformed input request: #: only 1 subschema matches out of 2
```
**Cause:** `EMBED_DIM = 1536` was set based on incorrect assumption. Titan Embeddings **v1** outputs 1536d. Titan Embeddings **v2** only supports 256, 512, or 1024 dimensions.

**Fix:** Changed `EMBED_DIM = 1536` to `EMBED_DIM = 1024` in both `app.py` and `setup_vectors.py`. Rebuilt and pushed the Docker image, dropped old embedding columns, re-ran setup_vectors.py.

### Missing tables after first pg_restore (physical_components, asset_version_notes)
**Cause:** These two tables have an `embedding vector(768)` column from the local pgvector setup. The pg_restore skipped them because the `vector` extension was not enabled on RDS at restore time.

**Fix:**
1. Enable pgvector on RDS first: `CREATE EXTENSION IF NOT EXISTS vector;`
2. Re-run pg_restore

---

## Phase 10 — ECR cleanup issue

### Error: ECR repository not empty during terraform destroy
```
RepositoryNotEmptyException: cannot be deleted because it still contains images
```
**Fix:** Added `force_delete = true` to ECR repository resource.

However, if Terraform destroy runs before `terraform apply` picks up the change, manually delete images first:
```bash
aws ecr batch-delete-image \
  --repository-name fivebyfive \
  --region us-east-1 \
  --image-ids "$(aws ecr list-images --repository-name fivebyfive --region us-east-1 --query 'imageIds' --output json)"
```

### Error: RDS ENI detach permission denied during terraform destroy
```
AuthFailure: You do not have permission to access the specified resource.
```
**Fix:** Destroy RDS instance first, wait for it to fully terminate, then destroy the rest:
```bash
terraform destroy -target module.rds.aws_db_instance.main
# Wait 5-10 minutes
terraform destroy
```

---

## Standard runbook — bringing the app up from scratch

Use this every time after `terraform destroy`.

### Step 1 — Provision infrastructure
```bash
cd /Users/aravindnunsavathu/Downloads/AI-code/chatbot-2-aws/terraform
terraform apply
```

### Step 2 — Push Docker image to ECR
```bash
cd /Users/aravindnunsavathu/Downloads/AI-code/chatbot-2-aws

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  $(cd terraform && terraform output -raw ecr_repository_url)

docker buildx build \
  --platform linux/amd64 \
  --push \
  -t $(cd terraform && terraform output -raw ecr_repository_url):latest \
  .
```

### Step 3 — Start the app on EC2
```bash
ssh -i ~/Downloads/fivebyfive-key.pem \
  ec2-user@$(cd terraform && terraform output -raw ec2_public_ip)
```
Inside SSH:
```bash
sudo /opt/start_all.sh   # starts both Cube and Streamlit containers
```
(`/opt/start_chatbot.sh` is a symlink to `start_all.sh` — both work.)

### Step 4 — Enable pgvector on RDS (must be done before restore)
Inside SSH on EC2 (must be root — `sudo su -`):
```bash
PGPASSWORD=$(grep DB_PASSWORD /opt/chatbot.env | cut -d= -f2) \
PGSSLMODE=require \
psql -h $(grep DB_HOST /opt/chatbot.env | cut -d= -f2) \
  -U fivebyfive_admin -d fivebyfiveqa \
  -c "CREATE EXTENSION vector SCHEMA public;"
```

Verify it installed:
```bash
PGPASSWORD=$(grep DB_PASSWORD /opt/chatbot.env | cut -d= -f2) \
PGSSLMODE=require \
psql -h $(grep DB_HOST /opt/chatbot.env | cut -d= -f2) \
  -U fivebyfive_admin -d fivebyfiveqa \
  -c "\dx"
```
You should see `vector` in the list.

### Step 5 — Restore data (RDS is empty after destroy)
```bash
# Terminal 1 — SSH tunnel (keep running)
ssh -i ~/Downloads/fivebyfive-key.pem \
  -L 5434:fivebyfive-postgres.cozsgk6satoj.us-east-1.rds.amazonaws.com:5432 \
  ec2-user@$(cd /Users/aravindnunsavathu/Downloads/AI-code/chatbot-2-aws/terraform && terraform output -raw ec2_public_ip) \
  -N

# Terminal 2 — restore
export PGPASSWORD=$(cd /Users/aravindnunsavathu/Downloads/AI-code/chatbot-2-aws/terraform && terraform output -raw db_password)
PGSSLMODE=require pg_restore \
  -h localhost -p 5434 \
  -U fivebyfive_admin \
  -d fivebyfiveqa \
  --no-owner \
  /Users/aravindnunsavathu/Downloads/fivebyfive_dump.dump
```

### Step 6 — Set up vectors (first time only, or after destroy)
Inside SSH on EC2 (must be root — `sudo su -`):
```bash
# Drop any existing embedding columns (e.g. 768d from local restore)
PGPASSWORD=$(grep DB_PASSWORD /opt/chatbot.env | cut -d= -f2) \
PGSSLMODE=require \
psql -h $(grep DB_HOST /opt/chatbot.env | cut -d= -f2) \
  -U fivebyfive_admin -d fivebyfiveqa -c "
ALTER TABLE fivebyfive.physical_components DROP COLUMN IF EXISTS embedding;
ALTER TABLE fivebyfive.asset_version_notes DROP COLUMN IF EXISTS embedding;
"

# Run setup — creates 1024d columns, embeds with Titan v2, builds HNSW indexes
sudo docker run --rm --env-file /opt/chatbot.env \
  --entrypoint python3 \
  $(cat /opt/ecr_url):latest setup_vectors.py
```

### Step 7 — Open the app
```bash
cd /Users/aravindnunsavathu/Downloads/AI-code/chatbot-2-aws/terraform
terraform output app_url
```

---

## Stopping to save costs

```bash
cd /Users/aravindnunsavathu/Downloads/AI-code/chatbot-2-aws/terraform
terraform destroy
```

**Note:** `terraform destroy` deletes RDS data. Re-run Step 4 each time to restore it.
The Docker image in ECR is also deleted — re-run Step 2 to push it again.

---

## Key config values

| Item | Value |
|---|---|
| AWS Region | us-east-1 |
| ECR repo | 032847239191.dkr.ecr.us-east-1.amazonaws.com/fivebyfive |
| RDS endpoint | fivebyfive-postgres.cozsgk6satoj.us-east-1.rds.amazonaws.com |
| RDS database | fivebyfiveqa |
| RDS username | fivebyfive_admin |
| LLM model | us.anthropic.claude-haiku-4-5-20251001-v1:0 |
| Embed model | amazon.titan-embed-text-v2:0 |
| EC2 key pair | ~/Downloads/fivebyfive-key.pem |
| App port | 8501 |

---

---

## Phase 11 — Conversation memory (v1.1.0)

**Q: When the user asks a question, does the chatbot remember the previous questions in that session?**

Added `build_history()` to extract the last 3 user/assistant Q&A pairs from `st.session_state.messages` and inject them as context into both `pick_tables()` and `generate_sql()` prompts. This allows follow-up questions like "show me more of those" or "filter by the same status" to be interpreted correctly.

```python
def build_history(messages: list, max_turns: int = 3) -> str:
    # extracts last N Q&A pairs and formats them for LLM context
```

Tagged as `v1.1.0`.

---

## Phase 12 — Cube.dev semantic layer (v1.2.0)

**Q: Is it a good idea to incorporate a semantic layer?**

Decided to add Cube.dev as a semantic layer to make SQL generation more consistent — pre-defined metrics, canonical join paths, governed measure definitions.

### Cube Cloud attempt (abandoned)

Tried Cube Cloud (managed):
- Git-first mode: model files in `cube/model/`, credentials via environment variables
- Could not complete DB connection: RDS is in a private subnet, Cube Cloud's SSH tunnel UI was not available on the free plan, and making RDS publicly accessible raised security concerns
- Abandoned Cube Cloud in favour of self-hosting Cube on the existing EC2

### Schema authoring

Generated 58 Cube YAML data model files from `fivebyfive_metadata.json` using `generate_cube_schemas.py`:
- One file per table in `cube/model/<TableName>.yaml`
- Each file defines: `sql_table`, `measures` (count + domain aggregates), `dimensions` (typed, with descriptions), `joins` (many_to_one via foreign keys)
- Vector columns (`VECTOR` type) excluded — those tables still use the direct pgvector path
- 8 composite-key tables (no `id` column) got synthetic primary keys: `"{CUBE}.col1 || '_' || {CUBE}.col2"`

### 7 Cube views defined in `cube/model/views/`

| View | Purpose |
|---|---|
| `AssetSummary` | Assets with site location and attributes |
| `AssetVersionStatus` | Full lifecycle status — capture, processing, packaging |
| `SiteOverview` | Sites by state, region, status |
| `EquipmentVolumes` | 3D equipment placements and installation status |
| `PhysicalComponentCatalog` | Hardware component specifications |
| `CompanyAccessRights` | Access control auditing by company |
| `DesignRevisions` | Proposed equipment changes and design states |

### Self-hosted Cube on EC2

Deployed Cube as a second Docker container on the existing EC2 t3.micro alongside the Streamlit chatbot:

**Memory management:** Added 1GB swap file (`/swapfile`) to handle running two containers on 1GB RAM.

**Setup commands (run once on existing EC2):**
```bash
# Swap
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Clone repo for model files
sudo dnf install -y git
sudo git clone https://github.com/aravindnunsavathu/chatbot-2-aws /opt/chatbot-2-aws

# Write /opt/cube.env (see Key config values below)
# Write /opt/start_all.sh (starts both containers on chatbot-net Docker network)
sudo /opt/start_all.sh
```

**Both containers share the Docker network `chatbot-net`** so the chatbot can reach Cube at `http://cube:4000` by container name.

**Cube model files are live-mounted** from `/opt/chatbot-2-aws/cube/` — `git pull` inside `start_all.sh` updates schemas without rebuilding the image.

**Security group:** Port 4000 added to EC2 SG for Cube REST API (applied with `terraform apply -target`).

### app.py integration — Cube REST API

Added routing logic in the chat loop with two paths:

**Path 1 — Cube REST API** (analytical questions):
1. `pick_view()` — LLM picks one of the 7 views or returns `"none"`
2. `generate_cube_query()` — LLM generates a Cube JSON query (measures, dimensions, filters, order, limit)
3. `run_cube_query()` — POST to `http://cube:4000/cubejs-api/v1/load`
4. Cube generates and executes optimised SQL, returns results
5. Expander shows the Cube query JSON

**Path 2 — Direct SQL + pgvector** (fallback):
- Triggered when Cube is unavailable or `pick_view()` returns `"none"`
- Used for vector/semantic similarity questions (`physical_components`, `asset_version_notes`)
- Same as original flow: pick tables → vector search → generate SQL → run against RDS
- Expander shows the generated SQL

**Sidebar** shows `🟢 7 views` when Cube is reachable, `🔴 Unavailable` otherwise.

### start_all.sh replaces start_chatbot.sh

`/opt/start_chatbot.sh` is now a symlink to `/opt/start_all.sh`, which starts both containers:
```bash
sudo /opt/start_all.sh
```

### Known routing limitation

Vector search trigger relies on the LLM's judgment in `pick_view()`. If the LLM picks `PhysicalComponentCatalog` instead of returning `"none"`, semantic search is silently skipped and Cube does a text filter instead.

Tagged as `v1.2.0`.

---

## Key config values (updated)

| Item | Value |
|---|---|
| AWS Region | us-east-1 |
| ECR repo | 032847239191.dkr.ecr.us-east-1.amazonaws.com/fivebyfive |
| RDS endpoint | fivebyfive-postgres.cozsgk6satoj.us-east-1.rds.amazonaws.com |
| RDS database | fivebyfiveqa |
| RDS username | fivebyfive_admin |
| LLM model | us.anthropic.claude-haiku-4-5-20251001-v1:0 |
| Embed model | amazon.titan-embed-text-v2:0 |
| EC2 key pair | ~/Downloads/fivebyfive-key.pem |
| EC2 public IP | 54.225.235.62 |
| App port | 8501 |
| Cube REST API port | 4000 |
| Cube env file | /opt/cube.env |
| Cube model files | /opt/chatbot-2-aws/cube/model/ |

---

## Phase 13 — README redeploy guide completed

Updated `README.md` to include the full 7-step post-destroy runbook, matching the standard runbook documented in this SESSION_LOG. Previously the README only had a 4-step deploy flow that skipped the database restore entirely.

Changes made:
- Added **"Redeploying after terraform destroy"** section with all 7 steps
- Step 4: enable pgvector on RDS via SSH (required before restore)
- Step 5: restore from `~/Downloads/fivebyfive_dump.dump` via SSH tunnel + `pg_restore`
- Step 6: drop stale 768d embedding columns + re-embed with Titan v2 (1024d)
- Added data-loss warning to the "Tearing down" section
- Updated Step 4 heading from "(one-time)" to "(once per fresh RDS instance)"

Commits: `49cf60b`, `d7e37ee` on `main`.

Also added dump file creation step to the **Tearing down** section (`pg_dump` command with correct flags) and updated Redeploy step 5 to point back to it instead of a vague parenthetical. Commit: see Phase 13 follow-up.

---

## Phase 14 — Cube schema expansion + deploy fixes

### New Cube views and measures (v1.4.0)

Added support for capacity, RAD count, equipment age, and coverage gap queries:

| File | Change |
|---|---|
| `cube/model/Volumes.yaml` | Added `min_volume_created_on` measure |
| `cube/model/AssetVersions.yaml` | Added `total_rad_count` (sum) measure |
| `cube/model/PhysicalComponents.yaml` | Added `total_weight_kg` (sum) measure |
| `cube/model/views/TowerCapacity.yaml` | **New view** — capacity utilization per tower |
| `cube/model/views/EquipmentVolumes.yaml` | Added capacity fields + `min_volume_created_on` + `total_weight_kg` |
| `cube/model/views/AssetVersionStatus.yaml` | Added `total_rad_count` |
| `cube/model/views/DesignRevisions.yaml` | Added `last_edited_on` + `asset_count` |
| `app.py` | Added city-filtering and oldest-equipment few-shot SQL examples; added `st.map()` for coverage gap questions |

### Issue: Cube compile error — measure referencing foreign cube

**Error:**
```
Member 'Volumes.total_weight_kg' references foreign cubes: PhysicalComponents.
Please split and move this definition to corresponding cubes.
```
**Cause:** Cube does not allow a measure's `sql` field to reference another cube via `{CubeName}.column`. The measure must live in the cube that owns the column.

**Fix:** Moved `total_weight_kg` from `Volumes.yaml` to `PhysicalComponents.yaml`. Exposed it in views via the `Volumes.PhysicalComponents` join path.

**Secondary cause:** `total_weight_kg` was also accidentally left in the `Volumes` section of `EquipmentVolumes.yaml` (where it no longer existed), causing the same error to persist after the first fix. Removed the stale reference.

### Issue: Cube metastore cache serving stale compiled schema

After fixing the YAML, Cube continued to throw the same compile error because `.cubestore` had cached the old compiled schema.

**Fix:**
```bash
sudo rm -rf /opt/chatbot-2-aws/cube/.cubestore
docker restart cube
```

### Issue: git pull on EC2 didn't pick up new files during deploy

The `start_all.sh` script runs `git pull` before starting containers, but if the commit was pushed after the deploy started (or the pull failed silently), new files are missing on EC2.

**Fix:** SSH in and pull manually, then restart only the affected container:
```bash
cd /opt/chatbot-2-aws && sudo git pull origin main
docker restart cube   # for Cube YAML changes only
sudo /opt/start_all.sh   # for app.py changes (requires image rebuild)
```

### Issue: `step_2_deploy_ec2` only shows JSON — no visible progress

The `aws ssm send-command` output is JSON (the CommandId). The command runs asynchronously on EC2 with no terminal feedback. Use SSH + `sudo /opt/start_all.sh` directly for visible real-time output.

### Issue: Docker image built for ARM64 instead of linux/amd64

**Error on EC2:**
```
no matching manifest for linux/amd64 in the manifest list entries
```
**Cause:** `docker build --platform linux/amd64` does not reliably cross-compile on Apple Silicon when using the standard build/tag/push flow.

**Fix:** Use `docker buildx build --push` which handles cross-compilation correctly:
```bash
docker buildx build \
  --platform linux/amd64 \
  --push \
  -t <ecr-url>:latest \
  .
```
Updated `terraform/outputs.tf` `step_1_push_image` output to use this command permanently. Commit: `ccef995`.

### How to verify the correct image is running on EC2

Compare the digest of the running container against the latest ECR push:
```bash
# Running container digest
docker inspect chatbot --format 'Started: {{.State.StartedAt}}  Image: {{.Image}}'

# Latest ECR digest
aws ecr describe-images --repository-name fivebyfive --region us-east-1 \
  --query 'sort_by(imageDetails,&imagePushedAt)[-1].{Pushed:imagePushedAt,Digest:imageDigest}' \
  --output table
```
Digests must match. If they don't, rebuild with `buildx` and re-run `sudo /opt/start_all.sh`.

---

## Phase 15 — Question support: capacity, available space, design states, approval dwell time

### New SQL few-shot examples added to app.py

| Example | Question type |
|---|---|
| Ex 6 | Towers at <X% capacity (HAVING filter on weight/design_capacity ratio) |
| Ex 7 | Available space per tower (available_capacity_kg = design_capacity - installed weight, no threshold) |
| Ex 8 | Towers with pending design simulations (DRAFT state, count distinct assets) |
| Ex 9 | Average days from creation to approval (CONFIRMED state, date arithmetic) |
| Ex 11 | City-level filtering via ILIKE on site_name/address_region (no city column in sites) |
| Ex 12 | Towers with oldest equipment (MIN(volumes.created_on)) |

### Discovery: actual design_state values differ from documentation

The Revisions cube description said `"e.g. draft, submitted, approved"` but the actual values in the database are:
- `DRAFT` — revision in progress / pending
- `CONFIRMED` — approved / complete
- `LUMEA_OUT_OF_SYNC` — external sync issue

Updated `design_state` dimension description in `Revisions.yaml` to show actual values. Added `avg_days_to_confirmation` measure (CASE WHEN CONFIRMED, type: avg) so approval dwell time can be queried via Cube.

### Issue: Cube fan-out join — TowerCapacity capacity % queries failed

**Error:** `'PhysicalComponents_total_weight_kg' not found for path 'TowerCapacity.PhysicalComponents_total_weight_kg'`

**Cause:** Cube cannot join from `Volumes` to both `PhysicalComponents` and `AssetVersions` in a single query (fan-out from one root to two leaf cubes). The `TowerCapacity` view depended on this.

**Fix:** Updated `TowerCapacity` description to exclude ratio/percentage questions. Capacity utilisation queries now route to the direct SQL path using the new few-shot examples.

### Issue: Cube join path — DesignRevisions "pending simulation" queries failed

**Error:** `Can't find join path to join 'Revisions,AssetVersions', 'Revisions,AssetVersions,Assets', 'Revisions'`

**Cause:** Same reverse-join limitation as the Volumes case. Revisions joins to Models (many_to_one), and AssetVersions joins to Models (many_to_one), but Cube cannot auto-traverse the reverse direction (`Models → AssetVersions`) from the Revisions side.

**Fix:** Added a direct `AssetVersions` join to `Revisions.yaml`:
```yaml
- name: AssetVersions
  sql: "{AssetVersions}.base_model_id = {TABLE}.model_id"
  relationship: many_to_one
```
This is the same pattern used to fix `Volumes.yaml`. Commit: `dfa6f68`.

### Recurring pattern: Cube reverse join limitation

Any time a cube A and cube B both join to a common cube C (many_to_one), Cube cannot traverse from A to B via C without an explicit direct join. Fix is always the same:

```yaml
# In A.yaml
joins:
  - name: B
    sql: "{B}.foreign_key = {TABLE}.local_key"
    relationship: many_to_one
```

Cubes fixed so far: `Volumes → AssetVersions`, `Revisions → AssetVersions`.

---

## Important lessons learned

1. **`docker restart` does not re-read `--env-file`** — must run `/opt/start_chatbot.sh` to pick up env changes
2. **Apple Silicon Macs must use `docker buildx --platform linux/amd64`** — regular `docker build --platform` doesn't cross-compile reliably
3. **EBS resize doesn't extend the filesystem** — must run `growpart` + `xfs_growfs` manually
4. **Anthropic models on new AWS accounts require inference profile prefix `us.`** — direct model IDs fail with on-demand throughput error
5. **LEGACY Bedrock models block new accounts** — check `modelLifecycle.status==ACTIVE` before choosing a model
6. **RDS in private subnet needs SSH tunnel from Mac** — EC2 acts as jump host
7. **Plain SQL pg_dump fails on RDS** — use `-Fc` custom format + `pg_restore`
8. **terraform destroy removes all data** — keep a local dump file and restore each time
9. **Enable pgvector extension on RDS before restoring** — tables with `vector` columns are silently skipped if the extension isn't present
10. **Titan Embeddings v2 max dimension is 1024, not 1536** — v1 outputs 1536d; v2 supports only 256, 512, or 1024
11. **Drop old embedding columns before re-running setup_vectors.py** — `ADD COLUMN IF NOT EXISTS` silently skips if wrong-dimension column already exists
12. **Use `--entrypoint python3` to run scripts in the container** — `ENTRYPOINT` in Dockerfile means commands passed to `docker run` are appended, not replacing the entrypoint
13. **Cube measures cannot reference foreign cube columns** — `sql: "{OtherCube}.column"` is invalid; the measure must live in the cube that owns the column
14. **Cube metastore caches compiled schema** — after fixing YAML errors, delete `.cubestore` and restart: `sudo rm -rf /opt/chatbot-2-aws/cube/.cubestore && docker restart cube`
15. **`git pull` in `start_all.sh` can miss files if the commit was in-flight** — SSH in and pull manually if new files are missing on EC2
16. **`aws ssm send-command` is async** — use `sudo /opt/start_all.sh` over SSH directly for real-time output and confirmation
17. **`docker build --platform linux/amd64` doesn't reliably cross-compile on Apple Silicon** — always use `docker buildx build --platform linux/amd64 --push` instead
18. **Cube cannot traverse reverse joins (A → C ← B)** — when cubes A and B both join to a common cube C, Cube cannot auto-traverse from A to B via C; add a direct explicit join `{B}.foreign_key = {TABLE}.local_key` in A's joins section
19. **Actual `design_state` values in DB are uppercase** — `DRAFT` (pending/in-progress), `CONFIRMED` (approved/complete), `LUMEA_OUT_OF_SYNC`; documentation said lowercase but the real values differ
20. **Cube fan-out (two leaf cubes from one root in one query) always fails** — if a view requires simultaneous joins to two separate leaf cubes (e.g., PhysicalComponents for weight AND AssetVersions for capacity), route to direct SQL; Cube cannot handle this combination
