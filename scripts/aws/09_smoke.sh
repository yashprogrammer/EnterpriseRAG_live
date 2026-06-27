#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/00_vars.sh"

if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  ALB_DNS="$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='LoadBalancerDns'].OutputValue | [0]" \
    --output text)"
elif [ -f "$STATE_DIR/alb.env" ]; then
  source "$STATE_DIR/alb.env"
fi

if [ -z "${ALB_DNS:-}" ] || [ "$ALB_DNS" = "None" ]; then
  echo "No ALB DNS found. Deploy the CloudFormation stack first." >&2
  exit 1
fi

curl -fsS "http://$ALB_DNS/health"
echo
curl -fsS "http://$ALB_DNS/admin/health" | jq .

TOKEN="$(curl -fsS "http://$ALB_DNS/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin@demo.local","password":"admin123"}' | jq -r .token)"

curl -fsS "http://$ALB_DNS/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is a Kubernetes deployment?","top_k":3}' | jq .
