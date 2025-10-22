# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from abc import ABC
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from rtclient.util.model_helpers import ModelWithDefaults

Voice = Literal["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "cedar", "marin"]
AudioFormat = Literal["pcm16", "g711_ulaw", "g711_alaw"]
Modality = Literal["text", "audio"]


class NoTurnDetection(ModelWithDefaults):
    type: Literal["none"] = "none"


class ServerVAD(ModelWithDefaults):
    type: Literal["server_vad", "semantic_vad"] = "server_vad"
    threshold: Optional[Annotated[float, Field(strict=True, ge=0.0, le=1.0)]] = None
    prefix_padding_ms: Optional[int] = None
    silence_duration_ms: Optional[int] = None


class AzureSemanticVAD(ModelWithDefaults):
    type: Literal["azure_semantic_vad", "azure_semantic_vad_multilingual"] = "azure_semantic_vad"
    threshold: Optional[Annotated[float, Field(strict=True, ge=0.0, le=1.0)]] = None
    prefix_padding_ms: Optional[int] = None
    silence_duration_ms: Optional[int] = None
    remove_filler_words: Optional[bool] = False
    end_of_utterance_detection: Optional["EndOfUtteranceDetection"] = None


class EndOfUtteranceDetection(ModelWithDefaults):
    model: Literal["semantic_detection_v1"] = "semantic_detection_v1"
    threshold: Optional[Annotated[float, Field(strict=True, ge=0.0, le=1.0)]] = 0.01
    timeout: Optional[int] = 2


TurnDetection = Annotated[Union[NoTurnDetection, ServerVAD, AzureSemanticVAD], Field(discriminator="type")]


class FunctionToolChoice(ModelWithDefaults):
    type: Literal["function"] = "function"
    function: str


ToolChoice = Literal["auto", "none", "required"] | FunctionToolChoice

MessageRole = Literal["system", "assistant", "user"]


class InputAudioTranscription(BaseModel):
    model: Literal["whisper-1", "gpt-4o-mini-transcribe", "azure-speech"]
    language: Optional[str] = None
    phrase_list: Optional[list[str]] = None


class ClientMessageBase(ModelWithDefaults):
    _is_azure: bool = False
    event_id: Optional[str] = None


Temperature = Annotated[float, Field(strict=True, ge=0.6, le=1.2)]
ToolsDefinition = list[Any]

MaxTokensType = Union[int, Literal["inf"]]


class InputAudioNoiseReduction(BaseModel):
    type: Literal["near_field", "far_field", "azure_deep_noise_suppression"] = "near_field"


class InputAudioEchoCancellation(BaseModel):
    type: Literal["server_echo_cancellation"] = "server_echo_cancellation"


class AzureVoiceConfig(BaseModel):
    name: str
    type: Literal["azure-standard", "azure-custom"]
    endpoint_id: Optional[str] = None
    temperature: Optional[Annotated[float, Field(ge=0.0, le=1.0)]] = None
    custom_lexicon_url: Optional[str] = None
    rate: Optional[str] = None #0.5-1.5


class AnimationConfig(BaseModel):
    outputs: Optional[list[Literal["viseme_id"]]] = None


class AvatarVideoConfig(BaseModel):
    bitrate: Optional[int] = None
    codec: Optional[Literal["h264", "vp9"]] = "h264"
    crop: Optional[dict[str, list[int]]] = None
    resolution: Optional[dict[str, int]] = None
    background: Optional[dict[str, str]] = None


class ICEServer(BaseModel):
    urls: list[str]
    username: Optional[str] = None
    credential: Optional[str] = None


class AvatarConfig(BaseModel):
    character: str
    style: str
    customized: bool = False
    ice_servers: Optional[list[ICEServer]] = None
    video: Optional[AvatarVideoConfig] = None


class BaseSessionUpdateParams(BaseModel, ABC):
    model: Optional[str] = None
    modalities: Optional[set[Modality]] = None
    instructions: Optional[str] = None
    input_audio_format: Optional[AudioFormat] = None
    output_audio_format: Optional[AudioFormat] = None
    temperature: Optional[Temperature] = None
    max_response_output_tokens: Optional[MaxTokensType] = None
    tools: Optional[list[Any]] = None
    tool_choice: Optional[Any] = None


class BaseTranscription(BaseModel, ABC):
    model: str
    language: Optional[str] = None


class OpenAITranscription(BaseTranscription):
    model: Literal["whisper-1", "gpt-4o-mini-transcribe"]


class AzureTranscription(BaseTranscription):
    model: Literal["azure-speech"]
    phrase_list: Optional[list[str]] = None


class OpenAISessionUpdateParams(BaseSessionUpdateParams):
    voice: Optional[Voice] = None
    input_audio_transcription: Optional[OpenAITranscription] = None
    input_audio_noise_reduction: Optional[InputAudioNoiseReduction] = None
    turn_detection: Optional[Union[NoTurnDetection, ServerVAD]] = None


class AzureVoiceLiveSessionUpdateParams(BaseSessionUpdateParams):
    voice: Optional[AzureVoiceConfig] = None
    input_audio_transcription: Optional[AzureTranscription] = None
    input_audio_noise_reduction: Optional[InputAudioNoiseReduction] = None
    input_audio_echo_cancellation: Optional[InputAudioEchoCancellation] = None
    input_audio_sampling_rate: Optional[Literal[16000, 24000]] = 24000
    turn_detection: Optional[Union[NoTurnDetection, AzureSemanticVAD]] = None
    output_audio_timestamp_types: Optional[list[Literal["word"]]] = None
    # Avatar та інші Azure-специфічні поля
    animation: Optional[dict] = None
    avatar: Optional[dict] = None


class SessionConfigFactory:
    @staticmethod
    def create_openai_config(
            voice: Voice = "alloy",
            system_prompt: str = "",
            audio_format: AudioFormat = "pcm16",
            turn_detection_type: Literal["server_vad", "none"] = "server_vad",
            transcription_model: Literal["whisper-1", "gpt-4o-mini-transcribe"] = "whisper-1",
            language: Optional[str] = None,
            tools: Optional[list[Any]] = None,
    ) -> OpenAISessionUpdateParams:
        if turn_detection_type == "none":
            turn_detection = NoTurnDetection()
        else:
            turn_detection = ServerVAD(type="server_vad")

        transcription = OpenAITranscription(
            model=transcription_model,
            language=language
        )

        return OpenAISessionUpdateParams(
            voice=voice,
            instructions=system_prompt,
            input_audio_format=audio_format,
            output_audio_format=audio_format,
            input_audio_transcription=transcription,
            turn_detection=turn_detection,
            input_audio_noise_reduction=InputAudioNoiseReduction(type="near_field"),
            tools=tools or [],
            tool_choice="auto",
        )

    @staticmethod
    def create_azure_voice_live_config(
            azure_voice: str,
            system_prompt: str = "",
            audio_format: AudioFormat = "pcm16",
            turn_detection_type: Literal[
                "azure_semantic_vad", "azure_semantic_vad_multilingual"] = "azure_semantic_vad",
            language: str = "sv",
            voice_rate: str = "1.0",
            threshold: float = 0.3,
            prefix_padding: int = 200,
            silence_duration: int = 200,
            tools: Optional[list[Any]] = None,
            model: str = "gpt-4o-realtime-preview",
    ) -> AzureVoiceLiveSessionUpdateParams:
        voice_config = AzureVoiceConfig(
            name=azure_voice,
            type="azure-standard",
            temperature=0.8,
            rate=voice_rate,
        )

        transcription = AzureTranscription(
            model="azure-speech",
            language=language,
        )

        turn_detection = AzureSemanticVAD(
            type=turn_detection_type,
            threshold=threshold,
            prefix_padding_ms=prefix_padding,
            silence_duration_ms=silence_duration,
            remove_filler_words=False,
        )

        return AzureVoiceLiveSessionUpdateParams(
            model=model,
            modalities={"text", "audio"},
            instructions=system_prompt,
            voice=voice_config,
            turn_detection=turn_detection,
            input_audio_transcription=transcription,
            input_audio_noise_reduction=InputAudioNoiseReduction(
                type="azure_deep_noise_suppression"
            ),
            input_audio_echo_cancellation=InputAudioEchoCancellation(
                type="server_echo_cancellation"
            ),
            input_audio_sampling_rate=24000,
            input_audio_format=audio_format,
            output_audio_format=audio_format,
            temperature=0.7,
            tools=tools or [],
            tool_choice="auto",
        )


class SessionAvatarConnectMessage(ClientMessageBase):
    """
    Connect avatar with client SDP for video streaming.
    """

    type: Literal["session.avatar.connect"] = "session.avatar.connect"
    client_sdp: str


class InputAudioBufferAppendMessage(ClientMessageBase):
    """
    Append audio data to the user audio buffer, this should be in the format specified by
    input_audio_format in the session config.
    """

    type: Literal["input_audio_buffer.append"] = "input_audio_buffer.append"
    audio: str


class InputAudioBufferCommitMessage(ClientMessageBase):
    """
    Commit the pending user audio buffer, which creates a user message item with the audio content
    and clears the buffer.
    """

    type: Literal["input_audio_buffer.commit"] = "input_audio_buffer.commit"


class InputAudioBufferClearMessage(ClientMessageBase):
    """
    Clear the user audio buffer, discarding any pending audio data.
    """

    type: Literal["input_audio_buffer.clear"] = "input_audio_buffer.clear"


MessageItemType = Literal["message"]


class InputTextContentPart(ModelWithDefaults):
    type: Literal["input_text"] = "input_text"
    text: str


class InputAudioContentPart(ModelWithDefaults):
    type: Literal["input_audio"] = "input_audio"
    audio: str
    transcript: Optional[str] = None


class OutputTextContentPart(ModelWithDefaults):
    type: Literal["text"] = "text"
    text: str


SystemContentPart = InputTextContentPart
UserContentPart = Union[Annotated[Union[InputTextContentPart, InputAudioContentPart], Field(discriminator="type")]]
AssistantContentPart = OutputTextContentPart

ItemParamStatus = Literal["completed", "incomplete"]


class SystemMessageItem(ModelWithDefaults):
    type: MessageItemType = "message"
    role: Literal["system"] = "system"
    id: Optional[str] = None
    content: list[SystemContentPart]
    status: Optional[ItemParamStatus] = None


class UserMessageItem(ModelWithDefaults):
    type: MessageItemType = "message"
    role: Literal["user"] = "user"
    id: Optional[str] = None
    content: list[UserContentPart]
    status: Optional[ItemParamStatus] = None


class AssistantMessageItem(ModelWithDefaults):
    type: MessageItemType = "message"
    role: Literal["assistant"] = "assistant"
    id: Optional[str] = None
    content: list[AssistantContentPart]
    status: Optional[ItemParamStatus] = None


MessageItem = Annotated[Union[SystemMessageItem, UserMessageItem, AssistantMessageItem], Field(discriminator="role")]


class FunctionCallItem(ModelWithDefaults):
    type: Literal["function_call"] = "function_call"
    id: Optional[str] = None
    name: str
    call_id: str
    arguments: str
    status: Optional[ItemParamStatus] = None


class FunctionCallOutputItem(ModelWithDefaults):
    type: Literal["function_call_output"] = "function_call_output"
    id: Optional[str] = None
    call_id: str
    output: str
    status: Optional[ItemParamStatus] = None


Item = Annotated[Union[MessageItem, FunctionCallItem, FunctionCallOutputItem], Field(discriminator="type")]


class ItemCreateMessage(ClientMessageBase):
    type: Literal["conversation.item.create"] = "conversation.item.create"
    previous_item_id: Optional[str] = None
    item: Item


class ItemTruncateMessage(ClientMessageBase):
    type: Literal["conversation.item.truncate"] = "conversation.item.truncate"
    item_id: str
    content_index: int
    audio_end_ms: int


class ItemDeleteMessage(ClientMessageBase):
    type: Literal["conversation.item.delete"] = "conversation.item.delete"
    item_id: str


class ResponseCreateParams(BaseModel):
    commit: bool = True
    cancel_previous: bool = True
    append_input_items: Optional[list[Item]] = None
    input_items: Optional[list[Item]] = None
    instructions: Optional[str] = None
    modalities: Optional[set[Modality]] = None
    voice: Optional[Union[dict, AzureVoiceConfig]] = None
    temperature: Optional[Temperature] = None
    max_output_tokens: Optional[MaxTokensType] = None
    tools: Optional[ToolsDefinition] = None
    tool_choice: Optional[ToolChoice] = None
    output_audio_format: Optional[AudioFormat] = None


class ResponseCreateMessage(ClientMessageBase):
    """
    Trigger model inference to generate a model turn.
    """

    type: Literal["response.create"] = "response.create"
    response: Optional[ResponseCreateParams] = None


class ResponseCancelMessage(ClientMessageBase):
    type: Literal["response.cancel"] = "response.cancel"


class RealtimeError(BaseModel):
    message: str
    type: Optional[str] = None
    code: Optional[str] = None
    param: Optional[str] = None
    event_id: Optional[str] = None


class ServerMessageBase(BaseModel):
    event_id: str


class ErrorMessage(ServerMessageBase):
    type: Literal["error"] = "error"
    error: RealtimeError


class Session(BaseModel):
    id: str
    model: str
    modalities: set[Modality]
    instructions: str
    voice: Union[dict, AzureVoiceConfig]
    input_audio_format: AudioFormat
    output_audio_format: AudioFormat
    input_audio_sampling_rate: Optional[int] = 24000
    input_audio_transcription: Optional[Union[dict, InputAudioTranscription]] = None
    input_audio_noise_reduction: Optional[Union[dict, InputAudioNoiseReduction]] = None
    input_audio_echo_cancellation: Optional[Union[dict, InputAudioEchoCancellation]] = None
    turn_detection: Optional[Union[dict, TurnDetection]] = None
    tools: ToolsDefinition
    tool_choice: ToolChoice
    temperature: Temperature
    max_response_output_tokens: Optional[MaxTokensType]
    output_audio_timestamp_types: Optional[list[Literal["word"]]] = None
    animation: Optional[Union[dict, AnimationConfig]] = None
    avatar: Optional[Union[dict, AvatarConfig]] = None


class SessionCreatedMessage(ServerMessageBase):
    type: Literal["session.created"] = "session.created"
    session: Session


class SessionUpdatedMessage(ServerMessageBase):
    type: Literal["session.updated"] = "session.updated"
    session: Session


class SessionAvatarConnectingMessage(ServerMessageBase):
    """
    Server responds with SDP for avatar connection.
    """

    type: Literal["session.avatar.connecting"] = "session.avatar.connecting"
    server_sdp: str


class InputAudioBufferCommittedMessage(ServerMessageBase):
    """
    Signals the server has received and processed the audio buffer.
    """

    type: Literal["input_audio_buffer.committed"] = "input_audio_buffer.committed"
    previous_item_id: Optional[str]
    item_id: str


class InputAudioBufferClearedMessage(ServerMessageBase):
    """
    Signals the server has cleared the audio buffer.
    """

    type: Literal["input_audio_buffer.cleared"] = "input_audio_buffer.cleared"


class InputAudioBufferSpeechStartedMessage(ServerMessageBase):
    """
    If the server VAD is enabled, this event is sent when speech is detected in the user audio buffer.
    It tells you where in the audio stream (in milliseconds) the speech started, plus an item_id
    which will be used in the corresponding speech_stopped event and the item created in the conversation
    when speech stops.
    """

    type: Literal["input_audio_buffer.speech_started"] = "input_audio_buffer.speech_started"
    audio_start_ms: int
    item_id: str


class InputAudioBufferSpeechStoppedMessage(ServerMessageBase):
    """
    If the server VAD is enabled, this event is sent when speech stops in the user audio buffer.
    It tells you where in the audio stream (in milliseconds) the speech stopped, plus an item_id
    which will be used in the corresponding speech_started event and the item created in the conversation
    when speech starts.
    """

    type: Literal["input_audio_buffer.speech_stopped"] = "input_audio_buffer.speech_stopped"
    audio_end_ms: int
    item_id: str


ResponseItemStatus = Literal["in_progress", "completed", "incomplete"]


class ResponseItemInputTextContentPart(BaseModel):
    type: Literal["input_text"] = "input_text"
    text: str


class ResponseItemInputAudioContentPart(BaseModel):
    type: Literal["input_audio"] = "input_audio"
    transcript: Optional[str]


class ResponseItemTextContentPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ResponseItemAudioContentPart(BaseModel):
    type: Literal["audio"] = "audio"
    transcript: Optional[str]


ResponseItemContentPart = Annotated[
    Union[
        ResponseItemInputTextContentPart,
        ResponseItemInputAudioContentPart,
        ResponseItemTextContentPart,
        ResponseItemAudioContentPart,
    ],
    Field(discriminator="type"),
]


class ResponseItemBase(BaseModel):
    id: Optional[str]


class ResponseMessageItem(ResponseItemBase):
    type: MessageItemType = "message"
    status: ResponseItemStatus
    role: MessageRole
    content: list[ResponseItemContentPart]


class ResponseFunctionCallItem(ResponseItemBase):
    type: Literal["function_call"] = "function_call"
    status: ResponseItemStatus
    name: str
    call_id: str
    arguments: str


class ResponseFunctionCallOutputItem(ResponseItemBase):
    type: Literal["function_call_output"] = "function_call_output"
    call_id: str
    output: str


ResponseItem = Annotated[
    Union[ResponseMessageItem, ResponseFunctionCallItem, ResponseFunctionCallOutputItem],
    Field(discriminator="type"),
]


class ItemCreatedMessage(ServerMessageBase):
    type: Literal["conversation.item.created"] = "conversation.item.created"
    previous_item_id: Optional[str]
    item: ResponseItem


class ItemTruncatedMessage(ServerMessageBase):
    type: Literal["conversation.item.truncated"] = "conversation.item.truncated"
    item_id: str
    content_index: int
    audio_end_ms: int


class ItemDeletedMessage(ServerMessageBase):
    type: Literal["conversation.item.deleted"] = "conversation.item.deleted"
    item_id: str


class ItemInputAudioTranscriptionCompletedMessage(ServerMessageBase):
    type: Literal["conversation.item.input_audio_transcription.completed"] = (
        "conversation.item.input_audio_transcription.completed"
    )
    item_id: str
    content_index: int
    transcript: str


class ItemInputAudioTranscriptionFailedMessage(ServerMessageBase):
    type: Literal["conversation.item.input_audio_transcription.failed"] = (
        "conversation.item.input_audio_transcription.failed"
    )
    item_id: str
    content_index: int
    error: RealtimeError


ResponseStatus = Literal["in_progress", "completed", "cancelled", "incomplete", "failed"]


class ResponseCancelledDetails(BaseModel):
    type: Literal["cancelled"] = "cancelled"
    reason: Literal["turn_detected", "client_cancelled"]


class ResponseIncompleteDetails(BaseModel):
    type: Literal["incomplete"] = "incomplete"
    reason: Literal["max_output_tokens", "content_filter"]


class ResponseFailedDetails(BaseModel):
    type: Literal["failed"] = "failed"
    error: Any


ResponseStatusDetails = Annotated[
    Union[ResponseCancelledDetails, ResponseIncompleteDetails, ResponseFailedDetails],
    Field(discriminator="type"),
]


class InputTokenDetails(BaseModel):
    cached_tokens: int
    text_tokens: int
    audio_tokens: int


class OutputTokenDetails(BaseModel):
    text_tokens: int
    audio_tokens: int


class Usage(BaseModel):
    total_tokens: int
    input_tokens: int
    output_tokens: int
    input_token_details: InputTokenDetails
    output_token_details: OutputTokenDetails


class Response(BaseModel):
    id: str
    status: ResponseStatus
    status_details: Optional[ResponseStatusDetails]
    output: list[ResponseItem]
    usage: Optional[Usage]


class ResponseCreatedMessage(ServerMessageBase):
    type: Literal["response.created"] = "response.created"
    response: Response


class ResponseDoneMessage(ServerMessageBase):
    type: Literal["response.done"] = "response.done"
    response: Response


class ResponseOutputItemAddedMessage(ServerMessageBase):
    type: Literal["response.output_item.added"] = "response.output_item.added"
    response_id: str
    output_index: int
    item: ResponseItem


class ResponseOutputItemDoneMessage(ServerMessageBase):
    type: Literal["response.output_item.done"] = "response.output_item.done"
    response_id: str
    output_index: int
    item: ResponseItem


class ResponseContentPartAddedMessage(ServerMessageBase):
    type: Literal["response.content_part.added"] = "response.content_part.added"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    part: Annotated[
        ResponseItemContentPart, Field(alias="part", validation_alias=AliasChoices("part", "content"))
    ]


class ResponseContentPartDoneMessage(ServerMessageBase):
    type: Literal["response.content_part.done"] = "response.content_part.done"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    part: Annotated[
        ResponseItemContentPart, Field(alias="part", validation_alias=AliasChoices("part", "content"))
    ]


class ResponseTextDeltaMessage(ServerMessageBase):
    type: Literal["response.text.delta"] = "response.text.delta"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    delta: str


class ResponseTextDoneMessage(ServerMessageBase):
    type: Literal["response.text.done"] = "response.text.done"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    text: str


class ResponseAudioTranscriptDeltaMessage(ServerMessageBase):
    type: Literal["response.audio_transcript.delta"] = "response.audio_transcript.delta"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    delta: str


class ResponseAudioTranscriptDoneMessage(ServerMessageBase):
    type: Literal["response.audio_transcript.done"] = "response.audio_transcript.done"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    transcript: str


class ResponseAudioDeltaMessage(ServerMessageBase):
    type: Literal["response.audio.delta"] = "response.audio.delta"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    delta: str


class ResponseAudioDoneMessage(ServerMessageBase):
    type: Literal["response.audio.done"] = "response.audio.done"
    response_id: str
    item_id: str
    output_index: int
    content_index: int


class ResponseAudioTimestampDeltaMessage(ServerMessageBase):
    """
    Audio timestamp delta for word-level synchronization.
    """

    type: Literal["response.audio_timestamp.delta"] = "response.audio_timestamp.delta"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    audio_offset_ms: int
    audio_duration_ms: int
    text: str
    timestamp_type: Literal["word"]


class ResponseAudioTimestampDoneMessage(ServerMessageBase):
    """
    Signals all audio timestamps have been sent.
    """

    type: Literal["response.audio_timestamp.done"] = "response.audio_timestamp.done"
    response_id: str
    item_id: str


class ResponseAnimationVisemeDeltaMessage(ServerMessageBase):
    """
    Viseme animation data for avatar facial animation.
    """

    type: Literal["response.animation_viseme.delta"] = "response.animation_viseme.delta"
    response_id: str
    item_id: str
    output_index: int
    content_index: int
    audio_offset_ms: int
    viseme_id: int


class ResponseAnimationVisemeDoneMessage(ServerMessageBase):
    """
    Signals all viseme messages have been sent.
    """

    type: Literal["response.animation_viseme.done"] = "response.animation_viseme.done"
    response_id: str
    item_id: str


class ResponseFunctionCallArgumentsDeltaMessage(ServerMessageBase):
    type: Literal["response.function_call_arguments.delta"] = "response.function_call_arguments.delta"
    response_id: str
    item_id: str
    output_index: int
    call_id: str
    delta: str


class ResponseFunctionCallArgumentsDoneMessage(ServerMessageBase):
    type: Literal["response.function_call_arguments.done"] = "response.function_call_arguments.done"
    response_id: str
    item_id: str
    output_index: int
    call_id: str
    name: str
    arguments: str


class RateLimits(BaseModel):
    name: str
    limit: int
    remaining: int
    reset_seconds: float


class RateLimitsUpdatedMessage(ServerMessageBase):
    type: Literal["rate_limits.updated"] = "rate_limits.updated"
    rate_limits: list[RateLimits]


class ItemInputAudioTranscriptionDeltaMessage(ServerMessageBase):
    type: Literal["conversation.item.input_audio_transcription.delta"] = (
        "conversation.item.input_audio_transcription.delta"
    )


UserMessageType = Annotated[
    Union[
        SessionUpdatedMessage,
        SessionAvatarConnectMessage,
        InputAudioBufferAppendMessage,
        InputAudioBufferCommitMessage,
        InputAudioBufferClearMessage,
        ItemCreateMessage,
        ItemTruncateMessage,
        ItemDeleteMessage,
        ResponseCreateMessage,
        ResponseCancelMessage,
    ],
    Field(discriminator="type"),
]

ServerMessageType = Annotated[
    Union[
        ErrorMessage,
        SessionCreatedMessage,
        SessionUpdatedMessage,
        SessionAvatarConnectingMessage,
        InputAudioBufferCommittedMessage,
        InputAudioBufferClearedMessage,
        InputAudioBufferSpeechStartedMessage,
        InputAudioBufferSpeechStoppedMessage,
        ItemCreatedMessage,
        ItemTruncatedMessage,
        ItemDeletedMessage,
        ItemInputAudioTranscriptionCompletedMessage,
        ItemInputAudioTranscriptionFailedMessage,
        ResponseCreatedMessage,
        ResponseDoneMessage,
        ResponseOutputItemAddedMessage,
        ResponseOutputItemDoneMessage,
        ResponseContentPartAddedMessage,
        ResponseContentPartDoneMessage,
        ResponseTextDeltaMessage,
        ResponseTextDoneMessage,
        ResponseAudioTranscriptDeltaMessage,
        ResponseAudioTranscriptDoneMessage,
        ResponseAudioDeltaMessage,
        ResponseAudioDoneMessage,
        ResponseAudioTimestampDeltaMessage,
        ResponseAudioTimestampDoneMessage,
        ResponseAnimationVisemeDeltaMessage,
        ResponseAnimationVisemeDoneMessage,
        ResponseFunctionCallArgumentsDeltaMessage,
        ResponseFunctionCallArgumentsDoneMessage,
        RateLimitsUpdatedMessage,
        ItemInputAudioTranscriptionDeltaMessage,
    ],
    Field(discriminator="type"),
]


def create_message_from_dict(data: dict) -> ServerMessageType:
    event_type = data.get("type")
    match event_type:
        case "error":
            return ErrorMessage(**data)
        case "session.created":
            return SessionCreatedMessage(**data)
        case "session.updated":
            return SessionUpdatedMessage(**data)
        case "session.avatar.connecting":
            return SessionAvatarConnectingMessage(**data)
        case "input_audio_buffer.committed":
            return InputAudioBufferCommittedMessage(**data)
        case "input_audio_buffer.cleared":
            return InputAudioBufferClearedMessage(**data)
        case "input_audio_buffer.speech_started":
            return InputAudioBufferSpeechStartedMessage(**data)
        case "input_audio_buffer.speech_stopped":
            return InputAudioBufferSpeechStoppedMessage(**data)
        case "conversation.item.created":
            return ItemCreatedMessage(**data)
        case "conversation.item.truncated":
            return ItemTruncatedMessage(**data)
        case "conversation.item.deleted":
            return ItemDeletedMessage(**data)
        case "conversation.item.input_audio_transcription.completed":
            return ItemInputAudioTranscriptionCompletedMessage(**data)
        case "conversation.item.input_audio_transcription.failed":
            return ItemInputAudioTranscriptionFailedMessage(**data)
        case "response.created":
            return ResponseCreatedMessage(**data)
        case "response.done":
            return ResponseDoneMessage(**data)
        case "response.output_item.added":
            return ResponseOutputItemAddedMessage(**data)
        case "response.output_item.done":
            return ResponseOutputItemDoneMessage(**data)
        case "response.content_part.added":
            return ResponseContentPartAddedMessage(**data)
        case "response.content_part.done":
            return ResponseContentPartDoneMessage(**data)
        case "response.text.delta":
            return ResponseTextDeltaMessage(**data)
        case "response.text.done":
            return ResponseTextDoneMessage(**data)
        case "response.audio_transcript.delta":
            return ResponseAudioTranscriptDeltaMessage(**data)
        case "response.audio_transcript.done":
            return ResponseAudioTranscriptDoneMessage(**data)
        case "response.audio.delta":
            return ResponseAudioDeltaMessage(**data)
        case "response.audio.done":
            return ResponseAudioDoneMessage(**data)
        case "response.audio_timestamp.delta":
            return ResponseAudioTimestampDeltaMessage(**data)
        case "response.audio_timestamp.done":
            return ResponseAudioTimestampDoneMessage(**data)
        case "response.animation_viseme.delta":
            return ResponseAnimationVisemeDeltaMessage(**data)
        case "response.animation_viseme.done":
            return ResponseAnimationVisemeDoneMessage(**data)
        case "response.function_call_arguments.delta":
            return ResponseFunctionCallArgumentsDeltaMessage(**data)
        case "response.function_call_arguments.done":
            return ResponseFunctionCallArgumentsDoneMessage(**data)
        case "rate_limits.updated":
            return RateLimitsUpdatedMessage(**data)
        case "conversation.item.input_audio_transcription.delta":
            return ItemInputAudioTranscriptionDeltaMessage(**data)
        case _:
            raise ValueError(f"Unknown event type: {event_type}")


class UnknownMessage:
    pass