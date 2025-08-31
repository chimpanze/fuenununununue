from fastapi.testclient import TestClient

from src.main import app
from src.core.game import GameWorld
from src.models import Player, Position, Resources, ResourceProduction, Buildings, BuildQueue, Fleet, Research, Planet


def test_trade_history_unit_events_created_and_completed():
    gw = GameWorld()

    # Two players with resources
    gw.world.create_entity(
        Player(name="Seller", user_id=101), Position(), Resources(metal=1000, crystal=1000, deuterium=1000),
        ResourceProduction(), Buildings(), BuildQueue(), Fleet(), Research(), Planet(name="Home", owner_id=101)
    )
    gw.world.create_entity(
        Player(name="Buyer", user_id=202), Position(), Resources(metal=1000, crystal=1000, deuterium=1000),
        ResourceProduction(), Buildings(), BuildQueue(), Fleet(), Research(), Planet(name="Home", owner_id=202)
    )

    # Create offer
    oid = gw._handle_trade_create_offer(101, "metal", 120, "crystal", 60)
    assert oid is not None

    # Seller history should include an offer_created event
    hist_seller = gw.list_trade_history(101, limit=10)
    assert any(e.get("type") == "offer_created" and int(e.get("offer_id")) == int(oid) for e in hist_seller)

    # Accept offer
    ok = gw._handle_trade_accept_offer(202, int(oid))
    assert ok is True

    # Both seller and buyer should see a trade_completed event
    hist_seller_after = gw.list_trade_history(101, limit=10)
    hist_buyer_after = gw.list_trade_history(202, limit=10)
    assert any(e.get("type") == "trade_completed" and int(e.get("offer_id")) == int(oid) for e in hist_seller_after)
    assert any(e.get("type") == "trade_completed" and int(e.get("offer_id")) == int(oid) for e in hist_buyer_after)


def _register_and_login(client: TestClient, username: str, email: str) -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": "Password123!"})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": "Password123!"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


ession = None  # silence linters for unused import patterns

def test_trade_history_api_endpoint_returns_events():
    with TestClient(app) as client:
        seller_id, seller_token = _register_and_login(client, "seller_hist", "seller_hist@example.com")
        buyer_id, buyer_token = _register_and_login(client, "buyer_hist", "buyer_hist@example.com")

        # Seller creates an offer
        r = client.post(
            "/trade/offers",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={
                "offered_resource": "metal",
                "offered_amount": 75,
                "requested_resource": "crystal",
                "requested_amount": 30,
            },
        )
        assert r.status_code == 200, r.text
        offer = r.json()
        oid = offer["id"]

        # Seller history should show offer_created
        r = client.get(
            f"/player/{seller_id}/trade/history",
            headers={"Authorization": f"Bearer {seller_token}"},
        )
        assert r.status_code == 200, r.text
        events = r.json()["events"]
        assert any(e.get("type") == "offer_created" and int(e.get("offer_id")) == int(oid) for e in events)

        # Buyer accepts the offer
        r = client.post(
            f"/trade/accept/{oid}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert r.status_code == 200, r.text

        # Both sides should see trade_completed
        r = client.get(
            f"/player/{seller_id}/trade/history",
            headers={"Authorization": f"Bearer {seller_token}"},
        )
        seller_events = r.json()["events"]
        assert any(e.get("type") == "trade_completed" and int(e.get("offer_id")) == int(oid) for e in seller_events)

        r = client.get(
            f"/player/{buyer_id}/trade/history",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        buyer_events = r.json()["events"]
        assert any(e.get("type") == "trade_completed" and int(e.get("offer_id")) == int(oid) for e in buyer_events)
