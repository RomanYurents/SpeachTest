from enum import Enum

from app.factory.azure_communication_handler import AzureCommunicationHandler
from app.factory.base_communication_handler import BaseCommunicationHandler
from app.factory.twilio_communication_handler import TwilioCommunicationHandler


class CommunicationProvider(Enum):
    AZURE = "azure"
    TWILIO = "twilio"


class CommunicationHandlerFactory:
    """Factory for creating communication handlers"""

    @staticmethod
    def create_handler(provider: CommunicationProvider, **kwargs) -> BaseCommunicationHandler:
        """Create appropriate communication handler based on provider"""

        if provider == CommunicationProvider.AZURE:
            return AzureCommunicationHandler(
                websocket=kwargs['websocket'],
                call_connection_id=kwargs['call_connection_id'],
                acs_client=kwargs['acs_client'],
                phone_number=kwargs.get('phone_number'),
                customer_phone=kwargs.get('customer_phone')
            )

        elif provider == CommunicationProvider.TWILIO:
            return TwilioCommunicationHandler(
                websocket=kwargs['websocket'],
                stream_sid=kwargs['stream_sid'],
                twilio_client=kwargs['twilio_client'],
                phone_number=kwargs.get('phone_number'),
                customer_phone=kwargs.get('customer_phone')
            )

        else:
            raise ValueError(f"Unsupported communication provider: {provider}")