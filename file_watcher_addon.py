import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging

logger = logging.getLogger(__name__)


class DataStorageWatcher(FileSystemEventHandler):
    """Watch data_storage directory for changes"""
    
    def __init__(self, publisher, base_dir):
        self.publisher = publisher
        self.base_dir = Path(base_dir)
        self.last_sync = {}  # Track last sync time per file
        self.sync_cooldown = 2  # seconds between syncs for same file
        
    def on_modified(self, event):
        if event.is_directory:
            return
            
        file_path = Path(event.src_path)
        
        # Only sync .jsonl and .json files
        if file_path.suffix not in ['.jsonl', '.json']:
            return
        
        # Check cooldown
        now = time.time()
        if file_path in self.last_sync:
            if now - self.last_sync[file_path] < self.sync_cooldown:
                return
        
        self.last_sync[file_path] = now
        
        # Determine signal type from filename
        if 'ECG_data' in file_path.name:
            signal_type = 'ECG'
        elif 'ADC_data' in file_path.name:
            signal_type = 'ADC'
        elif 'TEMP_data' in file_path.name:
            signal_type = 'TEMP'
        elif 'metadata' in file_path.name:
            signal_type = 'metadata'
        else:
            return
        
        logger.info(f"[FileWatcher] Detected change in {file_path.name}, syncing...")
        
        try:
            if signal_type == 'metadata':
                self.publisher.sync_file(str(file_path), 'metadata')
            else:
                # Sync data file in batches
                self.publisher.sync_data_file_incremental(str(file_path), signal_type)
        except Exception as e:
            logger.error(f"[FileWatcher] Error syncing {file_path}: {e}")
    
    def on_created(self, event):
        """Handle new files"""
        self.on_modified(event)


class AnomalyWatcher(FileSystemEventHandler):
    """Watch anomaly_logs directory for changes"""
    
    def __init__(self, publisher, base_dir):
        self.publisher = publisher
        self.base_dir = Path(base_dir)
        self.last_sync = {}
        self.sync_cooldown = 2  # Increased cooldown
        self.pending_syncs = {}  # Track pending sync timers
        
    def on_modified(self, event):
        if event.is_directory:
            return
            
        file_path = Path(event.src_path)
        
        # Accept both .json and .jsonl files
        if file_path.suffix not in ['.json', '.jsonl']:
            return
        
        # Only sync anomaly files
        if not any(x in file_path.name for x in ['anomalies_', 'ecg_anomalies', 'piezo_anomalies', 'temp_anomalies']):
            return
        
        # Determine anomaly type from filename
        if 'piezo_anomalies' in file_path.name:
            anomaly_type = 'piezo'
        elif 'temp_anomalies' in file_path.name:
            anomaly_type = 'temp'
        elif 'ecg_anomalies' in file_path.name or file_path.name.startswith('anomalies_'):
            anomaly_type = 'ecg'
        else:
            return
        
        # Cancel any pending sync for this file
        if file_path in self.pending_syncs:
            self.pending_syncs[file_path].cancel()
        
        # Schedule a new sync after 1 second of inactivity
        # This ensures we only sync AFTER all write operations are complete
        import threading
        timer = threading.Timer(1.0, self._perform_sync, args=[file_path, anomaly_type])
        timer.start()
        self.pending_syncs[file_path] = timer
        
    def _perform_sync(self, file_path, anomaly_type):
        """Actually perform the sync after debounce period"""
        # Check cooldown
        now = time.time()
        if file_path in self.last_sync:
            if now - self.last_sync[file_path] < self.sync_cooldown:
                logger.info(f"[AnomalyWatcher] Skipping {file_path.name} - cooldown active")
                return
        
        self.last_sync[file_path] = now
        
        logger.info(f"[AnomalyWatcher] Syncing {file_path.name} after debounce...")
        
        try:
            self.publisher.sync_anomaly_file(str(file_path), anomaly_type)
            # Remove from pending after successful sync
            if file_path in self.pending_syncs:
                del self.pending_syncs[file_path]
        except Exception as e:
            logger.error(f"[AnomalyWatcher] Error syncing {file_path}: {e}")
    
    def on_created(self, event):
        """Handle new anomaly files"""
        self.on_modified(event)


def start_file_watchers(publisher, base_data_dir="./var/iit_data"):
    """
    Start file watchers for automatic synchronization
    
    Args:
        publisher: MQTTPublisher instance
        base_data_dir: Base directory containing data_storage and anomaly_logs
    
    Returns:
        observer: Watchdog Observer instance (keep reference to prevent stopping)
    """
    base_path = Path(base_data_dir)
    
    data_storage_path = base_path / "data_storage"
    anomaly_logs_path = base_path / "anomaly_logs"
    
    # Create directories if they don't exist
    data_storage_path.mkdir(parents=True, exist_ok=True)
    anomaly_logs_path.mkdir(parents=True, exist_ok=True)
    
    # Create observer
    observer = Observer()
    
    # Add watchers
    data_watcher = DataStorageWatcher(publisher, data_storage_path)
    anomaly_watcher = AnomalyWatcher(publisher, anomaly_logs_path)
    
    observer.schedule(data_watcher, str(data_storage_path), recursive=True)
    observer.schedule(anomaly_watcher, str(anomaly_logs_path), recursive=True)
    
    # Start observer
    observer.start()
    
    logger.info(f"[FileWatcher] Started watching:")
    logger.info(f"  - Data: {data_storage_path}")
    logger.info(f"  - Anomalies: {anomaly_logs_path}")
    
    return observer


def sync_data_file_incremental(self, file_path: str, signal_type: str):
    """
    Sync only new lines from a .jsonl file (incremental sync)
    Add this method to MQTTPublisher class
    """
    try:
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
            
            self.publish(topic, payload)
            logger.info(f"[Sync] Sent {len(batch)} new samples for {signal_type}")
    
    except Exception as e:
        logger.error(f"[Sync] Error in incremental sync: {e}")


def sync_anomaly_file(self, file_path: str, anomaly_type: str):
    """
    Sync entire anomaly file
    """
    try:
        file_path = Path(file_path)
        
        if not file_path.exists():
            return
        
        # Read all anomalies
        with open(file_path, 'r') as f:
            anomalies = []
            for line in f:
                line = line.strip()
                if line:
                    try:
                        anomaly = json.loads(line)
                        anomalies.append(anomaly)
                    except:
                        continue
        
        if anomalies:
            # Send all anomalies
            topic = self.topics['anomalies'][anomaly_type.upper()]
            
            # Send in batches of 10
            for i in range(0, len(anomalies), 10):
                batch = anomalies[i:i+10]
                payload = {
                    'anomaly_type': anomaly_type,
                    'anomalies': batch,
                    'timestamp': time.time(),
                    'batch': i // 10,
                    'total_anomalies': len(anomalies)
                }
                
                self.publish(topic, payload)
                time.sleep(0.1)  # Small delay between batches
            
            logger.info(f"[Sync] Sent {len(anomalies)} anomalies for {anomaly_type}")
    
    except Exception as e:
        logger.error(f"[Sync] Error syncing anomalies: {e}")
