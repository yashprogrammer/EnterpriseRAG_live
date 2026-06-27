#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"
require_session_manager_plugin

if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
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
fi

TASK_ARN="$(aws ecs list-tasks \
  --cluster "$CLUSTER" \
  --service-name "$SERVICE" \
  --desired-status RUNNING \
  --query 'taskArns[0]' \
  --output text \
  --region "$AWS_REGION")"

if [ "$TASK_ARN" = "None" ] || [ -z "$TASK_ARN" ]; then
  echo "No running task found for $SERVICE" >&2
  exit 1
fi

aws ecs execute-command \
  --cluster "$CLUSTER" \
  --task "$TASK_ARN" \
  --container app \
  --interactive \
  --command "python scripts/seed_db.py --no-ingest" \
  --region "$AWS_REGION"

aws ecs execute-command \
  --cluster "$CLUSTER" \
  --task "$TASK_ARN" \
  --container app \
  --interactive \
  --command "python scripts/seed_db.py --noise-sample 0" \
  --region "$AWS_REGION"
