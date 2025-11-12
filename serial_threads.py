from serial.threaded import LineReader, Protocol
import threading
from handler_data   import DataRawReader

class ShellLineReader(LineReader):
    def __init__(self, on_line_callback, on_validated_callback, on_disconnected_callback,on_device_disconnect_callback):
        super().__init__()

        self.on_line_callback = on_line_callback
        self.on_validated_callback = on_validated_callback
        self.on_disconnected = on_disconnected_callback #when serial fails
        self.on_device_disconnected=on_device_disconnect_callback #when the shell port device is disconnected
        self.response_event = threading.Event()

        self.validated = False  
        self.connected_to_device=False
        self.initcommand=False
        self.startcommand=False
        self.stopcommand=False
        self.outconfigcommand=False
        self.start_responses = []



    def handle_line(self, line):
        line = line.strip()
        self.on_line_callback(line)

        if not self.validated and "shell" in line.lower():
            self.validated = True
            print(f"[DEBUG] From Shell Received...{line}")
            self.on_validated_callback()

        elif not self.connected_to_device and ">CONNECTED"==line:
            self.connected_to_device=True
            print(f"[DEBUG] CONNECTEDDDDD")
            self.response_event.set()

        elif self.connected_to_device and ">DISCONNECTED"==line:
            self.connected_to_device=False
            print(f"[DEBUG] DISCONNECTEDDDDD")
            self.on_device_disconnected() 

        elif self.connected_to_device and self.initcommand and "OK" in line:
            self.initcommand=False
            self.response_event.set()

        elif self.connected_to_device and self.startcommand:
            self.start_responses = list(filter(lambda k: k not in line, self.start_responses))
            if not self.start_responses:
                self.startcommand=False
                self.response_event.set()
        elif self.connected_to_device and self.stopcommand and "DONE" in line:
            self.stopcommand=False
            self.response_event.set()


        elif self.connected_to_device and self.outconfigcommand and "out mode: (hdr)" in line:
            self.outconfigcommand=False
            self.response_event.set()


    def handle_exception(self, exc_type, exc_val, exc_tb):
        # Called if serial fails
        self.on_disconnected()
    
    
class DataLineReader(LineReader):
    def __init__(self, on_line_callback, on_validated_callback, on_disconnected_callback):
        super().__init__()
        self.on_line_callback = on_line_callback
        self.on_validated_callback = on_validated_callback
        self.on_disconnected = on_disconnected_callback
        self.validated = False

    def handle_line(self, line):
        line = line.strip()
        print(f"[DEBUG] From Data Received...{line}")
        self.on_line_callback(line)

        # Check for validation condition
        if not self.validated and "data" in line.lower(): 
            self.validated = True
            print(f"[DEBUG] From Data Received...{line}")
            self.on_validated_callback()
            

    def handle_exception(self, exc_type, exc_val, exc_tb):
        # Called if serial fails
        print(f"[DEBUG] From Data Received...{exc_type} {exc_val} {exc_tb}")
        self.on_disconnected()        


