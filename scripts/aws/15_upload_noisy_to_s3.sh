#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"

STACK_NAME="${STACK_NAME:-adv-rag-cfn-prod}"
PREFIX="${PREFIX:-noisy_data/}"

BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='CorpusBucketName'].OutputValue | [0]" \
  --output text)"

if [ -z "$BUCKET" ] || [ "$BUCKET" = "None" ]; then
  echo "CorpusBucketName output not found. Deploy the updated CloudFormation stack first." >&2
  exit 1
fi

aws s3 sync "$ROOT_DIR/seed/docs/noisy_data/" "s3://$BUCKET/$PREFIX" \
  --exclude ".gitkeep" \
  --region "$AWS_REGION"

echo "Uploaded noisy corpus to s3://$BUCKET/$PREFIX"
