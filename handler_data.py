# handler_data.py

from serial.threaded import Protocol
import struct
import pandas as pd
from kivy.clock import Clock
from datetime import datetime
import os
#from registry import (get_control_panel, get_tab_content)
from channel_manager import get_channel_manager
from collections import deque, defaultdict
import threading
#from filter_engine import has as filter_has, _REGISTRY, StreamingBlock
_stream_blocks = {}     # signal_name -> StreamingBlock
_perchan_state = {}     # signal_name -> list[dict]  (for builtin per-sample)

cm = get_channel_manager()


PACKET_TYPES = {}
TYPE_INDEX   = {}

START_BYTE      = 0x02
HEADER_SMALLX   = 4    # sizeof(t_header_smallx) == 1+1+2

sample_counters = defaultdict(int)  # how many samples emitted so far per signal
last_device_ts  = {}    # last raw device timestamp seen per signal
segment_counters       = defaultdict(int)   # how many samples in current file‐segment
segment_indices        = defaultdict(int)   # which segment# we’re on per signal

# def _get_stream_block(signal_name: str, fs: float, seconds: float = 5.0) -> StreamingBlock:
#     sb = _stream_blocks.get(signal_name)
#     if sb is None or abs(sb.fs - fs) > 1e-6:
#         sb = StreamingBlock(fs=fs, seconds=seconds)
#         _stream_blocks[signal_name] = sb
#     return sb

# def _get_perchan_state(signal_name: str, n_ch: int):
#     st = _perchan_state.get(signal_name)
#     if st is None or len(st) != n_ch:
#         st = [dict() for _ in range(n_ch)]
#         _perchan_state[signal_name] = st
#     return st    


def _build_entry_for(name: str, type_key: str):
    """Compute the (full_hdr, entry) for a given channel/type using ChannelManager."""
    info = cm.get_all_channels()[name]

    # high nibble
    if info.nibble_map and type_key in info.nibble_map:
        nibble = info.nibble_map[type_key]
    else:
        nibble = info.nibble

    # per-type values with fallback to defaults
    #num_ch  = info.plot_config[type_key]
    labels = info.label_config[type_key]
    num_ch = len(labels) 
    rate    = info.data_rate_map.get(type_key, info.data_rate) if info.data_rate_map else info.data_rate
    labels  = info.label_config[type_key]

    full_hdr = (nibble << 4) | num_ch
    entry = {
        'name':      name,
        'data_rate': rate,
        'channels':  num_ch,
        'ch_labels': labels,
    }
    return full_hdr, entry


def update_selected_packet_type(name: str, type_key: str):
    """
    Refresh PACKET_TYPES and TYPE_INDEX for the given channel/type so the
    parser uses the correct channels/labels after a GUI change.
    """
    full_hdr, entry = _build_entry_for(name, type_key)

    # Update/ensure the specific full header in PACKET_TYPES
    PACKET_TYPES[full_hdr] = entry

    # Overwrite the single-nibble view used at runtime
    header = (full_hdr & 0xF0) >> 4
    TYPE_INDEX[header] = {
        'name':      entry['name'],
        'channels':  entry['channels'],
        'ch_labels': entry['ch_labels'],
        'data_rate': entry['data_rate'],
    }

    print(f"[type_update] {name} → {type_key} | header=0x{header:X}  ch={entry['channels']} labels={entry['ch_labels']}")

#Methods for updating PACKET TYPE definitions according to GUI selections. 
#Initially uses the default definitions on startup
for ch_name, info in cm.get_all_channels().items():
    if info.plot_config and info.default_configp:
        update_selected_packet_type(ch_name, info.default_configp)

#Updated PACKET TYPE AND TYPE_INDEX definitions upon change in Gui type or channel selection
def _on_gui_type_selected(channel_name: str, new_type: str):
    try:
        update_selected_packet_type(channel_name, new_type)
    except Exception as e:
        print(f"[ERROR] failed to update TYPE_INDEX for {channel_name}/{new_type}: {e}")
#Links on type selected event to Packet definition updates 
cm.on('type_selected', _on_gui_type_selected)        

#

def compute_tot_cols(channels: int, nbits: int) -> int:
    """How many 16-bit words exist per additional column.
       Computes the total columns based on the number of bits defined for a stream
       Assuming 16-bit word slab firmware packaging 
    """
    if nbits == 16:
        return channels
    if nbits == 20:
        return channels + (channels + 1) // 4   # 4 high nibbles per extra word
    if nbits == 24:
        return channels + (channels + 1) // 2   # 2 high bytes per extra word
    if nbits == 32:
        return channels * 2                      # low16 + high16 per channel
    return channels

def infer_nbits_from_totcols(channels: int, wire_totcols: int) -> int | None:
    """Try to deduce 16/20/24/32 from the header's low nibble (wire_totcols)."""
    candidates = [bw for bw in (16, 20, 24, 32) if compute_tot_cols(channels, bw) == wire_totcols]
    if len(candidates) == 1:
        return candidates[0]
    return None  # ambiguous or inconsistent

def _sign_extend(value: int, bits: int) -> int:
    """EXTENDS SIGN assuming signed integer value streams"""
    sign_bit = 1 << (bits - 1)
    mask = (1 << bits) - 1
    value &= mask
    return (value ^ sign_bit) - sign_bit

class DataRawReader(Protocol):
    """Defines the data reading protocol the follows the follwing steps
       1. Define arrays for each channel

    """
    def __init__(self,packet_queue):
        super().__init__()

        self.buffer =  bytearray()
        # self.buffer = deque()
        self.packet_queue = packet_queue
        self.last_timestamps = {}
        self.cm= get_channel_manager()
        
    def data_received(self, data):
        # print(self.buffer)
        self.buffer.extend(data)
        self._process_buffer()

    def _process_buffer(self):
        # print(self.buffer)
        try:
            while len(self.buffer)>1:
                 # 1) sync on START_BYTE
                if self.buffer[0] != START_BYTE:
                    self.buffer.pop(0)
                    #buf.popleft()
                    continue

                 # 2) read length
                length = int.from_bytes([self.buffer[1]], byteorder='little', signed=False)
                # print(length)

                # 3) wait until we have enough bytes for the small header
                min_header_bytes = 2 + HEADER_SMALLX
                if len(self.buffer) < min_header_bytes:
                    return
                
                # 4) peek into the small header at buf[2..5]
                hdr_off      = 2
                type_byte    = self.buffer[hdr_off]
                rows_byte    = self.buffer[hdr_off + 1]
                timestamp    = struct.unpack_from('<H', self.buffer, hdr_off + 2)[0]

                signal_type  = (type_byte & 0xF0) >> 4 #nibble packet identifier
                wire_totcols =  type_byte & 0x0F            # low nibble = words/row on wiree
                num_rows     =  rows_byte  & 0x7F  #2nd byte of header           
                eof_flag     = bool(rows_byte & 0x80) #

                entry = TYPE_INDEX.get(signal_type)

                if entry is None:
                    self.buffer.pop(0)
                    continue            

                #check matching length = header row*col*2+4  and header type
                expected_payload = num_rows * wire_totcols * 2
                if (length != expected_payload + HEADER_SMALLX):
                    self.buffer.pop(0)
                    continue

        #         # wait for full packet
                total = 2 + length
                if len(self.buffer) < total:
                    return
                
        #         # slice and enqueue
                packet = bytes(self.buffer[:total])
                del self.buffer[:total]
                payload_start = hdr_off + HEADER_SMALLX
                payload = packet[payload_start:payload_start + expected_payload]


                name        = entry['name']  
                labels      = entry['ch_labels']  
                data_rate   = entry['data_rate']
                num_ch         = entry['channels'] 

                nbits    = cm.get_runtime_bit_width(name) or 16

                #sanity-check against header's tot_cols; auto-correct if uniquely inferrable
                expected_wire = compute_tot_cols(num_ch, nbits)
                
                if expected_wire != wire_totcols:
                    inferred = infer_nbits_from_totcols(num_ch, wire_totcols)
                    if inferred:
                        nbits = inferred
                    else:
                        print(f"[WARN] {name}: configured nbits={nbits} ⇒ tot_cols={expected_wire}, "
                            f"but header tot_cols={wire_totcols}")
                        
                # print(f"[DEBUG] Type {name} Expected {expected_wire}, Received {wire_totcols}, Rows {num_rows}")

                self.packet_queue.put({
                    'signal_type':  signal_type,
                    'signal_name':  name,
                    'ch_labels':    labels,
                    'channels':     num_ch,
                    'rows':         num_rows,
                    'timestamp':    timestamp,
                    'eof':          eof_flag,
                    'payload':      payload,
                    'data_rate':     data_rate,
                    'nbits':        nbits,
                })         
                # print(f"[DEBUG] Recieved {name}, Channels {num_ch}, Rows {num_rows},"
                #       f" nbits {nbits}, totcols {wire_totcols} Timestamp {timestamp} EOF {eof_flag}")
            
        except Exception as e:
             print(f"[ERROR] {e}")


def unpack_16bit_frames(payload: bytes, num_channels: int) -> list[list[int]]:
    """
    Turn R·C·2 bytes into R frames of C signed-16 samples.
    This function is unused - only a reserve for format and strictly 16 bit frames
    """
    frames = []
    step = num_channels * 2
    for offset in range(0, len(payload), step):
        chunk = payload[offset:offset+step]
        if len(chunk) < step:
            break
        frame = [
            int.from_bytes(chunk[c*2:(c*2+2)], byteorder='little', signed=True)
            for c in range(num_channels)
        ]
        frames.append(frame)
    return frames


def unpack_frames(payload: bytes, channels: int, nbits: int,signal_name:str) -> list[list[int],]:
    """
    Generic unpacker for 16/20/24/32 bit rows.
    Row layout: first `channels` words are low16; remaining words carry MSBs by scheme.
    This is the data unpacking function used 
    """
    if not payload:
        return []
    word_count = len(payload) // 2
    words = list(struct.unpack('<' + 'H' * word_count, payload))
    tot_cols = compute_tot_cols(channels, nbits)

    frames = []
    for row_start in range(0, word_count, tot_cols):
        if row_start + tot_cols > word_count:
            break
        row = words[row_start:row_start + tot_cols]
        low16s = row[:channels]
        extras = row[channels:]

        if nbits == 16:
            if cm.get_signed_data(signal_name):
                frame = [_sign_extend(x, 16) for x in low16s]
            else:
                frame=low16s

        elif nbits == 20:
            # extras: ceil(ch/4) words; each packs 4 high nibbles [ch0..ch3] in bits [3:0],[7:4],[11:8],[15:12]
            hi4 = [0] * channels
            for i, w in enumerate(extras):
                base = i * 4
                for k in range(4):
                    ch = base + k
                    if ch < channels:
                        hi4[ch] = (w >> (k * 4)) & 0xF
            frame = [_sign_extend(((hi4[ch] << 16) | (low16s[ch] & 0xFFFF)), 20) for ch in range(channels)]

        elif nbits == 24:
            # extras: ceil(ch/2) words; low byte = hi8 for even ch, high byte = hi8 for odd ch
            hi8 = [0] * channels
            for i, w in enumerate(extras):
                even = i * 2
                odd  = even + 1
                if even < channels: hi8[even] = w & 0xFF
                if odd  < channels: hi8[odd]  = (w >> 8) & 0xFF
            frame = [_sign_extend(((hi8[ch] << 16) | (low16s[ch] & 0xFFFF)), 24) for ch in range(channels)]

        elif nbits == 32:
            # channels words in block order [low16][high16]*ch
            if len(row) < channels*2:
                break
            frame = []
            for ch in range(channels):
                lo = row[2*ch] & 0xFFFF
                hi = row[2*ch + 1] & 0xFFFF
                frame.append(_sign_extend(((hi << 16) | lo), 32))

        else:
            frame = [_sign_extend(x, 16) for x in low16s]

        frames.append(frame)
    return frames


def handler_data_fun(packet_queue, stop_event:threading.Event):
    """
    Continuously consume parsed-packet dicts from packet_queue, 
    update plots and write CSV if recording is on.
    @TODO Add timestamp verification to handle lost packets
    """
    
    tab_content = get_tab_content()
    control_panel = get_control_panel()
    # prepare a time‐stamped prefix for CSV files
    session_prefix = None


    while not stop_event.is_set():
        try:
            pkt = packet_queue.get(timeout=2)  # {'signal_type', 'channels','rows','timestamp','eof','payload'}
            signal_name = pkt['signal_name']
            nch     = pkt['channels']
            hdr_ts = pkt['timestamp']   #header timestamp
            payload = pkt['payload']
            data_rate = pkt['data_rate'] #Hz
            labels      = pkt['ch_labels']
            nbits = pkt['nbits']
            # print(signal_name)

            # 1) unpack raw payload into frames
            #frames = unpack_16bit_frames(payload, nch)
            frames = unpack_frames(payload, nch, nbits,signal_name)
            n_rows = len(frames)
            # print(frames)

            # 2) continuity check and detect lost samples
            lost = 0
            if signal_name in last_device_ts:
                expected_ms = n_rows * (1000.0 / data_rate)
                delta_ms   = (hdr_ts - last_device_ts[signal_name]) & 0xFFFF
                if abs(delta_ms - expected_ms) > (1000.0 / data_rate):
                    lost = round((delta_ms - expected_ms) / (1000.0 / data_rate))
                    # print(f"[WARN] {signal_name}: detected ~{lost} lost samples")
            last_device_ts[signal_name] = hdr_ts
            # print(f"[TS]: {signal_name} {hdr_ts}")


            # print(tab_content.plots[signal_name])
            # 2) dispatch to the right PlotWidget
            plot_widget = tab_content.plots[signal_name]
            
            if plot_widget:
                for frame in frames:
                    upto = min(len(plot_widget.signal_data), len(frame))
                    for i in range(upto):
                        plot_widget.signal_data[i].append(frame[i])

                    #for i in range(plot_widget.n_ch):
                        #plot_widget.signal_data[i].append(frame[i])
                        #maybe handle lost packets

            # 3) save to CSV if recording
            if control_panel.get_recording_state():
                # initialize session prefix
                if session_prefix is None:
                    # fresh recording start: clear _all_ counters & start segment 0
                    session_prefix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")[:-3]

                    sample_counters.clear()
                    last_device_ts.clear()
                    segment_counters.clear()
                    segment_indices.clear()

                # Determine segmentation parameters
                sel_type    = cm.get_selected_type(signal_name)
                seg_secs    = cm.get_max_record(signal_name, sel_type) or 0
                out_dir     = control_panel.get_path()
                os.makedirs(out_dir, exist_ok=True)
                # Build rows of data with elapsed‐seconds timestamp + channel columns
                base_idx = sample_counters[signal_name]
                nativets = last_device_ts[signal_name]
                rows = []
                #maybe add placeholders for lost packets
                for i, frame in enumerate(frames):
                    rows.append({
                        'time_s': (base_idx + i) / data_rate,
                        'TS':(nativets),
                        **dict(zip(labels, frame))
                    })
                sample_counters[signal_name]   += n_rows
                segment_counters[signal_name]  += n_rows

                # Choose filename: no suffix for first segment (idx=0), else _1, _2, …
                # idx       = segment_indices[signal_name]
                base_name = f"{session_prefix}_{signal_name}"
                # suffix    = f"_{idx}" if idx > 0 else ""
                # filename  = os.path.join(out_dir, f"{base_name}{suffix}.csv")
                filename  = os.path.join(out_dir, f"{base_name}.csv")
                # Write header only if file is new
                write_header = not os.path.exists(filename)
                df = pd.DataFrame(rows)
                df.to_csv(
                    filename,
                    index=False,
                    header=write_header,
                    mode='a' if not write_header else 'w'
                )                

                # Rotate to next file when capacity reached
                if seg_secs and segment_counters[signal_name] >= seg_secs * data_rate:
                    session_prefix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")[:-3]
                    sample_counters.clear()
                    last_device_ts.clear()
                    segment_indices[signal_name] += 1
                    segment_counters[signal_name]  = 0

            else:
                # Stop recording: reset session_prefix so next start re‐initializes
                session_prefix = None                    

        except Exception as e:
            print(f"[ERROR] handler {e}")


        
        