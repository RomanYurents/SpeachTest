# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Optional

import aiohttp
from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError
from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential

from rtclient.models import ServerMessageType, UserMessageType, create_message_from_dict
from rtclient.util.user_agent import get_user_agent


class ConnectionError(Exception):
    def __init__(self, message: str, headers=None):
        super().__init__(message)
        self.headers = headers

    pass


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
            token: str | None = None,
            api_key: str | None = None,
    ):
        self._azure_endpoint = azure_endpoint
        self._api_version = api_version
        self._token = token
        self._api_key = api_key
        self._is_azure_openai = url is not None
        # if self._is_azure_openai:
        #     if key_credential is None and token_credential is None:
        #         raise ValueError("key_credential or token_credential is required for Azure OpenAI")
        #     if azure_deployment is None:
        #         raise ValueError("azure_deployment is required for Azure OpenAI")
        # else:
        #     if key_credential is None:
        #         raise ValueError("key_credential is required for OpenAI")
        #     if model is None:
        #         raise ValueError("model is required for OpenAI")

        self._url = url if self._is_azure_openai else "wss://api.openai.com"
        self._token_credential = token_credential
        self._key_credential = key_credential
        self._session = ClientSession()
        self._model = model
        self._azure_deployment = azure_deployment
        self.request_id: Optional[uuid.UUID] = None

    async def _get_auth(self):
        if self._token_credential:
            scope = "https://cognitiveservices.azure.com/.default"
            token = await self._token_credential.get_token(scope)
            return {"Authorization": f"Bearer {token.token}"}
        elif self._key_credential:
            return {"api-key": self._key_credential.key}
        else:
            return {}

    @staticmethod
    def _get_azure_params():
        api_version = os.getenv("AZURE_OPENAI_API_VERSION")
        path = os.getenv("AZURE_OPENAI_PATH")
        return (
            "2024-10-01-preview" if api_version is None else api_version,
            "/openai/realtime" if path is None else path,
        )

    async def connect(self):
        """
        Connects to Azure Voice Live Realtime WebSocket API.
        Automatically handles both API key and bearer token auth.
        """
        try:
            self.request_id = str(uuid.uuid4())

            azure_ws_endpoint = self._azure_endpoint.rstrip('/').replace("https://", "wss://")

            path = (
                f"{azure_ws_endpoint}/voice-live/realtime"
                f"?api-version={self._api_version}&model={self._model}"
            )

            headers = {
                "x-ms-client-request-id": self.request_id,
                "api-key": self._api_key,
            }

            print(f"🔗 Connecting to Azure Voice Live:\n{path}\n")

            try:
                self.ws = await self._session.ws_connect(path, headers=headers)
            except aiohttp.WSServerHandshakeError as e:
                await self._session.close()
                print(f"Handshake failed: {e.status} {e.message}")
                if e.headers:
                    print("Headers:", e.headers)
                raise
            except Exception as e:
                print("Unexpected WebSocket error:", e)
                raise

        except WSServerHandshakeError as e:
            await self._session.close()
            error_message = f"Received status code {e.status} from the server"
            raise ConnectionError(error_message, e.headers) from e

    async def send(self, message: UserMessageType):
        message._is_azure = self._is_azure_openai
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
        await self.ws.close()
        await self._session.close()

    @property
    def closed(self) -> bool:
        return self.ws.closed

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()
