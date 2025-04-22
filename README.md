
# Infrasingularity Monitoring System

This repository contains the backend code for the **Infrasingularity AVS Monitoring System**, a Flask-based monitoring solution designed to continuously fetch, aggregate, enrich, and alert on data related to Active Validation Services (AVS) from IvyNet and EigenLayer. The system provides developers and platform admins a reliable way to track AVS health, performance, and operator behavior.

---

## 🚀 Overview

The monitoring backend performs the following core tasks:

- Periodically fetches machine-level AVS logs from IvyNet (secured with basic auth).
- Fetches enriched operator and validator metadata from EigenLayer APIs.
- Matches and maps AVS entries to aggregate node count, uptime, and performance metrics.
- Calculates ETH/EIGEN total value locked (TVL) and their USD equivalents.
- Triggers Slack alerts on unhealthy AVS or AVS with logged errors.
- Persists AVS state into a SQLite database for querying via REST API.
- Provides multiple endpoints to access data for dashboards or audits.

---

## 📁 Folder Structure

```
.
├── app.py                # Main Flask backend code
├── requirements.txt      # Python dependencies
├── README.md             # This documentation
├── avs.db                # SQLite database (auto-generated)
```

---

## ⚙️ Installation & Setup

### 1. Clone the Repo

```bash
git clone https://github.com/yourusername/infrasingularity-monitoring-be.git
cd infrasingularity-monitoring-be
```

### 2. Create a Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Environment Variables

Create a `.env` file in the root directory and add the following:

```
EIGENLAYER_API=https://example.com/eigenlayer/avs
IVYNET_API=https://example.com/ivynet/logs
IVYNET_USERNAME=yourusername
IVYNET_PASSWORD=yourpassword
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
OPERATOR_AVS_DATA_VALINFO_URL=https://validator.info/api/eigenlayer/v1/operators/OPERATOR_ADDRESS/valinfos
OPERATOR_AVS_DATA_STAKEINFO_URL=https://validator.info/api/eigenlayer/v1/operators/OPERATOR_ADDRESS/strategies?timeframe=twoWeeks
MARKET_DATA_URL=https://validator.info/api/market-data/v1/market-data?timeframe=twoWeeks
OPERATOR_ADDRESSES=0xabc123...,0xdef456...
```

---

## 🧪 Running the App

```bash
python app.py
```

The app will:

- Start a Flask server on port 5001.
- Begin fetching and merging AVS data every 1 minute.
- Log alerts to both Slack and the local database.

---

## 📡 API Endpoints

### `GET /api/avs`
Fetch full AVS dataset including enriched metrics and health data.

### `GET /api/avs/overall_status`
Returns a simplified list of AVS names and their health status.

### `GET /api/avs/by_name/<protocol_name>`
Query a specific AVS by its protocol name.

### `GET /api/alerts`
Get the history of triggered alerts from the system.

---

## 🧑‍💻 Developer Notes

- Code is organized with simplicity and maintainability in mind.
- All alerts are logged in `AlertLog` table and can be queried separately.
- Fuzzy matching is applied using Python's `SequenceMatcher` to match AVS entries between systems.

### Code Standards

- PEP8 formatting
- Explicit error handling and logging
- Uses SQLAlchemy ORM with Flask context
- Secrets handled via environment variables

---

## 🔧 Maintenance

### Database Reset

```bash
rm avs.db
python app.py  # Auto regenerates schema
```

### Update Packages

```bash
pip freeze > requirements.txt
```

---

## 📄 Project Report

For a detailed overview of the project's goals, system design, and AVS operational metrics, please refer to the [`Final Written Report`](./Final Written Report.docx).

---

## 🧠 Contributors

- **Capstone Team:** — Jyotsna Chellani, Alison Quan, Sri Amirdha Sudha, Javier Ramirez, Sarah Son, Siddharth Badyal  
  UT Austin | MS IT & Management

