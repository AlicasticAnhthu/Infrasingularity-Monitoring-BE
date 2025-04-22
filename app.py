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
from urllib.parse import urljoin
from difflib import SequenceMatcher
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

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
EIGENLAYER_API = os.getenv("EIGENLAYER_API")
IVYNET_API = os.getenv("IVYNET_API")
OPERATOR_AVS_DATA_VALINFO_URL = os.getenv("OPERATOR_AVS_DATA_VALINFO_URL")
OPERATOR_AVS_DATA_STAKEINFO_URL = os.getenv("OPERATOR_AVS_DATA_STAKEINFO_URL")
MARKET_DATA_URL = os.getenv("MARKET_DATA_URL")

# IvyNet API Credentials
IVYNET_USERNAME = os.getenv("IVYNET_USERNAME")
IVYNET_PASSWORD = os.getenv("IVYNET_PASSWORD")

# Slack Webhook URL
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

def send_slack_alert(message):
    payload = {"text": message}
    headers = {"Content-type": "application/json"}
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, headers=headers)
        if response.status_code != 200:
            print(f"Slack Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Slack Exception: {e}")

# Models
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
    operator_address = db.Column(db.String(100), nullable=True)
    total_eth_tvl = db.Column(db.Float, nullable=True)
    total_eigen_tvl = db.Column(db.Float, nullable=True)
    validation_success_score = db.Column(db.Float, nullable=True)
    eth_tvl_usd = db.Column(db.Float, nullable=True)
    eigen_tvl_usd = db.Column(db.Float, nullable=True)
    opt_in_date = db.Column(db.DateTime, nullable=True)
    operator_rank = db.Column(db.Integer, nullable=True)

class AlertLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=db.func.now())
    avs_name = db.Column(db.String(100), nullable=False)
    protocol_name = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)

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
    try:
        response = requests.get(EIGENLAYER_API, timeout=10)
        response.raise_for_status()
        eigen_data = response.json()
        
        with open("eigenlayer_raw_data.json", "w") as f:
            json.dump(eigen_data, f, indent=2)

        eigen_mapping = {}
        for protocol, avs in eigen_data.items():
            protocol_name = avs.get("metadata", {}).get("name", protocol).strip().lower()
            total_staked = avs.get("total_staked") or avs.get("totalUsdValue") or avs.get("totalValueDenominatedInEth") or 0.0
            if protocol_name:
                eigen_mapping[protocol_name] = {
                    "total_staked": total_staked,
                    "network_apy": avs.get("network_apy"),
                }
        return eigen_mapping
    except requests.exceptions.RequestException as e:
        print(f"❌ Request Error fetching EigenLayer data: {e}")
        return {}

def fetch_operator_data_by_addresses():
    operator_addresses = [addr.strip().lower() for addr in os.getenv("OPERATOR_ADDRESSES", "").split(",")]
    operator_data_map = {}
    try:
        market_resp = requests.get(MARKET_DATA_URL, timeout=10)
        market_resp.raise_for_status()
        market_prices = market_resp.json().get("data", [])
        price_map = {item["coinName"].lower(): float(item["price"]) for item in market_prices if item.get("price")}
    except Exception as e:
        print(f"⚠ Failed to fetch market prices: {e}")
        price_map = {}

    for address in operator_addresses:
        valinfo_url = OPERATOR_AVS_DATA_VALINFO_URL.replace("OPERATOR_ADDRESS", address)
        stakeinfo_url = OPERATOR_AVS_DATA_STAKEINFO_URL.replace("OPERATOR_ADDRESS", address)
        valinfo_data = []
        eth_tvl, eigen_tvl = 0, 0
        try:
            valinfo_response = requests.get(valinfo_url, timeout=10)
            if valinfo_response.status_code == 200:
                valinfo_data = valinfo_response.json().get("data", [])
                for avs in valinfo_data:
                    if avs.get("isHasBlocksStats"):
                        try:
                            blocks = avs.get("blocksCount", 0)
                            misses = avs.get("blocksMissesCount", 0)
                            avs["validationSuccessScore"] = ((blocks - misses) * 100 / blocks) if blocks else None
                        except:
                            avs["validationSuccessScore"] = None
        except Exception as e:
            print(f"⚠ Error fetching valinfo for {address}: {e}")

        try:
            stakeinfo_response = requests.get(stakeinfo_url, timeout=10)
            if stakeinfo_response.status_code == 200:
                for token in stakeinfo_response.json():
                    symbol = token.get("symbol", "").lower()
                    tvl = float(token.get("tvl", "0") or 0)
                    if "eth" in symbol:
                        eth_tvl += tvl
                    elif symbol == "eigen":
                        eigen_tvl += tvl
        except Exception as e:
            print(f"⚠ Error fetching stakeinfo for {address}: {e}")

        eth_usd = eth_tvl * price_map.get("eth", 0)
        eigen_usd = eigen_tvl * price_map.get("eigen", 0)

        operator_data_map[address] = [
            {
                **avs,
                "ethTvl": eth_tvl,
                "eigenTvl": eigen_tvl,
                "ethTvlUsd": eth_usd,
                "eigenTvlUsd": eigen_usd
            } for avs in valinfo_data
        ]
        print(f"\nDebug: Operator data map for {address}:")
        print(json.dumps(operator_data_map[address], indent=2))

    return operator_data_map

def fetch_ivynet_data():
    try:
        response = requests.get(IVYNET_API, auth=(IVYNET_USERNAME, IVYNET_PASSWORD), timeout=10)
        response.raise_for_status()
        ivynet_data = response.json()

        with open("ivynet_raw_data.json", "w") as f:
            json.dump(ivynet_data, f, indent=2)

        if not isinstance(ivynet_data, list): return {}

        configured_addresses = [addr.strip().lower() for addr in os.getenv("OPERATOR_ADDRESSES", "").split(",")]
        operator_info_map = fetch_operator_data_by_addresses()

        ivynet_avs = []
        for machine in ivynet_data:
            if not machine or not isinstance(machine, dict):
                continue
            avs_list = machine.get("avs_list", [])
            if not isinstance(avs_list, list):
                continue

            for avs in avs_list:
                if not avs or not isinstance(avs, dict):
                    continue
                name = avs.get("avs_name", "")
                operator_address = avs.get("operator_address")
                operator_address = operator_address.lower() if isinstance(operator_address, str) else ""

                valinfo_data = operator_info_map.get(operator_address, []) if operator_address in configured_addresses else []

                # fuzzy match
                matched = None
                best_score = 0.0
                for v in valinfo_data:
                    if not v or not v.get("name"): continue
                    score = SequenceMatcher(None, v["name"].lower(), name.lower()).ratio()
                    if score > best_score:
                        best_score = score
                        matched = v

                validation_score = matched.get("validationSuccessScore") if matched else None

                ivynet_avs.append({
                    "avs_name": name,
                    "protocol_name": name.lower(),
                    "node_count": 1,
                    "uptime": avs.get("uptime", 0),
                    "status": machine.get("status", "Unknown"),
                    "errors": json.dumps(machine.get("errors", [])),
                    "operator_address": operator_address,
                    "opt_in_date": (
                        datetime.fromisoformat(matched["optInDate"].replace("Z", "+00:00"))
                        if matched and matched.get("optInDate")
                        else None
                    ),
                    "operator_rank": matched.get("operatorRank") if matched else None,
                    "total_eth_tvl": matched.get("ethTvl") if matched else None,
                    "total_eigen_tvl": matched.get("eigenTvl") if matched else None,
                    "validation_success_score": validation_score,
                    "eth_tvl_usd": matched.get("ethTvlUsd") if matched else None,
                    "eigen_tvl_usd": matched.get("eigenTvlUsd") if matched else None,
                })

        # Aggregate node counts and uptimes per AVS
        aggregated = {}
        for avs in ivynet_avs:
            key = avs["avs_name"]
            if key in aggregated:
                aggregated[key]["node_count"] += 1
                aggregated[key]["uptime"] += avs["uptime"]
            else:
                aggregated[key] = avs

        print("\n✅ Debug: Aggregated IvyNet data:")
        print(json.dumps(aggregated, indent=2, default=_json_serializer))
        return aggregated

    except requests.exceptions.RequestException as e:
        print(f"❌ Request Exception for IvyNet: {e}")
        return {}

def clean_protocol_name(protocol_name):
    """Normalize IvyNet protocol names by removing prefixes and suffixes."""
    return re.sub(r'^(is-|tt-)|(-mainnet|-testnet)$', '', protocol_name).strip().lower()

def match_protocols(eigenlayer_data, ivynet_data):
    for ivy_avs in ivynet_data.values():
        clean_name = clean_protocol_name(ivy_avs["protocol_name"])
        match = next((k for k in eigenlayer_data if clean_name in k or k in clean_name), None)
        if match:
            ivy_avs["total_staked"] = eigenlayer_data[match].get("total_staked", 0.0)
            ivy_avs["network_apy"] = eigenlayer_data[match].get("network_apy")
        else:
            ivy_avs["total_staked"] = 0.0
            ivy_avs["network_apy"] = None
    return ivynet_data

def merge_avs_data():
    eigen_data = fetch_eigenlayer_data()
    ivynet_data = fetch_ivynet_data()
    merged_data = match_protocols(eigen_data, ivynet_data)
    with app.app_context():
        for name, data in merged_data.items():
            avs = AVS.query.filter_by(avs_name=name).first()
            if avs:
                for key, val in data.items():
                    if hasattr(avs, key):
                        setattr(avs, key, val)
            else:
                db.session.add(AVS(**data))

            if data["status"].lower() == "error" or json.loads(data.get("errors", "[]")):
                msg = f"🚨 AVS Alert: {name} ({data['protocol_name']}) has issues."
                db.session.add(AlertLog(avs_name=name, protocol_name=data["protocol_name"], message=msg))
                send_slack_alert(msg)
        db.session.commit()

def _json_serializer(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

# Schedule periodic fetch
scheduler.add_job(id="fetch_merged_avs_data", func=merge_avs_data, trigger="interval", minutes=1)

@app.route("/api/avs", methods=["GET"])
def get_merged_avs():
    with app.app_context():
        avs_list = AVS.query.all()
        return jsonify([{
            "avs_name": a.avs_name,
            "protocol_name": a.protocol_name,
            "total_staked": a.total_staked,
            "network_apy": a.network_apy,
            "node_count": a.node_count,
            "uptime": a.uptime,
            "status": a.status,
            "errors": json.loads(a.errors) if a.errors else [],
            "operator_address": a.operator_address,
            "total_eth_tvl": a.total_eth_tvl,
            "total_eigen_tvl": a.total_eigen_tvl,
            "validation_success_score": a.validation_success_score,
            "eth_tvl_usd": a.eth_tvl_usd,
            "eigen_tvl_usd": a.eigen_tvl_usd,
        } for a in avs_list])

@app.route("/api/avs/overall_status", methods=["GET"])
def get_avs_overall_status():
    with app.app_context():
        return jsonify({a.avs_name: a.status for a in AVS.query.all()})

@app.route("/api/avs/by_name/<string:protocol_name>", methods=["GET"])
def get_avs_by_name(protocol_name):
    avs = AVS.query.filter_by(protocol_name=protocol_name).first()
    if not avs:
        return jsonify({"error": "AVS protocol name not found"}), 404

    return jsonify({
        "avs_name": avs.avs_name,
        "protocol_name": avs.protocol_name,
        "total_staked": avs.total_staked,
        "network_apy": avs.network_apy,
        "node_count": avs.node_count,
        "uptime": avs.uptime,
        "status": avs.status,
        "errors": json.loads(avs.errors) if avs.errors else [],
        "operator_address": avs.operator_address,
        "total_eth_tvl": avs.total_eth_tvl,
        "total_eigen_tvl": avs.total_eigen_tvl,
        "validation_success_score": avs.validation_success_score,
        "eth_tvl_usd": avs.eth_tvl_usd,
        "eigen_tvl_usd": avs.eigen_tvl_usd
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

@app.route("/api/alerts", methods=["GET"])
def get_alert_logs():
    with app.app_context():
        alert_logs = AlertLog.query.order_by(AlertLog.timestamp.desc()).all()
        avs_lookup = {avs.avs_name: avs for avs in AVS.query.all()}

        enriched_logs = []
        for log in alert_logs:
            avs = avs_lookup.get(log.avs_name)
            if avs:
                error_list = json.loads(avs.errors or "[]")
                error_text = ", ".join(error_list) if error_list else "Unknown issue"
                message = f"🚨 AVS Alert: {log.avs_name} has issues: {error_text}."
            else:
                message = log.message  # fallback to saved message

            enriched_logs.append({
                "timestamp": log.timestamp.isoformat(),
                "avs_name": log.avs_name,
                "protocol_name": log.protocol_name,
                "message": message
            })

        return jsonify(enriched_logs)

if __name__ == "__main__":
    # Test data fetching from 2 sources
    # eigen_data = fetch_eigenlayer_data() 
    # ivynet_data = fetch_ivynet_data()
    app.run(debug=True, port=5001)

