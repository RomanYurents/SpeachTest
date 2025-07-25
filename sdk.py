from azure.identity import DefaultAzureCredential
from azure.communication.callautomation import CallAutomationClient
from azure.communication.callautomation import (
    CallAutomationClient,
    CommunicationUserIdentifier
)

endpoint_url = 'https://cs-sellifyai-dev.europe.communication.azure.com/'
credential = DefaultAzureCredential()
client = CallAutomationClient(endpoint_url, credential)

user = CommunicationUserIdentifier("8:acs:7f63737a-0686-4715-b062-400f026a555c_00000028-dcd4-1cea-59fe-ad3a0d003d39")
callback_url = "https://<MY-EVENT-HANDLER-URL>/events"
result = client.create_call(target_participant=user, callback_url=callback_url)
call_connection_id = result.call_connection_id