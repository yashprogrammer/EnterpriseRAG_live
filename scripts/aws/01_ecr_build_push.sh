#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"

aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1 || \
  aws ecr create-repository \
    --repository-name "$ECR_REPO" \
    --image-scanning-configuration scanOnPush=true \
    --region "$AWS_REGION" >/dev/null

aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
docker buildx build --platform linux/amd64 -t "$ECR_URI:$SHA" -t "$ECR_URI:latest" --push "$ROOT_DIR"
echo "$ECR_URI:$SHA"
