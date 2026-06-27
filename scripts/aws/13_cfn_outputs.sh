#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"

STACK_NAME="${STACK_NAME:-adv-rag-cfn-prod}"
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs' \
  --output table
