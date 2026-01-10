# IITdata_acq_complete.py
"""
Complete IITdata_acq.py with MQTT and All Anomaly Detection (ECG, PIEZO, TEMP)
CON NOTIFICHE REAL-TIME
"""
import logging
import sys

# MODIFICA 1: All'inizio (dopo import sys)
log_file = open('system.log', 'a', buffering=1)
sys.stdout = log_file
sys.stderr = log_file

logging.getLogger('matplotlib').setLevel(logging.WARNING)
logging.getLogger("kivy").setLevel(logging.ERROR) 
import time
import threading
import queue
from collections import deque

import serial
from serial.threaded import ReaderThread

# ---- custom imports ----
from serial_threads import ShellLineReader
from channel_manager import get_channel_manager
from handler_data import DataRawReader, unpack_frames

# ---- Database Sync ----
from db_sync_module import DatabaseSyncService, SyncConfig

# ---- Dashboard integration ----
from dashboard_server import (
    run_dashboard, 
    push_data, 
    set_device_status, 
    set_acquisition_status,
    send_anomaly_notification,  # IMPORTO LA FUNZIONE DI NOTIFICA
    add_system_log  # IMPORTO LA FUNZIONE DI LOGGING
)

# ---- Storage integration ----
from data_storage import get_storage_instance

# ---- MQTT integration ----
from mqtt_publisher import get_mqtt_publisher
import mqtt_config

# ---- File Watcher for automatic sync ----
try:
    from file_watcher_addon import start_file_watchers
    FILE_WATCHER_AVAILABLE = True
except ImportError:
    FILE_WATCHER_AVAILABLE = False
    print("[WARNING] file_watcher_addon not found. Install watchdog: pip install watchdog")

# ---- Anomaly Detection integration ----
from ecg_anomaly_detector import ECGAnomalyDetector, AnomalyDetectionWorker
from piezo_anomaly_detector import PiezoAnomalyDetector, PiezoAnomalyDetectionWorker
from temp_anomaly_detector import TemperatureAnomalyDetector, TemperatureAnomalyWorker

# ---- USB Port Configuration ----
from detect_usb_ports import load_port_config

from file_log_watcher import setup_file_log_watcher

# ====== COM ports - Load from configuration file ======
try:
    port_config = load_port_config('usb_ports_config.json')
    SHELL_PORT = port_config['shell_port']
    DATA_PORT = port_config['data_port']
    print(f"[Config] Loaded USB ports from usb_ports_config.json")
    print(f"[Config] Shell Port: {SHELL_PORT}")
    print(f"[Config] Data Port:  {DATA_PORT}")
    # Log to dashboard (will be called after dashboard starts)
    config_log_msg = f"Loaded USB ports - Shell: {SHELL_PORT}, Data: {DATA_PORT}"
except Exception as e:
    print(f"[Config] Error loading usb_ports_config.json: {e}")
    print(f"[Config] Using default ports")
    SHELL_PORT = "/dev/tty.usbmodem1201"
    DATA_PORT = "/dev/tty.usbmodem1203"
    config_log_msg = f"Using default ports - Shell: {SHELL_PORT}, Data: {DATA_PORT}"

BAUD = 115200
# ========================

# ====== Anomaly Detection Configuration ======
ENABLE_ANOMALY_DETECTION = True

# ECG Configuration
ECG_MODEL_PATH = "unified_ecg_anomaly_detector_quantized.tflite"
ECG_WINDOW_SIZE = 1000

# PIEZO Configuration
PIEZO_MODEL_PATH = "unified_piezo_anomaly_detector_quantized.tflite"
PIEZO_WINDOW_SIZE = 1000
PIEZO_OVERLAP_RATIO = 0.75   # 75% overlap for PIEZO detection

# TEMPERATURE Configuration
TEMP_HYPO_THRESHOLD = 35.0    # Hypothermia threshold (°C)
TEMP_HYPER_THRESHOLD = 37.5   # Hyperthermia threshold (°C)
TEMP_MIN_DURATION = 3         # Consecutive readings to trigger anomaly

# Log format for all detectors
ANOMALY_LOG_FORMAT = "json"  # "json" or "csv"
# ============================================

WHO_CMD      = b"who\r"
CONNECT_CMD  = b"connect 0\r"
START_CMD    = b"rem start\r"
STOP_CMD     = b"rem stop\r"

# -------------------------------------------------------------------
# Helpers functions
# -------------------------------------------------------------------
def send_ack(shell_ser, proto, cmd: str, *, flag_name: str, label: str, timeout: float = 2.0) -> bool:
    setattr(proto, flag_name, True)
    proto.response_event.clear()
    shell_ser.write(cmd.encode() if isinstance(cmd, str) else cmd)
    ok = proto.response_event.wait(timeout=timeout)
    if ok:
        print(f"[ACK] {label}")
    else:
        print(f"[WRN] {label} timed out")
    return ok

def validate_shell(shell_ser, proto, on_validated_evt: threading.Event) -> bool:
    on_validated_evt.clear()
    shell_ser.write(WHO_CMD)
    ok = on_validated_evt.wait(timeout=1.0)
    print("[ACK] WHO" if ok else "[WRN] WHO failed")
    return ok

def connect_device(shell_ser, proto) -> bool:
    return send_ack(shell_ser, proto, CONNECT_CMD, flag_name="connectcommand", label="CONNECT", timeout=7.0)

def init_module(shell_ser, proto, name: str, args: str) -> bool:
    cmd = f"rem {name.lower()} {args}\r"
    if not send_ack(shell_ser, proto, cmd, flag_name="initcommand", label=f"INIT {name}", timeout=1.8):
        return False
    if not hasattr(proto, "start_responses"):
        proto.start_responses = []
    proto.start_responses.append(f"{name.upper()} OK")
    return True

def start_streaming(shell_ser, proto) -> bool:
    return send_ack(shell_ser, proto, START_CMD, flag_name="startcommand", label="START", timeout=5.5)

def stop_streaming(shell_ser, proto) -> bool:
    return send_ack(shell_ser, proto, STOP_CMD, flag_name="stopcommand", label="STOP", timeout=2.5)

def start_data_reader(data_port: str, q: "queue.Queue[dict]"):
    try:
        print(f"[Serial] Attempting to open Data Port: {data_port}")
        ser = serial.Serial(data_port, BAUD, timeout=0.1)
        print(f"[Serial] Data Port opened successfully")
    except Exception as e:
        print(f"[ERROR] Cannot open Data Port {data_port}: {e}")
        raise
    rt = ReaderThread(ser, lambda: DataRawReader(q))
    rt.start()
    time.sleep(0.2)
    return rt, ser

def attempt_with_retries(fn, attempts=3, delay=0.8, backoff=1.6, label="step"):
    for i in range(1, attempts + 1):
        ok = fn()
        if ok:
            return True
        print(f"[WRN] {label} attempt {i}/{attempts} failed")
        if i < attempts:
            time.sleep(delay)
            delay *= backoff
    return False

# -------------------------------------------------------------------
# Database Sync Configuration
# -------------------------------------------------------------------
SYNC_CONFIG = SyncConfig(
    db_path="users.db",
    is_local=True,  # True for Raspberry, False for Cloud
    local_api_url="http://localhost:5001",   # Local dashboard API
    cloud_api_url="http://10.18.195.23:5002",  # CHANGE THIS!
    sync_interval=60,  # Sync every 60 seconds
    sync_token="test123"  # CHANGE THIS!
)

# -------------------------------------------------------------------
# Main with Dashboard, Storage, MQTT and ALL Anomaly Detection
# -------------------------------------------------------------------
def main():
    cm = get_channel_manager()
    
    # Get storage instance
    storage = get_storage_instance()
    
    # Initialize MQTT publisher
    mqtt = None
    if mqtt_config.MQTT_BROKER:
        print(f"[MQTT] Initializing MQTT publisher...")
        mqtt = get_mqtt_publisher(
            broker=mqtt_config.MQTT_BROKER,
            port=mqtt_config.MQTT_PORT,
            username=mqtt_config.MQTT_USERNAME,
            password=mqtt_config.MQTT_PASSWORD
        )
        
        if mqtt.connect():
            print(f"[MQTT] Connected to {mqtt_config.MQTT_BROKER}:{mqtt_config.MQTT_PORT}")
            
            # Start file watchers for automatic sync
            if FILE_WATCHER_AVAILABLE:
                try:
                    observer = start_file_watchers(mqtt, base_data_dir=".")
                    print("[FileWatcher] Started automatic file synchronization")
                    print("              - Data files will sync automatically")
                    print("              - Anomaly files will sync automatically")
                except Exception as e:
                    print(f"[FileWatcher] Failed to start: {e}")
            else:
                print("[FileWatcher] Not available. Install watchdog for auto-sync: pip install watchdog")
        else:
            print("[MQTT] Failed to connect. Continuing without MQTT...")
            mqtt = None
    else:
        print("[MQTT] MQTT not configured. Set MQTT_BROKER in mqtt_config.py")
    
    # ========== START DATABASE SYNC SERVICE ==========
    print("\n" + "=" * 60)
    print("STARTING DATABASE SYNCHRONIZATION SERVICE")
    print("=" * 60)
    print(f"[Sync] Instance: {'LOCAL (Raspberry)' if SYNC_CONFIG.IS_LOCAL else 'CLOUD'}")
    print(f"[Sync] Cloud API: {SYNC_CONFIG.CLOUD_API_URL}")
    print(f"[Sync] Sync interval: {SYNC_CONFIG.SYNC_INTERVAL} seconds")
    
    sync_service = DatabaseSyncService(SYNC_CONFIG)
    sync_service.start()
    print("[Sync] ✓ Synchronization service started")
    print("=" * 60 + "\n")
    # ==================================================
    
    # Initialize Anomaly Detection
    ecg_detector = None
    ecg_worker = None
    piezo_detector = None
    piezo_worker = None
    temp_detector = None
    temp_worker = None
    
    if ENABLE_ANOMALY_DETECTION:
        print("\n" + "=" * 60)
        print("INITIALIZING ANOMALY DETECTION SYSTEM")
        print("=" * 60)
        
        # 1) ECG Anomaly Detection CON CALLBACK
        try:
            print("\n[ECG Anomaly] Initializing ECG anomaly detector...")
            ecg_detector = ECGAnomalyDetector(
                model_path=ECG_MODEL_PATH,
                log_format=ANOMALY_LOG_FORMAT,
                notification_callback=send_anomaly_notification  # CALLBACK!!!
            )
            ecg_worker = AnomalyDetectionWorker(
                ecg_detector, 
                window_size=ECG_WINDOW_SIZE
            )
            ecg_worker.start()
            print(f"[ECG Anomaly] ✓ ECG anomaly detection enabled")
            print(f"              Window size: {ECG_WINDOW_SIZE} samples")
            print(f"              Threshold: {ecg_detector.threshold:.4f}")
            print(f"              Notifications: ENABLED")
        except Exception as e:
            print(f"[ECG Anomaly] ✗ Failed to initialize: {e}")
            ecg_detector = None
            ecg_worker = None
        
        # 2) PIEZO Anomaly Detection CON CALLBACK
        try:
            print("\n[PIEZO Anomaly] Initializing PIEZO anomaly detector...")
            piezo_detector = PiezoAnomalyDetector(
                model_path=PIEZO_MODEL_PATH,
                log_format=ANOMALY_LOG_FORMAT,
                notification_callback=send_anomaly_notification  # CALLBACK!!!
            )
            piezo_worker = PiezoAnomalyDetectionWorker(
                piezo_detector, 
                window_size=PIEZO_WINDOW_SIZE,
                overlap_ratio=PIEZO_OVERLAP_RATIO
            )
            piezo_worker.start()
            print(f"[PIEZO Anomaly] ✓ PIEZO anomaly detection enabled")
            print(f"                Window size: {PIEZO_WINDOW_SIZE} samples")
            print(f"                Overlap: {PIEZO_OVERLAP_RATIO * 100:.0f}%")
            print(f"                Threshold: {piezo_detector.threshold:.4f}")
            print(f"                Notifications: ENABLED")
        except Exception as e:
            print(f"[PIEZO Anomaly] ✗ Failed to initialize: {e}")
            piezo_detector = None
            piezo_worker = None
        
        # 3) TEMPERATURE Anomaly Detection CON CALLBACK
        try:
            print("\n[TEMP Anomaly] Initializing TEMPERATURE anomaly detector...")
            temp_detector = TemperatureAnomalyDetector(
                hypo_threshold=TEMP_HYPO_THRESHOLD,
                hyper_threshold=TEMP_HYPER_THRESHOLD,
                min_duration=TEMP_MIN_DURATION,
                log_format=ANOMALY_LOG_FORMAT,
                notification_callback=send_anomaly_notification  # CALLBACK!!!
            )
            temp_worker = TemperatureAnomalyWorker(temp_detector)
            temp_worker.start()
            print(f"[TEMP Anomaly] TEMPERATURE anomaly detection enabled")
            print(f"               Hypothermia: < {TEMP_HYPO_THRESHOLD}°C")
            print(f"               Hyperthermia: > {TEMP_HYPER_THRESHOLD}°C")
            print(f"               Min duration: {TEMP_MIN_DURATION} readings")
            print(f"               Notifications: ENABLED")
        except Exception as e:
            print(f"[TEMP Anomaly]  Failed to initialize: {e}")
            temp_detector = None
            temp_worker = None
        
        print("=" * 60 + "\n")
    else:
        print("\n[Anomaly] Anomaly detection disabled")
    
    # Start dashboard server in background thread
    print("[Dashboard] Starting dashboard server...")
    dashboard_thread = threading.Thread(
        target=run_dashboard,
        kwargs={'host': '0.0.0.0', 'port': 5001, 'debug': False},
        daemon=True
    )
    dashboard_thread.start()
    time.sleep(2)
    print("[Dashboard] Dashboard available at http://localhost:5001")
    
    # Log config info after dashboard is ready
    add_system_log('Config', config_log_msg, 'INFO')
    add_system_log('Dashboard', 'Dashboard server started on http://localhost:5001', 'INFO')

    # ---- open shell + attach ShellLineReader ----
    try:
        print(f"[Serial] Attempting to open Shell Port: {SHELL_PORT}")
        add_system_log('Serial', f'Attempting to open Shell Port: {SHELL_PORT}', 'INFO')
        shell_ser = serial.Serial(SHELL_PORT, BAUD, timeout=0.15)
        print(f"[Serial] Shell Port opened successfully")
        add_system_log('Serial', f'Shell Port {SHELL_PORT} opened successfully', 'INFO')
    except Exception as e:
        print(f"[ERROR] Cannot open Shell Port {SHELL_PORT}: {e}")
        print(f"[INFO] Dashboard is running at http://localhost:5001")
        print(f"[INFO] Configure correct ports in Settings and restart")
        add_system_log('Serial', f'Cannot open Shell Port {SHELL_PORT}: {e}', 'ERROR')
        add_system_log('Serial', 'Configure correct ports in Settings and restart', 'WARNING')
        set_device_status(False)
        # Keep dashboard running using Event wait (non-blocking for dashboard thread)
        stop_event = threading.Event()
        stop_event.wait()  # Wait forever, but dashboard thread continues
        return

    who_event = threading.Event()
    disc_event = threading.Event()

    def on_line(line: str):
        print(f"[SHELL] {line}")

    def on_validated():
        print("[SHELL] Validated")
        who_event.set()

    def on_shell_fail():
        print("[ERR] shell serial failure")
        set_device_status(False)

    def on_device_disc():
        print("[ERR] device disconnected (shell)")
        disc_event.set()
        set_device_status(False)

    shell_rt = ReaderThread(
        shell_ser,
        lambda: ShellLineReader(on_line, on_validated, on_shell_fail, on_device_disc)
    )
    shell_rt.start()
    proto = shell_rt.connect()[1]

    try:
        # 1) WHO validate
        if not validate_shell(shell_ser, proto, who_event):
            print("[ERROR] WHO validation failed - wrong ports?")
            print("[INFO] Dashboard is running at http://localhost:5001")
            print("[INFO] Configure correct ports in Settings and restart")
            set_device_status(False)
            # Keep dashboard running using Event wait
            stop_event = threading.Event()
            stop_event.wait()

        # 2) CONNECT
        if not attempt_with_retries(lambda: connect_device(shell_ser, proto),
                                    attempts=3, delay=0.8, label="CONNECT"):
            print("[ERROR] CONNECT failed - check device connection")
            print("[INFO] Dashboard is running at http://localhost:5001")
            set_device_status(False)
            # Keep dashboard running using Event wait
            stop_event = threading.Event()
            stop_event.wait()
        
        set_device_status(True)

        # 3) INIT modules
        wanted = {"ECG", "ADC", "TEMP"}
        if "TEMP" in wanted:
            wanted.add("PPG")

        init_order = []
        if "PPG" in wanted:  init_order.append("PPG")
        if "ECG" in wanted:  init_order.append("ECG")
        if "ADC" in wanted:  init_order.append("ADC")
        if "TEMP" in wanted: init_order.append("TEMP")

        all_channels = cm.get_all_channels()
        for name in init_order:
            if name not in all_channels:
                continue
            info = all_channels[name]
            sel  = info.selected_type or info.default_configp
            parts = cm.get_cmd_config(name, sel)
            if not parts:
                continue
            args = " ".join(parts)
            cm.set_runtime_bit_width(name=name, type_key=None, nbits=16)

            if not init_module(shell_ser, proto, name, args):
                return

        # 4) Start the DATA reader
        pkt_q: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
        data_rt, data_ser = start_data_reader(DATA_PORT, pkt_q)

        # 5) Clear system.log before starting new session
        try:
            with open('system.log', 'w') as f:
                f.write('')  # Truncate file
            print("[System] system.log cleared for new session")
        except Exception as e:
            print(f"[System] Warning: Could not clear system.log: {e}")

        # 6) START new storage session
        session_id = storage.start_new_session()
        print(f"[Storage] Session ID: {session_id}")
        
        if mqtt and mqtt_config.PUBLISH_STORAGE:
            mqtt.publish_session_start(session_id, {
                "signals": ["ECG", "ADC", "TEMP"],
                "device_id": mqtt_config.MQTT_CLIENT_ID
            })

        # 7) START acquisition
        if not start_streaming(shell_ser, proto):
            return
        
        print("\n" + "=" * 60)
        print("ACQUISITION STARTED")
        print("=" * 60)
        set_acquisition_status(True)

        # 7) Consume packets and push to dashboard + storage + MQTT + anomaly detection
        stop = threading.Event()
        
        storage_batches = {
            'ECG': [],
            'ADC': [],
            'TEMP': []
        }

        def consumer():
            while not stop.is_set():
                try:
                    pkt = pkt_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                name = pkt.get("signal_name")
                if name not in ("ECG", "ADC", "TEMP"):
                    continue
                
                frames = unpack_frames(
                    pkt["payload"], 
                    pkt["channels"], 
                    pkt["nbits"], 
                    name
                )
                
                timestamp = pkt.get("timestamp")
                
                # 1) Push to dashboard (real-time visualization)
                push_data(name, frames, timestamp)
                
                # 2) Save to local storage (persistent data)
                storage.save_data(name, frames, timestamp)
                
                # 3) Anomaly Detection for ECG
                if name == "ECG" and ecg_worker is not None:
                    ecg_samples = [frame[0] for frame in frames]
                    ecg_worker.add_data(ecg_samples)
                
                # 4) Anomaly Detection for PIEZO (ADC channel 1)
                if name == "ADC" and piezo_worker is not None:
                    piezo_samples = [frame[1] for frame in frames]  # Channel 1 = PIEZO
                    piezo_worker.add_data(piezo_samples)
                
                # 5) Anomaly Detection for TEMPERATURE
                if name == "TEMP" and temp_worker is not None:
                    # Temperature is stored as raw_value * 100, convert to Celsius
                    temp_celsius = frames[0][0] / 100.0
                    temp_worker.add_temperature(temp_celsius)
                
                # 6) Publish to MQTT
                if mqtt:
                    if mqtt_config.PUBLISH_REALTIME:
                        mqtt.publish_realtime(name, frames, timestamp)
                    
                    if mqtt_config.PUBLISH_STORAGE:
                        storage_batches[name].extend(frames)
                        
                        if len(storage_batches[name]) >= mqtt_config.STORAGE_BATCH_SIZE:
                            mqtt.publish_storage(
                                name, 
                                storage_batches[name], 
                                timestamp
                            )
                            storage_batches[name].clear()

        t_cons = threading.Thread(target=consumer, daemon=True)
        t_cons.start()

        # Keep running until user interrupts
        print("\n[Dashboard] Dashboard: http://localhost:5001")
        if mqtt:
            print(f"[MQTT] Publishing to: {mqtt_config.MQTT_BROKER}")
        
        print("\n[Anomaly] Active detectors:")
        if ecg_detector:
            print("  ✓ ECG anomaly detection (with notifications)")
        if piezo_detector:
            print("  ✓ PIEZO anomaly detection (with notifications)")
        if temp_detector:
            print("  ✓ TEMPERATURE anomaly detection (with notifications)")
        
        print("\n[System] Press Ctrl+C to stop acquisition...")
        print("=" * 60 + "\n")
        
        try:
            counter = 0
            while True:
                time.sleep(5)
                counter += 5
                
                # Print MQTT statistics every 5 seconds
                if mqtt and counter % 5 == 0:
                    stats = mqtt.get_statistics()
                    print(f"[MQTT] Sent: {stats['messages_sent']} msgs, "
                          f"{stats['bytes_sent'] / 1024:.1f} KB")
                
                # Print anomaly statistics every 30 seconds
                if counter % 30 == 0:
                    print("\n" + "=" * 60)
                    print("ANOMALY DETECTION STATISTICS")
                    print("=" * 60)
                    
                    if ecg_detector:
                        stats = ecg_detector.get_statistics()
                        print(f"[ECG] Samples: {stats['total_samples']:,}, "
                              f"Anomalies: {stats['anomalies_detected']} "
                              f"({stats['anomaly_rate_percent']:.2f}%)")
                    
                    if piezo_detector:
                        stats = piezo_detector.get_statistics()
                        print(f"[PIEZO] Windows: {stats['total_windows']:,}, "
                              f"Anomalies: {stats['anomalies_detected']} "
                              f"({stats['anomaly_rate_percent']:.2f}%)")
                    
                    if temp_detector:
                        stats = temp_detector.get_statistics()
                        state = temp_detector.get_current_state()
                        print(f"[TEMP] Readings: {stats['total_readings']:,}, "
                              f"Hypo: {stats['hypothermia_anomalies']}, "
                              f"Hyper: {stats['hyperthermia_anomalies']}")
                        if state['average_recent']:
                            print(f"       Recent avg: {state['average_recent']:.1f}°C")
                    
                    print("=" * 60 + "\n")
                
        except KeyboardInterrupt:
            print("\n\n" + "=" * 60)
            print("STOPPING ACQUISITION")
            print("=" * 60)
            stop.set()

        # 8) STOP + cleanup
        set_acquisition_status(False)
        
        # ========== STOP SYNC SERVICE ==========
        print("[Sync] Stopping synchronization service...")
        sync_service.stop()
        print("[Sync] ✓ Synchronization service stopped")
        # =======================================
        
        # Send remaining MQTT batches
        if mqtt and mqtt_config.PUBLISH_STORAGE:
            print("[MQTT] Flushing remaining data...")
            for name, batch in storage_batches.items():
                if batch:
                    mqtt.publish_storage(name, batch)
        
        # Stop anomaly detection
        if ecg_worker:
            print("[ECG Anomaly] Stopping worker...")
            ecg_worker.stop()
        if piezo_worker:
            print("[PIEZO Anomaly] Stopping worker...")
            piezo_worker.stop()
        if temp_worker:
            print("[TEMP Anomaly] Stopping worker...")
            temp_worker.stop()
        
        # End storage session
        print("[Storage] Ending session...")
        storage.end_session()
        
        # Publish session end to MQTT
        if mqtt and mqtt_config.PUBLISH_STORAGE:
            metadata_file = storage.session_dir / "metadata.json"
            try:
                import json
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                mqtt.publish_session_end(session_id, metadata['total_samples'])
            except Exception as e:
                print(f"[MQTT] Could not send session end: {e}")
        
        send_ack(shell_ser, proto, "rem stop\r",
                 flag_name="stopcommand", label="STOP", timeout=3.0)

        # Close data reader
        try:
            data_rt.close()
            data_rt.join()
        finally:
            try:
                data_ser.close()
            except Exception:
                pass

        # Disconnect device
        try:
            disc_event.clear()
            shell_ser.write(b"disconnect\r")
            shell_ser.flush()
            disc_event.wait(timeout=1.0)
        except Exception:
            pass

        # Close shell reader
        shell_rt.close()
        shell_rt.join()
        shell_ser.close()
        
        set_device_status(False)
        
        # Disconnect MQTT
        if mqtt:
            print("[MQTT] Disconnecting...")
            stats = mqtt.get_statistics()
            print(f"[MQTT] Final stats: {stats['messages_sent']} messages sent")
            mqtt.disconnect()
        
        # Print final anomaly detection summary
        print("\n" + "=" * 60)
        print("FINAL ANOMALY DETECTION SUMMARY")
        print("=" * 60)
        
        if ecg_detector:
            stats = ecg_detector.get_statistics()
            print(f"\n📊 ECG ANOMALIES:")
            print(f"  Total samples analyzed: {stats['total_samples']:,}")
            print(f"  Anomalies detected: {stats['anomalies_detected']}")
            print(f"  Anomaly rate: {stats['anomaly_rate_percent']:.2f}%")
            print(f"  Log file: {stats['log_file']}")
        
        if piezo_detector:
            stats = piezo_detector.get_statistics()
            print(f"\n📊 PIEZO ANOMALIES:")
            print(f"  Total windows analyzed: {stats['total_windows']:,}")
            print(f"  Total samples: {stats['total_samples']:,}")
            print(f"  Anomalies detected: {stats['anomalies_detected']}")
            print(f"  Anomaly rate: {stats['anomaly_rate_percent']:.2f}%")
            print(f"  Log file: {stats['log_file']}")
        
        if temp_detector:
            stats = temp_detector.get_statistics()
            print(f"\n🌡️ TEMPERATURE ANOMALIES:")
            print(f"  Total readings analyzed: {stats['total_readings']:,}")
            print(f"  Hypothermia anomalies: {stats['hypothermia_anomalies']} "
                  f"(< {stats['hypo_threshold']}°C)")
            print(f"  Hyperthermia anomalies: {stats['hyperthermia_anomalies']} "
                  f"(> {stats['hyper_threshold']}°C)")
            print(f"  Total anomalies: {stats['total_anomalies']}")
            print(f"  Log file: {stats['log_file']}")
        
        print("=" * 60)
        
        print(f"\n[Storage] Data saved to session: {session_id}")
        print("[Dashboard] Dashboard still running at http://localhost:5001")
        print("[Dashboard] Press Ctrl+C again to exit completely")
        
        # Keep dashboard running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[System] Shutting down...")
            # ========== FINAL SYNC STOP ==========
            print("[Sync] Final stop of synchronization service...")
            try:
                sync_service.stop()
            except:
                pass
            # =====================================
        
        return

    finally:
        # Cleanup
        try:
            shell_ser.write(b"disconnect\r")
            shell_ser.flush()
        except Exception:
            pass
        try:
            shell_rt.close()
            shell_rt.join()
            shell_ser.close()
        except Exception:
            pass
        
        if ecg_worker:
            try:
                ecg_worker.stop()
            except Exception:
                pass
        
        if piezo_worker:
            try:
                piezo_worker.stop()
            except Exception:
                pass
        
        if temp_worker:
            try:
                temp_worker.stop()
            except Exception:
                pass
        
        try:
            storage.end_session()
        except Exception:
            pass
        
        if mqtt:
            try:
                mqtt.disconnect()
            except Exception:
                pass
        
        set_device_status(False)
        set_acquisition_status(False)


if __name__ == "__main__":
    log_watcher = setup_file_log_watcher("system.log")
    log_watcher.start()
    main()