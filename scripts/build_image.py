#!/usr/bin/env python3
"""Register the packaged OpenHands guest as a Lambda MicroVM image."""

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--base-image-arn", required=True)
    parser.add_argument("--base-image-version", required=True)
    parser.add_argument("--build-role-arn", required=True)
    parser.add_argument(
        "--artifact-uri", required=True, help="s3://bucket/prefix/openhands.zip"
    )
    parser.add_argument("--init-key-file", required=True, type=Path)
    parser.add_argument(
        "--memory-mib", default=2048, choices=(512, 1024, 2048, 4096, 8192), type=int
    )
    args = parser.parse_args()
    artifact = urlsplit(args.artifact_uri)
    if (
        artifact.scheme != "s3"
        or not artifact.hostname
        or artifact.netloc != artifact.hostname
        or not artifact.path.endswith(".zip")
        or artifact.query
        or artifact.fragment
    ):
        parser.error("--artifact-uri must be an unversioned S3 URI ending in .zip")
    key = args.init_key_file.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        parser.error("generate a fresh bootstrap key with openssl rand -hex 32")
    if f":lambda:{args.region}:aws:microvm-image:" not in args.base_image_arn:
        parser.error("the managed base image must be in the selected Region")
    source = Path(__file__).resolve().parents[1] / "guest"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("Dockerfile", "entrypoint.py"):
            archive.writestr(
                zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)),
                (source / name).read_bytes(),
            )
    image_kwargs = {
        "name": args.name,
        "baseImageArn": args.base_image_arn,
        "baseImageVersion": args.base_image_version,
        "buildRoleArn": args.build_role_arn,
        "codeArtifact": {"uri": args.artifact_uri},
        "resources": [{"minimumMemoryInMiB": args.memory_mib}],
        "cpuConfigurations": [{"architecture": "X86_64"}],
        "environmentVariables": {"LAMBDA_MICROVM_INIT_API_KEY": key},
        "hooks": {
            "port": 8080,
            "microvmImageHooks": {"ready": "ENABLED", "readyTimeoutInSeconds": 120},
            "microvmHooks": {
                "run": "ENABLED",
                "runTimeoutInSeconds": 30,
                "resume": "ENABLED",
                "resumeTimeoutInSeconds": 30,
                "suspend": "ENABLED",
                "suspendTimeoutInSeconds": 30,
            },
        },
        "logging": {"disabled": {}},
    }
    # The same immutable artifact and inputs retry the same API operation.
    digest = hashlib.sha256(json.dumps(image_kwargs, sort_keys=True).encode())
    digest.update(payload.getvalue())
    session = boto3.Session(region_name=args.region)
    config = Config(
        connect_timeout=5,
        read_timeout=30,
        retries={"mode": "standard", "max_attempts": 3},
    )
    try:
        aws = session.client("lambda-microvms", config=config)
        s3 = session.client("s3", config=config)
        bucket, object_key = artifact.hostname, artifact.path.lstrip("/")
        artifact_data = payload.getvalue()
        try:
            s3.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=artifact_data,
                ServerSideEncryption="AES256",
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
                raise
            response = s3.get_object(Bucket=bucket, Key=object_key)
            with response["Body"] as body:
                if body.read(len(artifact_data) + 1) != artifact_data:
                    raise SystemExit(
                        "S3 object already exists with different content. Choose a new object key."
                    )
        result = aws.create_microvm_image(
            clientToken=digest.hexdigest(), **image_kwargs
        )
    except ClientError as error:
        raise SystemExit(
            f"AWS rejected the image setup request ({error.response['Error']['Code']})."
        ) from None
    except BotoCoreError as error:
        raise SystemExit(f"AWS image setup failed ({type(error).__name__}).") from None
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("imageArn", "imageVersion", "state")
                if key in result
            }
        )
    )


if __name__ == "__main__":
    main()
