from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from bson import ObjectId
from datetime import datetime
from user_model import users_collection, serialize_user

auth_bp = Blueprint("auth", __name__)

# ─────────────────────────────────────────────
# POST /auth/signup → Register a new user
# ─────────────────────────────────────────────
@auth_bp.route("/auth/signup", methods=["POST"])
def signup():
    data = request.json
    email = data.get("email")
    phone = data.get("phone", "")
    password = data.get("password")
    first_name = data.get("firstName", "")
    last_name = data.get("lastName", "")

    if not email or not password:
        return jsonify({ "error": "Email and password required" }), 400

    if users_collection.find_one({ "email": email }):
        return jsonify({ "error": "User already exists" }), 400

    hashed_password = generate_password_hash(password)

    user = {
        "userId": str(ObjectId()),
        "email": email,
        "phone": phone,
        "password": hashed_password,
        "firstName": first_name,
        "lastName": last_name,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow()
    }

    users_collection.insert_one(user)
    print("✅ User created:", email)
    return jsonify({ "message": "User created successfully" }), 201

# ─────────────────────────────────────────────
# POST /auth/login → Authenticate user
# ─────────────────────────────────────────────
@auth_bp.route("/auth/login", methods=["POST"])
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")

    user = users_collection.find_one({ "email": email })
    if not user or not check_password_hash(user["password"], password):
        return jsonify({ "error": "Invalid email or password" }), 400

    return jsonify({
        "message": "Login successful",
        "userId": str(user["_id"])
    })

# ─────────────────────────────────────────────
# GET /auth/user/<email> → Get user details
# ─────────────────────────────────────────────
@auth_bp.route("/auth/user/<email>", methods=["GET"])
def get_user_by_email(email):
    user = users_collection.find_one({ "email": email })
    if not user:
        return jsonify({ "error": "User not found" }), 404

    return jsonify(serialize_user(user))

# ─────────────────────────────────────────────
# PUT /auth/update → Update user profile
# ─────────────────────────────────────────────
@auth_bp.route("/auth/update", methods=["PUT"])
def update_user():
    data = request.json
    current_email = data.get("currentEmail")
    first_name = data.get("firstName", "")
    last_name = data.get("lastName", "")
    new_email = data.get("email")
    phone = data.get("phone", "")

    user = users_collection.find_one({ "email": current_email })
    if not user:
        return jsonify({ "error": "User not found" }), 404

    if new_email and new_email != current_email:
        if users_collection.find_one({ "email": new_email, "_id": { "$ne": user["_id"] } }):
            return jsonify({ "error": "This email is already in use" }), 400

    if phone:
        if users_collection.find_one({ "phone": phone, "_id": { "$ne": user["_id"] } }):
            return jsonify({ "error": "This phone number is already in use" }), 400

    update_data = {
        "firstName": first_name,
        "lastName": last_name,
        "email": new_email,
        "phone": phone,
        "updatedAt": datetime.utcnow()
    }

    users_collection.update_one({ "_id": user["_id"] }, { "$set": update_data })
    return jsonify({ "message": "User updated" })

# ─────────────────────────────────────────────
# PUT /auth/change-password → Change password
# ─────────────────────────────────────────────
@auth_bp.route("/auth/change-password", methods=["PUT"])
def change_password():
    data = request.json
    email = data.get("email")
    old_password = data.get("oldPassword")
    new_password = data.get("newPassword")
    confirm_password = data.get("confirmPassword")

    user = users_collection.find_one({ "email": email })
    if not user:
        return jsonify({ "error": "User not found" }), 404

    if not check_password_hash(user["password"], old_password):
        return jsonify({ "error": "Old password is incorrect" }), 400

    if new_password != confirm_password:
        return jsonify({ "error": "New passwords do not match" }), 400

    hashed = generate_password_hash(new_password)
    users_collection.update_one({ "_id": user["_id"] }, {
        "$set": { "password": hashed, "updatedAt": datetime.utcnow() }
    })

    return jsonify({ "message": "Password changed successfully" })

# ─────────────────────────────────────────────
# DELETE /auth/delete/<email> → Delete user
# ─────────────────────────────────────────────
@auth_bp.route("/auth/delete/<email>", methods=["DELETE"])
def delete_user(email):
    result = users_collection.find_one_and_delete({ "email": email })
    if not result:
        return jsonify({ "error": "User not found" }), 404

    return jsonify({ "message": "Account deleted successfully" })
