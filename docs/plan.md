# Project Improvement Plan

Document version: 1.0
Date: 2025-08-29

This plan synthesizes the key goals and constraints from docs/requirements.md (SRS) and docs/tasks.md (Roadmap/TODO) into a coherent, phased improvement program. It focuses on pragmatic, low‑risk steps that evolve the current monolithic FastAPI + ECS prototype into a robust, testable, and deployable service.


## 1) Guiding Principles and Constraints

- Scope and Vision
  - Build a backend for an Ogame‑like browser strategy game with ECS processing, multi‑planet management, buildings, fleets, research, and PvP combat.
  - Deliver a REST API first; WebSockets are for real‑time updates but can be phased in.
- Architecture
  - FastAPI HTTP server + background game loop thread (~1 Hz) driving ECS (esper).
  - Modular src/ layout; src.main:app is the entry point for ASGI servers.
- Data & Persistence
  - Target PostgreSQL via SQLAlchemy (async) and Alembic migrations; Redis for caching.
  - ECS components must sync with database models; design for consistency and atomicity.
- Security
  - JWT auth, rate limiting, input validation, CORS policy, and secure headers.
- Performance & Operations
  - Structured logging, metrics, health checks, load testing; Dockerized deployment with CI/CD.
- Testing
  - Multi‑layer tests (unit, integration, loop/threading) with pytest/httpx; enable zero‑dep smoke tests when needed.
- Known Pitfalls
  - esper world initialization (use esper.World()).
  - Global GameWorld state complicates parallel tests; provide factory/test hooks.


## 2) Phased Roadmap (Milestones)

- Milestone A: Infrastructure & Testing Foundations (High Priority)
  - Establish project structure, requirements.txt, pytest setup, and core smoke/unit tests.
  - Rationale: Enables safe iteration, CI, and confidence in behavior.
- Milestone B: Authentication & Basic Persistence (High Priority)
  - Implement JWT auth and introduce database models + migrations; wire minimal persistence flows.
  - Rationale: Security and persistence are prerequisites for multi‑user gameplay and durability.
- Milestone C: Core Game Systems Expansion (Medium Priority)
  - Enhance building, introduce research scaffolding, and prepare fleet basics.
  - Rationale: Deliver core gameplay loops while keeping systems testable.
- Milestone D: Real‑time & Notifications (Medium Priority)
  - Add WebSockets for live updates; offline notification storage.
  - Rationale: Improve UX responsiveness without blocking core delivery.
- Milestone E: Performance, Monitoring, and Deployment (Low→High as needed)
  - Caching, metrics, load testing, Docker, CI/CD, and security hardening.
  - Rationale: Production readiness and operability.
- Milestone F: Advanced Features (Low Priority)
  - Fleet movement, combat resolution, marketplace, and chaos testing.
  - Rationale: Build upon a stable foundation.


## 3) Architecture & Code Organization

- Target Layout
  - src/api/: FastAPI routers, dependency injection, auth routes, WebSocket handlers.
  - src/core/: game world lifecycle, loop control, config, database session mgmt.
  - src/models/: SQLAlchemy schemas; ECS component dataclasses (if retained separate from DB models).
  - src/systems/: ECS processors (ResourceProductionSystem, BuildingConstructionSystem, etc.).
  - migrations/: Alembic scripts.
  - tests/: unit and integration tests.
  - docs/: documentation.
- Migration Strategy
  - Step 1: Introduce src/ layout and expose the ASGI app at src.main:app to avoid breaking run commands.
  - Step 2: Extract ECS systems and components into src/systems and src/models.ecs; keep behavior identical.
  - Step 3: Introduce src/core/config.py and src/core/database.py; src.main imports them, preserving API compatibility.
  - Rationale: Small, reversible steps reduce risk and simplify review/testing.


## 4) Game Loop, Concurrency, and Reliability

- Loop Ownership
  - Start loop on FastAPI startup event; stop on shutdown (already in place). Ensure idempotent start/stop.
- Thread Safety
  - All HTTP commands enqueue to a thread‑safe Queue; loop dequeues and applies.
  - Define Command DTOs with explicit, minimal payloads; validate before enqueuing.
- Tick Rate & Scheduling
  - Keep 1 Hz default; centralize as config. Use monotonic time (time.monotonic) for durations.
- Error Isolation
  - Wrap per‑tick system processing in try/except; emit structured logs and metrics, continue loop.
- Testing Hooks
  - Expose start_game_loop/stop_game_loop overrides for tests; allow single‑tick processing for deterministic unit tests.
- Rationale: Prevents deadlocks and flakiness, improves observability, and enables deterministic testing.


## 5) Data Model & Persistence Plan

- SQLAlchemy (async) Entities (initial)
  - User(id, username, email, password_hash, created_at, last_login)
  - Planet(id, owner_id→User, name, galaxy, system, position, created_at)
  - Building(id, planet_id→Planet, type, level)
  - Research(id, user_id→User, type, level)
  - Fleet(id, user_id→User, planet_id→Planet nullable for in‑flight, composition JSON, speed, cargo)
  - Notification(id, user_id→User, type, payload JSON, priority, created_at, read_at)
- ECS <-> DB Sync Strategy
  - On login/load: hydrate ECS world for the user from DB.
  - Periodic persistence: save player changes every N seconds; transactional updates for build completion and resource deductions.
  - Use versioning/updated_at to detect conflicts; prefer loop‑owned mutations.
- Migrations
  - Alembic baseline, then incremental scripts as models evolve.
- Rationale: Durable state, integrity across systems, and room for scaling.


## 6) Authentication & Security

- Endpoints
  - POST /auth/register: create user, initial planet.
  - POST /auth/login: JWT issuance (24h), refresh route optional initially.
  - GET /auth/me: identity probe; later blacklist/refresh if needed.
- Protection
  - JWT dependency securing /player/* routes; extract user_id from token.
  - Rate limiting (basic: in‑memory per‑IP; target: Redis token bucket).
  - Input validation via Pydantic models; CORS tightened per environment.
  - Security headers via middleware (HSTS, CSP as feasible, X‑Frame‑Options).
- Passwords
  - Hash with bcrypt (passlib); enforce password policy.
- Rationale: Meet SRS security requirements early and prevent misuse.


## 7) Core Gameplay Systems (Near‑Term)

- Buildings
  - Prerequisites graph, cost and time scaling per level, queue management (cancel with partial refund), demolition endpoint.
  - Energy model: production/consumption; block actions when energy deficit (or degrade efficiency).
- Resources
  - Production = base_rate * 1.1^level * elapsed; respect energy and research bonuses.
  - Storage caps; overflow behavior; periodic production via loop.
- Research
  - Component + processor; prerequisites; effects on production/build times and fleets.
- Fleets (scaffolding)
  - Shipyard queue, basic composition validation, endpoints to list/build ships.
- Rationale: Delivers a functional core loop aligned with tasks and SRS.


## 8) Real‑time & Notifications

- WebSockets
  - /ws endpoint; manage connection registry keyed by user_id.
  - Events: resource updates, build/research completion, incoming attacks.
- Offline Notifications
  - Store events for offline users; provide GET/DELETE endpoints; optional email for critical.
- Rationale: Improves responsiveness and retention without blocking core delivery.


## 9) API Design & Documentation

- FastAPI routers by domain: /player, /buildings, /research, /fleet, /auth, /trade.
- Consistent request/response schemas with Pydantic models; include error models.
- Versioning strategy: prefix /v1; avoid breaking changes.
- Documentation: auto‑generated OpenAPI + docs/API.md with examples.
- Rationale: Clear, stable API surface for client teams.


## 10) Testing Strategy

- Unit Tests
  - Components, systems, calculations (costs, times, production). No external deps.
- Integration Tests
  - API endpoints with TestClient/httpx; startup/shutdown lifecycle; command queue behavior.
- Concurrency/Loop Tests
  - Multi‑request scenarios; ensure thread safety and correct ordering.
- Fixtures & Config
  - Test GameWorld factory; monkeypatch loop to deterministic mode; in‑memory SQLite for DB tests.
- CI
  - GitHub Actions to run lint/test; cache deps; coverage reporting.
- Rationale: Prevent regressions and allow safe refactors.


## 11) Performance, Monitoring, and Ops

- Caching
  - Redis for hot player/planet reads; 5‑minute TTL; cache‑aside with invalidation on writes.
- Metrics & Health
  - Expose /healthz and /status endpoints (DB, loop, queue depth, tick time).
  - Collect request latency, loop tick duration, system errors; ship to logs or Prometheus later.
- Load & Resilience
  - Locust scenarios for 1000+ concurrent players; chaos tests for DB/Redis outages.
- Deployment
  - Dockerfile (multi‑stage), docker‑compose for local dev (app+db+redis).
  - Environment‑specific config; secrets via env vars.
- Rationale: Production readiness and operability per SRS.


## 12) Risk Register and Mitigations

- Global State Coupling
  - Risk: Tests and scaling hindered by single global GameWorld.
  - Mitigation: Introduce world factory; dependency‑inject into routers; allow per‑test instances.
- ECS/DB Divergence
  - Risk: Inconsistent state between in‑memory ECS and DB.
  - Mitigation: Loop‑owned mutations; transactional boundaries; periodic sync; clear ownership.
- Threading Bugs
  - Risk: Race conditions and deadlocks.
  - Mitigation: Single writer pattern (loop), immutable command payloads, exhaustive tests.
- Feature Creep
  - Risk: Advanced features delay core delivery.
  - Mitigation: Phase gates; do not advance milestones without green tests.
- Security Gaps
  - Risk: Unauthorized access, weak passwords, missing headers.
  - Mitigation: JWT middleware, validators, passlib, rate limiting, security middleware.


## 13) Immediate Action Items (Next Sprint)

1. Create requirements.txt; add minimal deps: fastapi, uvicorn, esper, pytest, httpx.  
2. Introduce src/ layout; move ECS systems/components into src/systems and src/models.ecs; expose app in src.main.  
3. Add tests: components, systems (production and build completion), and a basic API smoke test using TestClient.  
4. Add src/core/config.py for tick rate and base costs; replace literals in systems.  
5. Add GitHub Actions workflow for pytest.  
6. Add README.md and docs/API.md skeleton with current endpoints.  

Success Criteria: tests pass locally and in CI; app runnable via `uvicorn src.main:app --reload`; no behavioral regressions.
