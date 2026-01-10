# temp_anomaly_detector.py
"""
Temperature Anomaly Detection Module
Monitors temperature data in real-time and logs anomalies based on:
- Hypothermia: Temperature < 35°C for sustained period
- Hyperthermia: Temperature > 37.5°C for sustained period
"""
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import threading
import queue
from collections import deque


class TemperatureAnomalyDetector:
    """
    Real-time temperature anomaly detector based on threshold and duration
    """
    
    def __init__(self, 
                 hypo_threshold: float = 35.0,
                 hyper_threshold: float = 37.5,
                 min_duration: int = 3,
                 log_format: str = "json",
                 notification_callback = None):
        """
        Initialize the temperature anomaly detector
        
        Args:
            hypo_threshold: Temperature below which hypothermia is detected (°C)
            hyper_threshold: Temperature above which hyperthermia is detected (°C)
            min_duration: Minimum number of consecutive readings to trigger anomaly
            log_format: "json" or "csv" for anomaly logs
            notification_callback: Callback function for notifications
        """
        self.hypo_threshold = hypo_threshold
        self.hyper_threshold = hyper_threshold
        self.min_duration = min_duration
        self.log_format = log_format.lower()
        self.notification_callback = notification_callback
        
        # State tracking
        self.temp_buffer = deque(maxlen=min_duration * 2)
        self.consecutive_hypo = 0
        self.consecutive_hyper = 0
        
        # Track current active anomaly
        self.current_anomaly_type = None
        self.current_anomaly_index = None
        
        # Statistics
        self.total_readings = 0
        self.hypo_anomalies = 0
        self.hyper_anomalies = 0
        self.lock = threading.Lock()
        
        # Setup logging
        self._setup_logging()
        
    def _setup_logging(self):
        """Setup anomaly log files"""
        self.log_dir = Path("anomaly_logs")
        self.log_dir.mkdir(exist_ok=True)
        
        # Clean old log files (older than 10 days)
        self._clean_old_logs()
        
        # Use today's date for filename
        today = datetime.now().strftime("%Y%m%d")
        
        if self.log_format == "json":
            self.log_file = self.log_dir / f"temp_anomalies_{today}.json"
            if not self.log_file.exists():
                with open(self.log_file, 'w') as f:
                    json.dump([], f)
                print(f"[TEMP Anomaly] Created new log file: {self.log_file}")
            else:
                print(f"[TEMP Anomaly] Appending to existing log file: {self.log_file}")
        else:
            self.log_file = self.log_dir / f"temp_anomalies_{today}.csv"
            if not self.log_file.exists():
                with open(self.log_file, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'timestamp', 'date', 'time', 
                        'anomaly_type', 'temperature', 'threshold',
                        'duration_readings', 'severity'
                    ])
                print(f"[TEMP Anomaly] Created new log file: {self.log_file}")
            else:
                print(f"[TEMP Anomaly] Appending to existing log file: {self.log_file}")
    
    def _clean_old_logs(self):
        """Delete log files older than 10 days"""
        cutoff_date = datetime.now() - timedelta(days=10)
        deleted_count = 0
        
        for log_file in self.log_dir.glob("temp_anomalies_*.json"):
            try:
                date_str = log_file.stem.split('_')[2]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                
                if file_date < cutoff_date:
                    log_file.unlink()
                    deleted_count += 1
                    print(f"[TEMP Anomaly] Deleted old log: {log_file.name}")
            except (ValueError, IndexError):
                pass
        
        for log_file in self.log_dir.glob("temp_anomalies_*.csv"):
            try:
                date_str = log_file.stem.split('_')[2]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                
                if file_date < cutoff_date:
                    log_file.unlink()
                    deleted_count += 1
                    print(f"[TEMP Anomaly] Deleted old log: {log_file.name}")
            except (ValueError, IndexError):
                pass
        
        if deleted_count > 0:
            print(f"[TEMP Anomaly] Cleaned {deleted_count} old log file(s)")
    
    def _calculate_severity(self, temp: float, anomaly_type: str) -> str:
        """
        Calculate severity of temperature anomaly
        
        Returns: "mild", "moderate", or "severe"
        """
        if anomaly_type == "hypothermia":
            if temp < 32.0:
                return "severe"
            elif temp < 34.0:
                return "moderate"
            else:
                return "mild"
        else:
            if temp > 40.0:
                return "severe"
            elif temp > 39.0:
                return "moderate"
            else:
                return "mild"
    
    def detect_anomaly(self, temperature: float) -> Optional[Dict]:
        """
        Detect if temperature reading indicates an anomaly
        
        Args:
            temperature: Temperature in Celsius
            
        Returns:
            Dictionary with detection results if anomaly detected, None otherwise
        """
        with self.lock:
            self.total_readings += 1
            now = datetime.now()
            
            self.temp_buffer.append({
                'temp': temperature,
                'timestamp': now
            })
            
            anomaly_detected = False
            anomaly_type = None
            is_new_anomaly = False
            
            # Check for hypothermia (< 35°C)
            if temperature < self.hypo_threshold:
                self.consecutive_hypo += 1
                self.consecutive_hyper = 0
                
                if self.consecutive_hypo >= self.min_duration:
                    anomaly_detected = True
                    anomaly_type = "hypothermia"
                    
                    if self.current_anomaly_type != "hypothermia":
                        is_new_anomaly = True
                        self.hypo_anomalies += 1
                        self.current_anomaly_type = "hypothermia"
            
            # Check for hyperthermia (> 37.5°C)
            elif temperature > self.hyper_threshold:
                self.consecutive_hyper += 1
                self.consecutive_hypo = 0
                
                if self.consecutive_hyper >= self.min_duration:
                    anomaly_detected = True
                    anomaly_type = "hyperthermia"
                    
                    if self.current_anomaly_type != "hyperthermia":
                        is_new_anomaly = True
                        self.hyper_anomalies += 1
                        self.current_anomaly_type = "hyperthermia"
            
            # Normal temperature - end current anomaly if any
            else:
                if self.current_anomaly_type is not None:
                    print(f"\n[TEMP Anomaly] {self.current_anomaly_type.upper()} ENDED\n")
                
                self.consecutive_hypo = 0
                self.consecutive_hyper = 0
                self.current_anomaly_type = None
                self.current_anomaly_index = None
            
            if not anomaly_detected:
                return None
            
            consecutive = self.consecutive_hypo if anomaly_type == "hypothermia" else self.consecutive_hyper
            severity = self._calculate_severity(temperature, anomaly_type)
            threshold = self.hypo_threshold if anomaly_type == "hypothermia" else self.hyper_threshold
            
            if is_new_anomaly:
                self._log_new_anomaly(anomaly_type, temperature, threshold, consecutive, severity, now)
            else:
                self._update_existing_anomaly(anomaly_type, temperature, consecutive, severity, now)
            
            result = {
                'is_anomaly': True,
                'is_new': is_new_anomaly,
                'anomaly_type': anomaly_type,
                'temperature': temperature,
                'threshold': threshold,
                'consecutive_readings': consecutive,
                'severity': severity,
                'timestamp': now.isoformat()
            }
            
            return result
    
    def _log_new_anomaly(self, anomaly_type: str, temperature: float, threshold: float, 
                         consecutive: int, severity: str, now: datetime):
        """Log new anomaly to file"""
        log_entry = {
            'timestamp': now.isoformat(),
            'date': now.strftime("%Y-%m-%d"),
            'time': now.strftime("%H:%M:%S"),
            'anomaly_type': anomaly_type,
            'temperature': temperature,
            'threshold': threshold,
            'duration_readings': consecutive,
            'severity': severity
        }
        
        if self.log_format == "json":
            with open(self.log_file, 'r') as f:
                data = json.load(f)
            
            data.append(log_entry)
            self.current_anomaly_index = len(data) - 1
            
            with open(self.log_file, 'w') as f:
                json.dump(data, f, indent=2)
        else:
            with open(self.log_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    log_entry['timestamp'],
                    log_entry['date'],
                    log_entry['time'],
                    log_entry['anomaly_type'],
                    log_entry['temperature'],
                    log_entry['threshold'],
                    log_entry['duration_readings'],
                    log_entry['severity']
                ])
        
        emoji = "🥶" if anomaly_type == "hypothermia" else "🥵"
        print(f"\n[TEMP Anomaly] {emoji} {anomaly_type.upper()} DETECTED at {log_entry['time']}")
        print(f"               Temperature: {temperature:.1f}°C (threshold: {threshold:.1f}°C)")
        print(f"               Duration: {consecutive} readings (Severity: {severity})")
        
        # Send notification
        if self.notification_callback:
            self.notification_callback('temp', {
                'time': log_entry['time'],
                'anomaly_type': anomaly_type,
                'temperature': temperature,
                'threshold': threshold,
                'severity': severity
            })
    
    def _update_existing_anomaly(self, anomaly_type: str, temperature: float, 
                                  consecutive: int, severity: str, now: datetime):
        """Update existing anomaly in log file"""
        if self.current_anomaly_index is None:
            return
        
        if self.log_format == "json":
            with open(self.log_file, 'r') as f:
                data = json.load(f)
            
            if self.current_anomaly_index < len(data):
                data[self.current_anomaly_index]['temperature'] = temperature
                data[self.current_anomaly_index]['duration_readings'] = consecutive
                data[self.current_anomaly_index]['severity'] = severity
                data[self.current_anomaly_index]['timestamp'] = now.isoformat()
                data[self.current_anomaly_index]['time'] = now.strftime("%H:%M:%S")
                
                with open(self.log_file, 'w') as f:
                    json.dump(data, f, indent=2)
        else:
            with open(self.log_file, 'r', newline='') as f:
                reader = csv.reader(f)
                rows = list(reader)
            
            if len(rows) > 1:
                rows[-1] = [
                    now.isoformat(),
                    now.strftime("%Y-%m-%d"),
                    now.strftime("%H:%M:%S"),
                    anomaly_type,
                    temperature,
                    self.hypo_threshold if anomaly_type == "hypothermia" else self.hyper_threshold,
                    consecutive,
                    severity
                ]
                
                with open(self.log_file, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
        
        if consecutive % 10 == 0:
            emoji = "🥶" if anomaly_type == "hypothermia" else "🥵"
            print(f"[TEMP Anomaly] {emoji} {anomaly_type.upper()} continuing... "
                  f"{consecutive} readings - Severity: {severity}")
    
    def get_statistics(self) -> Dict:
        """Get detection statistics"""
        with self.lock:
            return {
                'sensor': 'TEMPERATURE',
                'total_readings': self.total_readings,
                'hypothermia_anomalies': self.hypo_anomalies,
                'hyperthermia_anomalies': self.hyper_anomalies,
                'total_anomalies': self.hypo_anomalies + self.hyper_anomalies,
                'hypo_threshold': self.hypo_threshold,
                'hyper_threshold': self.hyper_threshold,
                'min_duration': self.min_duration,
                'log_file': str(self.log_file),
                'active_anomaly': self.current_anomaly_type
            }
    
    def get_current_state(self) -> Dict:
        """Get current detector state"""
        with self.lock:
            recent_temps = [entry['temp'] for entry in list(self.temp_buffer)[-5:]]
            avg_temp = sum(recent_temps) / len(recent_temps) if recent_temps else None
            
            return {
                'consecutive_hypo': self.consecutive_hypo,
                'consecutive_hyper': self.consecutive_hyper,
                'recent_temperatures': recent_temps,
                'average_recent': avg_temp,
                'active_anomaly_type': self.current_anomaly_type
            }


class TemperatureAnomalyWorker:
    """
    Background worker that processes temperature data queue
    """
    
    def __init__(self, detector: TemperatureAnomalyDetector):
        """
        Args:
            detector: TemperatureAnomalyDetector instance
        """
        self.detector = detector
        self.data_queue = queue.Queue(maxsize=100)
        self.stop_event = threading.Event()
        self.worker_thread = None
        
    def start(self):
        """Start the background worker"""
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        print("[TEMP Anomaly] Worker started")
    
    def stop(self):
        """Stop the background worker"""
        self.stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)
        print("[TEMP Anomaly] Worker stopped")
    
    def add_temperature(self, temp_celsius: float):
        """
        Add temperature reading to processing queue
        
        Args:
            temp_celsius: Temperature in Celsius
        """
        try:
            self.data_queue.put_nowait(temp_celsius)
        except queue.Full:
            print("[TEMP Anomaly] Queue full, dropping reading")
    
    def _worker_loop(self):
        """Main worker loop"""
        while not self.stop_event.is_set():
            try:
                temp = self.data_queue.get(timeout=0.5)
                self.detector.detect_anomaly(temp)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[TEMP Anomaly] Worker error: {e}")