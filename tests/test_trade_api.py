from fastapi.testclient import TestClient
from src.main import app


def _register_and_login(client: TestClient, username: str, email: str) -> tuple[int, str]:
    r = client.post("/auth/register", json={"username": username, "email": email, "password": "Password123!"})
    assert r.status_code == 200, r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": "Password123!"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return user_id, token


def test_trade_offer_flow_create_list_accept_and_resources_update():
    with TestClient(app) as client:
        # Register two users
        seller_id, seller_token = _register_and_login(client, "seller", "seller@example.com")
        buyer_id, buyer_token = _register_and_login(client, "buyer", "buyer@example.com")

        # Seller creates an offer: 100 metal for 50 crystal
        r = client.post(
            "/trade/offers",
            headers={"Authorization": f"Bearer {seller_token}"},
            json={
                "offered_resource": "metal",
                "offered_amount": 100,
                "requested_resource": "crystal",
                "requested_amount": 50,
            },
        )
        assert r.status_code == 200, r.text
        offer = r.json()
        assert offer.get("id") is not None

        # Seller's resources reflect escrow deduction
        r = client.get(f"/player/{seller_id}", headers={"Authorization": f"Bearer {seller_token}"})
        assert r.status_code == 200
        seller_data = r.json()
        assert seller_data["resources"]["metal"] == 100000 - 100

        # Public listing shows the open offer
        r = client.get("/trade/offers")
        assert r.status_code == 200
        offers = r.json()["offers"]
        assert any(o.get("id") == offer["id"] and o.get("status") == "open" for o in offers)

        # Buyer accepts the offer
        r = client.post(
            f"/trade/accept/{offer['id']}",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted"] is True

        # Verify transfer results
        r = client.get(f"/player/{seller_id}", headers={"Authorization": f"Bearer {seller_token}"})
        seller_after = r.json()
        r = client.get(f"/player/{buyer_id}", headers={"Authorization": f"Bearer {buyer_token}"})
        buyer_after = r.json()

        assert seller_after["resources"]["crystal"] == 100000 + 50
        assert buyer_after["resources"]["crystal"] == 100000 - 50
        assert buyer_after["resources"]["metal"] == 100000 + 100

        # Offer should now be accepted and include accepted_by
        r = client.get("/trade/offers", params={"status": "all"})
        payload = r.json()
        found = next((o for o in payload.get("offers", []) if o.get("id") == offer["id"]), None)
        assert found is not None
        assert found.get("status") == "accepted"
        assert int(found.get("accepted_by")) == buyer_id
