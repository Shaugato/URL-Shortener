import os
import time
import boto3
import pytest

@pytest.fixture(scope="session", autouse=True)
def setup_ddb_local():
    # assumes dynamodb-local is running on localhost:8000
    os.environ["AWS_REGION"] = "ap-southeast-2"
    os.environ["DDB_ENDPOINT_URL"] = "http://localhost:8000"
    os.environ["DDB_TABLE"] = "shortstack-urls-it"
    os.environ["PUBLIC_BASE_URL"] = "https://go.example.com"
    os.environ["APP_VERSION"] = "it"
    os.environ["BLOCK_PRIVATE_HOSTS"] = "true"

    ddb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"], endpoint_url=os.environ["DDB_ENDPOINT_URL"])
    try:
        ddb.create_table(
            TableName=os.environ["DDB_TABLE"],
            KeySchema=[{"AttributeName": "code", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "code", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # wait a moment
        time.sleep(0.5)
    except Exception:
        pass

@pytest.fixture()
def client():
    from application import create_app
    app = create_app()
    return app.test_client()

def test_realistic_flow(client):
    # 1) user shortens
    r = client.post("/api/shorten", json={"url": "https://example.com/path?q=1#frag"})
    assert r.status_code == 201
    data = r.get_json()
    code = data["code"]
    assert data["shortUrl"].endswith("/" + code)

    # 2) user clicks
    rr = client.get(f"/{code}", follow_redirects=False)
    assert rr.status_code == 302
    assert rr.headers["Location"].startswith("https://example.com")

def test_invalid_inputs(client):
    bad = [
        {"url": "example.com"},
        {"url": "ftp://example.com"},
        {"url": "http://localhost:80"},
        {"url": "https://"},
    ]
    for b in bad:
        r = client.post("/api/shorten", json=b)
        assert r.status_code == 400
