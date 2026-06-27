# Deploy EnterpriseRAG to AWS with CloudFormation

This deployment keeps the same runtime strategy:

- ALB HTTP `:80`
- ECS Fargate service with one task
- `app` FastAPI container on `:8000`
- `postgres:16` sidecar with data on EFS
- `qdrant/qdrant:v1.17.0` sidecar with vectors on EFS
- Secrets Manager for runtime secrets
- GitHub Actions OIDC for CD

CloudFormation owns the infrastructure in `infra/cloudformation/adv-rag.yml`. Docker image build/push, ECS Exec seeding, and smoke checks remain scripts around the stack.

## Naming

The CloudFormation scripts default to a parallel stack/project:

```bash
STACK_NAME=adv-rag-cfn-prod
CFN_PROJECT=adv-rag-cfn
```

This avoids collisions with previously created `adv-rag-*` CLI resources. In a clean account, or after importing/tearing down old resources, set `CFN_PROJECT=adv-rag` to use the original names.

## Prerequisites

- AWS CLI v2 configured for account `367012942955`
- Docker with buildx
- `jq`
- `session-manager-plugin` for ECS Exec bootstrap
- OpenAI API key
- Tavily API key
- Upstash Redis REST URL and token

`session-manager-plugin` may require an interactive macOS sudo install:

```bash
brew install --cask session-manager-plugin
```

## First Deploy

Build and push the image first:

```bash
scripts/aws/01_ecr_build_push.sh
```

Deploy the CloudFormation stack:

```bash
export OPENAI_API_KEY=...
export TAVILY_API_KEY=...
export UPSTASH_REDIS_URL=...
export UPSTASH_REDIS_TOKEN=...
scripts/aws/11_cfn_deploy.sh
```

The script discovers the default VPC/subnets, uses the pushed `:latest` image by default, prompts for missing secrets, and creates:

- IAM execution/task roles
- Secrets Manager secrets
- CloudWatch log group
- Security groups
- EFS filesystem, mount targets, and access points
- ALB, target group, listener
- ECS cluster, task definition, and service
- GitHub OIDC deploy role

Bootstrap the database and vector store:

```bash
scripts/aws/08_bootstrap.sh
```

That runs inside the app container:

```bash
python scripts/seed_db.py --no-ingest
python scripts/seed_db.py --noise-sample 0
```

Demo users:

- `admin@demo.local / admin123`
- `agent@demo.local / agent123`

Smoke test:

```bash
scripts/aws/09_smoke.sh
```

## Cloud-Native Corpus Ingestion

Qdrant runs as its own ECS service and is reachable inside the VPC at:

```text
http://qdrant.adv-rag-cfn.local:6333
```

Upload the noisy corpus to the CloudFormation-created S3 bucket:

```bash
scripts/aws/15_upload_noisy_to_s3.sh
```

Run a sharded ingestion job. This reads from S3, parses/chunks in Fargate, embeds with OpenAI, and upserts into the standalone Qdrant service:

```bash
SIZE_MB=200 PARALLELISM=4 scripts/aws/16_run_s3_ingest.sh
```

For a quick smoke test:

```bash
SIZE_MB=5 PARALLELISM=2 scripts/aws/16_run_s3_ingest.sh
```

The ingestion script uses deterministic vector IDs, so retries overwrite the same source/chunk vectors instead of duplicating them.

## Routine Deploy

After the stack exists:

```bash
scripts/aws/deploy.sh
```

This builds and pushes `linux/amd64` image tags `:sha` and `:latest`, updates the CloudFormation `AppImage` parameter, waits for ECS service stability, and runs smoke checks.

For an explicit immutable image:

```bash
APP_IMAGE=367012942955.dkr.ecr.us-east-1.amazonaws.com/adv-rag/app:<sha> \
  scripts/aws/12_cfn_update_image.sh
```

## GitHub Actions

After `scripts/aws/11_cfn_deploy.sh`, copy the `GitHubDeployRoleArn` output into the GitHub repository secret:

```text
AWS_DEPLOY_ROLE_ARN=arn:aws:iam::<account-id>:role/adv-rag-cfn-github-deployer
```

The CD workflow builds `:sha` and `:latest`, then updates the CloudFormation stack with `AppImage=<ECR URI>:<sha>`.

## Verification

Show stack outputs:

```bash
scripts/aws/13_cfn_outputs.sh
```

Useful checks:

```bash
aws ecs describe-services \
  --cluster adv-rag-cfn-cluster \
  --services adv-rag-cfn-app \
  --query 'services[0].{running:runningCount,desired:desiredCount,pending:pendingCount}'

curl -fsS "http://$(aws cloudformation describe-stacks \
  --stack-name adv-rag-cfn-prod \
  --query \"Stacks[0].Outputs[?OutputKey=='LoadBalancerDns'].OutputValue | [0]\" \
  --output text)/health"
```

`/health` is cheap liveness. `/admin/health` is dependency-aware and calls Postgres, Qdrant, Upstash, OpenAI, and Tavily.

## Rollback

Rollback by updating the stack to a known-good image:

```bash
APP_IMAGE=367012942955.dkr.ecr.us-east-1.amazonaws.com/adv-rag/app:<known-good-sha> \
  scripts/aws/12_cfn_update_image.sh
```

## Teardown

Delete the stack:

```bash
aws cloudformation delete-stack --stack-name adv-rag-cfn-prod --region us-east-1
```

If deletion protection or retained resources are later added, remove those explicitly. The current template does not retain EFS data on stack delete.

## Caveats

- Postgres on EFS is acceptable for this low-write deployment but should become RDS for production.
- CloudFormation cannot loop over an arbitrary subnet list, so the template creates three EFS mount targets from the first three discovered default subnets.
- Fargate is pinned to `linux/amd64`; Apple Silicon local builds must use `docker buildx build --platform linux/amd64`.
- ALB idle timeout is set to 300 seconds to tolerate slow RAG and ingestion requests.
