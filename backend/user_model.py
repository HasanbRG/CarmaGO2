from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
from db import db
import uuid


users_collection = db["users"]

def create_user(email, password, first_name="", last_name="", phone=""):
    user = {
        "userId": str(uuid.uuid4()),
        "email": email,
        "password": password,  # hashed before calling this
        "firstName": first_name,
        "lastName": last_name,
        "phone": phone,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow()
    }
    result = users_collection.insert_one(user)
    return users_collection.find_one({ "_id": result.inserted_id })

def update_user_timestamp(user_id):
    users_collection.update_one(
        { "_id": ObjectId(user_id) },
        { "$set": { "updatedAt": datetime.utcnow() } }
    )

def serialize_user(user):
    return {
        "_id": str(user["_id"]),
        "userId": user["userId"],
        "email": user["email"],
        "phone": user.get("phone", ""),
        "firstName": user.get("firstName", ""),
        "lastName": user.get("lastName", ""),
        "createdAt": user.get("createdAt", "").isoformat() if "createdAt" in user else "",
        "updatedAt": user.get("updatedAt", "").isoformat() if "updatedAt" in user else ""
    }

# Financial transaction functions - no more balance, just transaction tracking

def find_user_by_id(user_id):
    """Find user by either _id or userId field to handle both formats"""
    try:
        # First try to find by _id if it looks like an ObjectId
        if len(user_id) == 24:
            try:
                user = users_collection.find_one({"_id": ObjectId(user_id)})
                if user:
                    return user
            except:
                pass
        
        # Try to find by userId field
        user = users_collection.find_one({"userId": user_id})
        if user:
            return user
            
        # If still not found, try string _id
        user = users_collection.find_one({"_id": user_id})
        return user
        
    except Exception as e:
        print(f"Error finding user {user_id}: {e}")
        return None

def add_transaction(user_id, amount, transaction_type, description=""):
    """Record a transaction without affecting any balance"""
    try:
        print(f"DEBUG: add_transaction called - userId: {user_id}, amount: {amount}, type: {transaction_type}")
        
        # Find the user to get the correct userId format for transactions
        user = find_user_by_id(user_id)
        if not user:
            print(f"ERROR: User not found for ID: {user_id}")
            return None
            
        # Use the userId field for transaction storage (consistent format)
        transaction_user_id = user.get("userId", str(user["_id"]))
        print(f"DEBUG: Using transaction userId: {transaction_user_id}")
        
        # Record transaction only
        transactions_collection = db["transactions"]
        transaction = {
            "userId": transaction_user_id,
            "amount": amount,
            "type": transaction_type,  # "ride_payment", "ride_earning", "manual"
            "description": description,
            "timestamp": datetime.utcnow()
        }
        print(f"DEBUG: About to insert transaction: {transaction}")
        
        insert_result = transactions_collection.insert_one(transaction)
        print(f"DEBUG: Transaction inserted with ID: {insert_result.inserted_id}")
        
        # Verify insertion
        verification = transactions_collection.find_one({"_id": insert_result.inserted_id})
        print(f"DEBUG: Transaction verification: {verification}")
        
        return transaction
    except Exception as e:
        print(f"ERROR in add_transaction: {e}")
        import traceback
        print(f"ERROR traceback: {traceback.format_exc()}")
        raise e

def get_user_transactions(user_id):
    """Get user's transaction history"""
    transactions_collection = db["transactions"]
    
    # Find user to get the correct userId format
    user = find_user_by_id(user_id)
    if not user:
        print(f"ERROR: User not found for ID: {user_id}")
        return []
    
    # Use the userId field for transaction lookup
    transaction_user_id = user.get("userId", str(user["_id"]))
    print(f"DEBUG: Looking for transactions with userId: {transaction_user_id}")
    
    # Try both the userId field and the original user_id passed in
    transactions = list(transactions_collection.find({
        "$or": [
            {"userId": transaction_user_id},
            {"userId": user_id},
            {"userId": str(user["_id"])}
        ]
    }).sort("timestamp", -1))
    
    print(f"DEBUG: Found {len(transactions)} transactions for user")
    
    for transaction in transactions:
        transaction["_id"] = str(transaction["_id"])
    return transactions

def process_ride_payment(rider_id, driver_id, amount, ride_id):
    """Process payment from rider to driver - records transactions for both parties"""
    try:
        # Record expense for rider (negative amount)
        add_transaction(rider_id, -amount, "ride_payment", f"Ride fare payment")
        
        # Record income for driver (positive amount)
        add_transaction(driver_id, amount, "ride_earning", f"Ride fare received")
        
        return {"success": True}
    except Exception as e:
        print(f"Error processing ride payment: {e}")
        return {"success": False, "error": str(e)}
