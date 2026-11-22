"""Bounded AWS calls and server-side endpoint credentials for Lambda MicroVMs."""

import asyncio
import re
import time
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from openhands.app_server.errors import SandboxError

LAMBDA_BACKEND = 'lambda-microvm'
PORTS = (8000, 8001, 8011, 8012)
ID_PATTERN = re.compile(r'microvm-[a-zA-Z0-9-]+\Z')


class LambdaMicrovmClient:
    def __init__(self, region: str, client: Any = None):
        self.region = region
        self.client = client or boto3.client(
            'lambda-microvms',
            region_name=region,
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={'mode': 'standard', 'max_attempts': 3},
            ),
        )
        self._tokens: dict[tuple[str, int], tuple[float, str]] = {}
        self._token_lock = asyncio.Lock()

    async def call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(getattr(self.client, operation), **kwargs)
        except ClientError as exc:
            if exc.response['Error']['Code'] == 'ResourceNotFoundException':
                raise LookupError('Lambda MicroVM resource not found') from exc
            raise SandboxError(
                'Lambda MicroVM control request failed', status_code=502
            ) from exc
        except BotoCoreError as exc:
            raise SandboxError(
                'Lambda MicroVM control service unavailable', status_code=502
            ) from exc

    async def info(self, sandbox_id: str) -> dict[str, Any] | None:
        if not ID_PATTERN.fullmatch(sandbox_id):
            return None
        try:
            return await self.call('get_microvm', microvmIdentifier=sandbox_id)
        except LookupError:
            return None

    def endpoint(self, info: dict[str, Any]) -> str:
        host = info['endpoint']
        pattern = rf'[a-zA-Z0-9-]+\.lambda-microvm\.{re.escape(self.region)}\.on\.aws'
        if not re.fullmatch(pattern, host):
            raise SandboxError('Lambda MicroVM returned an invalid endpoint')
        return f'https://{host}'

    async def token(self, sandbox_id: str, port: int) -> str:
        if port not in PORTS:
            raise ValueError('Unsupported sandbox port')
        key = (sandbox_id, port)
        async with self._token_lock:
            cached = self._tokens.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            response = await self.call(
                'create_microvm_auth_token',
                microvmIdentifier=sandbox_id,
                expirationInMinutes=10,
                allowedPorts=[{'port': port}],
            )
            token = response['authToken']['X-aws-proxy-auth']
            # Bound memory usage; tokens are safe to recreate after eviction.
            if len(self._tokens) >= 1024:
                self._tokens.clear()
            self._tokens[key] = (time.monotonic() + 480, token)
            return token


@lru_cache(maxsize=4)
def get_lambda_client(region: str) -> LambdaMicrovmClient:
    return LambdaMicrovmClient(region)
