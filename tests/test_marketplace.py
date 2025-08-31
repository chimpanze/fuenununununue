from src.core.game import GameWorld
from src.models import Player, Position, Resources, ResourceProduction, Buildings, BuildQueue, Fleet, Research, Planet


def test_marketplace_create_and_accept_offer_transfers_resources():
    gw = GameWorld()

    # Create two players with ample resources
    gw.world.create_entity(
        Player(name="Seller", user_id=10), Position(), Resources(metal=1000, crystal=1000, deuterium=1000),
        ResourceProduction(), Buildings(), BuildQueue(), Fleet(), Research(), Planet(name="Home", owner_id=10)
    )
    gw.world.create_entity(
        Player(name="Buyer", user_id=20), Position(), Resources(metal=1000, crystal=1000, deuterium=1000),
        ResourceProduction(), Buildings(), BuildQueue(), Fleet(), Research(), Planet(name="Home", owner_id=20)
    )

    # Seller creates an offer: 100 metal for 50 crystal
    gw.queue_command({
        'type': 'trade_create_offer',
        'user_id': 10,
        'offered_resource': 'metal',
        'offered_amount': 100,
        'requested_resource': 'crystal',
        'requested_amount': 50,
    })
    gw._process_commands()

    offers = gw.list_market_offers()
    assert len(offers) == 1
    offer_id = offers[0]['id']
    assert offers[0]['status'] == 'open'

    # Escrow has deducted from seller's resources
    seller_snapshot = gw.get_player_data(10)
    assert seller_snapshot['resources']['metal'] == 900

    # Buyer accepts the offer
    gw.queue_command({'type': 'trade_accept_offer', 'user_id': 20, 'offer_id': offer_id})
    gw._process_commands()

    # Verify transfer: seller gains 50 crystal; buyer loses 50 crystal and gains 100 metal
    seller_after = gw.get_player_data(10)
    buyer_after = gw.get_player_data(20)

    assert seller_after['resources']['crystal'] == 1050
    assert buyer_after['resources']['crystal'] == 950
    assert buyer_after['resources']['metal'] == 1100

    # Offer marked as accepted
    offers2 = gw.list_market_offers(status=None)
    assert offers2[0]['status'] == 'accepted'
    assert offers2[0].get('accepted_by') == 20


def test_marketplace_validation_prevents_self_trade_and_insufficient():
    gw = GameWorld()

    # Single player
    gw.world.create_entity(
        Player(name="Solo", user_id=30), Position(), Resources(metal=100, crystal=10, deuterium=0),
        ResourceProduction(), Buildings(), BuildQueue(), Fleet(), Research(), Planet(name="Home", owner_id=30)
    )

    # Try to create an offer larger than resources (should not create)
    gw.queue_command({
        'type': 'trade_create_offer',
        'user_id': 30,
        'offered_resource': 'metal',
        'offered_amount': 1000,
        'requested_resource': 'crystal',
        'requested_amount': 1,
    })
    gw._process_commands()
    assert len(gw.list_market_offers()) == 0

    # Create a valid offer
    gw.queue_command({
        'type': 'trade_create_offer',
        'user_id': 30,
        'offered_resource': 'metal',
        'offered_amount': 50,
        'requested_resource': 'crystal',
        'requested_amount': 5,
    })
    gw._process_commands()
    offers = gw.list_market_offers()
    assert len(offers) == 1

    # Self-accept must be rejected
    gw.queue_command({'type': 'trade_accept_offer', 'user_id': 30, 'offer_id': offers[0]['id']})
    gw._process_commands()
    # Should remain open and no resource changes except escrow
    offers_after = gw.list_market_offers()
    assert offers_after[0]['status'] == 'open'
