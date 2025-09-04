import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.core.state import game_world


def _register_and_login(client: TestClient, username: str, email: str) -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": "Password123!"})
    assert r.status_code in (200, 201), r.text
    user_id = int(r.json()["id"])
    r = client.post("/auth/login", json={"username": username, "password": "Password123!"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


@pytest.mark.integration
def test_market_offer_accept_after_autoload_hydrates_offers_and_ids():
    """
    Integration test: verifies that after autoload, in-memory market offers are hydrated
    from the database and that accepting an offer works using the hydrated data.
    Also checks that ID counters are reconciled to avoid collisions.
    """
    # Skip if DB not enabled (autoload hydration relies on DB)
    try:
        from src.core.database import is_db_enabled
        if not is_db_enabled():
            pytest.skip("DB not enabled; skipping autoload hydration test for market offers")
    except Exception:
        pytest.skip("DB not available; skipping")

    with TestClient(app) as client:
        # Register seller and buyer
        seller_id, seller_token = _register_and_login(client, "seller_autoload", "seller_autoload@example.com")
        buyer_id, buyer_token = _register_and_login(client, "buyer_autoload", "buyer_autoload@example.com")

        # Seller creates an offer (persisted to DB and in-memory)
        r = client.post(
            "/trade/offers",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={
                "offered_resource": "metal",
                "offered_amount": 42,
                "requested_resource": "crystal",
                "requested_amount": 21,
            },
        )
        assert r.status_code == 200, r.text
        offer = r.json()
        oid = int(offer["id"]) if isinstance(offer, dict) else int(offer["id"])  # normalize

        # Simulate fresh process by clearing in-memory offers and resetting counters
        game_world._market_offers.clear()
        game_world._next_offer_id = 1

        # Trigger autoload hydration (loads open offers and reconciles next IDs)
        game_world.load_player_data(user_id=None)

        # Offers endpoint should now list the previously created offer
        r = client.get("/trade/offers")
        assert r.status_code == 200, r.text
        offers = r.json().get("offers", [])
        assert any(int(o.get("id")) == oid and o.get("status") == "open" for o in offers)

        # Buyer accepts the offer (uses in-memory hydrated offer for gameplay logic)
        r = client.post(f"/trade/accept/{oid}", headers={"Authorization": f"Bearer {buyer_token}"})
        assert r.status_code == 200, r.text
        assert r.json().get("accepted") is True

        # After hydration, the next offer id should be greater than the accepted one to avoid collisions
        assert int(game_world._next_offer_id) > oid
