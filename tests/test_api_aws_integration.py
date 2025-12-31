import os
import uuid

import boto3
import pytest

pytestmark = pytest.mark.aws


def _should_run():
    return os.getenv("RUN_AWS_INTEGRATION", "").lower() in ("1", "true", "yes")


@pytest.fixture(scope="function")
def aws_table(monkeypatch):
    if not _should_run():
        pytest.skip("Set RUN_AWS_INTEGRATION=1 to run real AWS integration tests.")

    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-southeast-2"))
    table_name = f"shortstack-it-{uuid.uuid4().hex[:12]}"

    monkeypatch.setenv("AWS_REGION", region)
    monkeypatch.setenv("DDB_TABLE", table_name)
    monkeypatch.delenv("DDB_ENDPOINT_URL", raising=False)

    ddb = boto3.client("dynamodb", region_name=region)

    ddb.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "code", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.get_waiter("table_exists").wait(TableName=table_name)

    try:
        yield table_name
    finally:
        try:
            ddb.delete_table(TableName=table_name)
            ddb.get_waiter("table_not_exists").wait(TableName=table_name)
        except Exception:
            pass


def test_real_aws_flow(aws_table, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://go.example.com")
    monkeypatch.setenv("BLOCK_PRIVATE_HOSTS", "true")
    monkeypatch.setenv("APP_VERSION", "aws-it")

    from application import create_app

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    r = client.post("/api/shorten", json={"url": "https://example.com"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["code"]
    assert body["shortUrl"]

    r2 = client.get(f"/{body['code']}", follow_redirects=False)
    assert r2.status_code in (301, 302)
    assert r2.headers["Location"] == "https://example.com"
