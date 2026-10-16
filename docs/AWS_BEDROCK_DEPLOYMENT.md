# Bedrock integration and AWS event deployment

The migration is based on `main` commit `8001bee8b6ddb07a17222bfdfceac529548a2827`, the merge of PR 22. It keeps the deterministic policy, approval hashes, replay checks, verification and rollback logic intact. Bedrock only parses the operator's request.

## Model selection

Set these server-side environment variables:

```dotenv
SCRAM_MODEL_PROVIDER=bedrock
AWS_REGION=us-west-2
BEDROCK_MODEL_ID=us.amazon.nova-lite-v1:0
```

Use the normal AWS credential chain. Fargate receives a task role limited to invoking the Nova Lite inference profile and its destination models. No AWS key belongs in a frontend variable, source file or container image. Explicit Bedrock failures block with `PARSE_UNCERTAIN`; they never switch to fixture actions. `auto` retains Gemini compatibility for existing local setups, and `prepared` explicitly selects fixture parsing.

The event account's Oregon playground showed Nova Micro 1.0, Nova Lite 1.0, Nova Pro 1.0, Nova 2 Lite and Claude models under "Models with access". The account's API listed these Nova inference profiles as active: `us.amazon.nova-micro-v1:0`, `us.amazon.nova-lite-v1:0`, `us.amazon.nova-pro-v1:0`, `us.amazon.nova-premier-v1:0`, `us.amazon.nova-2-lite-v1:0` and `global.amazon.nova-2-lite-v1:0`. Nova Lite was verified with real calls; the other models were listed, not invoked.

## Validation on October 16, 2026

- API regression suite: 289 passed, one existing Starlette/httpx deprecation warning.
- Local Linux/amd64 builds: both images built; Compose readiness, nginx configuration, static assets, cached voice clips and API proxy passed.
- AWS cloud IDE Linux/arm64 builds: both images built successfully.
- Live Bedrock tests: all five passed using Nova Lite and a local simulated executor with embedded SurrealDB. Other sponsor integrations were disabled for this isolated test.

| Scenario | Expected and observed result |
| --- | --- |
| C1 read-only analysis | GREEN, COMPLETED, output remains 72%, no write |
| C2 approved +2% | YELLOW, approval required, COMPLETED at 74% |
| C3 beyond boundary | RED, BLOCKED, no execution, output remains 72% |
| C4 replay at +3% | BLOCKED with HASH_MISMATCH and APPROVAL_ALREADY_USED, output remains 74% |
| C5 injected fault | Actual output 92% fails verification, RESTORED to 72%, rollback verified |

Repeat the live smoke from a credentialed machine after installing `api/requirements.txt`:

```sh
AWS_REGION=us-west-2 python scripts/bedrock_smoke.py --output /tmp/bedrock-results.json
```

To verify a deployed disposable demo, use `--base-url https://YOUR_DISTRIBUTION.cloudfront.net`. The smoke script resets demo state and changes its fault switch. It performs five real parser calls and validates execution outcomes.

## Container and infrastructure configuration

`api/Dockerfile` includes the shared fixtures, runs as a nonroot user and starts one Uvicorn worker. `web/Dockerfile` builds the live frontend and serves it from nonroot nginx. Both run in one Fargate task, with nginx proxying `/api/` to the API over localhost. The browser uses one HTTPS origin.

`deploy/cloudformation.yaml` creates one Fargate task with 1 vCPU and 2 GiB memory, an application load balancer, CloudFront, container logs and two task roles. It uses ARM64 by default. The frontend is restricted to one configured public IPv4 address. The origin accepts only CloudFront's managed IP ranges and a private origin header. It does not expose the API port publicly. CloudFront disables caching for API and UI requests. Logs expire after seven days.

The first deployment enables Bedrock and uses the simulated local executor and in-memory SurrealDB. Cached ElevenLabs clips are included. Daytona, remote SurrealDB, Arize, Anchor and Agent Memory need their existing server-side secrets configured separately to run live. No sponsor secrets are copied by these deployment helpers.

There is one replica because active runs and approvals live in the API process. A restart resets the demo. Before replacing a running task, finish or reject active runs, scale the service to zero, wait until the old task is STOPPED, then update and restore one task. The template's 0% minimum and 100% maximum alone do not guarantee a stopped task has finished shutting down before its replacement starts.

## Deployment workflow

1. Build both images from the repository root on an ARM64 machine, tagged `scram-api:bedrock` and `scram-web:bedrock`.
2. Run `deploy/publish_images.py --account-id EVENT_ACCOUNT_ID --tag UNIQUE_RELEASE_TAG`. It creates private ECR repositories if missing, pushes both images and records immutable digest URIs. Its temporary Docker login is removed after the push.
3. Run `deploy/prepare_deployment.py` with the event account ID, presenter IPv4 address, VPC, two public subnets in distinct AZs and the regional CloudFront prefix-list ID. It validates the template and creates a change set without executing it. The origin header is randomly generated and omitted from output.
4. Review and execute the prepared change set with CloudFormation. This creates IAM roles and network access for the demo.
5. Wait for the stack and ECS service to stabilize. Inspect `/api/health` for `model_provider=bedrock`, `sandbox_ready=true` and the expected model. Run the five-scenario smoke against the HTTPS URL from the allowed network.
6. After the event, remove the demo stack and its ECR images/repositories when no longer needed. The workshop account itself expires at the event's scheduled end.

References: [AWS Converse API example](https://docs.aws.amazon.com/nova/latest/userguide/using-converse-api.html), [CloudFront viewer IP](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/functions-event-structure.html), [CloudFront origin prefix list](https://docs.aws.amazon.com/vpc/latest/userguide/working-with-aws-managed-prefix-lists.html).
