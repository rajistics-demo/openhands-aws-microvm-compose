"""App-owned Lambda MicroVM sandboxes, initialized through the Agent Server API."""

import asyncio
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, ClassVar
from urllib.parse import urlencode, urlsplit

from fastapi import Request
from pydantic import Field, SecretStr
from sqlalchemy import text

from openhands.app_server.errors import SandboxDeleteRetryError, SandboxError
from openhands.app_server.sandbox.lambda_microvm_client import (
    LAMBDA_BACKEND,
    PORTS,
    LambdaMicrovmClient,
    get_lambda_client,
)
from openhands.app_server.sandbox.lambda_microvm_spec_service import (
    LambdaMicrovmSpecInfo,
)
from openhands.app_server.sandbox.managed_sandbox_service import (
    ManagedSandboxService,
    ManagedSandboxServiceInjector,
    ProviderOutcome,
)
from openhands.app_server.sandbox.sandbox_models import (
    AGENT_SERVER,
    VSCODE,
    WORKER_1,
    WORKER_2,
    ExposedUrl,
    SandboxInfo,
    SandboxPage,
    SandboxRecord,
    SandboxStartedBy,
    SandboxStatus,
)
from openhands.app_server.sandbox.sandbox_service import SandboxService
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    get_agent_server_env,
    resolve_sandbox_spec,
)
from openhands.app_server.sandbox.sandbox_store import (
    StoredSandbox,
    get_credential_org_id,
    get_stored_sandbox_by_session_api_key,
    hash_session_api_key,
    require_user_id,
    sandbox_started_by,
    search_stored_sandboxes,
)
from openhands.app_server.services.injector import InjectorState

logger = logging.getLogger(__name__)
STATUS = {
    'RUNNING': SandboxStatus.RUNNING,
    'SUSPENDED': SandboxStatus.PAUSED,
    'PENDING': SandboxStatus.STARTING,
    'TERMINATED': SandboxStatus.MISSING,
    'TERMINATING': SandboxStatus.MISSING,
}


@dataclass
class LambdaMicrovmService(ManagedSandboxService):
    backend: ClassVar[str] = LAMBDA_BACKEND
    sandbox_spec_service: SandboxSpecService
    aws: LambdaMicrovmClient
    proxy_base_url: str
    callback_url: str
    maximum_duration_seconds: int = 28800
    startup_timeout_seconds: int = 180
    web_url: str | None = None

    def exposed_urls(self, row: StoredSandbox) -> list[ExposedUrl]:
        urls = []
        for name, port in zip(
            (AGENT_SERVER, VSCODE, WORKER_1, WORKER_2), PORTS, strict=True
        ):
            url = f'{self.proxy_base_url.rstrip("/")}/{row.id}/{port}'
            if name == VSCODE:
                url += '/?' + urlencode(
                    {
                        'tkn': row.session_api_key.get_secret_value()
                        if row.session_api_key
                        else '',
                        'folder': '/workspace/project',
                    }
                )
            urls.append(ExposedUrl(name=name, port=port, url=url))
        return urls

    async def as_info(
        self, row: StoredSandbox, *, tolerate_status_failure: bool = False
    ) -> SandboxInfo:
        status_detail = None
        try:
            info = await self.aws.info(row.id)
        except SandboxError:
            if not tolerate_status_failure:
                raise
            # History remains readable without AWS. Do not mistake an unavailable
            # control plane for proof that the VM was deleted, or expose its keys.
            info = {'state': 'UNKNOWN'}
            status_detail = 'Lambda MicroVM status is temporarily unavailable'
        status = (
            STATUS.get(info['state'], SandboxStatus.ERROR)
            if info
            else SandboxStatus.MISSING
        )
        return SandboxInfo(
            id=row.id,
            created_by_user_id=row.created_by_user_id,
            credential_org_id=row.credential_org_id,
            started_by=sandbox_started_by(row),
            sandbox_spec_id=row.sandbox_spec_id,
            status=status,
            session_api_key=row.session_api_key.get_secret_value()
            if row.session_api_key and status == SandboxStatus.RUNNING
            else None,
            exposed_urls=self.exposed_urls(row)
            if status == SandboxStatus.RUNNING
            else [],
            created_at=row.created_at,
            status_detail=(
                'Lambda MicroVM is expired or terminated'
                if status == SandboxStatus.MISSING
                else status_detail
            ),
        )

    async def batch_get_sandboxes(
        self, sandbox_ids: list[str]
    ) -> list[SandboxInfo | None]:
        """Enrich saved conversations without making history depend on AWS health.

        Only batch display reads tolerate provider failures. Single reads and
        capacity/lifecycle operations still propagate errors and fail closed.
        """

        async def display_info(sandbox_id: str) -> SandboxInfo | None:
            row = await self._get_stored_sandbox(sandbox_id)
            return (
                await self.as_info(row, tolerate_status_failure=True) if row else None
            )

        return list(await asyncio.gather(*(display_info(sid) for sid in sandbox_ids)))

    async def search_sandboxes(
        self, page_id: str | None = None, limit: int = 100
    ) -> SandboxPage:
        page = await search_stored_sandboxes(
            self.db_session, self.user_context, self.backend, page_id, limit
        )
        return SandboxPage(
            items=[await self.as_info(row) for row in page.items],
            next_page_id=page.next_page_id,
        )

    async def get_sandbox(self, sandbox_id: str) -> SandboxInfo | None:
        row = await self._get_stored_sandbox(sandbox_id)
        return await self.as_info(row) if row else None

    async def get_sandbox_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxInfo | None:
        row = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, self.backend, session_api_key
        )
        return await self.as_info(row) if row else None

    async def get_sandbox_record_by_session_api_key(
        self, session_api_key: str
    ) -> SandboxRecord | None:
        row = await get_stored_sandbox_by_session_api_key(
            self.db_session, self.user_context, self.backend, session_api_key
        )
        return (
            SandboxRecord(
                id=row.id,
                created_by_user_id=row.created_by_user_id,
                credential_org_id=row.credential_org_id,
            )
            if row
            else None
        )

    async def wait_state(self, sandbox_id: str, wanted: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            info = await self.aws.info(sandbox_id)
            if info and info['state'] == wanted:
                return info
            if (
                not info
                or info['state'] in ('TERMINATED', 'FAILED')
                or (info['state'] == 'TERMINATING' and wanted != 'TERMINATED')
            ):
                raise SandboxError('Lambda MicroVM is no longer available')
            await asyncio.sleep(1)
        raise SandboxError('Lambda MicroVM lifecycle operation timed out')

    async def pause_old_sandboxes(
        self, max_num_sandboxes: int, exclude_id: str | None = None
    ) -> list[str]:
        """Lambda admission never evicts an agent to make room for another job."""
        user = await require_user_id(self.user_context)
        org = await get_credential_org_id(self.user_context)
        key = int.from_bytes(
            hashlib.sha256(f'lambda-admission:{user}:{org}'.encode()).digest()[:8],
            'big',
            signed=True,
        )
        await self.db_session.execute(
            text('SELECT pg_advisory_xact_lock(:key)'), {'key': key}
        )
        page_id = None
        occupied = 0
        while True:
            page = await self.search_sandboxes(page_id=page_id)
            occupied += sum(
                item.id != exclude_id
                and item.status in (SandboxStatus.RUNNING, SandboxStatus.STARTING)
                for item in page.items
            )
            if page.next_page_id is None:
                break
            page_id = page.next_page_id
        if occupied > max_num_sandboxes:
            raise SandboxError(
                'Lambda sandbox capacity reached; queue work until a slot is released',
                status_code=409,
            )
        return []

    async def start_sandbox(
        self,
        sandbox_spec_id: str | None = None,
        sandbox_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        started_by: SandboxStartedBy = SandboxStartedBy.SANDBOX_API,
    ) -> SandboxInfo:
        user = await require_user_id(self.user_context)
        org = await get_credential_org_id(self.user_context)
        spec = await resolve_sandbox_spec(
            sandbox_spec_id,
            await self.user_context.get_default_sandbox_spec_id(),
            self.sandbox_spec_service,
            logger,
        )
        if not isinstance(spec, LambdaMicrovmSpecInfo):
            raise SandboxError('A Lambda MicroVM image specification is required')
        if not spec.init_api_key.get_secret_value() or not self.callback_url.startswith(
            'https://'
        ):
            raise SandboxError(
                'Lambda MicroVM initialization key and HTTPS callback URL are required'
            )
        await self.pause_old_sandboxes(self.max_num_sandboxes - 1)
        created = await self.aws.call(
            'run_microvm',
            imageIdentifier=spec.image_arn,
            imageVersion=spec.image_version,
            clientToken=secrets.token_hex(24),
            maximumDurationInSeconds=self.maximum_duration_seconds,
            ingressNetworkConnectors=[
                f'arn:aws:lambda:{self.aws.region}:aws:network-connector:aws-network-connector:HTTP_INGRESS'
            ],
            idlePolicy={
                'autoResumeEnabled': False,
                'maxIdleDurationSeconds': 28800,
                'suspendedDurationSeconds': 28800,
            },
            logging={'disabled': {}},
        )
        sid = created['microvmId']
        key = secrets.token_urlsafe(32)
        row = self._new_stored_sandbox(
            id=sid,
            created_by_user_id=user,
            credential_org_id=org,
            started_by=started_by.value,
            sandbox_spec_id=spec.id,
            session_api_key=SecretStr(key),
            session_api_key_hash=hash_session_api_key(key),
        )
        try:
            self.db_session.add(row)
            await self.db_session.flush()
            info = await self.wait_state(sid, 'RUNNING')
            headers = {
                'X-aws-proxy-auth': await self.aws.token(sid, 8000),
                'X-aws-proxy-port': '8000',
            }
            endpoint = self.aws.endpoint(info)
            response = await self.httpx_client.get(
                f'{endpoint}/api/init', headers=headers
            )
            if response.status_code != 200 or response.json().get('state') != 'dormant':
                raise SandboxError('Lambda Agent Server is not dormant')
            body = {
                'session_api_keys': [key],
                'secret_key': secrets.token_urlsafe(32),
                'conversations_path': '/var/openhands/conversations',
                'bash_events_dir': '/var/openhands/bash_events',
                'conversation_worktree_root': '/workspace/worktrees',
                'env': {
                    WORKER_1: '8011',
                    WORKER_2: '8012',
                    'OH_VSCODE_BASE_PATH': f'{urlsplit(self.proxy_base_url).path}/{sid}/8001',
                    **spec.initial_env,
                    **get_agent_server_env(),
                    **self._lifecycle_env(),
                    **(extra_env or {}),
                },
                'allow_cors_origins': [
                    self.web_url
                    or f'{urlsplit(self.proxy_base_url).scheme}://{urlsplit(self.proxy_base_url).netloc}'
                ],
                'webhooks': [
                    {'base_url': f'{self.callback_url.rstrip("/")}/api/v1/webhooks'}
                ],
            }
            response = await self.httpx_client.post(
                f'{endpoint}/api/init',
                headers={
                    **headers,
                    'X-Init-API-Key': spec.init_api_key.get_secret_value(),
                },
                json=body,
            )
            if response.status_code != 200:
                raise SandboxError('Lambda Agent Server initialization failed')
        except BaseException:
            try:
                await asyncio.shield(
                    self.aws.call('terminate_microvm', microvmIdentifier=sid)
                )
            except Exception:
                logger.error(
                    'Lambda MicroVM cleanup failed after initialization failure'
                )
            raise
        return await self.as_info(row)

    async def _resume_at_provider(self, row: StoredSandbox) -> ProviderOutcome:
        info = await self.aws.info(row.id)
        if not info or info['state'] in ('TERMINATED', 'TERMINATING'):
            return ProviderOutcome.FAILED
        if info['state'] == 'RUNNING':
            return ProviderOutcome.ALREADY_DONE
        if info['state'] != 'SUSPENDED':
            raise SandboxError(
                'Lambda MicroVM is not resumable in its current state', status_code=409
            )
        await self.aws.call('resume_microvm', microvmIdentifier=row.id)
        await self.wait_state(row.id, 'RUNNING')
        return ProviderOutcome.CHANGED

    async def _pause_at_provider(self, row: StoredSandbox) -> ProviderOutcome:
        info = await self.aws.info(row.id)
        if not info or info['state'] in ('TERMINATED', 'TERMINATING'):
            return ProviderOutcome.FAILED
        if info['state'] == 'SUSPENDED':
            return ProviderOutcome.ALREADY_DONE
        if info['state'] != 'RUNNING':
            return ProviderOutcome.SKIPPED
        await self.aws.call('suspend_microvm', microvmIdentifier=row.id)
        await self.wait_state(row.id, 'SUSPENDED')
        return ProviderOutcome.CHANGED

    async def _delete_at_provider(self, row: StoredSandbox) -> None:
        try:
            info = await self.aws.info(row.id)
            if info and info['state'] != 'TERMINATED':
                await self.aws.call('terminate_microvm', microvmIdentifier=row.id)
                await self.wait_state(row.id, 'TERMINATED')
        except SandboxError as exc:
            raise SandboxDeleteRetryError('Lambda MicroVM deletion failed') from exc


class LambdaMicrovmServiceInjector(ManagedSandboxServiceInjector):
    backend: ClassVar[str] = LAMBDA_BACKEND
    region: str = Field(default_factory=lambda: os.environ['AWS_REGION'])
    proxy_base_url: str = Field(
        default_factory=lambda: os.environ['LAMBDA_MICROVM_PROXY_URL']
    )
    internal_url: str | None = Field(
        default_factory=lambda: os.getenv('SANDBOX_INTERNAL_URL')
    )
    maximum_duration_seconds: int = Field(default=28800, ge=300, le=28800)
    max_num_sandboxes: int = Field(default=2, ge=1)
    startup_timeout_seconds: int = Field(default=180, ge=10, le=600)
    callback_url: str | None = Field(
        default_factory=lambda: os.getenv('LAMBDA_MICROVM_CALLBACK_URL')
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxService, None]:
        from openhands.app_server.config import (
            get_db_session,
            get_global_config,
            get_httpx_client,
            get_sandbox_spec_service,
            get_user_context,
        )

        async with (
            get_user_context(state, request) as user,
            get_httpx_client(state, request) as http,
            get_sandbox_spec_service(state, request) as specs,
            get_db_session(state, request) as db,
        ):
            yield LambdaMicrovmService(
                user_context=user,
                httpx_client=http,
                db_session=db,
                sandbox_spec_service=specs,
                aws=get_lambda_client(self.region),
                proxy_base_url=self.proxy_base_url,
                web_url=get_global_config().web_url,
                callback_url=self.callback_url or get_global_config().web_url or '',
                maximum_duration_seconds=self.maximum_duration_seconds,
                max_num_sandboxes=self.max_num_sandboxes,
                startup_timeout_seconds=self.startup_timeout_seconds,
                lifecycle=self.lifecycle,
            )
