import pytest

from talk_to_mic import credentials


@pytest.fixture(autouse=True)
def no_stored_api_key(monkeypatch):
    """Keep a key saved in this machine's Credential Manager out of the tests."""
    monkeypatch.setattr(credentials, "read_api_key", lambda target=credentials.TARGET: None)
