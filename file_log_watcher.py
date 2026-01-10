"""
File-based Debug Logger
Scrive TUTTI i log in un file e li streama alla dashboard
"""

import os
import time
import threading
from datetime import datetime
from pathlib import Path

class FileLogWatcher:
    """
    Monitora un file di log e invia le nuove righe alla dashboard
    """
    
    def __init__(self, log_file_path, emit_callback):
        """
        Args:
            log_file_path: Path al file di log da monitorare
            emit_callback: Funzione per inviare log alla dashboard
        """
        self.log_file = Path(log_file_path)
        self.emit_callback = emit_callback
        self.stop_event = threading.Event()
        self.watcher_thread = None
        self.last_position = 0
        
        # Mapping prefissi → categorie
        self.category_map = {
            '[ECG Anomaly]': 'ECG Anomaly',
            '[PIEZO Anomaly]': 'PIEZO Anomaly',
            '[TEMP Anomaly]': 'TEMP Anomaly',
            '[MQTT]': 'MQTT',
            '[Dashboard]': 'Dashboard',
            '[Serial]': 'Serial',
            '[SHELL]': 'Serial',
            '[DEBUG]': 'Dashboard',
            '[ACK]': 'Serial',
            '[WRN]': 'Serial',
            '[Storage]': 'Dashboard',
            '[Config]': 'Config',
            '[FileWatcher]': 'Dashboard',
            '[Startup]': 'Dashboard',
            '[Anomaly]': 'Dashboard',
            '[System]': 'Dashboard',
            '[INFO   ]': 'Dashboard',
            '[ERROR  ]': 'Dashboard',
            '[WARNING]': 'Dashboard',
        }
        
        # Mapping prefissi → livelli
        self.level_map = {
            '[ERROR]': 'ERROR',
            '[ERROR  ]': 'ERROR',
            '[WRN]': 'WARNING',
            '[WARN]': 'WARNING',
            '[WARNING]': 'WARNING',
            '[INFO]': 'INFO',
            '[INFO   ]': 'INFO',
            '[DEBUG]': 'DEBUG',
            '✓': 'INFO',
            '✗': 'ERROR',
            '⚠': 'WARNING',
            'FAIL': 'ERROR',
            'Exception': 'ERROR',
            'Traceback': 'ERROR',
        }
    
    def start(self):
        """Avvia il watcher"""
        # Crea file se non esiste
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.touch(exist_ok=True)
        
        # Vai alla fine del file (per non riprocessare vecchi log)
        if self.log_file.exists():
            self.last_position = self.log_file.stat().st_size
        
        # Avvia thread watcher
        self.stop_event.clear()
        self.watcher_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.watcher_thread.start()
        
        print(f"[FileLogWatcher] Monitoring: {self.log_file}")
    
    def stop(self):
        """Ferma il watcher"""
        self.stop_event.set()
        if self.watcher_thread:
            self.watcher_thread.join(timeout=2.0)
    
    def _watch_loop(self):
        """Loop principale che monitora il file"""
        while not self.stop_event.is_set():
            try:
                if not self.log_file.exists():
                    time.sleep(0.5)
                    continue
                
                current_size = self.log_file.stat().st_size
                
                # Se il file è cresciuto, leggi le nuove righe
                if current_size > self.last_position:
                    with open(self.log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        f.seek(self.last_position)
                        new_lines = f.readlines()
                        self.last_position = f.tell()
                    
                    # Invia ogni riga alla dashboard
                    for line in new_lines:
                        line = line.rstrip('\n')
                        if line.strip():  # Ignora righe vuote
                            self._send_to_dashboard(line)
                
                time.sleep(0.1)  # Check ogni 100ms
                
            except Exception as e:
                print(f"[FileLogWatcher] Error: {e}")
                time.sleep(1)
    
    def _send_to_dashboard(self, line):
        """Invia una riga di log alla dashboard"""
        # Determina categoria
        category = 'Dashboard'
        for prefix, cat in self.category_map.items():
            if line.startswith(prefix):
                category = cat
                break
        
        # Determina livello
        level = 'INFO'
        for prefix, lvl in self.level_map.items():
            if prefix in line:
                level = lvl
                break
        
        # Timestamp
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        # Invia
        try:
            self.emit_callback(category, level, line, timestamp)
        except Exception as e:
            print(f"[FileLogWatcher] Emit error: {e}")


# ===== SETUP PER IITdata_acq.py =====

def setup_file_log_watcher(log_file_path="system.log"):
    """
    Setup del watcher che legge da file e invia alla dashboard
    
    Args:
        log_file_path: Path del file di log (default: system.log)
    
    Returns:
        FileLogWatcher instance
    """
    from dashboard_server import add_system_log
    
    def emit_callback(category, level, message, timestamp):
        # IMPORTANTE: add_system_log vuole (category, message, level)
        add_system_log(category, message, level)
    
    watcher = FileLogWatcher(log_file_path, emit_callback)
    return watcher


# ===== ESEMPIO DI USO =====

if __name__ == "__main__":
    # Test
    import subprocess
    
    log_file = "test.log"
    
    # Setup watcher
    from dashboard_server import add_system_log
    def emit(cat, lvl, msg, ts):
        print(f"[DASHBOARD] [{cat}] {msg}")
    
    watcher = FileLogWatcher(log_file, emit)
    watcher.start()
    
    # Scrivi alcuni log nel file
    with open(log_file, 'a') as f:
        f.write("[MQTT] Test message 1\n")
        f.flush()
    
    time.sleep(0.5)
    
    with open(log_file, 'a') as f:
        f.write("[Serial] Test message 2\n")
        f.write("[ECG Anomaly]  Initialized\n")
        f.flush()
    
    time.sleep(1)
    watcher.stop()
    
    # Cleanup
    os.remove(log_file)