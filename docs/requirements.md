# Ogame-like Game Server - Software Requirements Document

**Document Version**: 1.0  
**Date**: August 2025  
**Project**: Ogame-like Browser Game Server  
**Document Type**: Software Requirements Specification (SRS)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [Technical Requirements](#5-technical-requirements)
6. [API Specifications](#6-api-specifications)
7. [Database Requirements](#7-database-requirements)
8. [Security Requirements](#8-security-requirements)
9. [Performance Requirements](#9-performance-requirements)
10. [Testing Requirements](#10-testing-requirements)
11. [Deployment Requirements](#11-deployment-requirements)
12. [Acceptance Criteria](#12-acceptance-criteria)

---

## 1. Project Overview

### 1.1 Project Purpose
Develop a backend server for a browser-based space strategy game inspired by Ogame, featuring real-time resource management, building construction, fleet operations, and player vs player combat.

### 1.2 Project Scope
**In Scope:**
- RESTful API server with real-time game loop processing
- Entity-Component-System (ECS) architecture using Python Esper
- Multi-planet empire management system
- Resource production and building construction
- Fleet management and combat system
- Research and technology trees
- Player authentication and authorization
- Real-time notifications via WebSockets
- Database persistence and data management

**Out of Scope:**
- Frontend client application (to be developed separately as Nuxt.js app)
- Mobile applications
- Payment processing or monetization features
- Advanced graphics or 3D rendering
- Social media integrations

### 1.3 Target Users
- **Primary Users**: Strategy game players seeking complex resource management and PvP combat
- **Secondary Users**: Game developers studying ECS architecture implementation
- **System Administrators**: Managing game server infrastructure

---

## 2. System Architecture

### 2.1 Architecture Overview
The system shall implement a multi-threaded architecture with:
- **Main Thread**: HTTP API server handling client requests
- **Game Thread**: Continuous game loop processing ECS systems
- **Database Thread Pool**: Async database operations
- **WebSocket Thread**: Real-time client communication

### 2.2 Technology Stack
- **Backend Framework**: FastAPI (Python 3.9+)
- **ECS Framework**: Esper
- **Database**: PostgreSQL 13+
- **ORM**: SQLAlchemy 2.0+ with async support
- **Caching**: Redis 6+
- **Authentication**: JWT tokens with python-jose
- **WebSockets**: FastAPI WebSockets
- **Migration**: Alembic
- **Testing**: pytest with async support
- **Deployment**: Docker with docker-compose

### 2.3 Component Interaction
```
Client Request → FastAPI Router → Authentication → Business Logic → 
Command Queue → Game Loop → ECS Systems → Database → Response
```

---

## 3. Functional Requirements

### 3.1 User Management (UM)

**UM-001**: User Registration
- System shall allow users to register with unique username, email, and password
- Password must meet minimum security requirements (8+ chars, mixed case, numbers)
- System shall create initial home planet upon successful registration
- Email verification optional but recommended

**UM-002**: User Authentication  
- System shall authenticate users via username/email and password
- System shall issue JWT tokens with 24-hour expiration
- System shall support token refresh mechanism
- Failed login attempts shall be rate-limited (max 5 per minute)

**UM-003**: User Profile Management
- Users shall view and update profile information
- System shall track last login time and player statistics
- Users shall be able to change passwords with current password verification

### 3.2 Resource Management (RM)

**RM-001**: Resource Types
- System shall support three primary resources: Metal, Crystal, Deuterium
- Each planet shall have independent resource storage and production
- Resources shall be represented as integer values

**RM-002**: Resource Production
- Metal mines, crystal mines, and deuterium synthesizers shall produce resources automatically
- Production rates shall increase exponentially with building levels (formula: base_rate * 1.1^level)
- Production shall continue when players are offline
- System shall update resource counts every game loop tick (1 second intervals)

**RM-003**: Resource Storage
- Each planet shall have unlimited resource storage initially
- Future enhancement: storage buildings to increase capacity limits

### 3.3 Building System (BS)

**BS-001**: Building Types
- System shall support building types: metal_mine, crystal_mine, deuterium_synthesizer, solar_plant, robot_factory, shipyard, research_lab
- Each building type shall have specific functions and upgrade costs
- Building levels shall start at 0 and increase incrementally

**BS-002**: Building Construction
- Players shall queue building upgrades through API requests
- System shall validate resource availability before starting construction
- Building costs shall increase exponentially: cost = base_cost * 1.5^current_level
- Build times shall increase with level: time = base_time * 1.2^current_level
- Only one building shall construct at a time per planet

**BS-003**: Build Queue Management
- Each planet shall maintain a build queue with construction items
- Players shall view current build queue and estimated completion times
- Players shall be able to cancel construction with partial resource refund (50%)

**BS-004**: Building Prerequisites
- Certain buildings shall require prerequisite buildings at minimum levels
- Example: Shipyard requires Robot Factory level 2
- System shall enforce prerequisites before allowing construction

### 3.4 Research System (RS)

**RS-001**: Research Technologies
- System shall support research areas: Energy, Laser, Ion, Hyperspace, Plasma, Computer, Astrophysics
- Research levels shall be empire-wide (shared across all planets)
- Research shall be conducted at research labs

**RS-002**: Research Requirements
- Research shall require specific building levels and prerequisite technologies
- Research costs shall increase exponentially with level
- Only one research shall be active at a time per player

**RS-003**: Research Effects
- Technologies shall provide bonuses to production, build times, ship stats, or unlock new capabilities
- Example: Energy Technology increases energy production, unlocks new ship types

### 3.5 Fleet Management (FM)

**FM-001**: Ship Types
- System shall support ship types: Light Fighter, Heavy Fighter, Cruiser, Battleship, Bomber, Destroyer, Death Star
- Each ship type shall have specific stats: attack, defense, speed, cargo capacity
- Ship stats shall be modified by research levels

**FM-002**: Ship Construction
- Ships shall be built at shipyards
- Ship construction shall support build queues similar to buildings
- Multiple ships of same type can be built simultaneously
- Ship costs shall be fixed per unit regardless of quantity

**FM-003**: Fleet Operations
- Players shall organize ships into fleets for missions
- Fleets shall support missions: Attack, Transport, Spy, Colonize
- Fleet movements shall take time based on distance and ship speeds

### 3.6 Planet Management (PM)

**PM-001**: Planet Properties
- Each planet shall have properties: name, coordinates (galaxy, system, position), temperature, size
- Planet temperature shall affect deuterium production efficiency
- Planet size shall determine maximum building levels

**PM-002**: Multi-Planet System
- Players shall colonize additional planets using colony ships
- Maximum planet count shall be determined by Astrophysics research level
- Each planet shall have independent buildings, resources, and fleets

**PM-003**: Planet Coordinates
- Galaxy coordinates shall range: Galaxy 1-9, System 1-499, Position 1-15
- Position 1-3: Rocky planets, Position 4-6: Moderate planets, Position 7-15: Gas giants
- Coordinate system shall determine planet characteristics and distance calculations

### 3.7 Combat System (CS)

**CS-001**: Battle Mechanics
- Combat shall occur when attacking fleets reach target planets
- Battle resolution shall use Ogame-inspired damage calculation formulas
- Battles shall be resolved instantly upon fleet arrival

**CS-002**: Battle Reports
- System shall generate detailed battle reports for all participants
- Reports shall include: attacker/defender losses, resources stolen, debris created
- Battle reports shall be stored and accessible for 7 days

**CS-003**: Defense Systems
- Planets shall support defensive structures: Rocket Launcher, Light Laser, Heavy Laser, Ion Cannon
- Defensive structures shall participate in battles automatically
- Defenses shall have chance to be destroyed in battle

---

## 4. Non-Functional Requirements

### 4.1 Performance Requirements

**PERF-001**: Response Time
- API endpoints shall respond within 200ms for 95% of requests
- Database queries shall execute within 100ms average
- Game loop processing shall complete within 500ms per tick

**PERF-002**: Throughput
- System shall support 1000 concurrent active players
- API shall handle 10,000 requests per minute sustained load
- WebSocket connections shall support 5,000 concurrent users

**PERF-003**: Scalability
- System architecture shall support horizontal scaling
- Database shall support read replicas for improved performance
- Stateless API design to enable load balancer distribution

### 4.2 Reliability Requirements

**REL-001**: Availability
- System shall maintain 99.5% uptime during peak hours
- Planned maintenance windows shall not exceed 4 hours monthly
- System shall implement graceful degradation under high load

**REL-002**: Data Persistence
- All player data shall be persisted to database within 60 seconds of changes
- System shall implement database backup and recovery procedures
- Data corruption shall be prevented through transaction management

**REL-003**: Fault Tolerance
- Game loop shall continue operating despite individual system failures
- API shall return appropriate error messages for all failure scenarios
- System shall log all errors for debugging and monitoring

### 4.3 Usability Requirements

**USA-001**: API Design
- RESTful API shall follow consistent naming conventions
- API responses shall use standard HTTP status codes
- Comprehensive API documentation shall be provided

**USA-002**: Error Handling
- All error messages shall be clear and actionable
- Input validation errors shall specify exact validation failures
- System shall provide helpful error messages for common mistakes

---

## 5. Technical Requirements

### 5.1 Development Environment
- Python 3.9 or higher required
- Virtual environment for dependency isolation
- Git version control with branching strategy
- Automated code formatting with Black and linting with flake8

### 5.2 Code Quality Standards
- Minimum 90% test coverage for all business logic
- Type hints required for all function signatures
- Docstrings required for all public classes and methods
- Code review required for all changes

### 5.3 Dependency Management
- All dependencies specified in requirements.txt with version pinning
- Regular security updates for dependencies
- Minimal dependency footprint to reduce attack surface

---

## 6. API Specifications

### 6.1 Authentication Endpoints
```
POST /auth/register - User registration
POST /auth/login - User login
POST /auth/logout - User logout
GET /auth/me - Current user info
POST /auth/refresh - Token refresh
```

### 6.2 Player Management Endpoints
```
GET /player/{user_id} - Get complete player state
GET /player/{user_id}/planets - List all owned planets
GET /player/{user_id}/notifications - Get notifications
DELETE /notifications/{notification_id} - Mark notification as read
```

### 6.3 Building Management Endpoints
```
GET /player/{user_id}/buildings - Get building levels
POST /player/{user_id}/build - Queue building construction
DELETE /player/{user_id}/build-queue/{index} - Cancel construction
GET /building-costs/{building_type} - Get building costs and times
```

### 6.4 Fleet Management Endpoints
```
GET /player/{user_id}/fleet - Get fleet composition
POST /player/{user_id}/build-ships - Build ships
POST /player/{user_id}/fleet/dispatch - Send fleet on mission
POST /player/{user_id}/fleet/{fleet_id}/recall - Recall fleet
GET /player/{user_id}/fleet/missions - List active missions
```

### 6.5 Research Endpoints
```
GET /player/{user_id}/research - Get research levels
POST /player/{user_id}/research - Start research
GET /research-costs/{research_type} - Get research requirements
```

### 6.6 Combat Endpoints
```
GET /player/{user_id}/battle-reports - Get battle reports
GET /battle-reports/{report_id} - Get specific battle report
```

---

## 7. Database Requirements

### 7.1 Database Schema
**Users Table**: user_id, username, email, password_hash, created_at, last_login
**Planets Table**: planet_id, user_id, name, galaxy, system, position, temperature, size
**Buildings Table**: planet_id, building_type, level
**Research Table**: user_id, research_type, level
**Fleets Table**: fleet_id, user_id, planet_id, ship_counts, status, mission_data
**Battle_Reports Table**: report_id, attacker_id, defender_id, battle_data, created_at

### 7.2 Database Performance
- Primary keys on all tables with auto-increment
- Indexes on frequently queried columns (user_id, coordinates, timestamps)
- Foreign key constraints for data integrity
- Database connection pooling with max 20 connections

### 7.3 Data Migration
- Alembic migration scripts for schema changes
- Backward-compatible migrations when possible
- Database seeding scripts for initial data and testing

---

## 8. Security Requirements

### 8.1 Authentication Security
- Passwords hashed using bcrypt with minimum 12 salt rounds
- JWT tokens signed with secure secret key (min 256-bit)
- Token blacklisting for logout functionality
- Rate limiting on authentication endpoints

### 8.2 Input Validation
- All user inputs validated and sanitized
- SQL injection prevention through parameterized queries
- XSS prevention through input escaping
- File upload restrictions and validation

### 8.3 API Security
- CORS properly configured for production domains
- HTTPS enforcement in production
- Security headers: HSTS, CSP, X-Frame-Options
- API rate limiting per user and endpoint

---

## 9. Performance Requirements

### 9.1 Response Time Targets
- Authentication: < 100ms
- Player data retrieval: < 200ms
- Building/research operations: < 300ms
- Fleet operations: < 500ms
- Battle resolution: < 1000ms

### 9.2 Caching Strategy
- Redis caching for frequently accessed player data (5-minute TTL)
- Database query result caching for static data
- Session caching for authenticated users
- Cache invalidation on data updates

### 9.3 Database Optimization
- Query optimization with proper indexes
- Connection pooling and async operations
- Read replicas for GET operations
- Database query monitoring and slow query logging

---

## 10. Testing Requirements

### 10.1 Unit Testing
- All business logic components tested with minimum 90% coverage
- ECS systems tested with mock data and assertions
- Calculation functions tested with edge cases and boundary values
- Database models tested with fixture data

### 10.2 Integration Testing
- API endpoints tested with full request/response cycles
- Database integration tested with real database connections
- Game loop integration tested with threading and timing
- WebSocket functionality tested with mock clients

### 10.3 Performance Testing
- Load testing with 1000+ concurrent simulated players
- Stress testing to determine system breaking points
- Memory leak testing for long-running processes
- Database performance testing under high load

### 10.4 Test Environment
- Automated testing pipeline with GitHub Actions
- Test database separate from development and production
- Test fixtures and factory methods for consistent test data
- Continuous integration with automated test runs

---

## 11. Deployment Requirements

### 11.1 Containerization
- Docker containers for application and database
- Multi-stage Docker builds for optimized image sizes
- Docker Compose for local development environment
- Container health checks and restart policies

### 11.2 Production Environment
- Production deployment on cloud infrastructure (AWS/GCP/Azure)
- Environment-specific configuration management
- Database backup and disaster recovery procedures
- Monitoring and alerting for system health

### 11.3 CI/CD Pipeline
- Automated testing on code commits
- Automated deployment to staging environment
- Manual approval for production deployments
- Rollback procedures for failed deployments

---

## 12. Acceptance Criteria

### 12.1 Functional Acceptance
- [ ] Users can register, login, and manage accounts
- [ ] Players can build and upgrade buildings with proper cost calculation
- [ ] Resource production works automatically and accurately
- [ ] Research system unlocks technologies and provides bonuses  
- [ ] Fleet system allows ship building and mission dispatch
- [ ] Combat system resolves battles and generates reports
- [ ] Multi-planet system supports colonization and management
- [ ] Real-time updates delivered via WebSockets

### 12.2 Performance Acceptance
- [ ] API response times meet specified targets (95th percentile)
- [ ] System supports 1000 concurrent users without degradation
- [ ] Database queries execute within performance thresholds
- [ ] Game loop maintains 1-second tick rate under full load

### 12.3 Security Acceptance
- [ ] Authentication system prevents unauthorized access
- [ ] Input validation prevents injection attacks
- [ ] API rate limiting prevents abuse
- [ ] Data encryption in transit and at rest

### 12.4 Quality Acceptance
- [ ] Test coverage exceeds 90% for all business logic
- [ ] Code review process ensures quality standards
- [ ] Documentation complete for all APIs and systems
- [ ] Error handling provides clear, actionable messages

---

## Document Approval

**Prepared by**: Development Team  
**Reviewed by**: Project Stakeholders  
**Approved by**: Product Owner  

**Version History**:
- v1.0 - Initial requirements document - August 2025

---

*This document serves as the definitive requirements specification for the Ogame-like game server project. All development work should align with these requirements, and any changes must be approved through the formal change control process.*