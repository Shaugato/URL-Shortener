import os

import boto3
import pytest
from moto import mock_aws


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AWS_REGION", os.getenv("AWS_REGION", "ap-southeast-2"))
    monkeypatch.setenv("DDB_TABLE", os.getenv("DDB_TABLE", "shortstack-urls"))
    monkeypatch.delenv("DDB_ENDPOINT_URL", raising=False)

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://go.example.com")
    monkeypatch.setenv("BLOCK_PRIVATE_HOSTS", "true")
    monkeypatch.setenv("APP_VERSION", "it")
    monkeypatch.setenv("CODE_LEN", "7")

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
        ddb.create_table(
            TableName=os.environ["DDB_TABLE"],
            KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "code", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        from application import create_app

        app = create_app()
        app.config.update(TESTING=True)
        yield app.test_client()


def test_realistic_flow(client):
    r = client.post("/api/shorten", json={"url": "https://example.com/path?q=1#frag"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["code"]
    assert body["shortUrl"]

    r2 = client.get(f"/{body['code']}", follow_redirects=False)
    assert r2.status_code in (301, 302)
    assert r2.headers["Location"].startswith("https://example.com/path")
