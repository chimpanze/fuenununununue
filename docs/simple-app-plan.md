# Simple Vue.js Client Plan for the Game

Date: 2025-09-03

This document proposes a minimal-yet-complete plan for building a Vue 3 app to play the game, based on the current API described by the regenerated OpenAPI spec (openapi.yaml) and src/api/routes.py.

Notes:
- The repository already exposes a comprehensive REST API plus WebSocket updates. Auth uses OAuth2 Bearer with JWT issued by the backend.
- For development, target local backend at http://localhost:8000 and WS at ws://localhost:8000.

## 1) Tech Stack

- Vue 3 + Vite
- Pinia (state management)
- Vue Router (pages)
- Axios (HTTP client) or native fetch (either works; below uses Axios)
- TypeScript recommended but optional for MVP
- Tailwind CSS (optional) for quick UI; otherwise simple CSS

## 2) Environment & Configuration

- VITE_API_BASE_URL: default http://localhost:8000
- VITE_WS_BASE_URL: default ws://localhost:8000
- Rate limit safe usage; keep polling minimal and lean on WebSocket push messages where available.

Example .env.development:
```
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
```

## 3) Authentication Flow

Endpoints (from src/api/auth.py):
- POST /auth/register {username, password}
- POST /auth/login {username, password} -> {access_token, token_type}
- GET /auth/me -> current user profile
- POST /auth/logout (token blacklist)

Client strategy:
- On login/register success, store access_token in memory (Pinia) and localStorage for session restore.
- Attach Authorization: Bearer <token> to all API requests with an Axios interceptor.
- On 401/403, clear token and redirect to /login.

## 4) Core Game Flows & Endpoints (MVP)

Player:
- GET /player/{user_id}
- GET /player/{user_id}/planets
- POST /player/{user_id}/active-planet/{planet_id}
- POST /player/{user_id}/start {galaxy, system, position} (choose start location)

Buildings:
- GET /building-costs/{building_type}?level=<n>
- POST /player/{user_id}/build {building_type}
- POST /player/{user_id}/demolish/{building_type}
- POST /player/{user_id}/build/cancel/{index}

Research:
- GET /player/{user_id}/research
- POST /player/{user_id}/research {research_type}

Fleet:
- GET /player/{user_id}/fleet
- POST /player/{user_id}/ships {ship_type, count}
- POST /player/{user_id}/dispatch {from: {g,s,p}, to: {g,s,p}, ships: {type: count}, mission}
- POST /player/{user_id}/recall/{fleet_id}

Discovery & world info:
- GET /planets/available?galaxy=&system=&limit=&offset=

Notifications and reports (phase 2+):
- GET /player/{user_id}/notifications?limit&offset
- DELETE /notifications/{notification_id}
- GET /player/{user_id}/battle-reports
- GET /player/{user_id}/battle-reports/{report_id}
- GET /player/{user_id}/espionage-reports
- GET /player/{user_id}/espionage-reports/{report_id}

Status & health (dev tools):
- GET /game-status
- GET /healthz, /healthz/db
- GET /metrics

WebSocket:
- GET /ws (then send {type: "auth", user_id}) per current implementation; server-side will use ConnectionManager to route updates. Use after HTTP auth so you know user_id.

## 5) App Structure

- src/
  - main.ts
  - router/index.ts
  - store/
    - auth.ts (token, user)
    - player.ts (player profile, planets, active planet)
    - game.ts (resources, buildings, queues, research, fleets)
    - notifications.ts (list, unread count)
  - api/
    - http.ts (Axios instance with baseURL and interceptors)
    - endpoints.ts (typed wrappers for API calls)
    - ws.ts (WebSocket connection helper)
  - views/
    - LoginView.vue
    - RegisterView.vue
    - StartView.vue (choose start location from /planets/available, POST /player/{uid}/start)
    - DashboardView.vue (overview of resources, queues, quick actions)
    - BuildView.vue (list building levels, costs, queue building)
    - ShipyardView.vue (queue ship builds)
    - FleetView.vue (list fleets, dispatch form, recall)
    - ResearchView.vue (list research and start)
    - NotificationsView.vue
  - components/
    - ResourceBar.vue (metal/crystal/deuterium/energy + tick)
    - PlanetSelector.vue
    - BuildingCard.vue
    - ShipSelector.vue
    - ResearchCard.vue
    - NotificationsList.vue

Routing:
- Public: /login, /register
- Protected: /start, /dashboard, /build, /shipyard, /fleet, /research, /notifications
- Guard: if no token -> redirect to /login; if token but no active planet -> go to /start

## 6) API Client Sketch (Axios)

http.ts:
```ts
import axios from 'axios';

const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL });

http.interceptors.request.use(cfg => {
  const token = localStorage.getItem('token');
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

http.interceptors.response.use(r => r, err => {
  if (err.response && [401, 403].includes(err.response.status)) {
    localStorage.removeItem('token');
    window.location.href = '/login';
  }
  return Promise.reject(err);
});

export default http;
```

endpoints.ts (examples):
```ts
import http from './http';

export const Auth = {
  login: (username: string, password: string) => http.post('/auth/login', { username, password }),
  register: (username: string, password: string) => http.post('/auth/register', { username, password }),
  me: () => http.get('/auth/me'),
  logout: () => http.post('/auth/logout'),
};

export const Player = {
  get: (userId: number) => http.get(`/player/${userId}`),
  planets: (userId: number) => http.get(`/player/${userId}/planets`),
  selectActive: (userId: number, planetId: number) => http.post(`/player/${userId}/active-planet/${planetId}`),
  start: (userId: number, payload: any) => http.post(`/player/${userId}/start`, payload),
};

export const Build = {
  costs: (type: string, level = 0) => http.get(`/building-costs/${type}`, { params: { level } }),
  queue: (userId: number, building_type: string) => http.post(`/player/${userId}/build`, { building_type }),
  demolish: (userId: number, building_type: string) => http.post(`/player/${userId}/demolish/${building_type}`),
  cancel: (userId: number, index: number) => http.post(`/player/${userId}/build/cancel/${index}`),
};

export const Research = {
  get: (userId: number) => http.get(`/player/${userId}/research`),
  start: (userId: number, research_type: string) => http.post(`/player/${userId}/research`, { research_type }),
};

export const Fleet = {
  get: (userId: number) => http.get(`/player/${userId}/fleet`),
  buildShips: (userId: number, ship_type: string, count: number) => http.post(`/player/${userId}/ships`, { ship_type, count }),
  dispatch: (userId: number, payload: any) => http.post(`/player/${userId}/dispatch`, payload),
  recall: (userId: number, fleetId: number) => http.post(`/player/${userId}/recall/${fleetId}`),
};

export const World = {
  availablePlanets: (params: { galaxy?: number; system?: number; limit?: number; offset?: number }) =>
    http.get('/planets/available', { params }),
};

export const Notifications = {
  list: (userId: number, params: { limit?: number; offset?: number } = {}) =>
    http.get(`/player/${userId}/notifications`, { params }),
  del: (notificationId: number) => http.delete(`/notifications/${notificationId}`),
};
```

## 7) State Management (Pinia)

Stores:
- auth: token, user (id/username); actions: login, register, restoreFromStorage, logout, fetchMe.
- player: userId, planets, activePlanetId; actions: loadPlayer, loadPlanets, selectActivePlanet, chooseStart.
- game: buildings (levels, queue), resources, production, research, fleets; actions: refresh (GET /player/{uid}), queueBuild, startResearch, buildShips, dispatchFleet.
- notifications: items, unread; actions: fetch, remove.

On app start:
1) auth.restoreFromStorage(); if token -> Auth.me() to confirm; set userId; connect WebSocket.
2) player.loadPlayer(); if no planets or no active planet -> route to /start, else /dashboard.

## 8) WebSocket Handling

- Connect to `${VITE_WS_BASE_URL}/ws` after authentication.
- Immediately send an auth message with user_id if required by server (consult server’s ws handler; typically authenticate via Bearer token if supported—current code routes per user_id parameter, so include user_id in initial message if needed).
- Handle incoming messages and dispatch to stores:
  - build queue updates
  - research progress
  - fleet status updates
  - resource tick updates
  - notifications
- Reconnect with backoff on disconnect.

ws.ts sketch:
```ts
export function connectWS(userId: number, onMessage: (msg: any) => void) {
  const url = `${import.meta.env.VITE_WS_BASE_URL}/ws`;
  let ws = new WebSocket(url);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'auth', user_id: userId }));
  };
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => setTimeout(() => connectWS(userId, onMessage), 2000);
  return ws;
}
```

## 9) Screens & UX (MVP)

- Login: username/password -> token; link to Register.
- Register: create account; on success, auto-login or redirect to Login.
- Start (first-time): browse available planets by galaxy/system, choose one; call POST /player/{uid}/start; redirect to Dashboard.
- Dashboard: shows current planet (selector), resources (live tick), building queue, research queue, shipyard queue; quick actions to enqueue/build.
- Build: list key buildings, show current level and next cost, queue upgrade if affordable; surface server errors.
- Shipyard: show ship types and allow queueing builds.
- Fleet: list fleets (en-route/home), dispatch form with coordinates and selected ships; recall button where applicable.
- Research: list available researches (respect prerequisites if provided by API), start research.
- Notifications: list with pagination; delete dismisses.

## 10) Milestones

M1 — Bootstrap & Auth (0.5–1 day)
- Vite + Vue + Router + Pinia setup
- Auth store + login/register/me flows

M2 — Player & Start (0.5 day)
- Player/planets loading
- Start screen to select starting planet

M3 — Dashboard & Build (1 day)
- Resource bar with periodic refresh and/or WS updates
- Buildings list with costs and queueing

M4 — Fleet & Shipyard (1 day)
- Fleet list, dispatch, recall
- Ship build queue

M5 — Research & Notifications (0.5–1 day)
- Research screen
- Notifications list + delete

M6 — WebSocket polish & Error handling (0.5 day)
- Robust WS reconnect
- Toasts for server events

## 11) Developer Workflow

- npm create vite@latest game-ui -- --template vue
- cd game-ui && npm i && npm i axios pinia vue-router
- (optional) npm i -D tailwindcss postcss autoprefixer && npx tailwindcss init -p
- Add .env.development with API/WS base URLs
- Implement api/, store/, views/ per above
- Run: npm run dev

## 12) Optional Enhancements

- Generate a typed API client from openapi.yaml using openapi-typescript or openapi-generator (typescript-axios) to reduce mistakes
- Persist settings (e.g., default galaxy/system)
- Dark mode and responsive layout
- E2E tests with Playwright and unit tests with Vitest

---
This plan focuses on a thin client on top of the existing server. It should enable a playable loop quickly while leaving room for future refinement.
