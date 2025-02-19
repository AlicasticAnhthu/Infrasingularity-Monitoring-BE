from flask import Flask, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_apscheduler import APScheduler
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import os

# Flask App Initialization
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///avs.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize Extensions
CORS(app)
db = SQLAlchemy(app)
scheduler = APScheduler()
scheduler.init_app(app)

# Define InfraSingularity Operator Address
INFRA_OPERATOR = "0x53730f4088b116c807875eb67f71cbb1b065f530"

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(100), nullable=False)

class AVS(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    protocol_name = db.Column(db.String(100), nullable=False)
    total_staked = db.Column(db.Float, nullable=False)
    node_count = db.Column(db.Integer, nullable=False)
    commission = db.Column(db.Float, nullable=True)
    network_apy = db.Column(db.Float, nullable=True)
    uptime = db.Column(db.Float, nullable=True)
    downtime = db.Column(db.Float, nullable=True)
    operators = db.Column(db.String(500), nullable=False)
    
    def serialize(self):
        return {
            "id": self.id,
            "protocol_name": self.protocol_name,
            "total_staked": self.total_staked,
            "node_count": self.node_count,
            "commission": self.commission,
            "network_apy": self.network_apy,
            "uptime": self.uptime,
            "downtime": self.downtime,
            "operators": self.operators.split(',')
        }

# User Authentication Route
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data['username']).first()
    if user and check_password_hash(user.password_hash, data['password']):
        session['user_id'] = user.id
        return jsonify({"message": "Login successful"})
    return jsonify({"error": "Invalid credentials"}), 401

# Fetch Only InfraSingularity AVS Data
@app.route("/api/avs", methods=["GET"])
def get_avs():
    avs_list = AVS.query.all()
    filtered_avs = [avs.serialize() for avs in avs_list if INFRA_OPERATOR in avs.operators]
    return jsonify(filtered_avs)

# Fetch Specific AVS Details
@app.route("/api/avs/<int:avs_id>", methods=["GET"])
def get_avs_details(avs_id):
    avs = AVS.query.get(avs_id)
    if not avs:
        return jsonify({"error": "AVS not found"}), 404
    return jsonify(avs.serialize())

# Fetch Operator Metrics
@app.route("/api/operator/<operator_id>", methods=["GET"])
def get_operator_metrics(operator_id):
    avs_list = AVS.query.filter(AVS.operators.like(f"%{operator_id}%")).all()
    return jsonify([avs.serialize() for avs in avs_list])

# Fetch AVS Data Every Minute
@scheduler.task("interval", id="fetch_avs", seconds=60)
def fetch_avs_data():
    print("🔄 Fetching AVS data...")
    try:
        response = requests.get("https://api.u--1.com/v2/latest-avs-balances")
        avs_data = response.json()
        with app.app_context():
            for avs_id, avs in avs_data.items():
                if INFRA_OPERATOR in avs.get("operators", []):
                    existing_avs = AVS.query.filter_by(protocol_name=avs.get("metadata", {}).get("name", "Unknown")).first()
                    if existing_avs:
                        existing_avs.total_staked = avs.get("totalUsdValue", 0.0)
                        existing_avs.node_count = len(avs.get("operators", []))
                        existing_avs.commission = avs.get("metadata", {}).get("commission", None)
                        existing_avs.network_apy = avs.get("metadata", {}).get("network_apy", None)
                        existing_avs.uptime = avs.get("metadata", {}).get("uptime", None)
                        existing_avs.downtime = avs.get("metadata", {}).get("downtime", None)
                    else:
                        new_avs = AVS(
                            protocol_name=avs.get("metadata", {}).get("name", "Unknown"),
                            total_staked=avs.get("totalUsdValue", 0.0),
                            node_count=len(avs.get("operators", [])),
                            commission=avs.get("metadata", {}).get("commission", None),
                            network_apy=avs.get("metadata", {}).get("network_apy", None),
                            uptime=avs.get("metadata", {}).get("uptime", None),
                            downtime=avs.get("metadata", {}).get("downtime", None),
                            operators=','.join(avs.get("operators", []))
                        )
                        db.session.add(new_avs)
                    db.session.commit()
        print("✅ AVS data updated.")
    except Exception as e:
        print("❌ Error fetching AVS data:", str(e))

# Start Scheduler & Run App
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    scheduler.start()
    print("📌 Flask is using database at:", os.path.abspath("avs.db"))
    app.run(debug=True, port=5001)
