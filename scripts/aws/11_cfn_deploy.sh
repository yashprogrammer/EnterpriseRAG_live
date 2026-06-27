#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"

STACK_NAME="${STACK_NAME:-adv-rag-cfn-prod}"
TEMPLATE_FILE="$ROOT_DIR/infra/cloudformation/adv-rag.yml"
APP_IMAGE="${APP_IMAGE:-$ECR_URI:latest}"
UI_IMAGE="${UI_IMAGE:-$UI_ECR_URI:latest}"

read_secret() {
  local env_name="$1"
  local label="$2"
  local value="${!env_name:-}"
  if [ -z "$value" ]; then
    read -rsp "$label: " value
    echo
  fi
  if [ -z "$value" ]; then
    echo "$label is required" >&2
    exit 1
  fi
  printf '%s' "$value"
}

OPENAI_VALUE="$(read_secret OPENAI_API_KEY "OpenAI API key")"
TAVILY_VALUE="$(read_secret TAVILY_API_KEY "Tavily API key")"
UPSTASH_URL_VALUE="$(read_secret UPSTASH_REDIS_URL "Upstash Redis REST URL")"
UPSTASH_TOKEN_VALUE="$(read_secret UPSTASH_REDIS_TOKEN "Upstash Redis REST token")"
JWT_VALUE="${JWT_SECRET:-$(openssl rand -hex 32)}"
PG_PW="${POSTGRES_PASSWORD:-$(openssl rand -hex 24)}"

SUBNET_ARRAY=($SUBNETS)
if [ "${#SUBNET_ARRAY[@]}" -lt 3 ]; then
  echo "At least three default/public subnets are required for this stack script." >&2
  exit 1
fi

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$TEMPLATE_FILE" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
    ProjectName="$CFN_PROJECT" \
    AppImage="$APP_IMAGE" \
    UiImage="$UI_IMAGE" \
    VpcId="$VPC_ID" \
    PublicSubnetIds="$(echo "$SUBNETS" | tr ' ' ',')" \
    EfsMountSubnet1="${SUBNET_ARRAY[0]}" \
    EfsMountSubnet2="${SUBNET_ARRAY[1]}" \
    EfsMountSubnet3="${SUBNET_ARRAY[2]}" \
    DesiredCount="${DESIRED_COUNT:-1}" \
    OpenAIApiKey="$OPENAI_VALUE" \
    TavilyApiKey="$TAVILY_VALUE" \
    UpstashRedisUrl="$UPSTASH_URL_VALUE" \
    UpstashRedisToken="$UPSTASH_TOKEN_VALUE" \
    JwtSecret="$JWT_VALUE" \
    PostgresPassword="$PG_PW" \
    GitHubRepo="$GITHUB_REPO" \
    CreateGitHubOidcProvider="${CREATE_GITHUB_OIDC_PROVIDER:-false}"

aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs' \
  --output table
