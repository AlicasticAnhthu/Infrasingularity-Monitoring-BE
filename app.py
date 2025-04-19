from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_apscheduler import APScheduler
import requests
import os
import json
from werkzeug.security import generate_password_hash, check_password_hash
from enum import Enum
from flask_cors import CORS
import re

# Flask App Initialization
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:3000"}}, supports_credentials=True)

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

class AccessGroupEnum(Enum):
    READ = 'read'

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    access_group = db.Column(db.Enum(AccessGroupEnum), nullable=False)
    allowlist = db.Column(db.PickleType, nullable=False, default=[])

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

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

@app.route("/api/avs/overall_status", methods=["GET"])
def get_avs_overall_status():
    """API Route to fetch AVS names and statuses exactly as in database"""
    with app.app_context():
        avs_list = AVS.query.all()
        avs_status_dict = {
            avs.avs_name: avs.status for avs in avs_list
        }
        return jsonify(avs_status_dict)

@app.route("/api/avs/by_name/<string:protocol_name>", methods=["GET"])
def get_avs_by_name(protocol_name):
    """Query AVS info by protocol name"""
    avs_entry = AVS.query.filter_by(protocol_name=protocol_name).first()
    if not avs_entry:
        return jsonify({"error": "AVS protocol name not found"}), 404

    return jsonify({
        "protocol_name": avs_entry.protocol_name,
        "total_staked": avs_entry.total_staked,
        "network_apy": avs_entry.network_apy,
        "node_count": avs_entry.node_count,
        "uptime": avs_entry.uptime,
        "status": avs_entry.status,
        "errors": json.loads(avs_entry.errors) if avs_entry.errors else []
    })

@app.route('/api/account/create', methods=['POST'])
def create_account():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    allowlist = data.get('allowlist', [])

    # Validate required fields
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    # Fetch valid AVS names from database
    valid_avs_names = {avs.avs_name for avs in AVS.query.all()}

    # Check if allowlist values are valid
    invalid = [name for name in allowlist if name not in valid_avs_names]
    if invalid:
        return jsonify({
            "error": "Invalid AVS names in allowlist",
            "invalid_values": invalid
        }), 400

    if Account.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already exists'}), 409

    password_hash = generate_password_hash(password)
    new_account = Account(
        username=username,
        password_hash=password_hash,
        access_group=AccessGroupEnum.READ,
        allowlist=allowlist
    )
    db.session.add(new_account)
    db.session.commit()

    return jsonify({'message': 'Account created successfully'}), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    account = Account.query.filter_by(username=username).first()
    if account and account.check_password(password):
        return jsonify({
            'username': account.username,
            'access_group': account.access_group.value,
            'allowlist': account.allowlist
        }), 200
    else:
        return jsonify({'error': 'Invalid username or password'}), 401
    
@app.route('/api/account/<int:account_id>', methods=['GET'])
def get_account(account_id):
    account = Account.query.get(account_id)
    if not account:
        return jsonify({'error': 'Account not found'}), 404

    return jsonify({
        'id': account.id,
        'username': account.username,
        'access_group': account.access_group.value,
        'allowlist': account.allowlist
    }), 200

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    accounts = Account.query.all()
    return jsonify([{
        'id': account.id,
        'username': account.username,
        'access_group': account.access_group.value,
        'allowlist': account.allowlist
    } for account in accounts]), 200

@app.route('/api/account/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    account = Account.query.get(account_id)
    if not account:
        return jsonify({'error': 'Account not found'}), 404
    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'Account deleted successfully'}), 200

@app.route('/api/account/avs_status', methods=['GET'])
def get_filtered_avs_status_by_username():
    username = request.args.get('username')

    if not username:
        return jsonify({"error": "Username is required"}), 400

    account = Account.query.filter_by(username=username).first()
    if not account:
        return jsonify({"error": "Account not found"}), 404

    allowlist = account.allowlist
    avs_entries = AVS.query.filter(AVS.avs_name.in_(allowlist)).all()

    return jsonify([
        {
            "avs_name": avs.avs_name,
            "status": avs.status
        }
        for avs in avs_entries
    ])


# Run Flask app
if __name__ == "__main__":
    app.run(debug=True, port=5001)
    

