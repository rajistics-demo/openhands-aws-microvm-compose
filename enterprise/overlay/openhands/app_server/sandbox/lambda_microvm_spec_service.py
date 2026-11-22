"""The catalog identifies Lambda images without exposing AWS configuration."""

import os
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import Request
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_spec_service import (
    SandboxSpecService,
    SandboxSpecServiceInjector,
)
from openhands.app_server.services.injector import InjectorState
from pydantic import Field, SecretStr


class LambdaMicrovmSpecInfo(SandboxSpecInfo):
    image_arn: str = Field(exclude=True)
    image_version: str = Field(exclude=True)
    init_api_key: SecretStr = Field(exclude=True)


def bootstrap_key() -> SecretStr:
    key_file = os.getenv("LAMBDA_MICROVM_INIT_API_KEY_FILE")
    key = (
        Path(key_file).read_text().strip()
        if key_file
        else os.environ["LAMBDA_MICROVM_INIT_API_KEY"]
    )
    if not key:
        raise ValueError("Lambda MicroVM bootstrap key is empty")
    return SecretStr(key)


class LambdaMicrovmSpecServiceInjector(SandboxSpecServiceInjector):
    specs: list[LambdaMicrovmSpecInfo] = Field(
        default_factory=lambda: [
            LambdaMicrovmSpecInfo(
                id="lambda-agent-server",
                command=None,
                working_dir="/workspace/project",
                image_arn=os.environ["LAMBDA_MICROVM_IMAGE_ARN"],
                image_version=os.environ["LAMBDA_MICROVM_IMAGE_VERSION"],
                init_api_key=bootstrap_key(),
            )
        ]
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[SandboxSpecService, None]:
        yield PresetSandboxSpecService(specs=list(self.specs))
