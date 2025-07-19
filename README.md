# CarmaGO 

**CarmaGO** is a comprehensive car management and ride-sharing application that allows users to manage their cars, request rides, monitor car locations, handle taxi jobs, and track finances. The application features real-time car tracking, battery management, charging simulation, and a complete ride management system.

##  Features

- **Account Management**: User registration, login, and profile management
- **Car Management**: Add, monitor, and manage multiple cars with real-time status updates
- **Ride System**: Request rides, calculate routes, track ETAs, and manage active rides
- **Real-time Tracking**: Live car location updates on interactive maps
- **Battery Management**: Monitor battery levels and simulate car charging
- **Taxi Jobs**: Accept and manage taxi ride requests from other users
- **Financial Dashboard**: Track income, expenses, and transaction history
- **Real-time Notifications**: Toast notifications for all user actions
- **Responsive Design**: Mobile-friendly interface with modern UI/UX

##  Technology Stack

### Frontend
- **HTML5/CSS3/JavaScript**: Core web technologies
- **Google Maps API**: Interactive maps and route calculation
- **Socket.IO Client**: Real-time communication
- **Font Awesome**: Icons and UI elements
- **Poppins Font**: Modern typography

### Backend
- **Python Flask**: Web framework
- **Socket.IO**: Real-time communication
- **MongoDB**: Database for users, cars, and ride data
- **Flask-CORS**: Cross-origin resource sharing
- **Eventlet**: Async support for Socket.IO

### Infrastructure
- **Docker & Docker Compose**: Containerization and orchestration
- **MongoDB**: Database container
- **Nginx**: Static file serving (frontend container)

##  Prerequisites

Before running the application, ensure you have the following installed:

1. **Docker Desktop** (recommended)
   - Download from: https://www.docker.com/products/docker-desktop
   - Make sure Docker Desktop is running

2. **Alternative: Local Development Environment**
   - Python 3.8+ (for backend)
   - MongoDB 4.4+ (if running locally)

## 🚀 Quick Start (Docker - Recommended)

### 1. Clone the Repository
```bash
git clone <repository-url>
cd CarmaGO
```

### 2. Start with Docker Compose
```bash
# Start all services in background
docker-compose up -d --build

# Or start with logs visible
docker-compose up --build
```

### 3. Access the Application
- **Frontend**: http://localhost:8080   Make sure to navigate to LoginPage or Homepage or SignUpPage after opening this link
- **Backend API**: http://localhost:5001
- **MongoDB**: localhost:27017

### 4. Stop the Application
```bash
# Stop all containers
docker-compose down

# Stop and remove volumes (reset database)
docker-compose down -v
```

##  Local Development Setup

If you prefer running without Docker:

### Backend Setup
```bash
# Navigate to backend directory
cd backend

# Install Python dependencies
pip install -r requirements.txt

# Start the Flask server
python app.py
```

### Frontend Setup
```bash
# Navigate to frontend directory
cd frontend

# Install Node.js dependencies (if any)
npm install

# Serve with a local server (e.g., VS Code Live Server)


### Database Setup
```bash
# Start MongoDB locally
mongod --dbpath /path/to/your/mongodb/data
```

##  How to Use the Application

### 1. Getting Started
1. Open (http://localhost:8080/signUppage.html) in your browser to start or run it via vs code live server
2. Create an account by clicking "Sign Up"
3. Fill in your details (First Name, Last Name, Email, Phone, Password)
4. Log in with your credentials

### 2. Managing Cars
1. Navigate to **Account** page
2. Click **"+ Add New Car"** 
3. Enter car name, select model, and set location on the map
4. Your cars will appear in the "My Cars" section
5. Monitor battery levels and charging status
6. Delete cars using the delete button with confirmation modal

### 3. Requesting Rides (Locate Cars)
1. Navigate to **Locate Cars** page
2. Select one of your cars from the car list
3. Enter a destination in the input field
4. Click **"Calculate Route"** to see ETA
5. Click **"Confirm Ride"** to start the journey
6. Monitor real-time progress and cancel if needed

### 4. Taxi Jobs
1. Navigate to **Taxi Jobs** page
2. Monitor incoming ride requests from other users
3. Accept ride requests to earn money
4. Track active rides and driver earnings
5. View ride history and completion status

### 5. Financial Management
1. Navigate to **Account** page
2. View **"Financial Overview"** section
3. Monitor total income and expenses
4. Review transaction history in the table
5. Track earnings from completed rides

### 6. Car Battery & Charging
1. Monitor battery levels on any page
2. Start charging when battery is low
3. Battery charges at 2% per second
4. Cars cannot operate with 0% battery
5. Charging automatically stops at 100%

## 🔌 API Endpoints

### Authentication
- `POST /register` - Create new user account
- `POST /login` - User login
- `DELETE /auth/delete/{email}` - Delete user account

### Car Management
- `GET /cars/user/{userId}` - Get user's cars
- `POST /cars` - Add new car
- `DELETE /cars/{carId}` - Delete car
- `POST /charge-car` - Start car charging
- `POST /pause-charging` - Pause car charging

### Ride Management
- `POST /start-ride` - Start a ride
- `POST /cancel-ride` - Cancel active ride
- `GET /user-transactions/{email}` - Get user transactions

### Socket.IO Events
- `car-update` - Real-time car location/status updates
- `ride-completed` - Ride completion notifications
- `eta-response` - ETA calculation responses
- `driver-arrived` - Driver arrival notifications

##  Docker Services

The application consists of three Docker services:

### 1. MongoDB (`carmago-mongo`)
- **Image**: mongo:6
- **Port**: 27017
- **Purpose**: Database for all application data

### 2. Backend (`carmago-backend`)
- **Build**: `./backend`
- **Port**: 5001
- **Purpose**: Flask API server with Socket.IO support

### 3. Frontend (`carmago-frontend`)  
- **Build**: `./frontend`
- **Port**: 8080
- **Purpose**: Static file serving with Nginx

##  Troubleshooting

### Docker Issues
1. **Docker Desktop not running**:
   - Make sure Docker Desktop is started
   - Check system tray for Docker icon

2. **Port conflicts**:
   - Ensure ports 8080, 5001, and 27017 are not in use
   - Use `docker-compose down` to stop existing containers

3. **Build failures**:
   - Clear Docker cache: `docker system prune -a`
   - Rebuild: `docker-compose up --build --force-recreate`


##  Development Notes

### Key Features Implemented
- ✅ Real-time car tracking with Socket.IO
- ✅ Interactive maps with Google Maps API  
- ✅ Battery simulation and charging system
- ✅ Toast notification system (replaced window popups)
- ✅ Modal-based confirmations (no browser alerts)
- ✅ Consistent ETA calculations across pages
- ✅ Financial transaction tracking
- ✅ Responsive mobile-friendly design
- ✅ Car deletion with confirmation modals
- ✅ Logout toast notifications on all pages


**Enjoy using CarmaGO!** 🚗✨
