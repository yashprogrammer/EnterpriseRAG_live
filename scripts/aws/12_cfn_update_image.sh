#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"

STACK_NAME="${STACK_NAME:-adv-rag-cfn-prod}"
TEMPLATE_FILE="$ROOT_DIR/infra/cloudformation/adv-rag.yml"
APP_IMAGE="${APP_IMAGE:-$ECR_URI:latest}"
UI_IMAGE="${UI_IMAGE:-$UI_ECR_URI:latest}"

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$TEMPLATE_FILE" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides AppImage="$APP_IMAGE" UiImage="$UI_IMAGE"

CLUSTER="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue | [0]" \
  --output text)"
SERVICE="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ServiceName'].OutputValue | [0]" \
  --output text)"

aws ecs wait services-stable \
  --cluster "$CLUSTER" \
  --services "$SERVICE" \
  --region "$AWS_REGION"
