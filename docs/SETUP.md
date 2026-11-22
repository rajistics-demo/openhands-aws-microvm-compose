# Set up the AWS Lambda MicroVM pilot

Use a dedicated evaluation deployment of [OpenHands Enterprise Docker Compose](https://github.com/OpenHands/enterprise/blob/c6191be/README.md#run-locally-with-docker-compose).
Start the stock 1.88.0 stack and confirm you can sign in to Canvas and start a
Docker-backed conversation. Keep the original Compose files and record their
file order for rollback. Use the same Enterprise entitlement and login for the
pilot. The overlay expects the stock services `app`, `worker`, `db`, `redis`,
and the `app-filestore` volume. It uses Compose's `!override` tag (2.24.4+).

## 1. Prepare AWS and build the guest image

Choose a Region and account with Lambda MicroVMs access. Use an x86-64 managed
base version that lists `X86_64` support. Create a private S3 bucket in that
Region, with Block Public Access and encryption. Reserve one object key for
the build input. The build identity needs to upload that object, read it for
retries, register the image, and pass the specific Lambda build role.

Create the Lambda build role with [this trust policy](../iam/build-trust.json)
and [the single-artifact read policy](../iam/build-read-artifact.example.json).
Each policy value in capitals is a placeholder. The image build runs without
CloudWatch log delivery in this example. For a build with logs, scope
`logs:CreateLogStream` and `logs:PutLogEvents` to an operator-created log group
with short retention. See [AWS image builds and hooks](https://docs.aws.amazon.com/lambda/latest/dg/microvms-images.html).

Run the following from this repository. Supply your own AWS configuration
and replace the example inputs. Store the bootstrap key outside the repository.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'boto3==1.43.126'

aws lambda-microvms list-managed-microvm-images --region "$AWS_REGION"
aws lambda-microvms list-managed-microvm-image-versions \
  --region "$AWS_REGION" --image-identifier "$MANAGED_BASE_ARN"

umask 077
openssl rand -hex 32 > "$LAMBDA_INIT_KEY_FILE"
python scripts/build_image.py \
  --region "$AWS_REGION" --name "$PILOT_IMAGE_NAME" \
  --base-image-arn "$MANAGED_BASE_ARN" --base-image-version "$MANAGED_BASE_VERSION" \
  --build-role-arn "$PILOT_BUILD_ROLE_ARN" --artifact-uri "$PILOT_ARTIFACT_URI" \
  --init-key-file "$LAMBDA_INIT_KEY_FILE"
```

Keep the same bootstrap key and S3 object key when retrying the same
registration. Wait for the image to reach `CREATED` and its version to be
`SUCCESSFUL` and `ACTIVE` in AWS. Record the image ARN and the exact active
version. Keep the version pinned in Compose. To change the guest base,
CPU architecture, or bootstrap key, build and qualify a new image version.
The example builder creates a new named image and reports the AWS receipt.

## 2. Give Compose a scoped AWS role

Attach [the runtime policy](../iam/runtime.example.json) to a role your
operator can assume. Use the exact new image ARN and selected Region.
Install its short-lived session into a private AWS shared credentials file
under the `[default]` profile, including the session token. The build role and
runtime role have separate jobs. The runtime role can manage only VMs from the
selected image and pass the AWS-managed HTTP ingress connector for that Region.

Make the credential file and the bootstrap file readable by the Enterprise
image's `openhands` user (UID/GID 42420 in the tested release). Use restrictive
ownership and permissions such as `root:42420` and `0640` on a protected
directory. Pass the full file paths to Compose. When a session expires,
replace the file and recreate app, worker, and gateway to refresh cached AWS clients.

## 3. Configure routing and start the adapter

Use three HTTPS hostnames pointing at the isolated Compose host. The stock
`WEB_HOST`/`WEB_URL` is the app and Canvas origin. The runtime hostname serves
Agent Server and previews. The callback hostname accepts only the registered
event POSTs with the expected session-key header. Enterprise validates the key.

The [sample Caddy overlay](../compose/docker-compose.lambda-edge.yml) adds
those routes to the stock HTTPS Compose configuration and restricts browser
and runtime traffic to `LAMBDA_OPERATOR_CIDR`. Test it from that network.
The runtime URL passes through preview HTTP as served by the guest. Use an
operator-only edge, VPN, or comparable restriction to enforce who can reach
those preview ports. If using your own reverse proxy, preserve HTTPS, WebSocket
upgrade and streaming, and apply those same restrictions. Browser, runtime
and callback origins serve different trust domains.

Build and configure the adapter:

```bash
docker build -f docker/enterprise.Dockerfile -t openhands-lambda-enterprise:1.88.0 .
export LAMBDA_PACKAGE="$PWD"
cd "$ISOLATED_COMPOSE_DIR"
cp "$LAMBDA_PACKAGE/compose/lambda.env.example" lambda.env
chmod 600 lambda.env
# Fill in lambda.env. Keep the stock .env with WEB_HOST, WEB_URL and database settings.
docker compose --env-file .env --env-file lambda.env \
  -f docker-compose.yml -f docker-compose.https.yml \
  -f "$LAMBDA_PACKAGE/compose/docker-compose.lambda-microvm.yml" \
  -f "$LAMBDA_PACKAGE/compose/docker-compose.lambda-edge.yml" config --quiet
docker compose --env-file .env --env-file lambda.env \
  -f docker-compose.yml -f docker-compose.https.yml \
  -f "$LAMBDA_PACKAGE/compose/docker-compose.lambda-microvm.yml" \
  -f "$LAMBDA_PACKAGE/compose/docker-compose.lambda-edge.yml" up -d --wait
```

Confirm that the Sandbox V1 spec catalog lists `lambda-agent-server`.
Start an authorized Canvas conversation bound to that spec and perform the
[acceptance check](VALIDATION.md).

### Troubleshooting

- **Unknown AWS service.** The CLI and Python clients must recognize
  `lambda-microvms`. The derived Enterprise build checks its own AWS SDK.
- **Access denied.** Check the actual AWS account, Region, image ARN and
  connector ARN. The runtime operations authorize against the **image ARN**.
- **Guest remains dormant.** Check that the launch spec pins the active version
  and uses the same bootstrap key as the image build.
- **Conversation starts with missing history.** Verify guest egress to the
  callback hostname and the exact callback route and key. Check a real
  Enterprise history event before declaring Pause qualified.
- **Preview responds with 403.** Verify the operator egress CIDR at the runtime
  edge. A 409 during pause is expected for this transport.
