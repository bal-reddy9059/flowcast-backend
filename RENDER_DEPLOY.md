# Render backend + Vercel frontend

## Deploy the backend

1. Open the Render Dashboard and select **New > Blueprint**.
2. Connect `bal-reddy9059/flowcast-backend`.
3. Select the `dev` branch and apply the root `render.yaml`.
4. When prompted, provide:

```text
ADMIN_EMAIL=<production-admin-email>
ADMIN_PASSWORD=<strong-production-password>
FRONTEND_URL=https://<your-vercel-project>.vercel.app
```

The Blueprint creates:

- A Singapore-region Render web service.
- A PostgreSQL database linked through `DATABASE_URL`.
- Generated JWT and admin-setup secrets.
- Automatic deployment on every push to `dev`.
- A `/health` deployment health check.

The Docker build excludes the local Windows virtual environment and copies only
the UTF-8 `requirements-render.txt` manifest, `app/`, and `run.py`.

If creating a native Python service instead of using the Blueprint, use:

```text
Build Command: pip install -r requirements-render.txt
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
Health Check Path: /health
Branch: dev
```

## Optional backend variables

Add these in the Render service's **Environment** page only when used:

```text
TOMTOM_API_KEY=
HERE_API_KEY=
OPENWEATHERMAP_API_KEY=
GOOGLE_MAPS_DIRECTIONS_API_KEY=
ORS_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://<your-render-service>.onrender.com/api/v1/auth/google/callback
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM_NAME=FlowCast Alerts
```

Set `AI_ENABLED=true` only when `ANTHROPIC_API_KEY` is configured.

## Configure Vercel

Set these variables in the Vercel frontend project:

```text
BACKEND_URL=https://<your-render-service>.onrender.com
NEXT_PUBLIC_API_URL=https://<your-render-service>.onrender.com/api/v1
NEXT_PUBLIC_WS_URL=wss://<your-render-service>.onrender.com
```

Redeploy Vercel after changing `NEXT_PUBLIC_*` variables because Next.js embeds
them during the production build.
