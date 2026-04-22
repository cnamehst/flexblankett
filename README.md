# Flexblankett

Web application for tracking flexible working hours (flextid). Supports multiple employees, monthly/yearly summaries, special statuses (vacation, sick leave, parental care), per-day norm overrides for shortened days, CSV import, and a REST API with per-user API keys.

Built with Python/Flask, MariaDB, Bootstrap 5. Designed to run on Kubernetes. Supports local password authentication and IPA/LDAP single sign-on.

---

## Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12, Flask 3.0, SQLAlchemy |
| Database | MariaDB 11 (or MySQL 8+) |
| Web server | Gunicorn (2 workers) |
| Frontend | Bootstrap 5.3, Jinja2 templates |
| Container | Docker, Kubernetes |

---

## Prerequisites

### Database

An external MariaDB/MySQL instance is required — no database is deployed in the Kubernetes manifests.

```sql
CREATE DATABASE flexblankett CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'flexuser'@'%' IDENTIFIED BY 'yourpassword';
GRANT ALL PRIVILEGES ON flexblankett.* TO 'flexuser'@'%';
FLUSH PRIVILEGES;
```

### Kubernetes cluster requirements

- Traefik ingress controller
- cert-manager with a working `ClusterIssuer` (the manifests reference `ipa-acme` — change to your issuer)
- An `imagePullSecret` named `gitea-registry` in the `flexblankett` namespace if pulling from a private registry

---

## Deployment

### 1. Build and push the image

```bash
docker build -t your-registry/flexblankett:latest .
docker push your-registry/flexblankett:latest
```

Update the image reference in `k8s/deployment.yaml` to match your registry.

### 2. Create the secret

```bash
cp k8s/secret.yaml.example k8s/secret.yaml
```

Edit `k8s/secret.yaml`:

```yaml
stringData:
  SECRET_KEY: "a-long-random-string"   # e.g. openssl rand -hex 32
  DATABASE_URL: "mysql+pymysql://flexuser:yourpassword@db-host:3306/flexblankett"
```

`k8s/secret.yaml` is gitignored — never commit it.

### 3. Update the ingress hostname

Edit `k8s/ingress.yaml` and replace `flex.cname.se` with your hostname. Also update the `cert-manager.io/cluster-issuer` annotation to match your ClusterIssuer name.

### 4. Apply manifests

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/middleware.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/ingress.yaml
```

### 5. Seed the database

Run once after first deploy to create tables, load 2026 reference hours, and create the default admin user:

```bash
kubectl apply -f k8s/job-seed.yaml
```

Default credentials: `admin` / `admin123` — **change immediately after first login**.

### 6. Verify

```bash
kubectl get pods -n flexblankett
kubectl get certificate -n flexblankett
```

---

## LDAP / IPA authentication

LDAP support is optional and disabled by default. When enabled, users authenticate directly against the LDAP server — no bind account or service user is required. The app binds as the user (`uid=<username>,cn=users,cn=accounts,<base_dn>`) using the submitted password.

On first successful LDAP login the user row is auto-provisioned in the database with `password_hash='!ldap'` (local password login disabled). An admin must then create an **employee profile** for the user via the admin panel before they can use the timesheet.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LDAP_ENABLED` | `false` | Set to `true` to enable |
| `LDAP_HOST` | `ipa.cname.se` | LDAP server hostname |
| `LDAP_BASE_DN` | `dc=cname,dc=se` | Base DN for user search |
| `LDAP_CA_CERT` | `/etc/ssl/certs/ipa-ca.pem` | Path to CA certificate for TLS verification |

LDAP always uses port 636 (LDAPS). Plain LDAP is not supported.

### Local development with LDAP

Add to your `.env`:
```
LDAP_ENABLED=true
LDAP_HOST=ipa.example.com
LDAP_BASE_DN=dc=example,dc=com
LDAP_CA_CERT=/path/to/ca.pem
```

### Kubernetes deployment with LDAP

Two ConfigMaps are required — one for env vars, one for the CA certificate file:

```bash
# edit k8s/ldap-config.yaml — set your LDAP_HOST, LDAP_BASE_DN, and paste your CA cert
kubectl apply -f k8s/ldap-config.yaml
kubectl apply -f k8s/deployment.yaml   # already references both ConfigMaps
```

`k8s/ldap-config.yaml` contains both ConfigMaps: `flexblankett-ldap` (env vars) and `flexblankett-ipa-ca` (CA cert mounted at `/etc/ssl/certs/ipa-ca.pem`). They are kept separate because Kubernetes does not allow keys with dots or dashes to be injected as environment variables.

### After first LDAP login

The user lands on a "no employee profile" page. An admin must:

1. Log in with an admin account
2. Go to **Admin → Users**
3. Click **Edit** on the LDAP user
4. Fill in the employee name and settings, then save

---

## Schema migrations

There is no Alembic migration setup. When a new column is added, run the ALTER TABLE directly in the running pod:

```bash
kubectl exec -n flexblankett deploy/flexblankett -- python -c "
from app import create_app, db
from sqlalchemy import text
app = create_app()
with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(text('ALTER TABLE time_entries ADD COLUMN day_norm_hours DECIMAL(4,2) NULL'))
        conn.commit()
    print('Done.')
"
```

---

## Local development

```bash
cp .env.example .env
# edit .env with your DB credentials

# Start MariaDB locally
docker compose up -d

# Install dependencies
pip install -r requirements.txt

# Create tables and seed data
python seed_data.py

# Run dev server
python run.py
```

App available at `http://localhost:5000`.

---

## CI/CD

Two build pipelines run on push to `main`:

- **Gitea Actions** (`.gitea/workflows/build.yml`) — builds and pushes to the private Gitea registry
- **GitHub Actions** (`.github/workflows/build.yml`) — builds and pushes to GitHub Container Registry (`ghcr.io`)

The GitHub image is publicly pullable (once the package visibility is set to public in GitHub):

```bash
docker pull ghcr.io/cnamehst/flexblankett:latest
```

Gitea Actions workflow at `.gitea/workflows/build.yml` builds and pushes the image on push to `main`.

Required Gitea repository secrets:

| Secret | Value |
|--------|-------|
| `REGISTRY_USERNAME` | Registry username |
| `REGISTRY_PASSWORD` | Registry token with `write:packages` scope |

The workflow runs inside a `docker:27` container using the host Docker socket (DooD). The runner must have `/var/run/docker.sock` available and the registry CA trusted at the system level.

After a new image is pushed, restart the deployment to pull it:

```bash
kubectl rollout restart deployment/flexblankett -n flexblankett
```

Or use [Keel](https://keel.sh) to automate this on `:latest` tag updates.

---

## API

Each user can create named API keys under the **API** menu. Keys are prefixed `fbk_` and shown once on creation.

All requests require:
```
Authorization: Bearer fbk_...
```

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/balance` | Current flex balance — useful for Home Assistant |
| GET | `/api/v1/month/<year>/<month>` | All entries and summary for a month |
| GET | `/api/v1/overview/<year>` | Yearly summary |
| POST | `/api/v1/entry` | Create or update a day entry |

POST body example:
```json
{
  "date": "2026-04-30",
  "start_time": "07:00",
  "end_time": "13:00",
  "comment": "",
  "day_norm_hours": 6
}
```

### Home Assistant sensor example

```yaml
sensor:
  - platform: rest
    name: Flex Balance
    resource: https://flex.example.com/api/v1/balance
    headers:
      Authorization: "Bearer fbk_your_key_here"
    value_template: "{{ value_json.flex_balance }}"
    unit_of_measurement: "h"
    scan_interval: 3600
```

---

## Features

- **Monthly view** — enter start/end time per day, auto-calculates flex deviation
- **Special statuses** — Semester, Sjuk, Vård av barn credited at full day norm
- **Per-day norm override** — set shortened hours (e.g. 6h) for days like April 30th or squeeze days; affects special status calculations
- **Adjustments** — add/subtract time blocks per day for meetings, ATF etc.
- **Yearly overview** — accumulated flex balance per month
- **CSV import** — paste CSV (`datum,start,slut,kommentar`) to bulk-import past months
- **Admin panel** — manage users and employee profiles
- **REST API** — per-user API keys, read and write access
- **LDAP/IPA authentication** — optional SSO, auto-provisions users on first login
