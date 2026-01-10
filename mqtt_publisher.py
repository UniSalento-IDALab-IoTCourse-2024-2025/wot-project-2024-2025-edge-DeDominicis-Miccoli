"""
MQTT Publisher Module for IIT Device Data
Sends real-time data, storage data, AND anomalies to AWS EC2 via MQTT
Maintains full folder structure synchronization with local system

Requirements:
    pip install paho-mqtt

Features:
    - Real-time data publishing
    - Storage data publishing (batched)
    - Anomaly logs publishing (ECG, PIEZO, TEMP)
    - Session metadata synchronization
    - File structure mirroring
    - Automatic cleanup synchronization
"""
import json
import threading
import time
import hashlib
from datetime import datetime
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional
import paho.mqtt.client as mqtt


class MQTTPublisher:
    def __init__(self, broker, port=1883, username=None, password=None, 
                 client_id="iit_device", qos=1):
        """
        Initialize MQTT Publisher with extended sync capabilities
        
        Args:
            broker: MQTT broker address (EC2 IP or hostname)
            port: MQTT broker port (default: 1883)
            username: MQTT username (optional)
            password: MQTT password (optional)
            client_id: Unique client identifier
            qos: Quality of Service (0, 1, or 2)
        """
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.qos = qos
        
        # MQTT client
        self.client = mqtt.Client(client_id=client_id)
        
        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        
        # Set credentials if provided
        if username and password:
            self.client.username_pw_set(username, password)
        
        # Connection state
        self.connected = False
        self.reconnect_delay = 5
        
        # Publishing buffer
        self.publish_buffer = deque(maxlen=10000)
        self.buffer_lock = threading.Lock()
        
        # Background thread
        self._buffer_thread = None
        self._stop_buffer = threading.Event()
        
        # Extended Topics - now includes anomalies and sync
        self.topics = {
            'realtime': {
                'ECG': 'iit/device/realtime/ecg',
                'ADC': 'iit/device/realtime/adc',
                'TEMP': 'iit/device/realtime/temp'
            },
            'storage': {
                'ECG': 'iit/device/storage/ecg',
                'ADC': 'iit/device/storage/adc',
                'TEMP': 'iit/device/storage/temp'
            },
            'anomalies': {
                'ECG': 'iit/device/anomalies/ecg',
                'PIEZO': 'iit/device/anomalies/piezo',
                'TEMP': 'iit/device/anomalies/temp'
            },
            'session': 'iit/device/session',
            'status': 'iit/device/status',
            'metadata': 'iit/device/metadata',
            'sync': {
                'file_update': 'iit/device/sync/file_update',
                'file_delete': 'iit/device/sync/file_delete',
                'structure': 'iit/device/sync/structure',
                'cleanup': 'iit/device/sync/cleanup'
            }
        }
        
        # File tracking for synchronization
        self.tracked_files = {}  # path -> {hash, last_modified, size}
        self.sync_lock = threading.Lock()
        
        # Sync thread
        self._sync_thread = None
        self._stop_sync = threading.Event()
        self.sync_interval = 60  # Check for changes every 60 seconds
        
        # Statistics
        self.stats = {
            'messages_sent': 0,
            'messages_failed': 0,
            'bytes_sent': 0,
            'anomalies_sent': 0,
            'files_synced': 0,
            'last_publish': None,
            'last_sync': None
        }
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            self.connected = True
            print(f"[MQTT] Connected to broker {self.broker}:{self.port}")
            
            # Publish status message
            self._publish_direct(
                self.topics['status'],
                {
                    'status': 'connected',
                    'timestamp': datetime.now().isoformat(),
                    'client_id': self.client_id,
                    'capabilities': ['realtime', 'storage', 'anomalies', 'sync']
                }
            )
            
            # Send initial folder structure
            self._sync_folder_structure()
        else:
            self.connected = False
            print(f"[MQTT] Connection failed with code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
        self.connected = False
        print(f"[MQTT] Disconnected from broker (code: {rc})")
        
        if rc != 0:
            print(f"[MQTT] Unexpected disconnection. Reconnecting in {self.reconnect_delay}s...")
    
    def _on_publish(self, client, userdata, mid):
        """Callback when message is published"""
        self.stats['messages_sent'] += 1
        self.stats['last_publish'] = datetime.now().isoformat()
    
    def connect(self):
        """Connect to MQTT broker"""
        try:
            print(f"[MQTT] Connecting to {self.broker}:{self.port}...")
            self.client.connect(self.broker, self.port, keepalive=60)
            
            # Start network loop in background
            self.client.loop_start()
            
            # Wait for connection
            timeout = 10
            start = time.time()
            while not self.connected and (time.time() - start) < timeout:
                time.sleep(0.1)
            
            if self.connected:
                # Start buffer processing thread
                self._start_buffer_thread()
                
                # Start sync thread
                self._start_sync_thread()
                
                return True
            else:
                print("[MQTT] Connection timeout")
                return False
                
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        # Stop sync thread
        if self._sync_thread and self._sync_thread.is_alive():
            self._stop_sync.set()
            self._sync_thread.join(timeout=5)
        
        # Stop buffer thread
        if self._buffer_thread and self._buffer_thread.is_alive():
            self._stop_buffer.set()
            self._buffer_thread.join(timeout=5)
        
        # Publish disconnect status
        if self.connected:
            self._publish_direct(
                self.topics['status'],
                {
                    'status': 'disconnected',
                    'timestamp': datetime.now().isoformat(),
                    'client_id': self.client_id,
                    'statistics': self.stats
                }
            )
        
        # Stop MQTT loop and disconnect
        self.client.loop_stop()
        self.client.disconnect()
        self.connected = False
        print("[MQTT] Disconnected")
    
    def _publish_direct(self, topic, data):
        """Publish directly without buffering (for critical messages)"""
        try:
            payload = json.dumps(data)
            result = self.client.publish(topic, payload, qos=self.qos)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats['bytes_sent'] += len(payload)
                return True
            else:
                print(f"[MQTT] Publish failed: {result.rc}")
                self.stats['messages_failed'] += 1
                return False
                
        except Exception as e:
            print(f"[MQTT] Publish error: {e}")
            self.stats['messages_failed'] += 1
            return False
    
    def publish_realtime(self, signal_name, frames, timestamp=None):
        """Publish real-time data"""
        if signal_name not in self.topics['realtime']:
            return
        
        topic = self.topics['realtime'][signal_name]
        current_time = timestamp or datetime.now().isoformat()
        
        message = {
            'signal': signal_name,
            'timestamp': current_time,
            'frames': frames,
            'frame_count': len(frames)
        }
        
        self._add_to_buffer(topic, message)
    
    def publish_storage(self, signal_name, frames, timestamp=None):
        """Publish data for storage (can be batched)"""
        if signal_name not in self.topics['storage']:
            return
        
        topic = self.topics['storage'][signal_name]
        current_time = timestamp or datetime.now().isoformat()
        
        message = {
            'signal': signal_name,
            'timestamp': current_time,
            'frames': frames,
            'frame_count': len(frames)
        }
        
        self._add_to_buffer(topic, message)
    
    def publish_anomaly(self, anomaly_type: str, anomaly_data: Dict):
        """
        Publish detected anomaly
        
        Args:
            anomaly_type: 'ecg', 'piezo', or 'temp'
            anomaly_data: Anomaly detection result dictionary
        """
        anomaly_type_upper = anomaly_type.upper()
        if anomaly_type_upper not in self.topics['anomalies']:
            print(f"[MQTT] Unknown anomaly type: {anomaly_type}")
            return
        
        topic = self.topics['anomalies'][anomaly_type_upper]
        
        message = {
            'anomaly_type': anomaly_type,
            'timestamp': anomaly_data.get('timestamp', datetime.now().isoformat()),
            'data': anomaly_data,
            'client_id': self.client_id
        }
        
        # Direct publish for anomalies (high priority)
        if self._publish_direct(topic, message):
            self.stats['anomalies_sent'] += 1
            print(f"[MQTT] Anomaly published: {anomaly_type.upper()}")
    
    def publish_anomaly_log_file(self, log_file_path: str, anomaly_type: str):
        """
        Publish entire anomaly log file
        
        Args:
            log_file_path: Path to anomaly log file
            anomaly_type: 'ecg', 'piezo', or 'temp'
        """
        try:
            log_path = Path(log_file_path)
            if not log_path.exists():
                print(f"[MQTT] Anomaly log file not found: {log_file_path}")
                return
            
            with open(log_path, 'r') as f:
                if log_path.suffix == '.json':
                    anomalies = json.load(f)
                else:
                    # CSV - skip for now, JSON is primary
                    print(f"[MQTT] CSV format not supported for full file sync")
                    return
            
            message = {
                'type': 'anomaly_log_file',
                'anomaly_type': anomaly_type,
                'file_name': log_path.name,
                'date': log_path.stem.split('_')[-1],  # Extract date from filename
                'anomalies': anomalies,
                'count': len(anomalies),
                'timestamp': datetime.now().isoformat(),
                'client_id': self.client_id
            }
            
            topic = self.topics['anomalies'][anomaly_type.upper()]
            self._add_to_buffer(topic, message)
            
            print(f"[MQTT] Anomaly log file queued: {log_path.name} ({len(anomalies)} anomalies)")
            
        except Exception as e:
            print(f"[MQTT] Error publishing anomaly log file: {e}")
    
    def publish_session_start(self, session_id, metadata):
        """Publish session start event"""
        message = {
            'event': 'session_start',
            'session_id': session_id,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata
        }
        
        self._publish_direct(self.topics['session'], message)
    
    def publish_session_end(self, session_id, statistics):
        """Publish session end event"""
        message = {
            'event': 'session_end',
            'session_id': session_id,
            'timestamp': datetime.now().isoformat(),
            'statistics': statistics
        }
        
        self._publish_direct(self.topics['session'], message)
    
    def publish_metadata(self, session_id, metadata):
        """Publish session metadata update"""
        message = {
            'session_id': session_id,
            'timestamp': datetime.now().isoformat(),
            'metadata': metadata
        }
        
        self._add_to_buffer(self.topics['metadata'], message)
    
    def sync_file(self, file_path: str, file_type: str = 'data'):
        """
        Synchronize a specific file to the server
        
        Args:
            file_path: Path to file to sync
            file_type: Type of file ('data', 'metadata', 'anomaly')
        """
        try:
            path = Path(file_path)
            if not path.exists():
                # File deleted locally, notify server
                self._publish_file_deletion(str(path))
                return
            
            # Calculate file hash
            file_hash = self._calculate_file_hash(path)
            file_stat = path.stat()
            
            # Check if file changed
            with self.sync_lock:
                if str(path) in self.tracked_files:
                    if self.tracked_files[str(path)]['hash'] == file_hash:
                        # File unchanged
                        return
            
            # Read file content
            with open(path, 'r') as f:
                content = f.read()
            
            # Prepare sync message
            message = {
                'action': 'file_update',
                'file_path': str(path),
                'file_type': file_type,
                'file_name': path.name,
                'content': content,
                'hash': file_hash,
                'size': file_stat.st_size,
                'modified': file_stat.st_mtime,
                'timestamp': datetime.now().isoformat(),
                'client_id': self.client_id
            }
            
            # Publish
            if self._publish_direct(self.topics['sync']['file_update'], message):
                # Update tracking
                with self.sync_lock:
                    self.tracked_files[str(path)] = {
                        'hash': file_hash,
                        'last_modified': file_stat.st_mtime,
                        'size': file_stat.st_size
                    }
                self.stats['files_synced'] += 1
                print(f"[MQTT] File synced: {path.name}")
            
        except Exception as e:
            print(f"[MQTT] Error syncing file {file_path}: {e}")
    
    def _publish_file_deletion(self, file_path: str):
        """Notify server about file deletion"""
        message = {
            'action': 'file_delete',
            'file_path': file_path,
            'timestamp': datetime.now().isoformat(),
            'client_id': self.client_id
        }
        
        self._publish_direct(self.topics['sync']['file_delete'], message)
        
        # Remove from tracking
        with self.sync_lock:
            if file_path in self.tracked_files:
                del self.tracked_files[file_path]
        
        print(f"[MQTT] File deletion synced: {file_path}")
    
    def _sync_folder_structure(self):
        """Send complete folder structure to server"""
        try:
            structure = {
                'data_storage': self._scan_directory('data_storage'),
                'anomaly_logs': self._scan_directory('anomaly_logs')
            }
            
            message = {
                'action': 'structure_sync',
                'structure': structure,
                'timestamp': datetime.now().isoformat(),
                'client_id': self.client_id
            }
            
            self._publish_direct(self.topics['sync']['structure'], message)
            print("[MQTT] Folder structure synced")
            
        except Exception as e:
            print(f"[MQTT] Error syncing folder structure: {e}")
    
    def _scan_directory(self, base_path: str) -> Dict:
        """Recursively scan directory structure"""
        base = Path(base_path)
        if not base.exists():
            return {}
        
        structure = {
            'type': 'directory',
            'name': base.name,
            'path': str(base),
            'children': {}
        }
        
        try:
            for item in base.iterdir():
                if item.is_dir():
                    structure['children'][item.name] = self._scan_directory(str(item))
                else:
                    structure['children'][item.name] = {
                        'type': 'file',
                        'name': item.name,
                        'path': str(item),
                        'size': item.stat().st_size,
                        'modified': item.stat().st_mtime
                    }
        except Exception as e:
            print(f"[MQTT] Error scanning {base}: {e}")
        
        return structure
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file"""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def _start_sync_thread(self):
        """Start background thread for periodic sync"""
        self._stop_sync.clear()
        
        def sync_loop():
            while not self._stop_sync.is_set():
                if self.connected:
                    try:
                        # Sync data_storage files
                        self._sync_all_files('data_storage')
                        
                        # Sync anomaly logs
                        self._sync_all_files('anomaly_logs')
                        
                        self.stats['last_sync'] = datetime.now().isoformat()
                        
                    except Exception as e:
                        print(f"[MQTT] Sync error: {e}")
                
                # Wait for next sync interval
                self._stop_sync.wait(self.sync_interval)
        
        self._sync_thread = threading.Thread(target=sync_loop, daemon=True)
        self._sync_thread.start()
        print(f"[MQTT] Sync thread started (interval: {self.sync_interval}s)")
    
    def _sync_all_files(self, base_path: str):
        """Sync all files in a directory"""
        base = Path(base_path)
        if not base.exists():
            return
        
        # Track current files
        current_files = set()
        
        for file_path in base.rglob('*'):
            if file_path.is_file():
                current_files.add(str(file_path))
                
                # Determine file type
                if 'metadata.json' in file_path.name:
                    file_type = 'metadata'
                elif 'anomalies' in str(file_path):
                    file_type = 'anomaly'
                else:
                    file_type = 'data'
                
                self.sync_file(str(file_path), file_type)
        
        # Check for deleted files
        with self.sync_lock:
            tracked_in_base = {f for f in self.tracked_files.keys() if f.startswith(str(base))}
            deleted_files = tracked_in_base - current_files
            
            for deleted_file in deleted_files:
                self._publish_file_deletion(deleted_file)
    
    def publish_cleanup_event(self, deleted_items: List[str]):
        """
        Notify server about cleanup (files/folders deleted due to retention)
        
        Args:
            deleted_items: List of paths that were deleted
        """
        message = {
            'action': 'cleanup',
            'deleted_items': deleted_items,
            'timestamp': datetime.now().isoformat(),
            'client_id': self.client_id
        }
        
        self._publish_direct(self.topics['sync']['cleanup'], message)
        
        # Remove from tracking
        with self.sync_lock:
            for item in deleted_items:
                # Remove exact path and all subpaths
                to_remove = [k for k in self.tracked_files.keys() if k.startswith(item)]
                for k in to_remove:
                    del self.tracked_files[k]
        
        print(f"[MQTT] Cleanup synced: {len(deleted_items)} items")
    
    def _add_to_buffer(self, topic, data):
        """Add message to publishing buffer"""
        with self.buffer_lock:
            self.publish_buffer.append((topic, data))
    
    def _start_buffer_thread(self):
        """Start background thread for processing buffer"""
        self._stop_buffer.clear()
        
        def buffer_loop():
            while not self._stop_buffer.is_set():
                if not self.connected:
                    time.sleep(0.5)
                    continue
                
                # Process buffer
                with self.buffer_lock:
                    if len(self.publish_buffer) == 0:
                        time.sleep(0.01)
                        continue
                    
                    batch_size = min(100, len(self.publish_buffer))
                    messages = [self.publish_buffer.popleft() 
                               for _ in range(batch_size)]
                
                # Publish batch
                for topic, data in messages:
                    try:
                        payload = json.dumps(data)
                        result = self.client.publish(topic, payload, qos=self.qos)
                        
                        if result.rc == mqtt.MQTT_ERR_SUCCESS:
                            self.stats['bytes_sent'] += len(payload)
                        else:
                            self.stats['messages_failed'] += 1
                            with self.buffer_lock:
                                self.publish_buffer.append((topic, data))
                    except Exception as e:
                        print(f"[MQTT] Buffer publish error: {e}")
                        self.stats['messages_failed'] += 1
                
                time.sleep(0.001)
        
        self._buffer_thread = threading.Thread(target=buffer_loop, daemon=True)
        self._buffer_thread.start()
    
    def sync_data_file_incremental(self, file_path: str, signal_type: str):
        """
        Sync only new lines from a .jsonl file (incremental sync)
        Used by file watcher for automatic synchronization
        """
        try:
            from pathlib import Path
            import json
            
            file_path = Path(file_path)
            
            # Track last position for each file
            if not hasattr(self, '_file_positions'):
                self._file_positions = {}
            
            # Get last read position
            last_pos = self._file_positions.get(str(file_path), 0)
            
            # Read only new lines
            with open(file_path, 'r') as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                new_pos = f.tell()
            
            if not new_lines:
                return  # No new data
            
            # Update position
            self._file_positions[str(file_path)] = new_pos
            
            # Parse and send new data
            batch = []
            for line in new_lines:
                line = line.strip()
                if line:
                    try:
                        data = json.loads(line)
                        batch.append(data)
                    except:
                        continue
            
            if batch:
                # Send batch
                topic = self.topics['storage'][signal_type]
                payload = {
                    'session_id': file_path.parent.name,
                    'signal': signal_type,
                    'data': batch,
                    'timestamp': time.time()
                }
                
                self._publish_direct(topic, payload)
                print(f"[MQTT Sync] Sent {len(batch)} new samples for {signal_type}")
        
        except Exception as e:
            print(f"[MQTT Sync] Error in incremental sync: {e}")
    
    def sync_anomaly_file(self, file_path: str, anomaly_type: str):
        """
        Sync entire anomaly file (supports both .json array and .jsonl formats)
        Used by file watcher for automatic synchronization
        """
        try:
            from pathlib import Path
            import json
            
            file_path = Path(file_path)
            
            if not file_path.exists():
                return
            
            anomalies = []
            
            # Check file format
            if file_path.suffix == '.jsonl':
                # JSON Lines format - one JSON per line
                with open(file_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                anomaly = json.loads(line)
                                anomalies.append(anomaly)
                            except:
                                continue
            else:
                # Regular .json format - array of objects
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            anomalies = data
                        elif isinstance(data, dict):
                            anomalies = [data]
                except json.JSONDecodeError:
                    print(f"[MQTT Sync] Invalid JSON in {file_path}")
                    return
            
            if anomalies:
                # Send all anomalies in batches of 10
                topic = self.topics['anomalies'][anomaly_type.upper()]
                
                for i in range(0, len(anomalies), 10):
                    batch = anomalies[i:i+10]
                    payload = {
                        'anomaly_type': anomaly_type,
                        'file_name': file_path.name,  # Add filename for receiver
                        'anomalies': batch,
                        'timestamp': time.time(),
                        'batch': i // 10,
                        'total_anomalies': len(anomalies)
                    }
                    
                    self._publish_direct(topic, payload)
                    time.sleep(0.05)  # Small delay between batches
                
                print(f"[MQTT Sync] Sent {len(anomalies)} anomalies for {anomaly_type}")
        
        except Exception as e:
            print(f"[MQTT Sync] Error syncing anomalies: {e}")
    
    def get_statistics(self) -> Dict:
        """Get publishing statistics"""
        return {
            'connected': self.connected,
            'messages_sent': self.stats['messages_sent'],
            'messages_failed': self.stats['messages_failed'],
            'bytes_sent': self.stats['bytes_sent'],
            'anomalies_sent': self.stats['anomalies_sent'],
            'files_synced': self.stats['files_synced'],
            'buffer_size': len(self.publish_buffer),
            'tracked_files': len(self.tracked_files),
            'last_publish': self.stats['last_publish'],
            'last_sync': self.stats['last_sync']
        }


# Singleton instance
_mqtt_instance = None

def get_mqtt_publisher(broker, port=1883, username=None, password=None):
    """Get the global MQTT publisher instance"""
    global _mqtt_instance
    if _mqtt_instance is None:
        _mqtt_instance = MQTTPublisher(broker, port, username, password)
    return _mqtt_instance