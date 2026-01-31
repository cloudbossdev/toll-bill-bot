# Colorado Turo Toll Reconciliation MVP

This repo contains a Flask-based MVP for a Colorado-first Turo host toll reconciliation app.

## Features
- Subscriber and admin UI with role-based access.
- Email ingestion to store reservation windows.
- Toll sync placeholder for TollGuru (E-470 / ExpressToll).
- Auto-matching tolls to reservations by plate + rental window.
- CSV and PDF invoice exports with credit-based gating.
- Subscription plan selection and fleet sizing.

## Local setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Login with the seeded admin user:
- **Email**: admin@turotolls.com
- **Password**: admin
