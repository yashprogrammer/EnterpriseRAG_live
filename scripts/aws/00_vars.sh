#!/usr/bin/env bash
set -euo pipefail

export AWS_REGION="${AWS_REGION:-us-east-1}"
export PROJECT="${PROJECT:-adv-rag}"
export CFN_PROJECT="${CFN_PROJECT:-adv-rag-cfn}"
export STACK_NAME="${STACK_NAME:-adv-rag-cfn-prod}"
export ECR_REPO="${ECR_REPO:-adv-rag/app}"
export UI_ECR_REPO="${UI_ECR_REPO:-adv-rag/ui}"
export CLUSTER="${CLUSTER:-adv-rag-cluster}"
export SERVICE="${SERVICE:-adv-rag-app}"
export TASK_FAMILY="${TASK_FAMILY:-adv-rag-app}"
export LOG_GROUP="${LOG_GROUP:-/ecs/adv-rag-app}"
export ALB_NAME="${ALB_NAME:-adv-rag-alb}"
export TG_NAME="${TG_NAME:-adv-rag-tg}"
export EXECUTION_ROLE_NAME="${EXECUTION_ROLE_NAME:-adv-rag-ecs-task-execution}"
export TASK_ROLE_NAME="${TASK_ROLE_NAME:-adv-rag-ecs-task-role}"
export GITHUB_ROLE_NAME="${GITHUB_ROLE_NAME:-adv-rag-github-deployer}"
export GITHUB_REPO="${GITHUB_REPO:-yashprogrammer/EnterpriseRAG_live}"

export ACCOUNT_ID="${ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION")}"
export ECR_URI="${ECR_URI:-$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO}"
export UI_ECR_URI="${UI_ECR_URI:-$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$UI_ECR_REPO}"
export VPC_ID="${VPC_ID:-$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text --region "$AWS_REGION")}"
export SUBNETS="${SUBNETS:-$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true --query 'Subnets[].SubnetId' --output text --region "$AWS_REGION" | xargs)}"

ROOT_DIR="${ROOT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE_DIR="$ROOT_DIR/.aws"
mkdir -p "$STATE_DIR"
export ROOT_DIR STATE_DIR

tag_args() {
  printf 'Key=Project,Value=%s Key=ManagedBy,Value=aws-cli-runbook' "$PROJECT"
}

ensure_secret() {
  local name="$1"
  local value="$2"
  if aws secretsmanager describe-secret --secret-id "$name" --region "$AWS_REGION" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value --secret-id "$name" --secret-string "$value" --region "$AWS_REGION" >/dev/null
  else
    aws secretsmanager create-secret --name "$name" --secret-string "$value" --region "$AWS_REGION" >/dev/null
  fi
}

secret_arn() {
  aws secretsmanager describe-secret --secret-id "$1" --query ARN --output text --region "$AWS_REGION"
}

sg_id_by_name() {
  aws ec2 describe-security-groups \
    --filters Name=vpc-id,Values="$VPC_ID" Name=group-name,Values="$1" \
    --query 'SecurityGroups[0].GroupId' \
    --output text \
    --region "$AWS_REGION"
}

ignore_duplicate() {
  "$@" 2>/tmp/adv-rag-aws-error || {
    if grep -Eq 'InvalidPermission.Duplicate|AlreadyExists|EntityAlreadyExists|Duplicate' /tmp/adv-rag-aws-error; then
      return 0
    fi
    cat /tmp/adv-rag-aws-error >&2
    return 1
  }
}

require_session_manager_plugin() {
  if ! command -v session-manager-plugin >/dev/null 2>&1; then
    echo "session-manager-plugin is required for ECS Exec seeding." >&2
    echo "Install it, then rerun scripts/aws/08_bootstrap.sh." >&2
    return 1
  fi
}
