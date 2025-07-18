from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
from db import db

cars_collection = db["cars"]

def serialize_car(car):
    """
    Convert a MongoDB car document into a JSON-safe dictionary.
    """
    return {
        "_id": str(car["_id"]),
        "carId": car.get("carId"),
        "userId": str(car.get("userId")),
        "name": car.get("name", ""),
        "model": car.get("model", ""),
        "status": car.get("status", "Idle"),
        "location": {
            "lat": car.get("location", {}).get("lat"),
            "lng": car.get("location", {}).get("lng")
        },
        "battery": round(car.get("battery", 100), 1),
        "createdAt": car.get("createdAt", datetime.utcnow()).isoformat()
    }
