# Server-side API integrations

Gemini, PayMongo, and Polygon API requests pass through Django. All local configuration is in `backend-main/.env`, including the public frontend API origin and development proxy target. There is no separate frontend `.env`. Use `backend-main/.env.example` as a reference, without overwriting your existing database and email settings. Frontend start, build, and test commands load only `REACT_APP_API_BASE_URL` and `BACKEND_PROXY_TARGET` from the shared file; provider credentials stay on the server. Exported environment variables take precedence over the file.

## Configuration

- `GEMINI_API_KEY`, `GEMINI_MODEL`: Gemini credential and model available to that account. The chatbot's shelter knowledge and instructions are on the backend, in `chatbot/knowledge_base.txt` and active `KnowledgeItem` records.
- `PAYMONGO_TEST_KEY`, `PAYMONGO_LIVE_KEY`: secret keys beginning with `sk_test_` and `sk_live_`. `PAYMONGO_MODE` controls public totals and checkout, and defaults to test unless the existing legacy `PAYMONGO_SECRET_KEY` is live. Staff can select either configured mode when viewing payments. Clients cannot override checkout mode. The legacy secret setting remains supported as a fallback for its matching mode.
- `PAYMONGO_PAYMENT_METHODS`: comma-separated methods enabled on your PayMongo account.
- `FRONTEND_BASE_URL`, `FRONTEND_SUCCESS_URL`, `FRONTEND_CANCEL_URL`: frontend origin and checkout return pages. Set these to the deployed HTTPS site. Return pages are navigation destinations, not proof of payment; paid totals come from PayMongo.
- `POLYGONSCAN_API_KEY`: an **Etherscan V2** key with access to the selected Polygon chain. PolygonScan V1 is retired; moving an old V1 key does not convert it. `POLYGONSCAN_CHAIN_ID=80002` selects Amoy (the existing ledger network); `137` selects Polygon mainnet. Keep this consistent with the ledger's RPC and explorer links.

References: [Gemini generateContent](https://ai.google.dev/api/generate-content), [PayMongo payments](https://docs.paymongo.com/reference/list-all-payments), [Etherscan V2 migration](https://info.etherscan.com/etherscan-api-v1-will-be-fully-deprecated-by-15th-august-2025/).

## Routes

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /api/gemini/chat/` | Public, throttled | `{message, history: [{role, text}]}`; returns `{text, truncated}`. Legacy `/chat_api/chat/` also works. |
| `GET /api/paymongo/status/` | Public, throttled | Selected checkout mode and boolean availability of test/live mode. No credentials. |
| `GET /api/paymongo/payments/?mode=test&limit=100&after=pay_...` | Admin JWT | Payment records and `has_more`; both admin pages follow pagination. |
| `GET /api/paymongo/summary/` | Public, throttled | PHP paid-payment total and count only; excludes donor details. Cached for five minutes. Pagination failures return an error, never a partial total. |
| `POST /api/paymongo/checkout/` | Public, throttled | `{amount, donor_name, donor_email}`; validates minimum PHP100 and returns hosted checkout URL. No automatic retry of checkout creation. |
| `GET /api/polygonscan/transactions/<hash>/` | Public, throttled | Confirmed/pending/failed receipt status. Fixed upstream endpoint and chain; no arbitrary URL forwarding. |

Polygon verification checks transaction receipts alongside existing local ledger links. It does not prove the local record payload equals on-chain transaction input. Explorer links remain ordinary external navigation.

## Development and deployment

1. Restart Django after changing its `.env`.
2. Restart `npm start` after changing `backend-main/.env`. Set `BACKEND_PROXY_TARGET` there to change the development backend address (default `http://127.0.0.1:8000`). The development proxy forwards `/api`, `/admin_api`, `/chat_api`, and `/media` to Django.
3. For production on one origin, route these paths to Django through the hosting reverse proxy and serve React for frontend paths. CRA's `setupProxy.js` does **not** run in a production build.
4. For separate origins, set the public `REACT_APP_API_BASE_URL=https://api.your-domain.example` in `backend-main/.env` before running `npm run build` in `frontend-master`. Set Django `CORS_ALLOWED_ORIGINS` to the exact frontend origins (no paths/trailing slash) and `ALLOWED_HOSTS` to the backend host. CORS defaults to the explicit allowlist. The integration pages and shared Axios client use this public base URL.
5. Configure a shared Django cache and reverse-proxy rate limits for a multi-worker deployment. Django's default local-memory cache/throttles are per process. The public summary refresh is bounded to 50 pages and a time budget; very large payment histories should use a persisted, periodically refreshed aggregate.

Never deploy the backend `.env` in the React static directory. The four old `REACT_APP_*` provider variables were removed, and the frontend Google SDK dependencies were removed. Rebuild frontend assets after removing old hosting environment variables as well.

## Verification

`python manage.py test backend.test_integrations --settings=backend.test_settings`

Tests use an isolated SQLite database and mocked provider responses. They cover staff authorization, public-summary privacy and pagination, checkout validation and server-controlled mode, sanitized errors, chatbot limits, and Polygon receipts. No live checkout or billable provider call is needed.
