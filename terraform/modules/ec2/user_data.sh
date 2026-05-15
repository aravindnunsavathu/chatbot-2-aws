#!/bin/bash
set -e

# Install Docker
dnf install -y docker
systemctl start docker
systemctl enable docker

# Write environment file (root-only readable)
cat > /opt/chatbot.env <<ENVEOF
AWS_REGION=${aws_region}
LLM_MODEL_ID=${llm_model_id}
EMBED_MODEL_ID=${embed_model_id}
DB_HOST=${db_host}
DB_PORT=5432
DB_NAME=${db_name}
DB_USER=${db_username}
DB_PASSWORD=${db_password}
ENVEOF
chmod 600 /opt/chatbot.env

# Store ECR URL (used by start script and update script)
echo "${ecr_url}" > /opt/ecr_url

# Write the deploy/restart script — also used for updates
cat > /opt/start_chatbot.sh <<'SCRIPTEOF'
#!/bin/bash
set -e
ECR_URL=$(cat /opt/ecr_url)
AWS_REGION=$(grep AWS_REGION /opt/chatbot.env | cut -d= -f2)
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_URL
docker pull $ECR_URL:latest
docker stop chatbot 2>/dev/null || true
docker rm chatbot 2>/dev/null || true
docker run -d \
  --name chatbot \
  --restart unless-stopped \
  -p 8501:8501 \
  --env-file /opt/chatbot.env \
  $ECR_URL:latest
SCRIPTEOF
chmod +x /opt/start_chatbot.sh
