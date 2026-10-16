"""Tests never use operator credentials or send sponsor traffic."""
import os

os.environ["SCRAM_TRACE_MODE"] = "local"
for name in ("ARIZE_API_KEY", "ARIZE_SPACE_ID", "DAYTONA_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "ANCHOR_API_KEY",
             "SPECTRON_API_KEY", "SPECTRON_URL", "SPECTRON_CONTEXT_ID", "SURREAL_USER", "SURREAL_PASS"):
    os.environ[name] = ""
os.environ["SURREAL_URL"] = "mem://"

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["SCRAM_EXECUTOR"] = "local"

# The operator's model choice, AWS profile and ECS metadata must never reach a unit test.
os.environ["SCRAM_MODEL_PROVIDER"] = "auto"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
os.environ["AWS_CONFIG_FILE"] = os.devnull
for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
             "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN",
             "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
             "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE"):
    os.environ.pop(name, None)

import pytest
from botocore.endpoint import Endpoint


@pytest.fixture(autouse=True)
def no_aws_network(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Unit tests must stub AWS requests")
    monkeypatch.setattr(Endpoint, "make_request", unexpected_request)
