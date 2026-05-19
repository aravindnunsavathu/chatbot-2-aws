#!/bin/bash
set -e

# Install Docker, EC2 Instance Connect, git
dnf install -y docker ec2-instance-connect git
systemctl start docker
systemctl enable docker

# 1GB swap — needed to run Cube + Streamlit on t3.micro (1GB RAM)
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Generate a random Cube API secret once at boot
CUBE_API_SECRET=$(openssl rand -hex 32)

# Chatbot environment file (root-only)
cat > /opt/chatbot.env <<ENVEOF
AWS_REGION=${aws_region}
LLM_MODEL_ID=${llm_model_id}
EMBED_MODEL_ID=${embed_model_id}
DB_HOST=${db_host}
DB_PORT=5432
DB_NAME=${db_name}
DB_USER=${db_username}
DB_PASSWORD=${db_password}
CUBE_URL=http://cube:4000
CUBE_API_SECRET=$CUBE_API_SECRET
ENVEOF
chmod 600 /opt/chatbot.env

# Cube environment file (root-only)
cat > /opt/cube.env <<ENVEOF
CUBEJS_DB_TYPE=postgres
CUBEJS_DB_HOST=${db_host}
CUBEJS_DB_PORT=5432
CUBEJS_DB_NAME=${db_name}
CUBEJS_DB_USER=${db_username}
CUBEJS_DB_PASS=${db_password}
CUBEJS_DB_SSL=true
CUBEJS_DB_SSL_REJECT_UNAUTHORIZED=false
CUBEJS_API_SECRET=$CUBE_API_SECRET
CUBEJS_DEV_MODE=true
CUBEJS_TELEMETRY=false
ENVEOF
chmod 600 /opt/cube.env

# Store ECR URL
echo "${ecr_url}" > /opt/ecr_url

# Clone repo — provides Cube model files at /opt/chatbot-2-aws/cube/
git clone https://github.com/aravindnunsavathu/chatbot-2-aws /opt/chatbot-2-aws

# Start script — pulls latest images, updates model files, starts both containers
cat > /opt/start_all.sh <<'SCRIPTEOF'
#!/bin/bash
set -e
ECR_URL=$(cat /opt/ecr_url)
AWS_REGION=$(grep AWS_REGION /opt/chatbot.env | cut -d= -f2)

# ECR login
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_URL

# Shared Docker network so chatbot can reach cube by name
docker network create chatbot-net 2>/dev/null || true

# Pull latest images
docker pull $ECR_URL:latest
docker pull cubejs/cube:latest

# Update Cube model files from git
cd /opt/chatbot-2-aws && git pull origin main 2>/dev/null || true

# Stop and remove existing containers
docker stop chatbot cube 2>/dev/null || true
docker rm  chatbot cube 2>/dev/null || true

# Start Cube (model files mounted from cloned repo)
docker run -d \
  --name cube \
  --restart unless-stopped \
  --network chatbot-net \
  -p 4000:4000 \
  -p 15432:15432 \
  --env-file /opt/cube.env \
  -v /opt/chatbot-2-aws/cube:/cube/conf \
  cubejs/cube:latest

# Start chatbot
docker run -d \
  --name chatbot \
  --restart unless-stopped \
  --network chatbot-net \
  -p 8501:8501 \
  --env-file /opt/chatbot.env \
  $ECR_URL:latest
SCRIPTEOF
chmod +x /opt/start_all.sh

# Keep old name working as an alias
ln -sf /opt/start_all.sh /opt/start_chatbot.sh

# Launch
/opt/start_all.sh
