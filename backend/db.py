from pymongo import MongoClient

# Use environment variable (Docker will provide it)
import os
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/carmago-auth")

client = MongoClient(MONGO_URI)
db = client.get_default_database()
