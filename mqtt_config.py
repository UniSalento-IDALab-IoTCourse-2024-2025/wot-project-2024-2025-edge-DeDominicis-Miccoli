# ========================================
# MQTT Broker Configuration
# ========================================
#MQTT_BROKER = "34.195.226.183"
MQTT_BROKER = "10.18.195.23"
MQTT_PORT = 1883
MQTT_USERNAME = None
MQTT_PASSWORD = None
MQTT_CLIENT_ID = "iit_device_001"
MQTT_QOS = 1

# ========================================
# Publishing Configuration
# ========================================
# Enable/disable different data streams
PUBLISH_REALTIME = True          # Real-time visualization data
PUBLISH_STORAGE = True           # Persistent storage data
PUBLISH_ANOMALIES = True         # Anomaly detection results 
PUBLISH_SYNC = True              # File synchronization 

# Batch sizes for different data types
STORAGE_BATCH_SIZE = 100         # Frames per batch for storage data
ANOMALY_BATCH_SIZE = 10          # Anomalies per batch

# ========================================
# Synchronization Configuration
# ========================================
SYNC_INTERVAL = 60               # Seconds between sync checks
SYNC_ON_SESSION_START = True     # Sync all files when session starts
SYNC_ON_SESSION_END = True       # Sync all files when session ends
SYNC_ANOMALY_LOGS = True         # Keep anomaly logs synchronized
SYNC_METADATA = True             # Keep session metadata synchronized

# ========================================
# Security Configuration
# ========================================
USE_TLS = False
CA_CERT_PATH = None
CLIENT_CERT_PATH = None
CLIENT_KEY_PATH = None

# ========================================
# Connection Configuration
# ========================================
RECONNECT_DELAY = 5              # Seconds between reconnection attempts
MAX_RECONNECT_ATTEMPTS = None    # None = infinite retries
KEEPALIVE = 60                   # MQTT keepalive interval

# ========================================
# Topic Configuration
# ========================================
TOPIC_PREFIX = "iit/device"      # Base topic prefix

# Leave as None to use defaults
CUSTOM_TOPICS = None