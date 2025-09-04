# API Documentation (Skeleton)

This document summarizes the current HTTP API. It will evolve as the project is modularized.

Base URL: http://localhost:8000

Note: Persistence is Postgres-only; endpoints rely on the database as the single source of truth. File- or SQLite-based persistence has been removed (see docs/cleanup.md).

## Health
- GET /  
  Response:
  {
    "message": "Ogame-like Game Server",
    "status": "running"
  }

## Player
- GET /player/{user_id}  
  Returns full player snapshot (resources, buildings, fleet, research, planet).

- POST /player/{user_id}/build  
  Body:
  {
    "building_type": "metal_mine"
  }
  Queues construction for the given building type.

- GET /player/{user_id}/fleet  
  Returns the player's fleet composition and the current ship_build_queue.

- POST /player/{user_id}/build-ships  
  Body:
  {
    "ship_type": "light_fighter",
    "quantity": 5
  }
  Queues ship construction in the shipyard.

- POST /player/{user_id}/fleet/dispatch  
  Body:
  {
    "galaxy": 1,
    "system": 2,
    "position": 3,
    "mission": "attack",
    "speed": 1.0,                # optional
    "ships": {"light_fighter": 5}   # optional
  }
  Queues a fleet dispatch command from the active planet to the target coordinates with the specified mission. Travel time is computed based on ship speed, distance, and technology levels; composition handling will be extended in subsequent tasks.

- POST /player/{user_id}/fleet/{fleet_id}/recall  
  Recall an in-flight fleet back to its origin. Current model supports a single in-flight movement per player; fleet_id is accepted for compatibility.  
  Response:
  {
    "message": "Fleet recall queued",
    "recalled": true,
    "return_eta": "2025-08-30T20:19:00.000000"
  }

- POST /player/{user_id}/colonize  
  Body:
  {
    "galaxy": 1,
    "system": 1,
    "position": 2,
    "planet_name": "New Terra"
  }
  Initiates colonization using a colony ship. Requires at least one colony ship in the player's fleet. When the database is enabled, creates a new planet at the given coordinates if unoccupied; otherwise returns an error.

## Building Costs
- GET /building-costs/{building_type}?level=0  
  Returns cost and build_time_seconds for the given building at the specified level.

## Game Status
- GET /game-status  
  Returns game_running, total_entities, and server_time.

## Health
- GET /healthz  
  Returns service health details including:
  - loop.running, loop.tick_rate, loop.queue_depth
  - memory.current_bytes, memory.peak_bytes
  - database.status (currently "not_configured")
  - server_time



## Planets
- GET /player/{user_id}/planets
  Secured endpoint (Bearer token). Returns the list of planets owned by the authenticated user.
  Behavior:
  - When the database is enabled, returns all ORM-backed planets for the user.
  - When the database is disabled, returns the current ECS planet for the user.
  Example response:
  {
    "planets": [
      {
        "id": 1,                 # present when DB is enabled
        "name": "Homeworld",
        "galaxy": 1,
        "system": 1,
        "position": 1,
        "resources": {"metal": 500, "crystal": 300, "deuterium": 100},
        "temperature": 25,       # present when DB is enabled
        "size": 163,             # present when DB is enabled
        "last_update": "2025-08-30T18:34:00"  # present when DB is enabled
      }
    ]
  }

- POST /player/{user_id}/planets/{planet_id}/select
  Secured endpoint (Bearer token). Switches the active planet for the user to the specified planet_id.
  Notes:
  - Requires database layer to be enabled (IDs are DB-backed). Returns 400 if DB is disabled.
  - Returns 404 if the planet does not belong to the user or cannot be loaded.
  Example response:
  {
    "message": "Active planet switched",
    "planet_id": 2,
    "position": {"galaxy": 1, "system": 1, "planet": 2}
  }



## Battle Reports
- GET /player/{user_id}/battle-reports
  Secured (Bearer). Returns list of battle reports where the user is attacker or defender.
  Query params: limit (default 50, max 200), offset (default 0)
  Example response:
  {
    "reports": [
      {
        "id": 1,
        "created_at": "2025-08-30T20:45:00.000Z",
        "attacker_user_id": 1,
        "defender_user_id": 2,
        "location": {"galaxy": 1, "system": 1, "planet": 1},
        "outcome": {
          "winner": "attacker",
          "attacker_power": 100,
          "defender_power": 50,
          "attacker_remaining_power": 80,
          "defender_remaining_power": 0,
          "attacker_losses": {"light_fighter": 1},
          "defender_losses": {"light_fighter": 1},
          "attacker_remaining": {"light_fighter": 2},
          "defender_remaining": {}
        }
      }
    ]
  }

- GET /player/{user_id}/battle-reports/{report_id}
  Secured (Bearer). Returns a single battle report if the user is a participant; otherwise 404.



## Espionage
- Mission: "espionage" can be used with POST /player/{user_id}/fleet/dispatch to scout a target.
  - Body example:
  {
    "galaxy": 1,
    "system": 2,
    "position": 3,
    "mission": "espionage"
  }
  On arrival, the system generates a snapshot report of the target planet (if occupied) including basic planet info, resources, buildings, and fleet.

- GET /player/{user_id}/espionage-reports
  Secured (Bearer). Returns list of espionage reports where the user is attacker or defender.
  Query params: limit (default 50, max 200), offset (default 0)
  Example response:
  {
    "reports": [
      {
        "id": 1,
        "created_at": "2025-08-30T20:45:00.000Z",
        "attacker_user_id": 1,
        "defender_user_id": 2,
        "location": {"galaxy": 1, "system": 1, "planet": 1},
        "snapshot": {
          "planet": {"name": "Target", "temperature": 25, "size": 163},
          "resources": {"metal": 1000, "crystal": 500, "deuterium": 200},
          "buildings": {"metal_mine": 5, "crystal_mine": 4, "deuterium_synthesizer": 3, "solar_plant": 4, "robot_factory": 2, "shipyard": 1},
          "fleet": {"light_fighter": 3, "cruiser": 1}
        }
      }
    ]
  }

- GET /player/{user_id}/espionage-reports/{report_id}
  Secured (Bearer). Returns a single espionage report if the user is a participant; otherwise 404.



## Marketplace
- POST /trade/offers
  Body:
  {
    "offered_resource": "metal",
    "offered_amount": 100,
    "requested_resource": "crystal",
    "requested_amount": 50
  }
  Secured (Bearer, rate limited). Creates a trade offer and escrows the offered resources from the seller. Response returns the created offer.

- GET /trade/offers?status=open&limit=50&offset=0
  Public. Lists marketplace offers. status can be 'open', 'accepted', 'cancelled', or 'all' (for all statuses).
  Response:
  {"offers": [{"id": 1, "seller_user_id": 1, "offered_resource": "metal", "offered_amount": 100, "requested_resource": "crystal", "requested_amount": 50, "status": "open", "created_at": "..."}]}

- POST /trade/accept/{offer_id}
  Secured (Bearer, rate limited). Accepts an open offer by ID; transfers requested resources from buyer to seller and delivers the offered resources to buyer. Fails if self-trade, insufficient funds, or offer not open.
  Response: {"accepted": true, "offer_id": 1}

- GET /player/{user_id}/trade/history?limit=50&offset=0
  Secured (Bearer, rate limited). Returns newest-first trade events for the user. Events include:
  - type: "offer_created" or "trade_completed"
  - offer_id, seller_user_id, buyer_user_id (nullable for offer_created), resources and amounts, status, timestamp
  Response:
  {
    "events": [
      {
        "id": 1,
        "type": "offer_created",
        "offer_id": 3,
        "seller_user_id": 1,
        "buyer_user_id": null,
        "offered_resource": "metal",
        "offered_amount": 100,
        "requested_resource": "crystal",
        "requested_amount": 50,
        "status": "open",
        "timestamp": "2025-08-30T20:45:00.000Z"
      }
    ]
  }


## Notifications
- GET /player/{user_id}/notifications
  Secured (Bearer token). Returns recent notifications for the authenticated user.
  Query params:
  - limit: integer, default 50, min 1, max 200
  - offset: integer, default 0, min 0
  Behavior:
  - When the database is enabled, returns newest-first by created_at with stable IDs.
  - When the database is disabled, falls back to in-memory notifications (IDs may be null).
  Response:
  {
    "notifications": [
      {
        "id": 123,                    # may be null if in-memory fallback
        "user_id": 1,
        "type": "building_complete",
        "payload": {"building": "metal_mine", "level": 5},
        "priority": "normal",
        "created_at": "2025-08-30T20:19:00+00:00",
        "read_at": null
      }
    ]
  }

- DELETE /notifications/{id}
  Secured (Bearer token). Deletes a notification by ID if it belongs to the authenticated user.
  Notes:
  - Requires the database to be enabled; returns 404 if not found or DB is disabled.
  Response:
  {
    "deleted": true,
    "id": 123
  }
