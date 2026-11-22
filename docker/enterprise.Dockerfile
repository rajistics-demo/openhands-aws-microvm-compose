FROM ghcr.io/openhands/enterprise-server:1.88.0@sha256:cc67d10163e9e466b65d7a6e765a020fcc23ca036e78c6a6d846c458d1e2d9eb
USER root

COPY --chown=openhands:openhands enterprise/overlay/ /app/
COPY enterprise/integration.patch /tmp/lambda-microvm.patch
RUN cd /app && patch --batch --forward -p1 -i /tmp/lambda-microvm.patch \
    && rm /tmp/lambda-microvm.patch \
    && python -c "import boto3; assert 'lambda-microvms' in boto3.Session().get_available_services()" \
    && python -c "import openhands.app_server.sandbox.lambda_microvm_service, openhands.app_server.sandbox.lambda_microvm_proxy"

USER openhands
