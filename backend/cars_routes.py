from flask import Blueprint, request, jsonify
from bson import ObjectId
from datetime import datetime
from car_model import cars_collection, serialize_car

cars_bp = Blueprint('cars', __name__)

# ─────────────────────────────────────────────
# GET /cars/user/<user_id> → All cars for user
# ─────────────────────────────────────────────
@cars_bp.route("/cars/user/<user_id>", methods=["GET"])
def get_user_cars(user_id):
    try:
        cars = list(cars_collection.find({ "userId": ObjectId(user_id) }))
        return jsonify([serialize_car(car) for car in cars])
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

# ─────────────────────────────────────────────
# GET /cars/<car_id> → Get single car by ID
# ─────────────────────────────────────────────
@cars_bp.route("/cars/<car_id>", methods=["GET"])
def get_car_by_id(car_id):
    try:
        car = cars_collection.find_one({ "_id": ObjectId(car_id) })
        if not car:
            return jsonify({ "error": "Car not found" }), 404
        return jsonify(serialize_car(car))
    except Exception as e:
        return jsonify({ "error": str(e) }), 500

# ─────────────────────────────────────────────
# POST /cars → Create a new car
# ─────────────────────────────────────────────
@cars_bp.route("/cars", methods=["POST"])
def create_car():
    try:
        data = request.json
        car_data = {
            "carId": data["carId"],
            "userId": ObjectId(data["userId"]),
            "name": data.get("name", ""),
            "model": data["model"],
            "status": data.get("status", "Idle"),
            "location": {
                "lat": data.get("lat"),
                "lng": data.get("lng")
            },
            "battery": float(data.get("battery", 100)),
            "createdAt": datetime.utcnow()
        }
        result = cars_collection.insert_one(car_data)
        new_car = cars_collection.find_one({ "_id": result.inserted_id })
        return jsonify(serialize_car(new_car)), 201
    except Exception as e:
        return jsonify({ "error": str(e) }), 400

# ─────────────────────────────────────────────
# PUT /cars/<car_id> → Update existing car
# ─────────────────────────────────────────────
@cars_bp.route("/cars/<car_id>", methods=["PUT"])
def update_car(car_id):
    try:
        data = request.json
        update_fields = {}

        if "name" in data:
            update_fields["name"] = data["name"]
        if "model" in data:
            update_fields["model"] = data["model"]
        if "battery" in data:
            update_fields["battery"] = float(data["battery"])
        if "status" in data:
            update_fields["status"] = data["status"]
        if "location" in data:
            update_fields["location.lat"] = data["location"].get("lat")
            update_fields["location.lng"] = data["location"].get("lng")

        result = cars_collection.update_one({ "_id": ObjectId(car_id) }, { "$set": update_fields })

        if result.matched_count == 0:
            return jsonify({ "error": "Car not found" }), 404

        updated_car = cars_collection.find_one({ "_id": ObjectId(car_id) })
        return jsonify(serialize_car(updated_car))
    except Exception as e:
        return jsonify({ "error": str(e) }), 400

# ─────────────────────────────────────────────
# DELETE /cars/<car_id> → Remove a car
# ─────────────────────────────────────────────
@cars_bp.route("/cars/<car_id>", methods=["DELETE"])
def delete_car(car_id):
    try:
        result = cars_collection.delete_one({ "_id": ObjectId(car_id) })
        if result.deleted_count == 0:
            return jsonify({ "error": "Car not found" }), 404
        return jsonify({ "status": "deleted", "carId": car_id })
    except Exception as e:
        return jsonify({ "error": str(e) }), 500
