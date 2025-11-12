# IITdata_acq.py
"""
This example established data acquisition and depends on ShellLineReader + DataRawReader without using an elaborate GUI.
It does the following: 
- Uses fixed COM ports (the SHELL_PORT, DATA_PORT varaibles must be set to match the PC or raspberry)
- WHO validation using ShellLineReader.validated
- CONNECT/INIT/START/STOP via response_event flag pattern
- Auto-init PPG whenever TEMP is requested; init order enforces PPG before TEMP
- Streams ECG/ADC/TEMP and plots them with bare Matplotlib
Requires (channel_manager.py, serial_threads.py, handler_data.py).
"""
import logging
logging.getLogger('matplotlib').setLevel(logging.WARNING)
logging.getLogger("kivy").setLevel(logging.ERROR) 
import time
import threading
import queue
from collections import deque

import serial
from serial.threaded import ReaderThread
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# ---- custom imports ----
from serial_threads import ShellLineReader           # shell protocol with response_event & flags
from channel_manager import get_channel_manager      # contains channel and packet specific information.
from handler_data import DataRawReader, unpack_frames

# ====== COM ports - to be edited as needed ======
#for windows
SHELL_PORT = "COM3"     ## e.g. "COM5" or "/dev/ttyACM0"
DATA_PORT  = "COM7"     # e.g. "COM6" or "/dev/ttyACM1"
BAUD       = 115200
# ========================

WHO_CMD      = b"who\r"
CONNECT_CMD  = b"connect 0\r"
START_CMD    = b"rem start\r"
STOP_CMD     = b"rem stop\r"

# -------------------------------------------------------------------
# Helpers functions “set flag → clear event → send → wait”
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
    # during initcommand, ShellLineReader sets response_event when it reads "<NAME> OK"
    cmd = f"rem {name.lower()} {args}\r"
    if not send_ack(shell_ser, proto, cmd, flag_name="initcommand", label=f"INIT {name}", timeout=1.8):
        return False
    # ensure start_responses exists; queue token for start phase
    if not hasattr(proto, "start_responses"):
        proto.start_responses = []
    proto.start_responses.append(f"{name.upper()} OK")
    return True

def start_streaming(shell_ser, proto) -> bool:
    return send_ack(shell_ser, proto, START_CMD, flag_name="startcommand", label="START", timeout=5.5)

def stop_streaming(shell_ser, proto) -> bool:
    return send_ack(shell_ser, proto, STOP_CMD, flag_name="stopcommand", label="STOP", timeout=2.5)

def start_data_reader(data_port: str, q: "queue.Queue[dict]"):
    ser = serial.Serial(data_port, BAUD, timeout=0.1)
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
# Simple live plotter (ECG, ADC, TEMP)
# -------------------------------------------------------------------
class SimplePlots:
    def __init__(self, cm):
        sel = {k: cm.get_selected_type(k) for k in ("ECG", "ADC", "TEMP")}
        self.fs  = {k: cm.get_data_rate(k, sel[k]) for k in sel}
        self.lbl = {k: cm.get_label_config(k)[sel[k]] for k in sel}

        self.buf = {
            "ECG": [deque(maxlen=self.fs["ECG"] * 10) for _ in self.lbl["ECG"]],
            "ADC": [deque(maxlen=self.fs["ADC"] * 10) for _ in self.lbl["ADC"]],
            "TEMP": [deque(maxlen=max(2, self.fs["TEMP"] * 120))],
        }

        self.fig, (self.ax_ecg, self.ax_adc, self.ax_temp) = plt.subplots(3, 1, figsize=(8, 6), constrained_layout=True)
        self.lines = {
            "ECG": [self.ax_ecg.plot([], [], lw=1)[0] for _ in self.lbl["ECG"]],
            "ADC": [self.ax_adc.plot([], [], lw=1)[0] for _ in self.lbl["ADC"]],
            "TEMP": [self.ax_temp.plot([], [], lw=1)[0]],
        }
        self.ax_ecg.set_title("ECG")
        self.ax_adc.set_title("ADC")
        self.ax_temp.set_title("TEMP")
        self.ax_temp.set_xlabel("Seconds")

    def push(self, name, frames):
        if name == "TEMP":
            for r in frames:
                self.buf["TEMP"][0].append(r[0])
        else:
            for r in frames:
                for ch, dq in enumerate(self.buf[name]):
                    dq.append(r[ch])

    def _update_axis(self, ax, lines, bufs, fs):
        n = max((len(b) for b in bufs), default=1)
        x = [i / float(fs) for i in range(n)]
        for i, ln in enumerate(lines):
            y = list(bufs[i])
            ln.set_data(x[:len(y)], y)
        ax.relim(); ax.autoscale_view()

    def animate(self, _):
        self._update_axis(self.ax_ecg, self.lines["ECG"], self.buf["ECG"], self.fs["ECG"])
        self._update_axis(self.ax_adc, self.lines["ADC"], self.buf["ADC"], self.fs["ADC"])
        self._update_axis(self.ax_temp, self.lines["TEMP"], self.buf["TEMP"], self.fs["TEMP"])
        return [*self.lines["ECG"], *self.lines["ADC"], *self.lines["TEMP"]]

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main():
    cm = get_channel_manager()

    # ---- open shell + attach ShellLineReader (with callbacks) ----
    shell_ser = serial.Serial(SHELL_PORT, BAUD, timeout=0.15)

    who_event = threading.Event()
    disc_event = threading.Event()

    def on_line(line: str):
        # print shell output
        print(f"[SHELL] {line}")

    def on_validated():
        # called by ShellLineReader when WHO passes / prompt recognized
        print("[SHELL] Validated")
        who_event.set()

    def on_shell_fail():
        print("[ERR] shell serial failure")

    def on_device_disc():
        print("[ERR] device disconnected (shell)")
        disc_event.set()  # fired when reader sees ">DISCONNECTED"      
 

    shell_rt = ReaderThread(
        shell_ser,
        lambda: ShellLineReader(on_line, on_validated, on_shell_fail, on_device_disc)
    )
    shell_rt.start()
    # obtain protocol instance
    proto = shell_rt.connect()[1]

    try:
        # 1) WHO validate
        if not validate_shell(shell_ser, proto, who_event):
            return

        # 2) CONNECT
        if not attempt_with_retries(lambda: connect_device(shell_ser, proto),
                                    attempts=3, delay=0.8, label="CONNECT"):
            return        


        # 3) INIT modules with TEMP→PPG dependency
        # choose modules to use:
        wanted = {"ECG", "ADC", "TEMP"}           # can be modified as required
        # ensure PPG is included (and initialized first) if TEMP is wanted
        if "TEMP" in wanted:
            wanted.add("PPG")

        # construct init order: PPG first (if present - required for TEMP), then ECG, ADC, then TEMP last
        init_order = []
        if "PPG" in wanted:  init_order.append("PPG")
        if "ECG" in wanted:  init_order.append("ECG")
        if "ADC" in wanted:  init_order.append("ADC")
        if "TEMP" in wanted: init_order.append("TEMP")

        # send per-module init command using ChannelManager info
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

        # 4) Start the DATA reader (uses DataRawReader)
        pkt_q: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
        data_rt, data_ser = start_data_reader(DATA_PORT, pkt_q)

        # 5) START acquisition (ShellLineReader will wait for queued start_responses)
        if not start_streaming(shell_ser, proto):
            return
        print("[acq] running")

        # 6) Consume packets → unpack → plot
        plots = SimplePlots(cm)
        stop = threading.Event()

        def consumer():
            while not stop.is_set():
                try:
                    pkt = pkt_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                name = pkt.get("signal_name")
                if name not in ("ECG", "ADC", "TEMP"):
                    continue
                frames = unpack_frames(pkt["payload"], pkt["channels"], pkt["nbits"], name)
                plots.push(name, frames)

        t_cons = threading.Thread(target=consumer, daemon=True)
        t_cons.start()

        def _on_close(evt):
            stop.set()
        plots.fig.canvas.mpl_connect("close_event", _on_close)

        ani = FuncAnimation(plots.fig, plots.animate, interval=50, blit=False)
        plt.show()

        # 7) STOP + cleanup
        stop.set()

        send_ack(shell_ser, proto, "rem stop\r",
                 flag_name="stopcommand", label="STOP", timeout=3.0)
        print("[acq] stopping")       

        # Close data reader/port
        try:
            data_rt.close(); data_rt.join()
        finally:
            try:
                data_ser.close()
            except Exception:
                pass

        # --- DISCONNECT: send command and wait for the reader callback (">DISCONNECTED") ---
        try:
            disc_event.clear()
            shell_ser.write(b"disconnect\r"); shell_ser.flush()
            # wait up to 1.0s for on_device_disconnected() to fire
            disc_event.wait(timeout=1.0)
        except Exception:
            pass

                # Close shell reader/port last
        shell_rt.close(); shell_rt.join(); shell_ser.close()

        cleaned = True
        print("[acq] clean exit")
        return
    
        # # STOP (waits for 'DONE' via ShellLineReader.stopcommand)
        # proto.stopcommand = True
        # proto.response_event.clear()
        # shell_ser.write(b"rem stop\r"); shell_ser.flush()
        # proto.response_event.wait(timeout=3.0)
        # print("[acq] stopping")
 
        # # Close data reader/port
        # data_rt.close(); data_rt.join(); data_ser.close()
 
        #  # Disconnect shell and close
        # shell_ser.write(b"disconnect\r"); shell_ser.flush()
        # shell_rt.close(); shell_rt.join(); shell_ser.close()
 
        # cleaned = True
        # print("[acq] clean exit")
        # return        

    finally:
        # best-effort fallback to disconnect and close serial ports corretly in case of early incorrect exit
        if cleaned:
            return
        try:
            shell_ser.write(b"disconnect\r"); shell_ser.flush()
        except Exception:
            pass
        try:
            shell_rt.close(); shell_rt.join(); shell_ser.close()
        except Exception:
            pass

        # try:
        #     stop_streaming(shell_ser, proto)
        # except Exception:
        #     pass
        # try:
        #     data_rt.close(); data_rt.join(); data_ser.close()
        # except Exception:
        #     pass

    # finally:
    #     # best-effort disconnect & close
    #     try:
    #         shell_ser.write(b"disconnect\r"); shell_ser.flush()
    #     except Exception:
    #         pass
    #     try:
    #         shell_rt.close(); shell_rt.join(); shell_ser.close()
    #     except Exception:
    #         pass

if __name__ == "__main__":
    main()
