ARG AGENT_SERVER_IMAGE=ghcr.io/openhands/agent-server:1.60.1-python
FROM ${AGENT_SERVER_IMAGE}
USER root
COPY entrypoint.py /opt/lambda-microvm/entrypoint.py
ENTRYPOINT ["python", "/opt/lambda-microvm/entrypoint.py"]
