from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_apscheduler import APScheduler
import requests
import os
import json

# Flask App Initialization
app = Flask(__name__)

# Database setup
DATABASE_FILE = "avs.db"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATABASE_FILE}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Scheduler Setup
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# API Sources
EIGENLAYER_API = "https://api.u--1.com/v2/latest-avs-balances"
IVYNET_API = "https://api1.test.ivynet.dev/machine"

# IvyNet API Credentials
IVYNET_USERNAME = os.getenv("IVYNET_USERNAME")
IVYNET_PASSWORD = os.getenv("IVYNET_PASSWORD")

# Database Model
class AVS(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    avs_name = db.Column(db.String(100), nullable=False)
    protocol_name = db.Column(db.String(100), nullable=False)
    total_staked = db.Column(db.Float, nullable=True)
    network_apy = db.Column(db.Float, nullable=True)
    node_count = db.Column(db.Integer, nullable=True)
    uptime = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(50), nullable=True)
    errors = db.Column(db.Text, nullable=True)

# Ensure database tables exist
with app.app_context():
    db.create_all()

def fetch_eigenlayer_data():
    """Fetch AVS data from EigenLayer."""
    try:
        response = requests.get(EIGENLAYER_API, timeout=10)
        response.raise_for_status()
        eigen_data = response.json()

        if not isinstance(eigen_data, dict):
            print("⚠ Unexpected EigenLayer response format!")
            return {}

        eigen_mapping = {}
        for protocol, avs in eigen_data.items():
            protocol_name = avs.get("metadata", {}).get("name", protocol).strip().lower()
            total_staked = (
                avs.get("total_staked") or 
                avs.get("totalUsdValue") or 
                avs.get("totalValueDenominatedInEth") or 
                0.0
            )

            print(f"📌 Extracted: {protocol_name} | Total Staked: {total_staked} | APY: {avs.get('network_apy')}")

            if protocol_name:
                eigen_mapping[protocol_name] = {
                    "total_staked": total_staked,
                    "network_apy": avs.get("network_apy"),
                }

        print("🔍 EigenLayer Processed Data:", json.dumps(eigen_mapping, indent=2))
        return eigen_mapping
    except requests.exceptions.RequestException as e:
        print(f"❌ Request Error fetching EigenLayer data: {e}")
        return {}


def fetch_ivynet_data():
    """Fetch AVS data from IvyNet."""
    try:
        response = requests.get(IVYNET_API, auth=(IVYNET_USERNAME, IVYNET_PASSWORD), timeout=10)
        response.raise_for_status()
        ivynet_data = response.json()

        if not isinstance(ivynet_data, list):
            print("⚠ Unexpected IvyNet response format!")
            return {}

        ivynet_avs = []
        for machine in ivynet_data:
            for avs in machine.get("avs_list", []):
                ivynet_avs.append({
                    "avs_name": avs.get("avs_name", ""),
                    "protocol_name": avs.get("avs_name", "").lower(),  # Store lowercase for easy matching
                    "node_count": 1,  
                    "uptime": avs.get("uptime", 0),
                    "status": machine.get("status", "Unknown"),
                    "errors": json.dumps(machine.get("errors", []))
                })

        # Aggregate node counts and uptime per AVS
        aggregated_avs = {}
        for avs in ivynet_avs:
            name = avs["avs_name"]
            if name in aggregated_avs:
                aggregated_avs[name]["node_count"] += 1
                aggregated_avs[name]["uptime"] += avs["uptime"]
            else:
                aggregated_avs[name] = avs

        return aggregated_avs
    except requests.exceptions.RequestException as e:
        print(f"❌ Request Exception for IvyNet: {e}")
        return {}

import re

def clean_protocol_name(protocol_name):
    """Normalize IvyNet protocol names by removing prefixes and suffixes."""
    return re.sub(r'^(is-|tt-)|(-mainnet|-testnet)$', '', protocol_name).strip().lower()

def match_protocols(eigenlayer_data, ivynet_data):
    """Match IvyNet protocols to EigenLayer based on cleaned name similarity."""
    eigenlayer_protocols = {name.lower(): data for name, data in eigenlayer_data.items()}
    
    for ivy_avs in ivynet_data.values():
        ivy_protocol_name = clean_protocol_name(ivy_avs["protocol_name"])

        matched_protocol = None
        for eigen_name in eigenlayer_protocols.keys():
            if re.search(re.escape(ivy_protocol_name), eigen_name, re.IGNORECASE) or re.search(re.escape(eigen_name), ivy_protocol_name, re.IGNORECASE):
                matched_protocol = eigen_name
                break

        if matched_protocol:
            ivy_avs["total_staked"] = eigenlayer_protocols[matched_protocol].get("total_staked", 0.0)
            ivy_avs["network_apy"] = eigenlayer_protocols[matched_protocol].get("network_apy", None)
            print(f"✅ Matched: {ivy_avs['protocol_name']} <-> {matched_protocol} | Total Staked: {ivy_avs['total_staked']}")
        else:
            print(f"⚠ No match found for: {ivy_avs['protocol_name']} | Setting total_staked to 0.0")
            ivy_avs["total_staked"] = 0.0
            ivy_avs["network_apy"] = None

    return ivynet_data





def merge_avs_data():
    """Fetch and merge AVS data from EigenLayer and IvyNet."""
    eigen_data = fetch_eigenlayer_data()
    ivynet_data = fetch_ivynet_data()

    print("🔍 EigenLayer Protocols Available:", list(eigen_data.keys()))
    print("🔍 IvyNet Protocols Available:", list(ivynet_data.keys()))

    merged_data = match_protocols(eigen_data, ivynet_data)

    with app.app_context():
        for avs_name, ivynet_entry in merged_data.items():
            protocol_name = ivynet_entry["protocol_name"]

            existing_entry = AVS.query.filter_by(avs_name=avs_name).first()
            if existing_entry:
                existing_entry.total_staked = ivynet_entry["total_staked"]
                existing_entry.network_apy = ivynet_entry["network_apy"]
                existing_entry.node_count = ivynet_entry["node_count"]
                existing_entry.uptime = ivynet_entry["uptime"]
                existing_entry.status = ivynet_entry["status"]
                existing_entry.errors = ivynet_entry["errors"]
            else:
                new_avs = AVS(
                    avs_name=avs_name,
                    protocol_name=protocol_name,
                    total_staked=ivynet_entry["total_staked"],
                    network_apy=ivynet_entry["network_apy"],
                    node_count=ivynet_entry["node_count"],
                    uptime=ivynet_entry["uptime"],
                    status=ivynet_entry["status"],
                    errors=ivynet_entry["errors"]
                )
                db.session.add(new_avs)

        db.session.commit()
        print("✅ AVS data updated in database.")

# Schedule periodic AVS data fetching
scheduler.add_job(id="fetch_merged_avs_data", func=merge_avs_data, trigger="interval", minutes=1)

@app.route("/api/avs", methods=["GET"])
def get_merged_avs():
    """API Route to fetch merged AVS data"""
    with app.app_context():
        avs_list = AVS.query.all()
        return jsonify([
            {
                "avs_name": avs.avs_name,
                "protocol_name": avs.protocol_name,
                "total_staked": avs.total_staked,
                "network_apy": avs.network_apy,
                "node_count": avs.node_count,
                "uptime": avs.uptime,
                "status": avs.status,
                "errors": json.loads(avs.errors) if avs.errors else []
            }
            for avs in avs_list
        ])

# Run Flask app
if __name__ == "__main__":
    app.run(debug=True, port=5001)


