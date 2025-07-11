import azure.functions as func
import logging
import os
from openai import AzureOpenAI
import base64
import pandas as pd
import requests
import mimetypes
import json
from dotenv import load_dotenv
import io
import time
from msal import ConfidentialClientApplication
from msal_requests_auth.auth import ClientCredentialAuth
from requests import Session
import base64
from dataverse_api import DataverseClient
import numpy as np
from azure.search.documents.models import VectorizedQuery
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.functions import QueueMessage
import xml.etree.ElementTree as ET


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="speech_processor", methods=["GET"])
def speech_processor(req: func.HttpRequest) -> func.HttpResponse:
    load_dotenv()


    # try:
    #     # Read and parse the JSON body
    #     req_body = req.get_json()
    # except Exception as e:
    #     return func.HttpResponse('Invalid JSON body', status_code=400)


    # test_str = req_body.get("test")
            

    return func.HttpResponse("test", mimetype="application/json", status_code=200)


