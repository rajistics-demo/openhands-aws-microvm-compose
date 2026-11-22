"""Runtime-origin transport bridge; AWS credentials never reach the browser/guest.

This endpoint has the same access model as exposed E2B ports: Agent Server
authenticates API/WebSocket traffic, VSCode validates its connection token, and
the two preview ports serve user applications. It is not a general HTTP proxy.
Run as a separate Compose service on a runtime origin distinct from the app.
"""

import asyncio
import os
from urllib.parse import unquote

import aiohttp
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.lambda_microvm_client import (
    ID_PATTERN,
    LAMBDA_BACKEND,
    PORTS,
    get_lambda_client,
)
from openhands.app_server.sandbox.sandbox_store import StoredSandbox

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
HOP_HEADERS = {
    'host',
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
    'content-length',
    'set-cookie',
    'cookie',
    'authorization',
}


def forwarded_headers(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    return {
        key.decode('latin1'): value.decode('latin1')
        for key, value in headers
        if key.decode('latin1').lower() not in HOP_HEADERS
        and not key.decode('latin1').lower().startswith(('x-aws-', 'sec-websocket-'))
    }


async def target(sandbox_id: str, port: int, path: str) -> tuple[str, str]:
    if not ID_PATTERN.fullmatch(sandbox_id) or port not in PORTS:
        raise HTTPException(404)
    decoded = unquote(unquote(path)).strip('/')
    if port == 8000 and (decoded == 'api/init' or decoded.startswith('api/init/')):
        raise HTTPException(404)
    # Only an app-owned, committed row can reach the provider. In particular,
    # externally supplied AWS ids cannot be used to mint endpoint tokens.
    from storage.database import a_session_maker

    async with a_session_maker() as db:
        row = await db.scalar(
            select(StoredSandbox.id).where(
                StoredSandbox.id == sandbox_id, StoredSandbox.backend == LAMBDA_BACKEND
            )
        )
        if row is None:
            raise HTTPException(404)
    aws = get_lambda_client(os.environ['AWS_REGION'])
    info = await aws.info(sandbox_id)
    if not info or info['state'] in ('TERMINATED', 'TERMINATING'):
        raise HTTPException(410, 'Sandbox expired or terminated')
    if info['state'] != 'RUNNING':
        raise HTTPException(409, 'Sandbox is not running')
    return aws.endpoint(info), await aws.token(sandbox_id, port)


@app.get('/alive')
async def alive():
    return {'status': 'ok'}


@app.api_route(
    '/{sandbox_id}/{port}/{path:path}',
    methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
)
async def proxy_http(request: Request, sandbox_id: str, port: int, path: str):
    try:
        endpoint, token = await target(sandbox_id, port, path)
    except SandboxError as exc:
        raise HTTPException(502, 'Sandbox provider unavailable') from exc
    headers = forwarded_headers(request.scope['headers'])
    headers.update({'X-aws-proxy-auth': token, 'X-aws-proxy-port': str(port)})
    query = request.scope['query_string'].decode('ascii')
    url = f'{endpoint}/{path}' + (f'?{query}' if query else '')
    client = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=300),
        auto_decompress=False,
    )
    try:
        upstream = await client.request(
            request.method,
            url,
            headers=headers,
            data=request.stream(),
            allow_redirects=False,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        await client.close()
        raise HTTPException(502, 'Sandbox transport unavailable') from exc

    async def body():
        try:
            async for chunk in upstream.content.iter_chunked(65536):
                yield chunk
        finally:
            upstream.release()
            await client.close()

    response_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in HOP_HEADERS and not k.lower().startswith('x-aws-')
    }
    return StreamingResponse(
        body(), status_code=upstream.status, headers=response_headers
    )


@app.websocket('/{sandbox_id}/{port}/{path:path}')
async def proxy_websocket(websocket: WebSocket, sandbox_id: str, port: int, path: str):
    try:
        endpoint, token = await target(sandbox_id, port, path)
    except (HTTPException, SandboxError):
        await websocket.close(code=1008)
        return
    headers = forwarded_headers(websocket.scope['headers'])
    offered = websocket.headers.get('sec-websocket-protocol', '')
    protocols = [
        p.strip()
        for p in offered.split(',')
        if p.strip() and not p.strip().startswith('lambda-microvms')
    ]
    query = websocket.scope['query_string'].decode('ascii')
    url = (
        endpoint.replace('https://', 'wss://', 1)
        + f'/{path}'
        + (f'?{query}' if query else '')
    )
    aws_protocols = [
        'lambda-microvms',
        f'lambda-microvms.authentication.{token}',
        f'lambda-microvms.port.{port}',
        *protocols,
    ]
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10)
        ) as client:
            async with client.ws_connect(
                url,
                headers=headers,
                protocols=aws_protocols,
                max_msg_size=16 * 1024 * 1024,
            ) as upstream:
                await websocket.accept(
                    subprotocol=upstream.protocol
                    if upstream.protocol in protocols
                    else None
                )

                async def send_upstream():
                    while True:
                        message = await websocket.receive()
                        if message['type'] == 'websocket.disconnect':
                            return
                        if message.get('text') is not None:
                            await upstream.send_str(message['text'])
                        elif message.get('bytes') is not None:
                            await upstream.send_bytes(message['bytes'])

                async def send_downstream():
                    async for message in upstream:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            await websocket.send_text(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await websocket.send_bytes(message.data)
                        elif message.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            return

                tasks = [
                    asyncio.create_task(send_upstream()),
                    asyncio.create_task(send_downstream()),
                ]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
    except (aiohttp.ClientError, WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
