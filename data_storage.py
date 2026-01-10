"""
Data Storage Module for IIT Device Data
Saves acquisition data to local JSON files with timestamps
Automatically deletes data old

Directory structure:
data_storage/
├── 20241118/              # Date folder
│   ├── 20241118_143022/   # Session folder (timestamp)
│   │   ├── metadata.json
│   │   ├── ECG_data.jsonl
│   │   ├── ADC_data.jsonl
│   │   └── TEMP_data.jsonl
│   └── 20241118_150530/
└── 20241119/
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import threading
from collections import deque
import shutil
from typing import Optional, Callable

class DataStorage:
    def __init__(self, base_dir="data_storage", mqtt_callback: Optional[Callable] = None):
        """
        Initialize data storage
        
        Args:
            base_dir: Base directory for storing data files
            mqtt_callback: Callback function for MQTT synchronization
                          mqtt_callback(event_type, data)
                          event_type: 'file_created', 'file_updated', 'file_deleted', 'cleanup'
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)
        
        # MQTT callback for synchronization
        self.mqtt_callback = mqtt_callback
        
        # Current session info
        self.session_id = None
        self.session_start_time = None
        self.session_dir = None
        self.session_file = None
        
        # Buffer per scrittura batch (migliora performance)
        self.write_buffer = {
            'ECG': deque(maxlen=1000),
            'ADC': deque(maxlen=1000),
            'TEMP': deque(maxlen=100)
        }
        
        # Lock per thread safety
        self.lock = threading.Lock()
        
        # Flag per flush automatico
        self.auto_flush_enabled = True
        self.flush_interval = 30  # secondi
        self._flush_thread = None
        self._stop_flush = threading.Event()
        
        # Configurazione pulizia dati vecchi
        self.retention_days = 5  # Mantieni dati ultimi 5 giorni
        self.auto_cleanup_enabled = True
        self._cleanup_thread = None
        self._stop_cleanup = threading.Event()
        
        # FIX incomplete sessions on startup
        self._fix_incomplete_sessions()
        
    def _fix_incomplete_sessions(self):
        """
        Fix incomplete sessions from previous days and old sessions from today
        If end_time is null and status is 'active', set end_time to last_update
        
        Logic:
        - Previous days: Check ALL sessions
        - Today: Check all sessions EXCEPT the most recent one (might still be running)
        """
        today = datetime.now().strftime("%Y%m%d")
        fixed_count = 0
        
        try:
            for date_dir in self.base_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                
                dir_name = date_dir.name
                
                # Skip if not a valid date directory
                if len(dir_name) != 8 or not dir_name.isdigit():
                    continue
                
                # Get all sessions for this date, sorted by name (which is timestamp)
                session_dirs = sorted([d for d in date_dir.iterdir() if d.is_dir()], reverse=True)
                
                is_today = (dir_name == today)
                
                for idx, session_dir in enumerate(session_dirs):
                    # If it's today and this is the first (most recent) session, skip it
                    if is_today and idx == 0:
                        continue
                    
                    metadata_file = session_dir / "metadata.json"
                    if not metadata_file.exists():
                        continue
                    
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        
                        # Check if session is incomplete (end_time is null and status is active)
                        if metadata.get('end_time') is None and metadata.get('status') == 'active':
                            # Fix: Set end_time to last_update (or start_time if no updates)
                            end_time = metadata.get('last_update') or metadata.get('start_time')
                            metadata['end_time'] = end_time
                            metadata['status'] = 'completed'
                            
                            # Write back fixed metadata
                            with open(metadata_file, 'w') as f:
                                json.dump(metadata, f, indent=2)
                            
                            fixed_count += 1
                            print(f"[Storage] Fixed incomplete session: {metadata['session_id']} "
                                  f"(end_time set to {end_time})")
                            
                            # Notify MQTT about fix
                            self._notify_mqtt('file_updated', {
                                'file_path': str(metadata_file),
                                'file_type': 'metadata',
                                'reason': 'incomplete_session_fix'
                            })
                    
                    except Exception as e:
                        print(f"[Storage] Error fixing session {session_dir.name}: {e}")
            
            if fixed_count > 0:
                print(f"[Storage] Fixed {fixed_count} incomplete session(s)")
            else:
                print(f"[Storage] No incomplete sessions found")
        
        except Exception as e:
            print(f"[Storage] Error during incomplete session cleanup: {e}")
    
    def set_mqtt_callback(self, callback: Callable):
        """Set MQTT callback for synchronization"""
        self.mqtt_callback = callback
        
    def _notify_mqtt(self, event_type: str, data: dict):
        """Notify MQTT about file changes"""
        if self.mqtt_callback:
            try:
                self.mqtt_callback(event_type, data)
            except Exception as e:
                print(f"[Storage] Error in MQTT callback: {e}")
    
    def start_new_session(self):
        """Start a new acquisition session"""
        with self.lock:
            # Generate session ID from timestamp
            self.session_start_time = datetime.now()
            self.session_id = self.session_start_time.strftime("%Y%m%d_%H%M%S")
            
            # Create session directory: data_storage/YYYYMMDD/HHMMSS/
            date_dir = self.base_dir / self.session_start_time.strftime("%Y%m%d")
            date_dir.mkdir(exist_ok=True)
            
            self.session_dir = date_dir / self.session_id
            self.session_dir.mkdir(exist_ok=True)
            
            # Create session metadata file
            metadata = {
                "session_id": self.session_id,
                "start_time": self.session_start_time.isoformat(),
                "end_time": None,
                "status": "active",
                "signals": ["ECG", "ADC", "TEMP"],
                "total_samples": {
                    "ECG": 0,
                    "ADC": 0,
                    "TEMP": 0
                }
            }
            
            metadata_file = self.session_dir / "metadata.json"
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Notify MQTT about new file
            self._notify_mqtt('file_created', {
                'file_path': str(metadata_file),
                'file_type': 'metadata'
            })
            
            # Clear buffers
            for signal in self.write_buffer:
                self.write_buffer[signal].clear()
            
            # Start auto-flush thread
            if self.auto_flush_enabled:
                self._start_flush_thread()
            
            # Start auto-cleanup thread (esegue pulizia all'avvio di una nuova sessione)
            if self.auto_cleanup_enabled:
                self._start_cleanup_thread()
            
            print(f"[Storage] New session started: {self.session_id}")
            print(f"[Storage] Data directory: {self.session_dir}")
            
            return self.session_id
    
    def save_data(self, signal_name, frames, timestamp=None):
        """
        Save data frames to buffer
        
        Args:
            signal_name: Name of signal (ECG, ADC, TEMP)
            frames: List of data frames
            timestamp: Optional timestamp (defaults to current time)
        """
        if self.session_id is None:
            print("[Storage] Warning: No active session. Call start_new_session() first.")
            return
        
        if signal_name not in self.write_buffer:
            return
        
        current_time = timestamp or datetime.now().isoformat()
        
        with self.lock:
            for frame in frames:
                data_point = {
                    "timestamp": current_time,
                    "values": frame
                }
                self.write_buffer[signal_name].append(data_point)
    
    def flush_to_disk(self):
        """Write buffered data to disk"""
        if self.session_id is None:
            return
        
        with self.lock:
            for signal_name, buffer in self.write_buffer.items():
                if len(buffer) == 0:
                    continue
                
                # Prepare file path: session_dir/ECG_data.jsonl (JSON Lines format)
                data_file = self.session_dir / f"{signal_name}_data.jsonl"
                
                # Check if file is new
                is_new_file = not data_file.exists()
                
                # Write in append mode (JSONL format: one JSON object per line)
                with open(data_file, 'a') as f:
                    for data_point in buffer:
                        json.dump(data_point, f)
                        f.write('\n')
                
                # Notify MQTT
                if is_new_file:
                    self._notify_mqtt('file_created', {
                        'file_path': str(data_file),
                        'file_type': 'data'
                    })
                else:
                    self._notify_mqtt('file_updated', {
                        'file_path': str(data_file),
                        'file_type': 'data'
                    })
                
                # Update metadata
                self._update_metadata(signal_name, len(buffer))
                
                # Clear buffer after writing
                buffer.clear()
            
            print(f"[Storage] Data flushed to disk: {self.session_dir}")
    
    def _update_metadata(self, signal_name, sample_count):
        """Update session metadata with new sample counts"""
        metadata_file = self.session_dir / "metadata.json"
        
        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            
            metadata["total_samples"][signal_name] += sample_count
            metadata["last_update"] = datetime.now().isoformat()
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Notify MQTT about metadata update
            self._notify_mqtt('file_updated', {
                'file_path': str(metadata_file),
                'file_type': 'metadata'
            })
            
        except Exception as e:
            print(f"[Storage] Error updating metadata: {e}")
    
    def end_session(self):
        """End the current acquisition session"""
        if self.session_id is None:
            return
        
        # Stop auto-flush thread
        if self._flush_thread and self._flush_thread.is_alive():
            self._stop_flush.set()
            self._flush_thread.join(timeout=5)
        
        # Stop auto-cleanup thread
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._stop_cleanup.set()
            self._cleanup_thread.join(timeout=5)
        
        # Final flush
        self.flush_to_disk()
        
        # Update metadata with CORRECT end_time based on samples
        with self.lock:
            metadata_file = self.session_dir / "metadata.json"
            
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                # RECALCULATE total_samples by counting actual lines in files
                actual_samples = {}
                for signal in ['ECG', 'ADC', 'TEMP']:
                    data_file = self.session_dir / f"{signal}_data.jsonl"
                    if data_file.exists():
                        with open(data_file, 'r') as f:
                            actual_samples[signal] = sum(1 for _ in f)
                    else:
                        actual_samples[signal] = 0
                
                metadata["total_samples"] = actual_samples
                
                duration_ecg = None
                duration_adc = None
                
                if actual_samples.get('ECG', 0) > 0:
                    duration_ecg = actual_samples['ECG'] / 250.0
                
                if actual_samples.get('ADC', 0) > 0:
                    duration_adc = actual_samples['ADC'] / 250.0
                
                # Check for discrepancies
                if duration_ecg is not None and duration_adc is not None:
                    diff = abs(duration_ecg - duration_adc)
                    if diff > 1.0:  # 1 second tolerance
                        print(f"[Storage] WARNING: ECG/ADC discrepancy in {self.session_id}: {diff:.1f} seconds")
                
                # Use ECG as reference (or ADC if ECG is missing)
                if duration_ecg is not None:
                    final_duration = duration_ecg
                elif duration_adc is not None:
                    final_duration = duration_adc
                else:
                    print(f"[Storage] ERROR: No ECG or ADC data in {self.session_id}")
                    final_duration = 0
                
                # Calculate correct end_time
                start_time = datetime.fromisoformat(metadata["start_time"])
                end_time = start_time + timedelta(seconds=final_duration)
                
                metadata["end_time"] = end_time.isoformat()
                metadata["status"] = "completed"
                
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
                
                # Notify MQTT about final metadata
                self._notify_mqtt('file_updated', {
                    'file_path': str(metadata_file),
                    'file_type': 'metadata'
                })
                
                print(f"[Storage] Session ended: {self.session_id}")
                print(f"[Storage] Actual samples: {actual_samples}")
                print(f"[Storage] Duration: {final_duration:.1f} seconds")
            except Exception as e:
                print(f"[Storage] Error finalizing metadata: {e}")
            
            self.session_id = None
            self.session_dir = None
    
    def _start_flush_thread(self):
        """Start background thread for periodic flushing"""
        self._stop_flush.clear()
        
        def flush_loop():
            while not self._stop_flush.is_set():
                self._stop_flush.wait(self.flush_interval)
                if not self._stop_flush.is_set():
                    self.flush_to_disk()
        
        self._flush_thread = threading.Thread(target=flush_loop, daemon=True)
        self._flush_thread.start()
    
    def _start_cleanup_thread(self):
        """Start background thread for periodic cleanup of old data"""
        self._stop_cleanup.clear()
        
        def cleanup_loop():
            # Esegui subito una pulizia all'avvio
            self.cleanup_old_data()
            
            # Poi ripeti ogni 24 ore
            while not self._stop_cleanup.is_set():
                self._stop_cleanup.wait(86400)  # 24 ore
                if not self._stop_cleanup.is_set():
                    self.cleanup_old_data()
        
        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()
    
    def cleanup_old_data(self):
        """
        Delete data older than retention_days
        Removes entire date directories that are older than the retention period
        """
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        cutoff_date_str = cutoff_date.strftime("%Y%m%d")
        
        deleted_count = 0
        deleted_size = 0
        deleted_items = []
        
        try:
            for date_dir in self.base_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                
                # Extract date from directory name (format: YYYYMMDD)
                dir_name = date_dir.name
                
                # Verifica che sia una directory con formato data valido
                if len(dir_name) != 8 or not dir_name.isdigit():
                    continue
                
                # Confronta date come stringhe (funziona perché formato YYYYMMDD)
                if dir_name < cutoff_date_str:
                    # Calcola dimensione prima di cancellare
                    dir_size = sum(f.stat().st_size for f in date_dir.rglob('*') if f.is_file())
                    deleted_size += dir_size
                    
                    # Track deleted path
                    deleted_items.append(str(date_dir))
                    
                    # Cancella directory e tutto il contenuto
                    shutil.rmtree(date_dir)
                    deleted_count += 1
                    
                    print(f"[Storage] Deleted old data: {dir_name} ({dir_size / 1024 / 1024:.2f} MB)")
            
            if deleted_count > 0:
                print(f"[Storage] Cleanup complete: {deleted_count} directories deleted, "
                      f"{deleted_size / 1024 / 1024:.2f} MB freed")
                
                # Notify MQTT about cleanup
                self._notify_mqtt('cleanup', {
                    'deleted_items': deleted_items,
                    'count': deleted_count,
                    'size': deleted_size
                })
            else:
                print(f"[Storage] Cleanup complete: No old data to delete (retention: {self.retention_days} days)")
                
        except Exception as e:
            print(f"[Storage] Error during cleanup: {e}")
    
    def get_storage_info(self):
        """
        Get information about stored data
        
        Returns:
            Dict with storage statistics
        """
        total_size = 0
        date_count = 0
        session_count = 0
        oldest_date = None
        newest_date = None
        
        try:
            for date_dir in self.base_dir.iterdir():
                if not date_dir.is_dir():
                    continue
                
                dir_name = date_dir.name
                if len(dir_name) != 8 or not dir_name.isdigit():
                    continue
                
                date_count += 1
                
                if oldest_date is None or dir_name < oldest_date:
                    oldest_date = dir_name
                if newest_date is None or dir_name > newest_date:
                    newest_date = dir_name
                
                for session_dir in date_dir.iterdir():
                    if session_dir.is_dir():
                        session_count += 1
                        session_size = sum(f.stat().st_size for f in session_dir.rglob('*') if f.is_file())
                        total_size += session_size
            
            return {
                "total_size_mb": total_size / 1024 / 1024,
                "date_directories": date_count,
                "total_sessions": session_count,
                "oldest_date": oldest_date,
                "newest_date": newest_date,
                "retention_days": self.retention_days
            }
        except Exception as e:
            print(f"[Storage] Error getting storage info: {e}")
            return None
    
    def get_all_sessions(self):
        """Get list of all saved sessions"""
        sessions = []
        
        for date_dir in sorted(self.base_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            
            for session_dir in sorted(date_dir.iterdir(), reverse=True):
                if not session_dir.is_dir():
                    continue
                
                metadata_file = session_dir / "metadata.json"
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r') as f:
                            metadata = json.load(f)
                        sessions.append(metadata)
                    except Exception as e:
                        print(f"[Storage] Error reading {metadata_file}: {e}")
        
        return sessions
    
    def load_session_data(self, session_id, signal_name, limit=None):
        """
        Load data from a specific session
        
        Args:
            session_id: Session ID (format: YYYYMMDD_HHMMSS)
            signal_name: Signal to load (ECG, ADC, TEMP)
            limit: Maximum number of samples to load (None = all)
        
        Returns:
            List of data points with timestamps
        """
        # Find session directory
        date_part = session_id.split('_')[0]
        session_dir = self.base_dir / date_part / session_id
        
        if not session_dir.exists():
            print(f"[Storage] Session not found: {session_id}")
            return []
        
        data_file = session_dir / f"{signal_name}_data.jsonl"
        
        if not data_file.exists():
            print(f"[Storage] Data file not found: {data_file}")
            return []
        
        data_points = []
        
        try:
            with open(data_file, 'r') as f:
                for line in f:
                    if limit and len(data_points) >= limit:
                        break
                    
                    data_point = json.loads(line.strip())
                    data_points.append(data_point)
            
            print(f"[Storage] Loaded {len(data_points)} samples from {session_id}/{signal_name}")
        except Exception as e:
            print(f"[Storage] Error loading data: {e}")
        
        return data_points
    
    def get_sessions_by_date(self, date_str):
        """
        Get all sessions for a specific date
        
        Args:
            date_str: Date string in format YYYYMMDD
        
        Returns:
            List of session metadata
        """
        date_dir = self.base_dir / date_str
        
        if not date_dir.exists():
            return []
        
        sessions = []
        
        for session_dir in sorted(date_dir.iterdir(), reverse=True):
            if not session_dir.is_dir():
                continue
            
            metadata_file = session_dir / "metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    sessions.append(metadata)
                except Exception as e:
                    print(f"[Storage] Error reading {metadata_file}: {e}")
        
        return sessions
    
    def fix_all_metadata(self):
        """
        FIX ALL EXISTING METADATA FILES
        Recalculates total_samples and end_time for all sessions
        """
        print("[Storage] ====== FIXING ALL METADATA FILES ======")
        fixed_count = 0
        error_count = 0
        
        for date_folder in self.base_dir.iterdir():
            if not date_folder.is_dir():
                continue
            
            for session_dir in date_folder.iterdir():
                if not session_dir.is_dir():
                    continue
                
                metadata_file = session_dir / "metadata.json"
                if not metadata_file.exists():
                    continue
                
                try:
                    # Read current metadata
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                    
                    print(f"\n[Storage] Fixing session: {metadata.get('session_id', 'unknown')}")
                    
                    # RECALCULATE total_samples by counting actual lines
                    actual_samples = {}
                    for signal in ['ECG', 'ADC', 'TEMP']:
                        data_file = session_dir / f"{signal}_data.jsonl"
                        if data_file.exists():
                            with open(data_file, 'r') as f:
                                actual_samples[signal] = sum(1 for _ in f)
                        else:
                            actual_samples[signal] = 0
                    
                    print(f"  Old samples: {metadata.get('total_samples', {})}")
                    print(f"  New samples: {actual_samples}")
                    
                    metadata["total_samples"] = actual_samples
                    
                    # RECALCULATE end_time based on ECG/ADC samples (250 Hz)
                    # IGNORE TEMP (1 Hz) - it's sampled at different rate
                    duration_ecg = None
                    duration_adc = None
                    
                    if actual_samples.get('ECG', 0) > 0:
                        duration_ecg = actual_samples['ECG'] / 250.0
                    
                    if actual_samples.get('ADC', 0) > 0:
                        duration_adc = actual_samples['ADC'] / 250.0
                    
                    # Check for discrepancies
                    if duration_ecg is not None and duration_adc is not None:
                        diff = abs(duration_ecg - duration_adc)
                        if diff > 1.0:  # 1 second tolerance
                            print(f"  WARNING: ECG/ADC discrepancy: {diff:.1f} seconds")
                    
                    # Use ECG as reference (or ADC if ECG is missing)
                    if duration_ecg is not None:
                        final_duration = duration_ecg
                    elif duration_adc is not None:
                        final_duration = duration_adc
                    else:
                        print(f"  ERROR: No ECG or ADC data")
                        error_count += 1
                        continue
                    
                    # Calculate correct end_time
                    start_time = datetime.fromisoformat(metadata["start_time"])
                    end_time = start_time + timedelta(seconds=final_duration)
                    
                    old_end = metadata.get('end_time', 'N/A')
                    metadata["end_time"] = end_time.isoformat()
                    metadata["status"] = "completed"
                    
                    print(f"  Old end_time: {old_end}")
                    print(f"  New end_time: {metadata['end_time']}")
                    print(f"  Duration: {final_duration:.1f} seconds ({final_duration/60:.1f} min)")
                    
                    # Write back fixed metadata
                    with open(metadata_file, 'w') as f:
                        json.dump(metadata, f, indent=2)
                    
                    fixed_count += 1
                    
                except Exception as e:
                    print(f"  ERROR: {e}")
                    error_count += 1
        
        print(f"\n[Storage] ====== FIX COMPLETE ======")
        print(f"[Storage] Fixed: {fixed_count} sessions")
        print(f"[Storage] Errors: {error_count} sessions")


# Singleton instance
_storage_instance = None

def get_storage_instance(base_dir="data_storage"):
    """Get the global storage instance"""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = DataStorage(base_dir)
    return _storage_instance


# SCRIPT TO FIX ALL METADATA
if __name__ == "__main__":
    print("Running metadata fix script...")
    storage = DataStorage("data_storage")
    storage.fix_all_metadata()