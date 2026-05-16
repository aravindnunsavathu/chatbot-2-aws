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

## Phase 9 — ECR cleanup issue

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
sudo /opt/start_chatbot.sh
```

### Step 4 — Restore data (RDS is empty after destroy)
```bash
# Terminal 1 — SSH tunnel
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

### Step 5 — Set up vectors (first time only, or after destroy)
Inside SSH on EC2:
```bash
sudo docker run --rm --env-file /opt/chatbot.env \
  $(sudo cat /opt/ecr_url):latest python3 setup_vectors.py
```

### Step 6 — Open the app
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

## Important lessons learned

1. **`docker restart` does not re-read `--env-file`** — must run `/opt/start_chatbot.sh` to pick up env changes
2. **Apple Silicon Macs must use `docker buildx --platform linux/amd64`** — regular `docker build --platform` doesn't cross-compile reliably
3. **EBS resize doesn't extend the filesystem** — must run `growpart` + `xfs_growfs` manually
4. **Anthropic models on new AWS accounts require inference profile prefix `us.`** — direct model IDs fail with on-demand throughput error
5. **LEGACY Bedrock models block new accounts** — check `modelLifecycle.status==ACTIVE` before choosing a model
6. **RDS in private subnet needs SSH tunnel from Mac** — EC2 acts as jump host
7. **Plain SQL pg_dump fails on RDS** — use `-Fc` custom format + `pg_restore`
8. **terraform destroy removes all data** — keep a local dump file and restore each time
