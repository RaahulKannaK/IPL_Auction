import os

# Render free: 512MB RAM, 0.1 CPU
# Use 1 worker to avoid memory issues
workers = 1
threads = 4  # Handle concurrent requests with threads, not processes
worker_class = "gthread"
worker_connections = 1000

# Timeouts (Aiven can be slow)
timeout = 30
keepalive = 5

# Memory management
max_requests = 500
max_requests_jitter = 50

bind = f"0.0.0.0:{os.environ.get('PORT', '5005')}"