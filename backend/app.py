import eventlet
eventlet.monkey_patch()


from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from pymongo import MongoClient
from bson import ObjectId
from threading import Thread, Event
import time
from datetime import datetime
import requests
import traceback

app = Flask(__name__)

# Enable CORS for all routes with wide-open permissions (dev only)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configure SocketIO with explicit CORS
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
    logger=True,  # Helps debug connection issues
    engineio_logger=True  # More debug info
)

# MongoDB Setup
client = MongoClient("mongodb://mongo:27017/")
db = client["carmago-auth"]
cars_collection = db["cars"]
ride_requests_collection = db["ride_requests"]

# Import user model functions
from user_model import get_user_transactions, process_ride_payment, add_transaction

def calculate_distance(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371  # Earth's radius in kilometers
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    return R * c

# Global simulation state
ride_threads = {}
ride_cancel_events = {}
charging_threads = {}
charging_pause_events = {}
request_timeout_threads = {}  # Track timeout threads for ride requests
locate_car_rides = {}  # Track locate-car rides for history saving

def handle_request_timeout(ride_request_id):
    time.sleep(15)  # Wait 15 seconds for driver response
    
    # Check if request still exists and is still pending
    ride_request = ride_requests_collection.find_one({"_id": ObjectId(ride_request_id)})
    if not ride_request or ride_request["status"] != "pending":
        return
        
    print(f"Request {ride_request_id} timed out, finding next driver")
    
    # Add current suggested car to declined list due to timeout
    if ride_request.get("suggestedCarId"):
        ride_requests_collection.update_one(
            {"_id": ObjectId(ride_request_id)},
            {"$addToSet": {"declinedBy": ride_request["suggestedCarId"]}}
        )
    
    # Find next nearest available car, excluding cars that declined or timed out
    declined_cars = ride_request.get("declinedBy", [])
    if ride_request.get("suggestedCarId"):
        declined_cars.append(ride_request["suggestedCarId"])
    
    available_cars = list(cars_collection.find({
        "_id": {"$nin": declined_cars},
        "status": "Idle",
        "battery": {"$gt": 20}
    }))
    
    if not available_cars:
        # No more available cars, notify rider
        socketio.emit('ride-declined', {
            'rideId': str(ride_request_id),
            'status': "declined",
            'reason': "No available drivers"
        })
        
        # Update request status
        ride_requests_collection.update_one(
            {"_id": ObjectId(ride_request_id)},
            {"$set": {
                "status": "declined",
                "reason": "No available drivers",
                "declinedAt": datetime.utcnow()
            }}
        )
        return
        
    # Calculate distances to find next nearest car
    nearest_car = None
    min_distance = float('inf')
    
    for car in available_cars:
        if not car.get('location'):
            continue
            
        distance = calculate_distance(
            ride_request["pickup"]["lat"],
            ride_request["pickup"]["lng"],
            car["location"].get("lat", 0),
            car["location"].get("lng", 0)
        )
        
        if distance < min_distance:
            min_distance = distance
            nearest_car = car
    
    if nearest_car:
        # Update request with new suggested car
        ride_requests_collection.update_one(
            {"_id": ObjectId(ride_request_id)},
            {"$set": {
                "suggestedCarId": nearest_car["_id"],
                "suggestedCarOwnerId": nearest_car["userId"],
                "distance": min_distance
            }}
        )
        
        # Notify new driver
        socketio.emit('new-ride-request', {
            'rideId': str(ride_request_id),
            'riderEmail': ride_request["riderEmail"],
            'pickup': ride_request["pickup"],
            'dropoff': ride_request["dropoff"],
            'fareEstimate': ride_request["fareEstimate"],
            'pickupAddress': ride_request["pickupAddress"],
            'dropoffAddress': ride_request["dropoffAddress"],
            'distance': min_distance,
            'targetCarId': str(nearest_car["_id"]),
            'targetCarOwnerId': str(nearest_car["userId"])
        })
        
        # Start new timeout for next driver
        timeout_thread = Thread(target=handle_request_timeout, args=(ride_request_id,))
        timeout_thread.daemon = True
        timeout_thread.start()
        request_timeout_threads[str(ride_request_id)] = timeout_thread

# ─── Import Blueprints ───
from auth_routes import auth_bp
app.register_blueprint(auth_bp)
from cars_routes import cars_bp
app.register_blueprint(cars_bp)
from user_model import users_collection  # Import users collection

import requests

def reverse_geocode(lat, lng):
    api_key = 'AIzaSyB-8K0ndNli1FxzigKdSe3T0bnqUCw3D_o'
    url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{lng}&key={api_key}"
    resp = requests.get(url)
    try:
        results = resp.json()['results']
        return results[0]['formatted_address'] if results else f"{lat},{lng}"
    except Exception:
        return f"{lat},{lng}"


# --- Reset stuck car statuses on startup ---
def reset_stuck_cars():
    result = cars_collection.update_many(
        {"status": {"$in": ["Working", "Charging"]}},
        {"$set": {"status": "Idle"}}
    )
    if result.modified_count > 0:
        print(f"🧹 Reset {result.modified_count} stuck cars to Idle on startup.")

reset_stuck_cars()

# ─── Ride Simulation ───
def get_route_from_google_maps(start_lat, start_lng, end_lat, end_lng):
    """Get detailed route waypoints that follow actual roads"""
    api_key = "AIzaSyB-8K0ndNli1FxzigKdSe3T0bnqUCw3D_o"
    url = f"https://maps.googleapis.com/maps/api/directions/json"
    
    params = {
        'origin': f"{start_lat},{start_lng}",
        'destination': f"{end_lat},{end_lng}",
        'key': api_key,
        'mode': 'driving',
        'units': 'metric',
        'alternatives': 'false'  # Get the best route only
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data['status'] == 'OK' and len(data['routes']) > 0:
            # Get the main route and decode the detailed polyline
            route = data['routes'][0]
            overview_polyline = route['overview_polyline']['points']
            
            # Decode the polyline to get detailed road-following coordinates
            detailed_coordinates = decode_polyline(overview_polyline)
            
            # Use more waypoints to follow roads more accurately, especially around turns
            total_points = len(detailed_coordinates)
            
            if total_points <= 20:
                # If we have few points, use them all
                waypoints = detailed_coordinates
            else:
                # Use more waypoints for better road following
                # Sample 20 waypoints to capture curves and turns better
                waypoints = [detailed_coordinates[0]]  # Start point
                
                # Add 18 intermediate points evenly spaced
                for i in range(1, 19):
                    index = int((i / 19.0) * (total_points - 1))
                    waypoints.append(detailed_coordinates[index])
                
                # Add end point
                waypoints.append(detailed_coordinates[-1])
            
            print(f"Generated road-following route with {len(waypoints)} waypoints from {total_points} polyline points")
            return waypoints
        else:
            print(f"Google Maps API error: {data.get('status', 'Unknown error')}")
            # Fallback to simple interpolation
            return [(start_lat, start_lng), (end_lat, end_lng)]
    except Exception as e:
        print(f"Error getting route from Google Maps: {e}")
        # Fallback to simple interpolation
        return [(start_lat, start_lng), (end_lat, end_lng)]

def decode_polyline(polyline_str):
    """Decode Google Maps polyline algorithm"""
    index, lat, lng = 0, 0, 0
    coordinates = []
    changes = {'latitude': 0, 'longitude': 0}
    
    # Converts characters to proper latitude and longitude
    while index < len(polyline_str):
        for unit in ['latitude', 'longitude']: 
            shift, result = 0, 0

            while True:
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if not byte >= 0x20:
                    break

            if (result & 1):
                changes[unit] = ~(result >> 1)
            else:
                changes[unit] = (result >> 1)

        lat += changes['latitude']
        lng += changes['longitude']

        coordinates.append((lat / 100000.0, lng / 100000.0))

    return coordinates

def simulate_ride_with_tracking(car_id, route, cancel_event, ride_id=None, ride_request_data=None):
    """Enhanced ride simulation with real-time tracking and status updates"""
    try:
        # Ensure car_id is ObjectId for database operations
        if isinstance(car_id, str):
            car_obj_id = ObjectId(car_id)
            car_id_str = car_id
        else:
            car_obj_id = car_id
            car_id_str = str(car_id)
            
        # Get current car location from database
        car = cars_collection.find_one({"_id": car_obj_id})
        if not car:
            print(f"Car {car_id_str} not found")
            return
            
        car_current_lat = car.get("location", {}).get("lat", 51.4545)
        car_current_lng = car.get("location", {}).get("lng", -2.5879)
        
        # Route should be [pickup, dropoff] - but car starts from its current location
        pickup_lat, pickup_lng = route[0]
        dropoff_lat, dropoff_lng = route[1] if len(route) > 1 else route[0]
        
        # Phase 1: Car drives from current location to pickup
        print(f"Phase 1: Car {car_id_str} driving from current location ({car_current_lat:.5f}, {car_current_lng:.5f}) to pickup ({pickup_lat:.5f}, {pickup_lng:.5f})")
        pickup_route = get_route_from_google_maps(car_current_lat, car_current_lng, pickup_lat, pickup_lng)
        print(f"Pickup route waypoints: {len(pickup_route)} points")
        
        # Phase 2: Car drives from pickup to dropoff  
        print(f"Phase 2: Car {car_id_str} will drive from pickup ({pickup_lat:.5f}, {pickup_lng:.5f}) to dropoff ({dropoff_lat:.5f}, {dropoff_lng:.5f})")
        dropoff_route = get_route_from_google_maps(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
        print(f"Dropoff route waypoints: {len(dropoff_route)} points")
        
        # Remove the first point of dropoff_route to avoid duplicate pickup location
        if len(dropoff_route) > 1:
            dropoff_route = dropoff_route[1:]  # Skip first point as it's the same as pickup
            print(f"Adjusted dropoff route to {len(dropoff_route)} points (removed duplicate pickup)")
        
        # Combine phases with a marker for pickup arrival
        total_waypoints = pickup_route + [("PICKUP_ARRIVED",)] + dropoff_route
        print(f"Total combined waypoints: {len(total_waypoints)} (including pickup arrival marker)")
        
        # Debug: Print first few waypoints of each phase
        print(f"First 3 pickup waypoints: {pickup_route[:3] if len(pickup_route) >= 3 else pickup_route}")
        print(f"First 3 dropoff waypoints: {dropoff_route[:3] if len(dropoff_route) >= 3 else dropoff_route}")
        
        # Much slower, more realistic simulation
        total_steps = len(total_waypoints) * 4  # 4 steps per waypoint for smooth road-following movement
        step_delay = 1.5  # 1.5 second delay for realistic car speed
        current_phase = "driving_to_pickup"
        pickup_arrived = False
        
        print(f"Starting slow realistic simulation with {total_steps} steps, {step_delay}s delay")
        
        # Emit ride started status
        if ride_id:
            socketio.emit('ride-status-update', {
                'rideId': ride_id,
                'status': 'driving_to_pickup',
                'message': 'Driver is on the way to pick you up'
            })
        
        waypoint_index = 0
        sub_step = 0
        
        for i in range(total_steps + 1):
            if cancel_event.is_set():
                print(f"Ride simulation cancelled for car {car_id_str}")
                break
            
            # Check if we hit the pickup arrival marker
            if waypoint_index < len(total_waypoints) and total_waypoints[waypoint_index] == ("PICKUP_ARRIVED",):
                if not pickup_arrived:
                    pickup_arrived = True
                    current_phase = "arrived_at_pickup"
                    
                    # Set car position to exact pickup location
                    current_lat, current_lng = pickup_lat, pickup_lng
                    print(f"Car {car_id_str} arrived at pickup at ({current_lat:.5f}, {current_lng:.5f})!")
                    
                    if ride_id:
                        socketio.emit('driver-arrived', {
                            'rideId': ride_id,
                            'status': 'arrived_at_pickup',
                            'message': 'Driver has arrived - starting your journey'
                        })
                    
                    # Wait a bit at pickup location
                    time.sleep(2)
                    
                    # Process payment when rider is picked up
                    if ride_request_data:
                        try:
                            print("DEBUG: Processing payment at pickup...")
                            fare_amount = float(ride_request_data.get("fareEstimate", 0))
                            rider_id = ride_request_data["riderId"]
                            
                            # Get car owner info to find driver's userId
                            car = cars_collection.find_one({"_id": car_obj_id})
                            if car:
                                # Get rider info to find userId
                                rider = users_collection.find_one({"_id": ObjectId(rider_id)}) or \
                                        users_collection.find_one({"userId": rider_id})
                                
                                # Get driver info to find userId  
                                driver = users_collection.find_one({"_id": car["userId"]}) or \
                                         users_collection.find_one({"userId": str(car["userId"])}) or \
                                         users_collection.find_one({"email": str(car.get("userId", ""))})
                                
                                if rider and driver and fare_amount > 0:
                                    rider_user_id = rider.get("userId")
                                    driver_user_id = driver.get("userId")
                                    
                                    payment_result = process_ride_payment(rider_user_id, driver_user_id, fare_amount, str(ride_id))
                                    if payment_result["success"]:
                                        print(f"✅ Payment processed successfully at pickup - £{fare_amount} from {rider_user_id} to {driver_user_id}")
                                        
                                        # Emit payment confirmation to both parties
                                        socketio.emit('payment-processed', {
                                            'rideId': ride_id,
                                            'amount': fare_amount,
                                            'riderId': rider_user_id,
                                            'driverId': driver_user_id,
                                            'status': 'success',
                                            'message': f'Payment of £{fare_amount} processed successfully'
                                        })
                                    else:
                                        print(f"❌ Payment failed at pickup - {payment_result.get('error', 'Unknown error')}")
                                        
                                        # Emit payment failure
                                        socketio.emit('payment-failed', {
                                            'rideId': ride_id,
                                            'amount': fare_amount,
                                            'error': payment_result.get('error', 'Payment failed'),
                                            'message': 'Payment failed - continuing ride anyway'
                                        })
                                else:
                                    print("DEBUG: Skipping payment - missing rider/driver info or zero fare")
                        except Exception as payment_error:
                            print(f"ERROR processing payment at pickup: {payment_error}")
                    
                    current_phase = "in_progress" 
                    
                    if ride_id:
                        socketio.emit('ride-started', {
                            'rideId': ride_id,
                            'status': 'in_progress', 
                            'message': 'Driving to your destination'
                        })
                
                waypoint_index += 1
                sub_step = 0
                
                # Ensure we have a valid next waypoint after the marker
                if waypoint_index < len(total_waypoints):
                    next_waypoint = total_waypoints[waypoint_index]
                    if len(next_waypoint) == 2:
                        print(f"Transitioning to dropoff route, next waypoint: ({next_waypoint[0]:.5f}, {next_waypoint[1]:.5f})")
                continue
                
            # Calculate current position with smooth interpolation
            if waypoint_index < len(total_waypoints):
                current_waypoint = total_waypoints[waypoint_index]
                
                # Skip special markers
                if len(current_waypoint) != 2:
                    waypoint_index += 1
                    continue
                
                # Get next waypoint for interpolation
                if waypoint_index + 1 < len(total_waypoints):
                    next_waypoint = total_waypoints[waypoint_index + 1]
                    # Make sure next waypoint is valid coordinates, not a marker
                    while waypoint_index + 1 < len(total_waypoints) and len(next_waypoint) != 2:
                        waypoint_index += 1
                        if waypoint_index + 1 < len(total_waypoints):
                            next_waypoint = total_waypoints[waypoint_index + 1]
                        else:
                            break
                    
                    if waypoint_index + 1 < len(total_waypoints) and len(next_waypoint) == 2:
                        # Smooth interpolation between current and next waypoint  
                        # Using fewer steps per waypoint since we have more waypoints
                        progress = sub_step / 4.0  # 4 steps per waypoint for smoother road following
                        current_lat = current_waypoint[0] + (next_waypoint[0] - current_waypoint[0]) * progress
                        current_lng = current_waypoint[1] + (next_waypoint[1] - current_waypoint[1]) * progress
                    else:
                        current_lat, current_lng = current_waypoint
                else:
                    current_lat, current_lng = current_waypoint
                
                # Move to next waypoint when sub_step reaches 4 (reduced from 8)
                sub_step += 1
                if sub_step >= 4:
                    waypoint_index += 1
                    sub_step = 0
            else:
                # At final destination
                current_lat, current_lng = total_waypoints[-1] if total_waypoints else (dropoff_lat, dropoff_lng)
                print(f"Car {car_id_str} reached final destination!")
                break  # Exit the loop when we reach the final destination
            
            # Simulate battery consumption
            battery_drain = 0.3 if i % 3 == 0 else 0  # Drain every 3rd step
            cars_collection.update_one(
                {"_id": car_obj_id},
                {
                    "$set": {
                        "location.lat": current_lat,
                        "location.lng": current_lng
                    },
                    "$inc": {"battery": -battery_drain}
                }
            )
            
            # Get updated car data
            car = cars_collection.find_one({"_id": car_obj_id})
            if not car:
                break
                
            # Check if battery is dead
            if car["battery"] <= 0:
                cars_collection.update_one(
                    {"_id": car_obj_id},
                    {"$set": {"status": "Idle", "battery": 0}}
                )
                
                # Emit ride completion due to battery
                if ride_id:
                    socketio.emit('ride-status-update', {
                        'rideId': ride_id,
                        'status': 'cancelled',
                        'reason': 'Battery dead',
                        'message': 'Ride cancelled due to low battery'
                    })
                
                socketio.emit("ride-completed", {
                    "carId": car_id_str,
                    "reason": "Battery dead"
                })
                break
            
            # Calculate overall progress for ETA
            overall_progress = i / total_steps
            
            # Check for completion when close to end - more realistic timing
            if overall_progress >= 0.90:  # 90% progress triggers completion
                print(f"Car {car_id_str} reached 90% progress - completing ride")
                # Set to final destination
                current_lat, current_lng = total_waypoints[-1] if total_waypoints else (dropoff_lat, dropoff_lng)
                overall_progress = 1.0  # Force 100% completion
                
                # Update final location immediately
                cars_collection.update_one(
                    {"_id": car_obj_id},
                    {"$set": {
                        "status": "Idle",
                        "location": {
                            "lat": current_lat,
                            "lng": current_lng
                        }
                    }}
                )
                
                # Handle ride completion for both taxi-job and locate-car
                if ride_id:
                    # This is a taxi-job ride
                    ride_request = ride_requests_collection.find_one({"_id": ObjectId(ride_id)})
                    ride_requests_collection.update_one(
                        {"_id": ObjectId(ride_id)},
                        {"$set": {
                            "status": "completed",
                            "completedAt": datetime.utcnow()
                        }}
                    )
                    
                    # Save completed ride to history
                    if ride_request:
                        save_ride_to_history(ride_request, "completed", "Completed")
                    
                    # Emit ride completion status update immediately
                    socketio.emit('ride-status-update', {
                        'rideId': ride_id,
                        'status': 'completed',
                        'message': 'Ride completed successfully!'
                    })
                elif car_id_str in locate_car_rides:
                    # This is a locate-car ride - save to history
                    ride_data = locate_car_rides[car_id_str]
                    pickup_coords = ride_data['pickup_location']
                    dropoff_coords = ride_data['destination']
                    save_locate_car_ride_to_history(
                        car_id_str, 
                        "locate-car-user",  # Default user for locate-car rides
                        pickup_coords, 
                        dropoff_coords, 
                        "completed", 
                        "Completed"
                    )
                    # Clean up from tracking
                    del locate_car_rides[car_id_str]
                
                # Emit ride completed event immediately
                socketio.emit("ride-completed", {
                    "carId": car_id_str,
                    "reason": "Completed"
                })
                
                # Emit final location update immediately
                socketio.emit("car-update", {
                    "carId": car_id_str,
                    "lat": current_lat,
                    "lng": current_lng,
                    "battery": car["battery"],
                    "status": "Idle",
                    "rideId": None,
                    "progress": 1.0,
                    "phase": "completed"
                })
                
                print(f"Car {car_id_str} completed ride instantly")
                return  # Exit the function immediately - no more processing
            
            # Calculate remaining steps for more accurate ETA
            remaining_steps = total_steps - i
            remaining_time_seconds = remaining_steps * step_delay
            
            # Format remaining time
            if remaining_time_seconds <= 0:
                eta_formatted = "0s"
            else:
                remaining_minutes = int(remaining_time_seconds // 60)
                remaining_seconds = int(remaining_time_seconds % 60)
                if remaining_minutes > 0:
                    eta_formatted = f"{remaining_minutes}m {remaining_seconds}s"
                else:
                    eta_formatted = f"{remaining_seconds}s"
            
            # Emit car location update with enhanced data
            socketio.emit("car-update", {
                "carId": car_id_str,
                "lat": current_lat,
                "lng": current_lng,
                "battery": car["battery"],
                "status": car["status"],
                "rideId": ride_id,
                "progress": overall_progress,
                "phase": current_phase,
                "driverETA": eta_formatted,
                "remainingSteps": remaining_steps,
                "totalSteps": total_steps
            })
            
            time.sleep(step_delay)
        
        # This code only runs if we didn't exit early via the 90% completion check
        # Ride completed - car reaches final destination
        print(f"Car {car_id_str} completed ride (normal completion)")
        
        # Set car status to Idle and update final location
        cars_collection.update_one(
            {"_id": car_obj_id},
            {"$set": {
                "status": "Idle",
                "location": {
                    "lat": current_lat,
                    "lng": current_lng
                }
            }}
        )
        
        # Handle ride completion for both taxi-job and locate-car
        if ride_id:
            # This is a taxi-job ride
            ride_request = ride_requests_collection.find_one({"_id": ObjectId(ride_id)})
            ride_requests_collection.update_one(
                {"_id": ObjectId(ride_id)},
                {"$set": {
                    "status": "completed",
                    "completedAt": datetime.utcnow()
                }}
            )
            
            # Save completed ride to history
            if ride_request:
                save_ride_to_history(ride_request, "completed", "Completed")
            
            # Emit ride completion status update
            socketio.emit('ride-status-update', {
                'rideId': ride_id,
                'status': 'completed',
                'message': 'Ride completed successfully!'
            })
        elif car_id_str in locate_car_rides:
            # This is a locate-car ride - save to history
            ride_data = locate_car_rides[car_id_str]
            pickup_coords = ride_data['pickup_location']
            dropoff_coords = ride_data['destination']
            save_locate_car_ride_to_history(
                car_id_str, 
                "locate-car-user",  # Default user for locate-car rides
                pickup_coords, 
                dropoff_coords, 
                "completed", 
                "Completed"
            )
            # Clean up from tracking
            del locate_car_rides[car_id_str]
        
        # Emit ride completed event
        socketio.emit("ride-completed", {
            "carId": car_id_str,
            "reason": "Completed"
        })
        
        # Emit final car location update with Idle status
        socketio.emit("car-update", {
            "carId": car_id_str,
            "lat": current_lat,
            "lng": current_lng,
            "battery": car["battery"] if car else 50,
            "status": "Idle"
        })
        
    except Exception as e:
        print(f"Error in ride simulation: {e}")
        cars_collection.update_one(
            {"_id": ObjectId(car_id_str) if isinstance(car_id, str) else car_id},
            {"$set": {"status": "Idle"}}
        )

# Legacy function for backward compatibility
def simulate_ride(car_id, path, cancel_event):
    """Legacy ride simulation function"""
    return simulate_ride_with_tracking(car_id, path, cancel_event, None)

    steps_per_leg = 10
    step_delay = 0.3
    car_id_str = str(car_id)

    car = cars_collection.find_one({"_id": car_id})
    battery = car.get("battery", 100.0)
    total_steps = (len(path) - 1) * steps_per_leg
    eta_seconds = total_steps * step_delay

    time.sleep(0.1)
    # Remove duplicate eta-update emission - ETA is now included in car-update events

    for i in range(len(path) - 1):
        lat1, lng1 = path[i]
        lat2, lng2 = path[i + 1]
        for step in range(steps_per_leg):
            if cancel_event.is_set():
                cars_collection.update_one({"_id": car_id}, {
                    "$set": {"location.lat": lat1, "location.lng": lng1, "battery": battery, "status": "Idle"}
                })
                socketio.emit("car-update", {
                    "carId": car_id_str, "lat": lat1, "lng": lng1, "battery": battery, "status": "Idle"
                })
                socketio.emit("ride-completed", {
                    "carId": car_id_str, "lat": lat1, "lng": lng1, "reason": "Cancelled"
                })

                # Save cancelled ride with addresses
                from_address = reverse_geocode(*path[0])
                to_address = reverse_geocode(lat1, lng1)
                rides_collection = db["rides"]
                rides_collection.insert_one({
                    "userId": car["userId"],
                    "carId": car.get("carId"),
                    "carName": car.get("name"),
                    "start": path[0],
                    "end": [lat1, lng1],
                    "fromAddress": from_address,
                    "toAddress": to_address,
                    "batteryUsed": round(car.get("battery", 100.0) - battery, 1),
                    "duration": int(eta_seconds),  # or optionally track actual elapsed time so far
                    "date": datetime.utcnow(),
                    "reason": "Cancelled"
                })
                ride_cancel_events.pop(car_id_str, None)
                ride_threads.pop(car_id_str, None)
                return

            progress = step / steps_per_leg
            lat = lat1 + (lat2 - lat1) * progress
            lng = lng1 + (lng2 - lng1) * progress
            battery = max(0, battery - 0.1)

            if battery <= 0:
                cars_collection.update_one({"_id": car_id}, {
                    "$set": {"location.lat": lat, "location.lng": lng, "battery": 0, "status": "Idle"}
                })
                socketio.emit("car-update", {
                    "carId": car_id_str, "lat": lat, "lng": lng, "battery": 0, "status": "Idle"
                })
                socketio.emit("ride-completed", {
                    "carId": car_id_str, "lat": lat, "lng": lng, "reason": "Battery dead"
                })

                # Save battery dead ride with addresses
                from_address = reverse_geocode(*path[0])
                to_address = reverse_geocode(lat, lng)
                rides_collection = db["rides"]
                rides_collection.insert_one({
                    "userId": car["userId"],
                    "carId": car.get("carId"),
                    "carName": car.get("name"),
                    "start": path[0],
                    "end": [lat, lng],
                    "fromAddress": from_address,
                    "toAddress": to_address,
                    "batteryUsed": round(car.get("battery", 100.0) - 0, 1),  # battery is now 0
                    "duration": int(eta_seconds),
                    "date": datetime.utcnow(),
                    "reason": "Battery dead"
                })
                ride_cancel_events.pop(car_id_str, None)
                ride_threads.pop(car_id_str, None)
                return

            cars_collection.update_one({"_id": car_id}, {
                "$set": {"location.lat": lat, "location.lng": lng, "battery": round(battery, 1), "status": "Working"}
            })
            socketio.emit("car-update", {
                "carId": car_id_str, "lat": lat, "lng": lng, "battery": round(battery, 1), "status": "Working"
            })
            
            # Check if this is the last step to avoid unnecessary delay
            if i < len(path) - 1:
                time.sleep(step_delay)

    final_lat, final_lng = path[-1]
    cars_collection.update_one({"_id": car_id}, {
        "$set": {"location.lat": final_lat, "location.lng": final_lng, "battery": round(battery, 1), "status": "Idle"}
    })
    socketio.emit("car-update", {
        "carId": car_id_str, "lat": final_lat, "lng": final_lng, "battery": round(battery, 1), "status": "Idle"
    })
    socketio.emit("ride-completed", {
        "carId": car_id_str, "lat": final_lat, "lng": final_lng, "reason": "Completed"
    })

    # Save completed ride with addresses
    from_address = reverse_geocode(*path[0])
    to_address = reverse_geocode(final_lat, final_lng)
    rides_collection = db["rides"]
    rides_collection.insert_one({
        "userId": car["userId"],
        "carId": car.get("carId"),
        "carName": car.get("name"),
        "start": path[0],
        "end": [final_lat, final_lng],
        "fromAddress": from_address,
        "toAddress": to_address,
        "batteryUsed": round(car.get("battery", 100.0) - battery, 1),
        "duration": int(eta_seconds),
        "date": datetime.utcnow(),
        "reason": "Completed"
    })

    ride_cancel_events.pop(car_id_str, None)
    ride_threads.pop(car_id_str, None)

# ─── Charging Simulation ───
def simulate_charging(car_id):
    car_id_str = str(car_id)
    try:
        while True:
            pause_event = charging_pause_events.get(car_id_str)
            if pause_event and pause_event.is_set():
                cars_collection.update_one({"_id": car_id}, {"$set": {"status": "Idle"}})
                car = cars_collection.find_one({"_id": car_id})
                socketio.emit("car-update", {
                    "carId": car_id_str,
                    "lat": car["location"]["lat"],
                    "lng": car["location"]["lng"],
                    "battery": car["battery"],
                    "status": "Idle"
                })
                break  # Stop charging on pause

            car = cars_collection.find_one({"_id": car_id})
            battery = car.get("battery", 0)
            if battery >= 100:
                break

            battery = min(100, battery + 1)
            cars_collection.update_one(
                {"_id": car_id},
                {"$set": {"battery": battery, "status": "Charging"}}
            )
            socketio.emit("car-update", {
                "carId": car_id_str,
                "lat": car["location"]["lat"],
                "lng": car["location"]["lng"],
                "battery": battery,
                "status": "Charging"
            })
            time.sleep(0.5)

        # After charging is complete or paused, set status to Idle
        cars_collection.update_one({"_id": car_id}, {"$set": {"status": "Idle"}})
        car = cars_collection.find_one({"_id": car_id})
        socketio.emit("car-update", {
            "carId": car_id_str,
            "lat": car["location"]["lat"],
            "lng": car["location"]["lng"],
            "battery": car["battery"],
            "status": "Idle"
        })
    finally:
        charging_threads.pop(car_id_str, None)
        charging_pause_events.pop(car_id_str, None)

# ─── API Endpoints ───
@app.route("/cars", methods=["POST"])
def create_car():
    data = request.get_json()
    lat = data.get("lat")
    lng = data.get("lng")
    if lat is None or lng is None:
        return jsonify({"error": "Missing location coordinates"}), 400

    car = {
        "userId": ObjectId(data["userId"]),
        "email": data.get("email"),
        "carId": data["carId"],
        "name": data.get("name", ""),
        "model": data["model"],
        "status": "Idle",
        "battery": 100.0,
        "location": {"lat": lat, "lng": lng},
        "createdAt": datetime.utcnow()
    }

    cars_collection.insert_one(car)
    return jsonify({"status": "success", "message": "✅ Car added successfully."}), 201

@app.route("/start-ride", methods=["POST"])
def start_ride():
    data = request.get_json()
    car_id_str = str(data["carId"])
    car_id = ObjectId(car_id_str)
    route = data.get("route")

    if not route:
        return {"status": "error", "error": "Missing route"}, 400

    car = cars_collection.find_one({"_id": car_id})
    if not car:
        return {"status": "error", "error": "Car not found"}, 404
    if car.get("status") in ["Working", "Charging"]:
        return {"status": "error", "error": f"Car is currently {car['status']}"}, 400

    # Extract pickup (first point) and dropoff (last point) from the full route
    # This is what simulate_ride_with_tracking expects
    pickup_dropoff_route = [
        (route[0]["lat"], route[0]["lng"]),  # Pickup location (first waypoint)
        (route[-1]["lat"], route[-1]["lng"])  # Dropoff location (last waypoint)
    ]
    
    # Update car status to Working
    cars_collection.update_one(
        {"_id": car_id},
        {"$set": {"status": "Working"}}
    )
    
    # Get updated car data and emit immediate status update
    updated_car = cars_collection.find_one({"_id": car_id})
    socketio.emit("car-update", {
        "carId": car_id_str,
        "lat": updated_car["location"]["lat"],
        "lng": updated_car["location"]["lng"],
        "battery": updated_car["battery"],
        "status": "Working"
    })
    
    # Track locate-car rides for history (since this endpoint is used by locate-car page)
    # Get car's current location as pickup for personal use
    car_current_location = {
        'lat': updated_car["location"]["lat"], 
        'lng': updated_car["location"]["lng"]
    }
    
    locate_car_rides[car_id_str] = {
        'car_id': car_id_str,
        'pickup_location': car_current_location,  # Car's current location as pickup
        'destination': {'lat': route[-1]["lat"], 'lng': route[-1]["lng"]},
        'start_time': datetime.utcnow(),
        'source': 'locate-car',
        'route': route
    }
    
    cancel_event = Event()
    ride_cancel_events[car_id_str] = cancel_event
    thread = Thread(target=simulate_ride, args=(car_id, pickup_dropoff_route, cancel_event))
    ride_threads[car_id_str] = thread
    thread.start()
    return {"status": "started"}

@app.route("/cancel-ride", methods=["POST"])
def cancel_ride():
    try:
        data = request.get_json()
        print(f"Cancel ride request data: {data}")
        
        ride_id = data.get("rideId")
        car_id = data.get("carId")
        cancelled_by = data.get("cancelledBy")  # "rider" or "driver"
        reason = data.get("reason", "Cancelled")

        print(f"Parsed data - ride_id: {ride_id}, car_id: {car_id}, cancelled_by: {cancelled_by}")

        # Handle both rideId (taxi-job page) and carId (locate-car page) cases
        if ride_id:
            print(f"Looking for ride request with ID: {ride_id}")
            # Original logic for taxi-job page with ride requests
            try:
                ride_request = ride_requests_collection.find_one({"_id": ObjectId(ride_id)})
            except Exception as e:
                print(f"Error parsing ride_id as ObjectId: {e}")
                return jsonify({"error": f"Invalid ride ID format: {ride_id}"}), 400
                
            if not ride_request:
                print(f"Ride request not found for ID: {ride_id}")
                return jsonify({"error": "Ride request not found"}), 404

            print(f"Found ride request with status: {ride_request.get('status')}")
            # Only allow cancellation if ride is pending, accepted, or in progress
            if ride_request["status"] not in ["pending", "accepted", "in_progress"]:
                print(f"Cannot cancel ride in status: {ride_request['status']}")
                return jsonify({"error": "Cannot cancel ride in current status"}), 400

            # Get car ID from either assignedCarId or suggestedCarId
            car_id_from_request = ride_request.get("assignedCarId") or ride_request.get("suggestedCarId")
            print(f"Car assignment - assignedCarId: {ride_request.get('assignedCarId')}, suggestedCarId: {ride_request.get('suggestedCarId')}")
            if not car_id_from_request:
                print("No car assigned to this ride")
                return jsonify({"error": "No car assigned to this ride"}), 400
            car_id_for_cancellation = str(car_id_from_request)
        elif car_id:
            # New logic for locate-car page (direct car control)
            car_id_for_cancellation = str(car_id)
            
            # Check if car is actually working
            car = cars_collection.find_one({"_id": ObjectId(car_id)})
            if not car:
                return jsonify({"error": "Car not found"}), 404
            
            if car.get("status") != "Working":
                return jsonify({"error": "No active ride to cancel"}), 400
        else:
            return jsonify({"error": "Missing rideId or carId"}), 400

        # Handle ride request updates and history saving
        if ride_id:
            # Update ride request status for taxi-job cancellations
            ride_requests_collection.update_one(
                {"_id": ObjectId(ride_id)},
                {"$set": {
                    "status": "cancelled",
                    "cancelledBy": cancelled_by,
                    "cancellationReason": reason,
                    "cancelledAt": datetime.utcnow()
                }}
            )

            # Save cancelled ride to history
            save_ride_to_history(ride_request, "cancelled", reason)

            # If car was assigned, free it up
            if ride_request.get("assignedCarId") or ride_request.get("suggestedCarId"):
                # Use the same car we identified earlier
                assigned_car_id = car_id_for_cancellation
            else:
                assigned_car_id = car_id_for_cancellation
        else:
            # For locate-car page, save ride history if we have ride data
            if car_id_for_cancellation in locate_car_rides:
                ride_data = locate_car_rides[car_id_for_cancellation]
                pickup_coords = ride_data['pickup_location']
                dropoff_coords = ride_data['destination']
                save_locate_car_ride_to_history(
                    car_id_for_cancellation, 
                    "locate-car-user",  # Default user for locate-car rides
                    pickup_coords, 
                    dropoff_coords, 
                    "cancelled", 
                    reason
                )
                # Clean up from tracking
                del locate_car_rides[car_id_for_cancellation]
            
            assigned_car_id = car_id_for_cancellation

        # Free up the car
        cars_collection.update_one(
            {"_id": ObjectId(assigned_car_id)},
            {"$set": {"status": "Idle"}}
        )
        
        # Stop car simulation if it was moving
        if assigned_car_id in ride_cancel_events:
            ride_cancel_events[assigned_car_id].set()
            ride_cancel_events.pop(assigned_car_id, None)
        
        if assigned_car_id in ride_threads:
            ride_threads.pop(assigned_car_id, None)

        # Emit car status update
        car = cars_collection.find_one({"_id": ObjectId(assigned_car_id)})
        if car:
            socketio.emit("car-update", {
                "carId": assigned_car_id,
                "lat": car["location"]["lat"],
                "lng": car["location"]["lng"],
                "battery": car["battery"],
                "status": "Idle"
            })

        # Clean up timeout thread (only for ride requests)
        if ride_id:
            request_timeout_threads.pop(ride_id, None)

            # Notify all parties about the cancellation
            socketio.emit('ride-cancelled', {
                'rideId': ride_id,
                'cancelledBy': cancelled_by,
                'reason': reason,
                'status': "cancelled"
            })

        return jsonify({"status": "cancelled", "message": "Ride cancelled successfully"})

    except Exception as e:
        print(f"Error cancelling ride: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/charge-car", methods=["POST"])
def charge_car():
    try:
        car_id = request.json["carId"]
        print("Received charge request for car_id:", car_id)
        car_id_obj = ObjectId(car_id)
        if str(car_id_obj) in charging_threads:
            return {"status": "already-charging"}
        charging_pause_events[str(car_id_obj)] = Event()
        charging_threads[str(car_id_obj)] = Thread(target=simulate_charging, args=(car_id_obj,))
        charging_threads[str(car_id_obj)].start()
        return {"status": "charging"}
    except Exception as e:
        print("Charge car error:", e)
        return {"status": "error", "message": str(e)}, 400

@app.route("/pause-charging", methods=["POST"])
def pause_charging():
    car_id = str(request.json["carId"])
    if car_id in charging_pause_events:
        charging_pause_events[car_id].set()
        return {"status": "paused"}
    return {"status": "error", "error": "Not charging"}, 400

@app.route("/resume-charging", methods=["POST"])
def resume_charging():
    car_id = str(request.json["carId"])
    if car_id in charging_pause_events:
        charging_pause_events[car_id].clear()
        return {"status": "resumed"}
    return {"status": "error", "error": "Charging not started"}, 400

@app.route("/rides/user/<user_id>", methods=["GET"])
def get_rides_for_user(user_id):
    rides_collection = db["rides"]
    rides = list(rides_collection.find({"userId": ObjectId(user_id)}))
    def serialize_ride(r):
        r["_id"] = str(r["_id"])
        r["userId"] = str(r["userId"])
        return r
    return jsonify([serialize_ride(r) for r in rides])

@app.route("/request-ride", methods=["POST"])
def create_ride_request():
    try:
        print("Received request to create ride")
        data = request.get_json()
        print(f"Incoming payload: {data}")

        rider_id = data.get("riderId")
        rider_email = data.get("riderEmail")
        pickup = data.get("pickup")   # Expects dict: {lat: ..., lng: ...}
        dropoff = data.get("dropoff") # Expects dict: {lat: ..., lng: ...}
        fare_estimate = data.get("fareEstimate", 0)

        print("Parsed data:")
        print(f"rider_id: {rider_id}")
        print(f"rider_email: {rider_email}")
        print(f"pickup: {pickup}")
        print(f"dropoff: {dropoff}")
        print(f"fare_estimate: {fare_estimate}")

        if not (rider_id and pickup and dropoff):
            print("Missing required fields")
            return jsonify({"error": "Missing fields"}), 400

        # No need to convert riderId to ObjectId since we store it as string
        if not rider_id:
            print("Missing rider_id")
            return jsonify({"error": "Invalid rider ID"}), 400

        # Get addresses for pickup and dropoff
        try:
            pickup_addr = reverse_geocode(pickup["lat"], pickup["lng"])
            dropoff_addr = reverse_geocode(dropoff["lat"], dropoff["lng"])
            print(f"Pickup address: {pickup_addr}, Dropoff address: {dropoff_addr}")
        except Exception as e:
            print(f"Geocoding failed: {e}")
            return jsonify({"error": "Geocoding failed"}), 400

        # Find nearest available car
        available_cars = list(cars_collection.find({
            "status": "Idle",
            "battery": {"$gt": 20}  # Only cars with > 20% battery
        }))

        if not available_cars:
            return jsonify({"error": "No cars available"}), 400

        nearest_car = None
        min_distance = float('inf')
        
        # Calculate distances to find nearest car
        for car in available_cars:
            if not car.get('location'):
                continue
                
            distance = calculate_distance(
                pickup["lat"],
                pickup["lng"],
                car["location"].get("lat", 0),
                car["location"].get("lng", 0)
            )
            
            if distance < min_distance:
                min_distance = distance
                nearest_car = car

        if not nearest_car:
            return jsonify({"error": "No available cars nearby"}), 400

        # Create ride request document
        ride_request = {
            "riderId": rider_id,
            "riderEmail": rider_email,
            "pickup": pickup,
            "dropoff": dropoff,
            "pickupAddress": pickup_addr,
            "dropoffAddress": dropoff_addr,
            "fareEstimate": fare_estimate,
            "status": "pending",
            "suggestedCarId": nearest_car["_id"],
            "suggestedCarOwnerId": nearest_car["userId"],
            "distance": min_distance,
            "createdAt": datetime.utcnow()
        }
        
        result = ride_requests_collection.insert_one(ride_request)
        ride_request_id = str(result.inserted_id)

        # Send request to nearest driver
        target_car_owner_id = str(nearest_car["userId"])
        print(f"Emitting new-ride-request to driver {target_car_owner_id}")
        
        # Check if driver is connected
        driver_connection = connected_drivers.get(target_car_owner_id)
        if driver_connection:
            # Send to specific driver's socket
            socketio.emit("new-ride-request", {
                "rideId": ride_request_id,
                "riderEmail": rider_email,
                "pickup": pickup,
                "dropoff": dropoff,
                "fareEstimate": fare_estimate,
                "pickupAddress": pickup_addr,
                "dropoffAddress": dropoff_addr,
                "distance": min_distance,
                "targetCarId": str(nearest_car["_id"]),
                "targetCarOwnerId": target_car_owner_id
            }, room=driver_connection["socketId"])
            print(f"✅ Sent ride request to driver's socket: {driver_connection['socketId']}")
        else:
            print(f"���️ Driver {target_car_owner_id} not connected, sending broadcast")
            # Fallback to broadcast if driver not connected
            socketio.emit("new-ride-request", {
                "rideId": ride_request_id,
                "riderEmail": rider_email,
                "pickup": pickup,
                "dropoff": dropoff,
                "fareEstimate": fare_estimate,
                "pickupAddress": pickup_addr,
                "dropoffAddress": dropoff_addr,
                "distance": min_distance,
                "targetCarId": str(nearest_car["_id"]),
                "targetCarOwnerId": target_car_owner_id
            })

        # Start timeout thread for driver response
        timeout_thread = Thread(target=handle_request_timeout, args=(ride_request_id,))
        timeout_thread.daemon = True
        timeout_thread.start()
        request_timeout_threads[ride_request_id] = timeout_thread

        return jsonify({
            "status": "ok",
            "rideRequestId": ride_request_id,
            "message": "Ride request sent to nearest driver"
        })

    except Exception as e:
        print(f"Error creating ride request: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/ride-requests/pending", methods=["GET"])
def get_pending_ride_requests():
    # Optionally: filter by area, but for now return all pending requests
    requests = list(ride_requests_collection.find({"status": "pending"}))
    for r in requests:
        r["_id"] = str(r["_id"])
        r["riderId"] = str(r["riderId"])
    return jsonify(requests)


@app.route("/ride-request/accept", methods=["POST"])
def accept_ride_request():
    try:
        print("DEBUG: Starting accept_ride_request")
        data = request.get_json()
        print(f"DEBUG: Received data: {data}")
        
        ride_request_id = data.get("rideRequestId")
        car_owner_id = data.get("carOwnerId")
        car_id = data.get("carId")

        print(f"DEBUG: Parsed values - rideRequestId: {ride_request_id}, carOwnerId: {car_owner_id}, carId: {car_id}")

        if not (ride_request_id and car_owner_id and car_id):
            print("DEBUG: Missing required fields")
            return jsonify({"error": "Missing fields"}), 400

        # Find the ride request first
        print(f"DEBUG: Looking for ride request with ID: {ride_request_id}")
        ride_request = ride_requests_collection.find_one({"_id": ObjectId(ride_request_id)})
        if not ride_request:
            print("DEBUG: Ride request not found")
            return jsonify({"error": "Ride request not found"}), 404

        print(f"DEBUG: Found ride request: {ride_request}")

        # Verify this is the suggested car/owner
        suggested_car_id = str(ride_request["suggestedCarId"])
        suggested_owner_id = str(ride_request["suggestedCarOwnerId"])
        
        print(f"DEBUG: Suggested car: {suggested_car_id}, provided car: {car_id}")
        print(f"DEBUG: Suggested owner: {suggested_owner_id}, provided owner: {car_owner_id}")
        
        if suggested_car_id != car_id or suggested_owner_id != car_owner_id:
            print("DEBUG: Car/owner mismatch")
            return jsonify({"error": "This ride was suggested for a different car"}), 400

        # Get car and driver details
        print(f"DEBUG: Looking for car with ID: {car_id}")
        car = cars_collection.find_one({"_id": ObjectId(car_id)})
        if not car:
            print("DEBUG: Car not found")
            return jsonify({"error": "Car not found"}), 404

        print(f"DEBUG: Found car: {car}")

        # Find and update the ride request
        print("DEBUG: Updating ride request status")
        result = ride_requests_collection.update_one(
            {"_id": ObjectId(ride_request_id), "status": "pending"},
            {"$set": {
                "status": "accepted",
                "assignedCarId": ObjectId(car_id),
                "assignedCarOwnerId": ObjectId(car_owner_id),
                "acceptedAt": datetime.utcnow()
            }}
        )

        print(f"DEBUG: Update result modified_count: {result.modified_count}")
        if result.modified_count == 0:
            print("DEBUG: No documents were modified - either not found or already accepted")
            return jsonify({"error": "Request not found or already accepted"}), 400

        # Clean up timeout thread
        try:
            request_timeout_threads.pop(ride_request_id, None)
            print("DEBUG: Timeout thread cleanup successful")
        except Exception as cleanup_error:
            print(f"WARNING: Timeout thread cleanup failed: {cleanup_error}")

        # Update car status
        print("DEBUG: Updating car status to Working")
        cars_collection.update_one(
            {"_id": ObjectId(car_id)},
            {"$set": {"status": "Working"}}
        )

        # Payment will be processed when rider is picked up during the ride simulation

        # Start the enhanced ride simulation with tracking
        print("DEBUG: Starting ride simulation")
        path = [
            (ride_request["pickup"]["lat"], ride_request["pickup"]["lng"]),
            (ride_request["dropoff"]["lat"], ride_request["dropoff"]["lng"])
        ]
        cancel_event = Event()
        ride_cancel_events[car_id] = cancel_event
        thread = Thread(target=simulate_ride_with_tracking, args=(car_id, path, cancel_event, ride_request_id, ride_request))
        ride_threads[car_id] = thread
        thread.start()

        # Get driver info from users collection
        print("DEBUG: Getting driver information")
        try:
            # Try both ObjectId and string format for userId
            driver = users_collection.find_one({"_id": ObjectId(car_owner_id)}) or \
                     users_collection.find_one({"userId": car_owner_id}) or \
                     users_collection.find_one({"email": car.get("userId", "")})
            driver_name = driver.get("name", driver.get("firstName", "Driver")) if driver else "Driver"
            print(f"DEBUG: Driver info: {driver}")
            print(f"DEBUG: Driver name: {driver_name}")
        except Exception as driver_error:
            print(f"ERROR getting driver info: {driver_error}")
            driver_name = "Driver"  # Fallback

        # Prepare socket data with extra ObjectId safety
        try:
            socket_data = {
                'rideId': str(ride_request_id),
                'carOwnerId': str(car_owner_id),
                'carId': str(car_id),
                'carName': str(car.get("name", "Car")),
                'carModel': str(car.get("model", "Unknown")),
                'driverName': str(driver_name),
                'carOwnerEmail': str(car.get("userId", "")),
                'carLocation': {
                    'lat': float(car.get("location", {}).get("lat", 0)),
                    'lng': float(car.get("location", {}).get("lng", 0))
                },
                'status': "accepted"
            }
            print(f"DEBUG: Socket data prepared: {socket_data}")
        except Exception as data_error:
            print(f"ERROR preparing socket data: {data_error}")
            # Fallback minimal data
            socket_data = {
                'rideId': str(ride_request_id),
                'carOwnerId': str(car_owner_id),
                'carId': str(car_id),
                'carName': "Car",
                'carModel': "Unknown",
                'driverName': "Driver",
                'carOwnerEmail': "",
                'carLocation': {'lat': 0, 'lng': 0},
                'status': "accepted"
            }

        # Extra safety: convert any potential ObjectIds to strings recursively
        def clean_objectids(obj):
            if isinstance(obj, ObjectId):
                return str(obj)
            elif isinstance(obj, dict):
                return {k: clean_objectids(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_objectids(item) for item in obj]
            else:
                return obj
        
        cleaned_socket_data = clean_objectids(socket_data)
        print(f"DEBUG: Cleaned socket data: {cleaned_socket_data}")

        # Notify the rider about the ride acceptance with detailed info
        print("DEBUG: Emitting ride-accepted event")
        try:
            socketio.emit('ride-accepted', cleaned_socket_data)
            print("DEBUG: Socket emission successful")
        except Exception as socket_error:
            print(f"ERROR in socket emission: {socket_error}")

        print("DEBUG: Returning success response")
        return jsonify({"status": "accepted"})
        
    except Exception as e:
        print(f"ERROR in accept_ride_request: {str(e)}")
        print(f"ERROR type: {type(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/ride-request/decline", methods=["POST"])
def decline_ride_request():
    data = request.get_json()
    ride_request_id = data.get("rideRequestId")
    car_owner_id = data.get("carOwnerId")

    if not ride_request_id:
        return jsonify({"error": "Missing rideRequestId"}), 400

    # Add the declining car to a list of declined cars for this request
    # but keep the request as pending for other drivers
    ride_requests_collection.update_one(
        {"_id": ObjectId(ride_request_id), "status": "pending"},
        {"$addToSet": {
            "declinedBy": ObjectId(car_owner_id) if car_owner_id else None
        }}
    )

    print(f"Driver {car_owner_id} declined ride {ride_request_id}, finding next driver...")
    
    # Find next available driver immediately (similar to timeout logic)
    ride_request = ride_requests_collection.find_one({"_id": ObjectId(ride_request_id)})
    if not ride_request or ride_request["status"] != "pending":
        return jsonify({"status": "declined"})
    
    # Find next nearest available car, excluding cars that have already declined
    declined_cars = ride_request.get("declinedBy", [])
    available_cars = list(cars_collection.find({
        "_id": {"$nin": declined_cars},
        "status": "Idle", 
        "battery": {"$gt": 20}
    }))
    
    if not available_cars:
        # No more available cars, notify rider and update status
        socketio.emit('ride-declined', {
            'rideId': str(ride_request_id),
            'status': "declined",
            'reason': "No available drivers"
        })
        
        ride_requests_collection.update_one(
            {"_id": ObjectId(ride_request_id)},
            {"$set": {
                "status": "declined",
                "reason": "No available drivers", 
                "finalDeclinedAt": datetime.utcnow()
            }}
        )
        return jsonify({"status": "declined", "reason": "No available drivers"})
    
    # Find nearest car from remaining available cars
    min_distance = float('inf')
    nearest_car = None
    
    for car in available_cars:
        distance = calculate_distance(
            ride_request["pickup"]["lat"],
            ride_request["pickup"]["lng"],
            car["location"].get("lat", 0),
            car["location"].get("lng", 0)
        )
        
        if distance < min_distance:
            min_distance = distance
            nearest_car = car
    
    if nearest_car:
        # Update request with new suggested car
        ride_requests_collection.update_one(
            {"_id": ObjectId(ride_request_id)},
            {"$set": {
                "suggestedCarId": nearest_car["_id"],
                "suggestedCarOwnerId": nearest_car["userId"],
                "distance": min_distance
            }}
        )
        
        # Notify new driver
        socketio.emit('new-ride-request', {
            'rideId': str(ride_request_id),
            'riderEmail': ride_request["riderEmail"],
            'pickup': ride_request["pickup"],
            'dropoff': ride_request["dropoff"], 
            'fareEstimate': ride_request["fareEstimate"],
            'pickupAddress': ride_request["pickupAddress"],
            'dropoffAddress': ride_request["dropoffAddress"],
            'distance': min_distance,
            'targetCarId': str(nearest_car["_id"]),
            'targetCarOwnerId': str(nearest_car["userId"])
        })
        
        # Start new timeout for next driver
        timeout_thread = Thread(target=handle_request_timeout, args=(ride_request_id,))
        timeout_thread.daemon = True
        timeout_thread.start()
        request_timeout_threads[str(ride_request_id)] = timeout_thread

    # The timeout thread will handle finding the next driver and notifying the rider

    return jsonify({"status": "declined"})


@app.route("/ride-completed", methods=["POST"])
def complete_ride():
    data = request.get_json()
    ride_request_id = data.get("rideRequestId")
    reason = data.get("reason")  # e.g., "Completed", "Cancelled"

    if not ride_request_id or not reason:
        return jsonify({"error": "Missing fields"}), 400

    # Find the ride request first
    ride_request = ride_requests_collection.find_one({"_id": ObjectId(ride_request_id)})
    if not ride_request:
        return jsonify({"error": "Ride request not found"}), 404

    # Update ride request status
    result = ride_requests_collection.update_one(
        {"_id": ObjectId(ride_request_id), "status": {"$in": ["accepted", "pending"]}},
        {"$set": {"status": "completed", "completedAt": datetime.utcnow(), "reason": reason}}
    )

    if result.modified_count == 0:
        return jsonify({"error": "Request not found or already completed"}), 400

    # Save completed ride to history
    save_ride_to_history(ride_request, "completed", reason)

    # Define 'to_address' properly
    to_address = ride_request.get("dropoffAddress", "Unknown")

    # Notify the rider and car owner that the ride has been completed (or canceled)
    socketio.emit('ride-completed', {
        'rideId': ride_request_id,
        'reason': reason,
        'dropoffAddress': to_address
    })

    return jsonify({"status": "completed"})

def save_ride_to_history(ride_request, status, reason=""):
    """Save completed/cancelled ride to history"""
    try:
        rides_collection = db["rides"]
        
        # Check if ride is already saved to prevent duplicates
        ride_id = str(ride_request["_id"])
        existing_ride = rides_collection.find_one({"rideRequestId": ride_id})
        if existing_ride:
            print(f"DEBUG: Ride {ride_id} already saved to history, skipping duplicate save")
            return existing_ride["_id"]
        
        # Get car info
        car_id = ride_request.get("assignedCarId") or ride_request.get("suggestedCarId")
        car = cars_collection.find_one({"_id": ObjectId(car_id)}) if car_id else None
        
        ride_history = {
            "rideRequestId": ride_id,  # Add this to track original ride request
            "userId": ObjectId(ride_request["riderId"]),
            "carId": str(car_id) if car_id else "",
            "carName": car.get("name", "Unknown Car") if car else "Unknown Car",
            "carModel": car.get("model", "Unknown Model") if car else "Unknown Model",
            "fromAddress": ride_request.get("pickupAddress", "Unknown"),
            "toAddress": ride_request.get("dropoffAddress", "Unknown"),
            "start": f"{ride_request['pickup']['lat']}, {ride_request['pickup']['lng']}" if ride_request.get("pickup") else "",
            "end": f"{ride_request['dropoff']['lat']}, {ride_request['dropoff']['lng']}" if ride_request.get("dropoff") else "",
            "fareAmount": ride_request.get("fareEstimate", 0),
            "status": status,  # "completed", "cancelled", etc.
            "reason": reason,
            "date": datetime.utcnow(),
            "duration": None,  # Will be updated if ride completes
            "batteryUsed": None  # Placeholder for future battery tracking
        }
        
        result = rides_collection.insert_one(ride_history)
        print(f"DEBUG: Saved ride to history with ID: {result.inserted_id}")
        return result.inserted_id
    except Exception as e:
        print(f"ERROR saving ride to history: {e}")
        return None

def save_locate_car_ride_to_history(car_id, user_id, pickup_coords, dropoff_coords, status, reason="", fare_amount=0):
    """Save locate-car ride to history - for personal use with no fare"""
    try:
        rides_collection = db["rides"]
        
        # Get car info
        car = cars_collection.find_one({"_id": ObjectId(car_id)}) if car_id else None
        
        # Create a unique identifier for this locate-car ride
        locate_ride_id = f"locate_{car_id}_{int(datetime.utcnow().timestamp())}"
        
        # For locate-car rides, always set fare to 0 (personal use)
        fare_amount = 0
        
        # Get addresses using reverse geocoding
        pickup_address = "Car Starting Location"
        dropoff_address = "Destination"
        
        try:
            if pickup_coords:
                pickup_address = reverse_geocode(pickup_coords['lat'], pickup_coords['lng'])
            if dropoff_coords:
                dropoff_address = reverse_geocode(dropoff_coords['lat'], dropoff_coords['lng'])
        except Exception as e:
            print(f"DEBUG: Error getting addresses for locate-car ride: {e}")
            # Fall back to coordinate display
            pickup_address = f"Location ({pickup_coords['lat']:.4f}, {pickup_coords['lng']:.4f})" if pickup_coords else "Unknown Location"
            dropoff_address = f"Destination ({dropoff_coords['lat']:.4f}, {dropoff_coords['lng']:.4f})" if dropoff_coords else "Unknown Destination"
        
        ride_history = {
            "rideRequestId": locate_ride_id,  # Unique ID for locate-car rides
            "userId": user_id,  # Keep as string for locate-car users
            "carId": str(car_id),
            "carName": car.get("name", "Unknown Car") if car else "Unknown Car",
            "carModel": car.get("model", "Unknown Model") if car else "Unknown Model",
            "fromAddress": pickup_address,
            "toAddress": dropoff_address,
            "start": f"{pickup_coords['lat']}, {pickup_coords['lng']}" if pickup_coords else "",
            "end": f"{dropoff_coords['lat']}, {dropoff_coords['lng']}" if dropoff_coords else "",
            "fareAmount": 0,  # Always 0 for personal use
            "status": status,  # "completed", "cancelled", etc.
            "reason": reason,
            "date": datetime.utcnow(),
            "duration": None,
            "batteryUsed": None,
            "rideType": "locate-car"  # Mark as locate-car ride (personal use)
        }
        
        result = rides_collection.insert_one(ride_history)
        print(f"DEBUG: Saved locate-car ride to history with ID: {result.inserted_id}")
        print(f"DEBUG: Personal ride - Car: {car_id}, Status: {status}, From: {pickup_address}, To: {dropoff_address}")
        return result.inserted_id
    except Exception as e:
        print(f"ERROR saving locate-car ride to history: {e}")
        return None

# ─── Socket.IO Event ───
# Store connected drivers
connected_drivers = {}

@socketio.on("register-driver")
def handle_driver_registration(data):
    try:
        user_id = data.get("userId")
        email = data.get("email")
        if user_id:
            connected_drivers[user_id] = {
                "socketId": request.sid,
                "email": email,
                "connectedAt": datetime.utcnow()
            }
            print(f"👨‍✈️ Driver registered - User ID: {user_id}, Socket ID: {request.sid}")
            # Send confirmation back to driver
            emit("registration-success", {
                "message": "Successfully registered as driver",
                "userId": user_id
            })
    except Exception as e:
        print(f"⚠️ Error registering driver: {e}")
        emit("registration-error", {"error": str(e)})

@socketio.on("disconnect")
def handle_disconnect():
    # Remove driver from connected_drivers on disconnect
    for user_id, data in list(connected_drivers.items()):
        if data["socketId"] == request.sid:
            del connected_drivers[user_id]
            print(f"👋 Driver disconnected - User ID: {user_id}")
            break

@socketio.on("get-eta")
def handle_eta_request(data):
    car_id = str(data.get("carId"))
    path = data.get("route")

    if not car_id or not path:
        return

    steps_per_leg = 10
    step_delay = 0.3
    eta = int((len(path) - 1) * steps_per_leg * step_delay)
    # Remove duplicate eta-update emission - ETA is now included in car-update events

def calculate_eta(current_lat, current_lng, target_lat, target_lng):
    """Calculate ETA in minutes and seconds based on actual simulation speed"""
    try:
        # Calculate distance in kilometers using Haversine formula
        from math import radians, sin, cos, sqrt, asin
        
        # Convert to radians
        lat1, lon1, lat2, lon2 = map(radians, [current_lat, current_lng, target_lat, target_lng])
        
        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        distance_km = 6371 * c  # Earth radius in km
        
        # Calculate based on actual simulation parameters
        # Using 8 waypoints max, 8 steps per waypoint, 1.5s per step
        waypoints_needed = min(8, max(2, int(distance_km * 3)))  # More waypoints for longer distances
        total_simulation_steps = waypoints_needed * 8  # 8 steps per waypoint
        step_delay = 1.5  # seconds per step
        
        # Total simulation time in seconds
        total_seconds = total_simulation_steps * step_delay
        
        # Add pickup wait time (2 seconds) and some buffer
        total_seconds += 5
        
        # Convert to minutes and seconds for display
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    except Exception:
        return "2m 30s"  # Default fallback

def calculate_distance_km(lat1, lng1, lat2, lng2):
    """Calculate distance in kilometers between two coordinates"""
    try:
        from math import radians, sin, cos, sqrt, asin
        
        # Convert to radians
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lng1, lat2, lng2])
        
        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        distance_km = 6371 * c  # Earth radius in km
        
        return round(distance_km, 2)
    except:
        return 0

# ─── Ride History Endpoints ───
@app.route("/ride-history/<user_id>", methods=["GET"])
def get_user_ride_history(user_id):
    """Get ride history for a specific user (both as rider and driver)"""
    try:
        rides_collection = db["rides"]
        
        # Get rides where user was the rider
        rider_rides = list(rides_collection.find({"userId": ObjectId(user_id)}))
        
        # Get rides where user was the driver
        # We need to find rides where the driver's userId matches
        # First get all cars owned by this user
        user_cars = list(cars_collection.find({"userId": ObjectId(user_id)}))
        car_ids = [str(car["_id"]) for car in user_cars]
        
        # Find rides where this user was the driver (carId matches their cars)
        driver_rides = list(rides_collection.find({"carId": {"$in": car_ids}}))
        
        # Combine and format rides
        all_rides = []
        
        # Add rider rides
        for ride in rider_rides:
            ride_data = {
                "_id": str(ride["_id"]),
                "type": "rider",
                "fromAddress": ride.get("fromAddress", "Unknown"),
                "toAddress": ride.get("toAddress", "Unknown"),
                "fareAmount": ride.get("fareAmount", 0),
                "status": ride.get("status", "unknown"),
                "reason": ride.get("reason", ""),
                "date": ride.get("date"),
                "carName": ride.get("carName", "Unknown Car"),
                "carModel": ride.get("carModel", "Unknown Model")
            }
            all_rides.append(ride_data)
        
        # Add driver rides (avoiding duplicates if user was both rider and driver)
        for ride in driver_rides:
            # Check if this ride is already in the list (user was the rider)
            ride_id = str(ride["_id"])
            if not any(r["_id"] == ride_id for r in all_rides):
                ride_data = {
                    "_id": ride_id,
                    "type": "driver",
                    "fromAddress": ride.get("fromAddress", "Unknown"),
                    "toAddress": ride.get("toAddress", "Unknown"),
                    "fareAmount": ride.get("fareAmount", 0),
                    "status": ride.get("status", "unknown"),
                    "reason": ride.get("reason", ""),
                    "date": ride.get("date"),
                    "carName": ride.get("carName", "Unknown Car"),
                    "carModel": ride.get("carModel", "Unknown Model")
                }
                all_rides.append(ride_data)
        
        # Sort by date (newest first)
        all_rides.sort(key=lambda x: x.get("date", datetime.min), reverse=True)
        
        return jsonify(all_rides)
        
    except Exception as e:
        print(f"ERROR getting ride history: {e}")
        return jsonify({"error": str(e)}), 500

# ─── Financial Endpoints ───
@app.route("/finances/transactions/<user_id>", methods=["GET"])
def get_transactions(user_id):
    try:
        print(f"DEBUG: Getting transactions for userId: {user_id}")
        transactions = get_user_transactions(user_id)
        print(f"DEBUG: Found {len(transactions)} transactions")
        return jsonify(transactions)
    except Exception as e:
        print(f"ERROR in get_transactions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/finances/add-transaction", methods=["POST"])
def add_transaction_endpoint():
    try:
        data = request.get_json()
        user_id = data.get("userId")
        amount = float(data.get("amount", 0))
        description = data.get("description", "Manual transaction")
        
        print(f"DEBUG: Adding transaction - userId: {user_id}, amount: {amount}, description: {description}")
        print(f"DEBUG: Request data: {data}")
        
        if amount == 0:
            print("DEBUG: Amount is zero, returning error")
            return jsonify({"error": "Amount cannot be zero"}), 400
        
        # Add transaction only (no balance to update)
        print(f"DEBUG: Calling add_transaction...")
        transaction = add_transaction(user_id, amount, "manual", description)
        print(f"DEBUG: Transaction result: {transaction}")
        
        # Check if transaction was actually recorded
        transactions = get_user_transactions(user_id)
        print(f"DEBUG: User now has {len(transactions)} transactions")
        
        return jsonify({"success": True})
    except Exception as e:
        print(f"ERROR in add_transaction_endpoint: {e}")
        import traceback
        print(f"ERROR traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

# Test endpoint for debugging
@app.route("/test-debug", methods=["GET", "POST"])
def test_debug():
    print("=" * 50)
    print("DEBUG TEST ENDPOINT HIT!")
    print("Method:", request.method)
    print("Path:", request.path)
    print("=" * 50)
    return jsonify({"message": "Debug endpoint working", "method": request.method})

# ─── Start Server ───
if __name__ == "__main__":
    print("🚀 Server running at http://0.0.0.0:5001")
    socketio.run(app, host="0.0.0.0", port=5001)