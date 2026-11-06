from app.payments import make_return_code


def set_prices(client, admin_headers, **values):
    response = client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"settings": values},
    )
    assert response.status_code == 200, response.json


def top_up(client, user, amount):
    invoice = client.post(
        "/api/payments/invoices",
        headers=user["headers"],
        json={"amount_toman": amount},
    )
    assert invoice.status_code == 201, invoice.json
    data = invoice.json
    code = make_return_code(data["invoice_id"], 91827364 + data["invoice_id"], amount)
    result = client.post(
        "/api/payments/redeem",
        headers=user["headers"],
        json={"code": code},
    )
    assert result.status_code == 200, result.json


def test_subscription_plans_are_disabled_until_prices_configured(client):
    response = client.get("/api/public/subscription-plans")
    assert response.status_code == 200
    assert all(plan["enabled"] is False for plan in response.json)


def test_purchase_requires_login_and_configured_price(client, user, admin_headers):
    assert client.post("/api/subscriptions/purchase", json={"plan_code": "monthly"}).status_code == 401
    assert client.post(
        "/api/subscriptions/purchase",
        headers=user["headers"],
        json={"plan_code": "monthly"},
    ).status_code == 503
    set_prices(client, admin_headers, subscription_monthly_price="10000")
    assert client.post(
        "/api/subscriptions/purchase",
        headers=user["headers"],
        json={"plan_code": "unknown"},
    ).status_code == 400


def test_subscription_purchase_deducts_wallet_atomically(client, user, admin_headers):
    set_prices(client, admin_headers, subscription_monthly_price="10000")
    top_up(client, user, 30000)

    result = client.post(
        "/api/subscriptions/purchase",
        headers=user["headers"],
        json={"plan_code": "monthly"},
    )
    assert result.status_code == 200, result.json
    assert result.json["balance_toman"] == 20000
    assert result.json["amount_toman"] == 10000
    assert result.json["status"] == "active"

    state = client.get("/api/subscriptions", headers=user["headers"])
    assert state.status_code == 200
    assert state.json["active"]["plan_code"] == "monthly"
    assert client.get("/api/auth/me", headers=user["headers"]).json["balance_toman"] == 20000


def test_insufficient_wallet_does_not_create_subscription(client, user, admin_headers):
    set_prices(client, admin_headers, subscription_monthly_price="25000")
    result = client.post(
        "/api/subscriptions/purchase",
        headers=user["headers"],
        json={"plan_code": "monthly"},
    )
    assert result.status_code == 402
    assert client.get("/api/subscriptions", headers=user["headers"]).json["active"] is None
    assert client.get("/api/auth/me", headers=user["headers"]).json["balance_toman"] == 0


def test_subscription_price_validation(client, admin_headers):
    bad = client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={"settings": {"subscription_monthly_price": "100.5"}},
    )
    assert bad.status_code == 400
