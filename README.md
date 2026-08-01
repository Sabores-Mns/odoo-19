# Odoo 19 Docker Setup

A clean, production-ready Docker Compose environment for running Odoo 19.0 with PostgreSQL 18.

---

## Quick Start

### 1. Prerequisites
Ensure you have Docker and Docker Compose installed on your system:
- Docker Engine
- Docker Compose

### 2. Start the Stack
Run the following command to start PostgreSQL and Odoo in detached mode:

```bash
docker compose up -d
```

### 3. Access Odoo
Open your browser and navigate to:
http://localhost:10020

You will be greeted by the Odoo Database Creation Wizard.

---

## Credentials and Default Configuration

| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| Master Password | admin | Used to create, backup, restore, or drop databases |
| Web Port | 10020 | Main Odoo web interface (http://localhost:10020) |
| Live Chat / WebSocket Port | 20019 | Longpolling and WebSocket live chat port |
| PostgreSQL User | odoo | Database user |
| PostgreSQL Password | odoo19@2025 | Database user password |
| PostgreSQL Host Port | 5433 | Host port mapped to PostgreSQL container port 5432 |

---

## Project Structure

```text
├── addons/             # Directory for custom Odoo modules
├── etc/
│   ├── odoo.conf       # Main Odoo configuration file
│   └── requirements.txt# Additional Python packages to install on boot
├── postgresql/         # Persistent PostgreSQL database storage (git-ignored)
├── odoo_data/          # Persistent Odoo filestore and session data (git-ignored)
├── docker-compose.yml  # Docker services configuration
├── entrypoint.sh       # Custom container entrypoint script
└── run.sh              # Installation script for remote deployments
```

---

## Custom Addons

To install custom Odoo modules:
1. Place your module directory inside the `./addons` folder.
2. Restart the Odoo container:
   ```bash
   docker compose restart odoo19
   ```
3. In Odoo, activate Developer Mode, navigate to Apps > Update Apps List, and search for your module.

---

## Reset Database and Start From Scratch

To wipe all existing data and start with a fresh installation wizard:

```bash
# Stop containers and remove volumes
docker compose down -v

# Clean local data directories
rm -rf postgresql odoo_data
mkdir -p postgresql odoo_data

# Start clean containers
docker compose up -d
```

---

## Container Management Commands

| Action | Command |
| :--- | :--- |
| Start Services | `docker compose up -d` |
| Stop Services | `docker compose down` |
| Restart Odoo | `docker compose restart odoo19` |
| View Odoo Logs | `docker compose logs -f odoo19` |
| View Database Logs | `docker compose logs -f db` |

---

## Automated Deployment (GitHub Actions)

This repository includes a CI/CD workflow configured in `.github/workflows/deploy.yml`.

Whenever code is pushed to the `main` branch, the self-hosted GitHub Actions runner automatically:
1. Pulls the latest code.
2. Adjusts script and directory permissions.
3. Spawns or updates containers via `docker compose up -d`.
4. Restarts the Odoo container to load any new custom modules.
