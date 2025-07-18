// CarmaGO Authentication Utility
// This file provides shared authentication functions for all pages

// Check if user is authenticated
function isAuthenticated() {
  const userEmail = localStorage.getItem("loggedInUser");
  const userId = localStorage.getItem("loggedInUserId");
  return !!(userEmail && userId);
}

// Get current user data
function getCurrentUser() {
  return {
    email: localStorage.getItem("loggedInUser"),
    id: localStorage.getItem("loggedInUserId")
  };
}

// Redirect to login page
function redirectToLogin(message = "Please log in to access this page") {
  if (message) {
    // Try to use showToast if available, otherwise fall back to alert
    if (typeof showToast === 'function') {
      showToast(message, "warning");
    } else {
      alert(message);
    }
  }
  window.location.href = "logInpage.html";
}

// Logout user
function logoutUser() {
  localStorage.clear();
  // Try to use showToast if available, otherwise fall back to alert
  if (typeof showToast === 'function') {
    showToast("You have been logged out!", "info");
    setTimeout(() => {
      window.location.href = "logInpage.html";
    }, 1500);
  } else {
    alert("👋 You have been logged out!");
    window.location.href = "logInpage.html";
  }
}

// Update navigation based on authentication status
function updateNavigation() {
  const authRequiredLinks = document.querySelectorAll('[data-auth-required="true"]');
  const authActions = document.querySelector('.auth-actions');
  const welcomeMsg = document.getElementById("welcome-msg");
  const logoutBtn = document.getElementById("logout-btn");
  
  if (isAuthenticated()) {
    const user = getCurrentUser();
    
    // Show auth-required links
    authRequiredLinks.forEach(link => {
      link.style.display = 'flex';
    });
    
    // Update welcome message if it exists
    if (welcomeMsg) {
      welcomeMsg.textContent = `👋 Hello, ${user.email}`;
      welcomeMsg.style.display = 'block';
    }
    
    // Show logout button if it exists
    if (logoutBtn) {
      logoutBtn.style.display = 'inline-flex';
    }
  } else {
    // Hide auth-required links
    authRequiredLinks.forEach(link => {
      link.style.display = 'none';
    });
    
    // Hide welcome message
    if (welcomeMsg) {
      welcomeMsg.style.display = 'none';
    }
    
    // Hide logout button
    if (logoutBtn) {
      logoutBtn.style.display = 'none';
    }
  }
}

// Check authentication and redirect if required
function requireAuthentication(redirectMessage) {
  if (!isAuthenticated()) {
    redirectToLogin(redirectMessage);
    return false;
  }
  return true;
}

// Initialize authentication on page load
function initAuth() {
  updateNavigation();
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAuth);
} else {
  initAuth();
}
