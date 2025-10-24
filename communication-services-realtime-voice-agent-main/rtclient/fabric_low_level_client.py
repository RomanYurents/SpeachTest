# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import uuid
from abc import ABC, abstractmethod
from typing import Optional
from collections.abc import AsyncIterator

import aiohttp
from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError
from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential

from rtclient.models import ServerMessageType, UserMessageType, create_message_from_dict


class ConnectionError(Exception):
    def __init__(self, message: str, headers=None):
        super().__init__(message)
        self.headers = headers


class BaseRealtimeClient(ABC):
    def __init__(self):
        self.ws = None
        self._session = None
        self.request_id: Optional[uuid.UUID] = None

    @abstractmethod
    async def connect(self):
        pass

    async def send(self, message: UserMessageType):
        message_json = message.model_dump_json(exclude_unset=True)
        await self.ws.send_str(message_json)

    async def send_raw(self, payload: dict):
        await self.ws.send_str(json.dumps(payload))

    async def recv(self) -> ServerMessageType | None:
        if self.ws.closed:
            return None
        websocket_message = await self.ws.receive()
        if websocket_message.type == WSMsgType.TEXT:
            data = json.loads(websocket_message.data)
            msg = create_message_from_dict(data)
            return msg
        else:
            return None

    def __aiter__(self) -> AsyncIterator[ServerMessageType | None]:
        return self

    async def __anext__(self):
        message = await self.recv()
        if message is None:
            raise StopAsyncIteration
        return message

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self._session:
            await self._session.close()

    @property
    def closed(self) -> bool:
        return self.ws.closed if self.ws else True

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()


class OpenAIRealtimeClient(BaseRealtimeClient):
    def __init__(
            self,
            url: str,
            azure_deployment: str,
            token_credential: Optional[AsyncTokenCredential] = None,
            key_credential: Optional[AzureKeyCredential] = None,
    ):
        super().__init__()
        self._url = url
        self._azure_deployment = azure_deployment
        self._token_credential = token_credential
        self._key_credential = key_credential

        if not token_credential and not key_credential:
            raise ValueError("Either token_credential or key_credential is required")

    async def _get_auth(self):
        if self._token_credential:
            scope = "https://cognitiveservices.azure.com/.default"
            token = await self._token_credential.get_token(scope)
            return {"Authorization": f"Bearer {token.token}"}
        elif self._key_credential:
            return {"api-key": self._key_credential.key}
        return {}

    async def connect(self):
        try:
            self.request_id = uuid.uuid4()
            self._session = ClientSession(base_url=self._url)

            api_version = "2024-10-01-preview"
            path = "/openai/realtime"

            auth_headers = await self._get_auth()
            headers = {
                "x-ms-client-request-id": str(self.request_id),
                **auth_headers,
            }

            self.ws = await self._session.ws_connect(
                path,
                headers=headers,
                params={
                    "deployment": self._azure_deployment,
                    "api-version": api_version
                },
            )

        except WSServerHandshakeError as e:
            if self._session:
                await self._session.close()
            error_message = f"Received status code {e.status} from the server"
            raise ConnectionError(error_message, e.headers) from e


class AzureVoiceLiveClient(BaseRealtimeClient):
    def __init__(
            self,
            azure_endpoint: str,
            model: str,
            api_version: str,
            api_key: str,
    ):
        super().__init__()
        self._azure_endpoint = azure_endpoint
        self._model = model
        self._api_version = api_version
        self._api_key = api_key

    async def connect(self):
        try:
            self.request_id = str(uuid.uuid4())
            self._session = ClientSession()

            azure_ws_endpoint = self._azure_endpoint.rstrip('/').replace("https://", "wss://")

            path = (
                f"{azure_ws_endpoint}/voice-live/realtime"
                f"?api-version={self._api_version}&model={self._model}"
            )

            headers = {
                "x-ms-client-request-id": self.request_id,
                "api-key": self._api_key,
            }

            print(f"Connecting to Azure Voice Live:\n{path}\n")

            self.ws = await self._session.ws_connect(path, headers=headers)

        except aiohttp.WSServerHandshakeError as e:
            if self._session:
                await self._session.close()
            print(f"Handshake failed: {e.status} {e.message}")
            if e.headers:
                print("Headers:", e.headers)
            raise ConnectionError(f"Received status code {e.status}", e.headers) from e


class RealtimeClientFactory:
    @staticmethod
    def create_openai_client(
            url: str,
            azure_deployment: str,
            token_credential: Optional[AsyncTokenCredential] = None,
            key_credential: Optional[AzureKeyCredential] = None,
    ) -> OpenAIRealtimeClient:
        return OpenAIRealtimeClient(
            url=url,
            azure_deployment=azure_deployment,
            token_credential=token_credential,
            key_credential=key_credential,
        )

    @staticmethod
    def create_azure_voice_live_client(
            azure_endpoint: str,
            model: str,
            api_version: str,
            api_key: str,
    ) -> AzureVoiceLiveClient:
        return AzureVoiceLiveClient(
            azure_endpoint=azure_endpoint,
            model=model,
            api_version=api_version,
            api_key=api_key,
        )


class RTLowLevelClient:

    def __init__(
            self,
            url: Optional[str] = None,
            token_credential: Optional[AsyncTokenCredential] = None,
            key_credential: Optional[AzureKeyCredential] = None,
            model: Optional[str] = None,
            azure_deployment: Optional[str] = None,
            azure_endpoint: str | None = None,
            api_version: str | None = None,
            api_key: str | None = None,
    ):
        if url is not None and azure_deployment is not None:
            self._client = RealtimeClientFactory.create_openai_client(
                url=url,
                azure_deployment=azure_deployment,
                token_credential=token_credential,
                key_credential=key_credential,
            )
            self._mode = "realtime"
        elif azure_endpoint is not None and api_key is not None:
            self._client = RealtimeClientFactory.create_azure_voice_live_client(
                azure_endpoint=azure_endpoint,
                model=model,
                api_version=api_version,
                api_key=api_key,
            )
            self._mode = "voice_live"
        else:
            raise ValueError(
                "Invalid parameters. Provide either:\n"
                "1. url + azure_deployment for OpenAI Realtime\n"
                "2. azure_endpoint + api_key for Azure Voice Live"
            )

    async def connect(self, mode: Optional[str] = None):
        await self._client.connect()

    async def send(self, message: UserMessageType):
        await self._client.send(message)

    async def send_raw(self, payload: dict):
        await self._client.send_raw(payload)

    async def recv(self) -> ServerMessageType | None:
        return await self._client.recv()

    def __aiter__(self):
        return self._client.__aiter__()

    async def __anext__(self):
        return await self._client.__anext__()

    async def close(self):
        await self._client.close()

    @property
    def closed(self) -> bool:
        return self._client.closed

    @property
    def request_id(self):
        return self._client.request_id

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self._client.__aexit__(*args)
