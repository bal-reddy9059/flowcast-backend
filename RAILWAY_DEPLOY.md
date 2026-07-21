# Railway backend + Vercel frontend deployment

The backend contains Railway config-as-code and the frontend contains
`vercel.json`. Once both GitHub repositories are connected, every push to the
selected branch automatically deploys both applications.

## One-time Railway setup

1. Create a Railway project and add a PostgreSQL service.
2. Add a service named `flowcast-backend` from the
   `bal-reddy9059/flowcast-backend` GitHub repository.
3. Generate a public domain for `flowcast-backend`.

## Backend variables

Set these in the `flowcast-backend` service:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
SECRET_KEY=<generate-a-long-random-secret>
ADMIN_EMAIL=<production-admin-email>
ADMIN_PASSWORD=<generate-a-strong-password>
ADMIN_SETUP_SECRET=<generate-a-long-random-secret>
FRONTEND_URL=https://<your-vercel-domain>
API_REQUEST_TIMEOUT_SECONDS=2.8
REDIS_ENABLED=false
RATE_LIMIT_REDIS_ENABLED=false
REAL_DATA_ONLY=false
```

Optional integrations can be copied from the local environment into Railway's
sealed variables: `TOMTOM_API_KEY`, `HERE_API_KEY`,
`OPENWEATHERMAP_API_KEY`, `GOOGLE_MAPS_DIRECTIONS_API_KEY`, `ORS_API_KEY`,
`ANTHROPIC_API_KEY`, Google OAuth variables, and SMTP variables.

For Google OAuth, set:

```text
GOOGLE_REDIRECT_URI=https://${{flowcast-backend.RAILWAY_PUBLIC_DOMAIN}}/api/v1/auth/google/callback
```

## Vercel frontend setup

1. Import `bal-reddy9059/flowcast-frontend` into Vercel.
2. Keep the detected framework as Next.js.
3. Set these Production, Preview, and Development variables:

```text
BACKEND_URL=https://<your-backend-domain>.up.railway.app
NEXT_PUBLIC_API_URL=https://<your-backend-domain>.up.railway.app/api/v1
NEXT_PUBLIC_WS_URL=wss://<your-backend-domain>.up.railway.app
```

Redeploy after adding or changing `NEXT_PUBLIC_*` variables because Next.js
embeds them during its build. Then replace the Railway backend's `FRONTEND_URL`
with the generated Vercel production domain and redeploy the backend.

## Automatic deployments

In the Railway backend service Settings:

- Set the production branch to `dev` (the backend repository's current branch).
- Enable automatic deployments.
- Enable "Wait for CI" if GitHub branch checks are configured.

In Vercel, Git integration automatically deploys production from `main` and
creates a preview deployment for every pull request. After this one-time
connection, pushing to `main` deploys both applications automatically.
