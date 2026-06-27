#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"

STACK_NAME="${STACK_NAME:-adv-rag-cfn-prod}"
SIZE_MB="${SIZE_MB:-200}"
PREFIX="${PREFIX:-noisy_data/}"
PARALLELISM="${PARALLELISM:-4}"

output() {
  local key="$1"
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue | [0]" \
    --output text
}

CLUSTER_NAME="$(output ClusterName)"
TASK_FAMILY="$(output IngestTaskFamily)"
BUCKET="$(output CorpusBucketName)"
SERVICE_NAME="$(output ServiceName)"

SERVICE_JSON="$(aws ecs describe-services \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --region "$AWS_REGION" \
  --output json)"

SUBNETS_CSV="$(printf '%s' "$SERVICE_JSON" | jq -r '.services[0].networkConfiguration.awsvpcConfiguration.subnets | join(",")')"
SGS_CSV="$(printf '%s' "$SERVICE_JSON" | jq -r '.services[0].networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')"

TASK_ARNS=()
for ((i = 0; i < PARALLELISM; i++)); do
  TASK_ARN="$(aws ecs run-task \
    --cluster "$CLUSTER_NAME" \
    --task-definition "$TASK_FAMILY" \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS_CSV],securityGroups=[$SGS_CSV],assignPublicIp=ENABLED}" \
    --overrides "$(jq -cn \
      --arg bucket "$BUCKET" \
      --arg prefix "$PREFIX" \
      --arg size "$SIZE_MB" \
      --arg shard_index "$i" \
      --arg shard_count "$PARALLELISM" \
      '{containerOverrides:[{name:"ingest",command:["python","scripts/ingest_s3.py","--bucket",$bucket,"--prefix",$prefix,"--size-mb",$size,"--shard-index",$shard_index,"--shard-count",$shard_count]}]}')" \
    --region "$AWS_REGION" \
    --query 'tasks[0].taskArn' \
    --output text)"
  TASK_ARNS+=("$TASK_ARN")
  echo "Started shard $((i + 1))/$PARALLELISM: $TASK_ARN"
done

aws ecs wait tasks-stopped \
  --cluster "$CLUSTER_NAME" \
  --tasks "${TASK_ARNS[@]}" \
  --region "$AWS_REGION"

aws ecs describe-tasks \
  --cluster "$CLUSTER_NAME" \
  --tasks "${TASK_ARNS[@]}" \
  --region "$AWS_REGION" \
  --query 'tasks[].containers[].{name:name,exit:exitCode,reason:reason,last:lastStatus}' \
  --output table

echo "Recent ingestion summaries:"
aws logs tail "/ecs/$CFN_PROJECT-app" \
  --log-stream-name-prefix "ingest/ingest/" \
  --since 2h \
  --region "$AWS_REGION" \
  | grep -E "S3 INGESTION PLAN|shard|done:|FAILED|S3 INGESTION COMPLETE|files ingested|failed/skipped|total chunks" \
  | tail -n 120 || true
