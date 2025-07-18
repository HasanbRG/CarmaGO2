#!/usr/bin/env python3
"""
Database cleanup script for CarmaGO
Clears all data from MongoDB collections for fresh testing
"""

from pymongo import MongoClient
from datetime import datetime

def clear_database():
    # Connect to MongoDB
    client = MongoClient("mongodb://mongo:27017/")
    db = client["carmago"]
    
    print("🧹 Starting database cleanup...")
    print(f"📅 Cleanup started at: {datetime.now()}")
    
    # List of collections to clear
    collections_to_clear = [
        "users",           # User accounts
        "cars",            # Car records
        "ride_requests",   # Ride requests
        "taxi_jobs",       # Taxi job records
        "sessions"         # User sessions (if any)
    ]
    
    # Clear each collection
    for collection_name in collections_to_clear:
        try:
            collection = db[collection_name]
            count_before = collection.count_documents({})
            
            if count_before > 0:
                result = collection.delete_many({})
                print(f"✅ Cleared '{collection_name}': {result.deleted_count} documents deleted")
            else:
                print(f"ℹ️  Collection '{collection_name}' was already empty")
                
        except Exception as e:
            print(f"❌ Error clearing '{collection_name}': {str(e)}")
    
    # Verify cleanup
    print("\n📊 Database status after cleanup:")
    for collection_name in collections_to_clear:
        try:
            collection = db[collection_name]
            count = collection.count_documents({})
            status = "✅ Empty" if count == 0 else f"⚠️  Still has {count} documents"
            print(f"   {collection_name}: {status}")
        except Exception as e:
            print(f"   {collection_name}: ❌ Error checking - {str(e)}")
    
    print(f"\n🎉 Database cleanup completed at: {datetime.now()}")
    print("🚀 Ready for fresh testing!")

if __name__ == "__main__":
    clear_database()
