import os
import boto3
import time
import pytest
from moto import mock_aws
import boto3

@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")
    monkeypatch.setenv("DDB_TABLE", "shortstack-urls")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://go.example.com")
    monkeypatch.setenv("BLOCK_PRIVATE_HOSTS", "true")
    monkeypatch.setenv("APP_VERSION", "test")
    monkeypatch.setenv("CODE_LEN", "7")

@mock_aws
def test_shorten_and_redirect(env, monkeypatch):
    # create table in moto
    ddb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
    ddb.create_table(
        TableName=os.environ["DDB_TABLE"],
        KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "code", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    from application import create_app
    app = create_app()
    client = app.test_client()

    r = client.post("/api/shorten", json={"url": "https://example.com"})
    assert r.status_code == 201
    code = r.get_json()["code"]

    rr = client.get(f"/{code}", follow_redirects=False)
    assert rr.status_code == 302
    assert rr.headers["Location"] == "https://example.com"

@mock_aws
def test_rejects_private_url(env):
    ddb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
    ddb.create_table(
        TableName=os.environ["DDB_TABLE"],
        KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "code", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    from application import create_app
    app = create_app()
    client = app.test_client()

    r = client.post("/api/shorten", json={"url": "http://localhost:8080/admin"})
    assert r.status_code == 400

@mock_aws
def test_alias_collision_returns_409(env):
    ddb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
    ddb.create_table(
        TableName=os.environ["DDB_TABLE"],
        KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "code", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    from application import create_app
    app = create_app()
    client = app.test_client()

    r1 = client.post("/api/shorten", json={"url": "https://a.com", "alias": "myLink"})
    assert r1.status_code == 201

    r2 = client.post("/api/shorten", json={"url": "https://b.com", "alias": "myLink"})
    assert r2.status_code == 409

@mock_aws
def test_expiry_enforced_immediately(env):
    ddb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
    ddb.create_table(
        TableName=os.environ["DDB_TABLE"],
        KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "code", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    from application import create_app
    app = create_app()
    client = app.test_client()

    r = client.post("/api/shorten", json={"url": "https://example.com", "ttlHours": 1})
    assert r.status_code == 201
    code = r.get_json()["code"]

    tbl = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"]).Table(os.environ["DDB_TABLE"])
    tbl.update_item(
        Key={"code": code},
        UpdateExpression="SET expires_at = :e",
        ExpressionAttributeValues={":e": int(time.time()) - 10},
    )

    rr = client.get(f"/{code}", follow_redirects=False)
    assert rr.status_code == 410
