# ecg_anomaly_detector.py
"""
ecg Anomaly Detection Module
Monitors ecgelectric sensor data in real-time and logs detected anomalies
"""
import numpy as np
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Callable
import threading
import queue

try:
    import tensorflow as tf
except ImportError:
    print("[ERROR] TensorFlow not installed. Run: pip install tensorflow")
    tf = None


class ECGAnomalyDetector:
    """
    Real-time ecg anomaly detector using TensorFlow Lite model
    """
    
    def __init__(self, 
                 model_path: str = "unified_ecg_anomaly_detector_quantized.tflite",
                 threshold: float = None,
                 log_format: str = "json",
                 notification_callback: Optional[Callable] = None):
        """
        Initialize the ecg anomaly detector
        
        Args:
            model_path: Path to the TFLite model
            threshold: Reconstruction error threshold (if None, auto-detected from config)
            log_format: "json" or "csv" for anomaly logs
            notification_callback: Callback function(anomaly_type, anomaly_data)
        """
        self.model_path = Path(model_path)
        self.log_format = log_format.lower()
        self.threshold = threshold
        self.notification_callback = notification_callback
        self.interpreter = None
        self.input_details = None
        self.output_details = None
        
        # Statistics
        self.total_windows = 0
        self.anomalies_detected = 0
        self.lock = threading.Lock()
        
        # Load model
        self._load_model()
        
        # Setup logging
        self._setup_logging()
        
    def _load_model(self):
        """Load the TFLite model from active configuration"""
        if tf is None:
            raise RuntimeError("TensorFlow is required but not installed")
        
        # Try to load active model configuration first
        active_model_path = Path("models/ecg/active_model.json")
        model_loaded_from_config = False
        
        if active_model_path.exists():
            try:
                print(f"[ECG Anomaly] Reading active model configuration...")
                with open(active_model_path, 'r') as f:
                    active_config = json.load(f)
                
                # Get model folder and threshold from active config
                model_folder = active_config.get('model_folder')
                config_threshold = active_config.get('threshold')
                
                if model_folder:
                    # Build path to model in the active folder
                    model_dir = Path("models/ecg") / model_folder
                    model_file = model_dir / "model.tflite"
                    
                    if model_file.exists():
                        self.model_path = model_file
                        if self.threshold is None and config_threshold is not None:
                            self.threshold = config_threshold
                        model_loaded_from_config = True
                        print(f"[ECG Anomaly] Using active model: {model_folder}")
                        print(f"[ECG Anomaly] Model path: {self.model_path}")
                        print(f"[ECG Anomaly] Threshold: {self.threshold}")
                    else:
                        print(f"[ECG Anomaly] WARNING: Model file not found in {model_dir}")
                        print(f"[ECG Anomaly] Falling back to default model path")
            except Exception as e:
                print(f"[ECG Anomaly] Error reading active model config: {e}")
                print(f"[ECG Anomaly] Falling back to default model path")
        
        # Fallback: use provided model_path if active config not available
        if not model_loaded_from_config:
            if not self.model_path.exists():
                raise FileNotFoundError(f"ecg model not found: {self.model_path}")
            print(f"[ECG Anomaly] Using default model path: {self.model_path}")
        
        # Load TFLite model
        print(f"[ECG Anomaly] Loading model from: {self.model_path}")
        self.interpreter = tf.lite.Interpreter(model_path=str(self.model_path))
        self.interpreter.allocate_tensors()
        
        # Get input and output details
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        
        input_shape = self.input_details[0]['shape']
        print(f"[ECG Anomaly] Model loaded successfully. Input shape: {input_shape}")
        
        # Load threshold from model's config.json if still not set
        if self.threshold is None:
            config_path = self.model_path.parent / "config.json"
            if config_path.exists():
                try:
                    with open(config_path, 'r') as f:
                        config = json.load(f)
                        self.threshold = config.get('threshold', 0.1)
                        print(f"[ECG Anomaly] Loaded threshold from model config: {self.threshold}")
                except Exception as e:
                    print(f"[ECG Anomaly] Error reading model config: {e}")
                    self.threshold = 0.1
            else:
                self.threshold = 0.1  # Default threshold for ecg
                print(f"[ECG Anomaly] Using default threshold: {self.threshold}")
        
        print(f"[ECG Anomaly] Final threshold: {self.threshold}")
        print(f"[ECG Anomaly] ✓ Initialization complete")
    
    def _setup_logging(self):
        """Setup anomaly log files"""
        self.log_dir = Path("anomaly_logs")
        self.log_dir.mkdir(exist_ok=True)
        
        # Clean old log files (older than 5 days)
        self._clean_old_logs()
        
        # Use today's date for filename
        today = datetime.now().strftime("%Y%m%d")
        
        if self.log_format == "json":
            self.log_file = self.log_dir / f"anomalies_{today}.json"
            # Initialize with empty list if file doesn't exist
            if not self.log_file.exists():
                with open(self.log_file, 'w') as f:
                    json.dump([], f)
                print(f"[ECG Anomaly] Created new log file: {self.log_file}")
            else:
                print(f"[ECG Anomaly] Appending to existing log file: {self.log_file}")
        else:  # CSV
            self.log_file = self.log_dir / f"ecg_anomalies_{today}.csv"
            # Create with header if file doesn't exist
            if not self.log_file.exists():
                with open(self.log_file, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'timestamp', 'date', 'time', 
                        'reconstruction_error', 'threshold',
                        'sample_data'
                    ])
                print(f"[ECG Anomaly] Created new log file: {self.log_file}")
            else:
                print(f"[ECG Anomaly] Appending to existing log file: {self.log_file}")
    
    def _clean_old_logs(self):
        """Delete log files older than 5 days"""
        cutoff_date = datetime.now() - timedelta(days=5)
        deleted_count = 0
        
        for log_file in self.log_dir.glob("ecg_anomalies_*.json"):
            try:
                # Extract date from filename: ecg_anomalies_YYYYMMDD.json
                date_str = log_file.stem.split('_')[2]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                
                if file_date < cutoff_date:
                    log_file.unlink()
                    deleted_count += 1
                    print(f"[ECG Anomaly] Deleted old log: {log_file.name}")
            except (ValueError, IndexError):
                # Skip files with unexpected naming format
                pass
        
        for log_file in self.log_dir.glob("ecg_anomalies_*.csv"):
            try:
                date_str = log_file.stem.split('_')[2]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                
                if file_date < cutoff_date:
                    log_file.unlink()
                    deleted_count += 1
                    print(f"[ECG Anomaly] Deleted old log: {log_file.name}")
            except (ValueError, IndexError):
                pass
        
        if deleted_count > 0:
            print(f"[ECG Anomaly] Cleaned {deleted_count} old log file(s)")
    
    def preprocess_sample(self, data: np.ndarray) -> np.ndarray:
        """
        Preprocess ecg sample for model input
        
        Args:
            data: Raw ecg data (1D array)
            
        Returns:
            Preprocessed data ready for model (2D: [1, sequence_length])
        """
        # Expected input shape from model: [1, 1000]
        expected_shape = self.input_details[0]['shape']
        expected_length = expected_shape[1]
        
        # Ensure we have the right length
        if len(data) < expected_length:
            data = np.pad(data, (0, expected_length - len(data)), mode='constant')
        elif len(data) > expected_length:
            data = data[:expected_length]
        
        # Normalize to [0, 1] range (same as training!)
        min_val = np.min(data)
        max_val = np.max(data)
        if max_val - min_val > 0:
            data = (data - min_val) / (max_val - min_val)
        else:
            data = data * 0
        
        # Reshape to [batch, timesteps] = [1, 1000] - 2D not 3D!
        data = data.reshape(1, expected_length).astype(np.float32)
        
        return data
    
    def detect_anomaly(self, data: np.ndarray) -> Dict:
        """
        Detect if ecg sample contains anomaly
        
        Args:
            data: Raw ecg data array
            
        Returns:
            Dictionary with detection results
        """
        with self.lock:
            self.total_windows += 1
        
        # Preprocess
        input_data = self.preprocess_sample(data)
        
        # Run inference
        self.interpreter.set_tensor(self.input_details[0]['index'], input_data)
        self.interpreter.invoke()
        
        # Get reconstruction
        reconstruction = self.interpreter.get_tensor(self.output_details[0]['index'])
        
        # Calculate reconstruction error (MSE)
        error = np.mean(np.square(input_data - reconstruction))
        
        # Detect anomaly
        is_anomaly = error > self.threshold
        
        result = {
            'is_anomaly': bool(is_anomaly),
            'reconstruction_error': float(error),
            'threshold': float(self.threshold),
            'timestamp': datetime.now().isoformat(),
            'sample_shape': data.shape,
            'sensor': 'ecg'
        }
        
        if is_anomaly:
            with self.lock:
                self.anomalies_detected += 1
            self._log_anomaly(result, data)
            
            # CHIAMATA CALLBACK PER NOTIFICA IMMEDIATA
            if self.notification_callback:
                try:
                    self.notification_callback('ecg', result)
                except Exception as e:
                    print(f"[ECG Anomaly] Error in notification callback: {e}")
        
        return result
    
    def _log_anomaly(self, result: Dict, sample_data: np.ndarray):
        """Log detected anomaly to file"""
        now = datetime.now()
        
        log_entry = {
            'timestamp': result['timestamp'],
            'date': now.strftime("%Y-%m-%d"),
            'time': now.strftime("%H:%M:%S.%f")[:-3],
            'reconstruction_error': result['reconstruction_error'],
            'threshold': result['threshold'],
            'sensor': 'ecg',
            'sample_data': sample_data[:100].tolist()
        }
        
        if self.log_format == "json":
            with open(self.log_file, 'r') as f:
                data = json.load(f)
            data.append(log_entry)
            with open(self.log_file, 'w') as f:
                json.dump(data, f, indent=2)
        else:  # CSV
            with open(self.log_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    log_entry['timestamp'],
                    log_entry['date'],
                    log_entry['time'],
                    log_entry['reconstruction_error'],
                    log_entry['threshold'],
                    json.dumps(log_entry['sample_data'])
                ])
        
        print(f"[ECG Anomaly] ⚠️ DETECTED at {log_entry['time']} "
              f"(error: {result['reconstruction_error']:.4f})")
    
    def get_statistics(self) -> Dict:
        """Get detection statistics"""
        with self.lock:
            if self.total_windows > 0:
                anomaly_rate = (self.anomalies_detected / self.total_windows) * 100
            else:
                anomaly_rate = 0.0
            
            return {
                'sensor': 'ecg',
                'total_windows': self.total_windows,
                'total_samples': self.total_windows * 1000,  # window_size
                'anomalies_detected': self.anomalies_detected,
                'anomaly_rate_percent': anomaly_rate,
                'threshold': self.threshold,
                'log_file': str(self.log_file)
            }


class AnomalyDetectionWorker:
    """
    Background worker that processes ecg data queue and detects anomalies
    """
    
    def __init__(self, detector: ECGAnomalyDetector, 
                 window_size: int = 1000,
                 overlap_ratio: float = 0.0):
        """
        Args:
            detector: ECGAnomalyDetector instance
            window_size: Number of samples to accumulate before detection
            overlap_ratio: Overlap between windows (0.0 = no overlap, 0.75 = 75% overlap)
        """
        self.detector = detector
        self.window_size = window_size
        self.step_size = int(window_size * (1 - overlap_ratio))
        self.data_queue = queue.Queue(maxsize=1000)
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.buffer = []
        
        print(f"[ECG Anomaly] Worker config: window={window_size}, step={self.step_size}")
        
    def start(self):
        """Start the background worker"""
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        print("[ECG Anomaly] Worker started")
    
    def stop(self):
        """Stop the background worker"""
        self.stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)
        print("[ECG Anomaly] Worker stopped")
    
    def add_data(self, ecg_samples: List[float]):
        """
        Add ecg samples to processing queue
        
        Args:
            ecg_samples: List of ecg values
        """
        try:
            self.data_queue.put_nowait(ecg_samples)
        except queue.Full:
            pass  # Drop samples if queue is full
    
    def _worker_loop(self):
        """Main worker loop"""
        while not self.stop_event.is_set():
            try:
                samples = self.data_queue.get(timeout=0.5)
                
                # Add to buffer
                self.buffer.extend(samples)
                
                # Process when buffer has enough data
                while len(self.buffer) >= self.window_size:
                    window = self.buffer[:self.window_size]
                    self.buffer = self.buffer[self.step_size:]  # Slide by step_size
                    
                    # Convert to numpy array
                    ecg_data = np.array(window, dtype=np.float32)
                    
                    # Detect anomalies
                    self.detector.detect_anomaly(ecg_data)
                    
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[ECG Anomaly] Worker error: {e}")


# ========================================
# Standalone Test
# ========================================

if __name__ == "__main__":
    print("=" * 60)
    print("ecg Anomaly Detection - Test")
    print("=" * 60)
    
    try:
        # Initialize detector
        detector = ECGAnomalyDetector(
            model_path="unified_ecg_anomaly_detector_quantized.tflite",
            log_format="json"
        )
        
        # Create worker with 75% overlap
        worker = ecgAnomalyDetectionWorker(
            detector, 
            window_size=1000,
            overlap_ratio=0.75  # More detections!
        )
        worker.start()
        
        # Simulate some data
        print("\nSimulating ecg data...")
        import time
        
        for i in range(10):
            # Generate fake normal ecg signal
            fake_data = np.sin(np.linspace(0, 4*np.pi, 500)) * 100 + np.random.randn(500) * 10
            worker.add_data(fake_data.tolist())
            time.sleep(0.5)
        
        time.sleep(2)
        
        stats = detector.get_statistics()
        print(f"\n[Stats] Windows: {stats['total_windows']}, "
              f"Samples: {stats['total_samples']}, "
              f"Anomalies: {stats['anomalies_detected']}")
        
    except FileNotFoundError as e:
        print(f"\n⚠️ Model not found: {e}")
        print("Please train and download the model first!")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        if 'worker' in locals():
            worker.stop()