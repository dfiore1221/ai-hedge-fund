# Always-On AIFundOS Deployment

This package runs the AIFundOS watchdog every 15 minutes and optionally serves the dashboard. It is intended for a private, always-on machine or small private cloud server.

## Safety Boundary

- The worker can modify only the simulated ledger and local AIFundOS memory.
- No live brokerage credentials are required or authorized.
- Keep `.env` private and never commit it.
- Do not expose port 8501 directly to the public internet. Put the dashboard behind a private VPN, authenticated tunnel, or access-control proxy.

## Deployment Outline

1. Provision a private Linux host with Docker Compose.
2. Clone the private repository to the host.
3. Transfer `.env` through the host's secret manager or another encrypted channel.
4. From `deploy/`, start the services with `docker compose up -d --build`.
5. Review `reports/automation_watchdog/service.log` and run `python main.py email-health check` inside the worker.
6. Access the dashboard only through a private network or authenticated tunnel.

The portfolio, memory database, reports, and data cache are mounted as persistent host directories so restarts do not erase learning history.
