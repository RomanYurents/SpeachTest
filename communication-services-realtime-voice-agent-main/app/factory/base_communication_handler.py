import logging
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Role(str, Enum):
    AI = "ai"
    USER = "user"


class Message(BaseModel):
    role: Role
    message: str


class BaseCommunicationHandler(ABC):
    """Abstract base class for communication handlers"""

    def __init__(self, call_id: str, phone_number: str = None, customer_phone: str = None):
        self.call_id = call_id
        self.phone_number = phone_number
        self.customer_phone = customer_phone
        self.call_ended = False
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.is_closed = False
        self.audio_format = None

    @abstractmethod
    async def initialize_call(self) -> bool:
        """Initialize the call connection"""
        pass

    @abstractmethod
    async def end_call(self) -> bool:
        """End the current call"""
        pass

    @abstractmethod
    async def transfer_call(self, target_number: str, reason: str = "") -> bool:
        """Transfer call to another number"""
        pass

    @abstractmethod
    async def send_audio_async(self, rt_client, audio_data: str) -> None:
        """Send audio data to the call"""
        pass

    @abstractmethod
    async def receive_audio(self, data_payload) -> None:
       pass

    @abstractmethod
    async def stop_audio(self) -> None:
        pass

    async def get_call_duration(self) -> float:
        """Get call duration in seconds"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        elif self.start_time:
            return (datetime.utcnow() - self.start_time).total_seconds()
        return 0