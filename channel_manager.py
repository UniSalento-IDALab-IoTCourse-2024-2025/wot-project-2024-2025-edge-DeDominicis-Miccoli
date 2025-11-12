from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Callable, List, Union



@dataclass
class ChannelInfo: 
    selected:           bool =                                      False 

    type:               Optional[Tuple[str, ...]] =                 None
    default_configp:    Optional[str] =                             None
    selected_type:      Optional[str] =                             None  # ← track current option
    signed_data :       Optional[bool]=                             True
    runtime_bit_width: Optional[int] = 16             
    runtime_bit_width_map: Optional[Dict[str, int]] = None  

    
    plot_config:        Optional[Dict[str, int]] =                  None
    plot_autoupdate:    Optional[int]=                        None
    shell_config:       Optional[Dict[str,Union[str, List[str]]]]=  None


    label_config:       Optional[Dict[str,List[str]]] =             None
    subplot_labels_map: Optional[Dict[str, List[List[str]]]] = None


    nibble:             Optional[int] =                             None
    nibble_map:         Optional[Dict[str,int]]   =                 None

    data_rate:          Optional[int] =                             None
    data_rate_map:      Optional[Dict[str,int]]   =                 None 

    path:               Optional[str]             =                 None
    path_map:           Optional[Dict[str,str]] =                   None

    ylim:               Optional[Tuple[int, int]]        =          None
    ylim_map:           Optional[Dict[str,Tuple[int,int]]] =        None

    path_to_save:       Optional[str]         =                     None    

    max_record:         Optional[int] =                             None  
    max_record_map:     Optional[Dict[str,int]] =                   None

    nbits_pos:         Optional[int]             =             None
    nbits_pos_map:      Optional[Dict[str,int]]   =                 None 

    window_size:         Optional[int]             =             1000
    window_size_map:     Optional[Dict[str,int]]    =             None

    plotduration:         Optional[int]             =             30
    plotduration_map:     Optional[Dict[str,int]]    =             None

    help_commands:     Optional[List[str]] = None

    filter_mode: Optional[str] = "Raw"            # "Raw" | "Filtered"
    filter_type: Optional[str] = "MovingAvg"      # "MovingAvg" | "Lowpass" | "Highpass" | "None" | "NK2:..."
    filter_params: Optional[Dict[str, float]] = field(default_factory=lambda: {"window": 5, "fc": 10.0})

    # Per-subplot (per label) overrides; keys are integer channel indices (0..n-1)
    filter_mode_map:  Optional[Dict[int, str]]         = field(default_factory=dict)
    filter_type_map:  Optional[Dict[int, str]]         = field(default_factory=dict)
    filter_params_map:Optional[Dict[int, Dict[str, float]]] = field(default_factory=dict)



    def __repr__(self):
        return (
            f"ChannelInfo(selected={self.selected}, "
            f"selected_type={self.selected_type}, "
            f"plot_config={self.plot_config})"
        )
    
    def __post_init__(self):
        # only channels that actually send data need a nibble
        if self.plot_config and self.nibble is None and not self.nibble_map:
            raise ValueError("ChannelInfo for data channels needs nibble or nibble_map")
        if self.plot_config:
            if self.data_rate is None and not self.data_rate_map:
                raise ValueError("Need data_rate or data_rate_map")
            if self.path is None      and not self.path_map:
                raise ValueError("Need path or path_map")
            if self.ylim is None      and not self.ylim_map:
                raise ValueError("Need ylim or ylim_map")   


class ChannelManager:
    def __init__(self):

        self.channels: Dict[str, ChannelInfo] = {  
            "ECG": ChannelInfo(
                    type = (["ECG"]),
                    plot_config                 = {"ECG": 1},
                    plot_autoupdate                  = 2,
                    default_configp             = "ECG",
                    shell_config                = {"ECG": ["1", "250", "3", "01", "1", "1", "0010", "-1"]},
                    #["1", "250", "3", "01"]
                    #["1", "250", "3", "01", "1", "1", "0100", "-1"]
                    label_config                = {"ECG": ["ECG"]},
                    nibble                      = 0xC,   
                    data_rate                   = 250,
                    path                        = ".csv", 
                    ylim                        = (-32768, 32767),  
                    help_commands  =[
                    "Freq Gain lpf hpf",
                    "250 311    ← unipolar gain = 12 bit 0",
                    "2 24 2    ← bipolar gain = 24 bit 2"
                ],
                    max_record                 = 3600, #1hour       
                                         
            ), 
            "ADC": ChannelInfo(
                    type                        =("1 CH", "2 CH", "3 CH", "4 CH"), 
                    plot_config                 ={"1 CH": 1, "2 CH": 2, "3 CH": 3, "4 CH": 4},
                    default_configp             = "3 CH",
                    plot_autoupdate              = 2,
                    shell_config                ={"1 CH": ["1", "250", "1", "01"], 
                                                  "2 CH": ["1", "250", "1", "03"],
                                                  "3 CH": ["1", "250", "1", "07", "-1"],
                    #["1", "250", "1", "07", "-1"],
                    # ["1", "250", "1", "07"],      
                                                },
                    label_config                = {"1 CH": ["CH 1"],
                                                    "2 CH": ["CH 1", "CH 2"],
                                                    "3 CH": ["CH 1", "CH 2", "CH 3"],
                                                    },
                    nibble                      = 0xA,                     
                    data_rate                   = 250,
                    path                        = ".csv", 
                    ylim                        = (-32768, 32767),  
                    max_record                 = 3600,    #1hour
            ), 

            "PPG": ChannelInfo(
                    type=(["PPG"]),
                    plot_config={"PPG": 2},
                    plot_autoupdate = 2,
                    default_configp = "PPG",
                    shell_config={"PPG": ["1", "250", "1", "02", "-1"]},
                    #1 250 1 02 -1 ["1", "250", "1", "02", "-1"]
                    #["1", "250", "1", "02"]

                    label_config={"PPG":["IR","RED"]},
                    nibble= 0xB,   
                    data_rate    = 250,
                    path           = ".csv",
                    ylim           = (-32768, 32767),                      
            ),
            "TEMP": ChannelInfo(
                    type=(["TEMP"]),
                    plot_config={"TEMP": 1},
                    plot_autoupdate =2,
                    default_configp = "TEMP",
                    shell_config={"TEMP": []},
                    label_config={"TEMP":["Temp"]},
                    nibble= 0x9,   
                    data_rate    = 1,
                    path           = ".csv",
                    ylim           = (-32768, 32767),   
                    plotduration = 120,
            ),
            # "IMU": ChannelInfo(
            #         type=("Acc", "Gyro", "Both"),
            #         plot_config={"Acc": 1, "Gyro":1, "Both": 2},
            #         plot_autoupdate  = 2,
            #         default_configp = "Both",
            #         shell_config={"Acc": ["1", "02"],
            #                       "Gyro": ["1", "02"],
            #                       "Both": "1"},
            #         label_config={"Acc":["Ax","Ay","Az"],
            #                       "Gyro":["Gx","Gy","Gz"],
            #                       "Both":["Ax","Ay","Az","Gx","Gy","Gz"]
            #                       },
            #         subplot_labels_map={
            #             "Acc":  [["Ax","Ay","Az"]],
            #             "Gyro": [["Gx","Gy","Gz"]],
            #             "Both": [["Ax","Ay","Az"], ["Gx","Gy","Gz"]],
            #         },                                  
            #         nibble       = 0xF,   
            #         data_rate    = 104,
            #         path           = ".csv", 
            #         ylim           = (-32768, 32767),                                            
            # ),            
            #"Display": ChannelInfo(),
            "SHELL": ChannelInfo(selected=True)
        }

        # ─────────────────────────────────────────────────────────────────────
        # Initialize each channel’s selected_type to its default_configp
        # ─────────────────────────────────────────────────────────────────────
        for info in self.channels.values():
            if info.default_configp is not None:
                info.selected_type = info.default_configp


        self._listeners: Dict[str, List[Callable]] = {
            'type_selected': [],
            'channel_selected': [],
            'channel_unselected': [],
        }

    # ----------------------------------------------------------------
    # Observer API (runs in any framework)
    # ----------------------------------------------------------------
    def on(self, event_name, callback):
        self._listeners[event_name].append(callback)

    def off(self, event_name, callback):
        self._listeners[event_name].remove(callback)

    def _emit(self, event_name, *args):
        for fn in list(self._listeners[event_name]):
            fn(*args)
    # --------------------------
    # Plot Config Retrieval
    # --------------------------

    def get_plot_count(self, name: str, type_key: Optional[str] = None):
        info = self.channels.get(name)
        # print(f"Channel Info {info}")
  
        # if not info or not info.plot_config:
        #     return 0
        # print(f"Type Key {type_key}")
        if type_key:
            # print(f"Num PLots selected {info.plot_config[type_key]}")
            return info.plot_config[type_key]
        else:
            # print(f"Values {list(info.default_configp.values())[0]}")
            return info.plot_config[info.default_configp]
        
    def get_plot_autoupdate_config(self,name:str):
        """
        Return the plot autoupdate and zoom options for each channel.
        """        
        info = self.channels.get(name)
        return info.plot_autoupdate

    def get_cmd_config(self, name: str, type_key: Optional[str] = None):
        """
        Return the command used to send init commands to each module
        e.g. PPG 1, ECG 1, EEG 1
        """        
        info = self.channels.get(name)
        raw = (info.shell_config[type_key]
            if type_key
            else info.shell_config[info.default_configp])

            # print(f"Values {list(info.default_configp.values())[0]}")
        return raw if isinstance(raw, list) else [raw]

    def get_nbits_pos(self, name: str, type_key: Optional[str] = None) -> Optional[int]:
        """
        Return the position of nbits command for channel `name`.
        If `type_key` is provided and a per-type map exists, use that;
        otherwise fall back to the default `nbits_pos`.
        """
        info = self.channels[name]
        if type_key and info.nbits_pos_map:
            return info.nbits_pos_map.get(type_key, info.nbits_pos)
        return info.nbits_pos


    def get_signed_data(self, name:str):
        info=self.channels[name]
        return info.signed_data

    def set_runtime_bit_width(self, name: str, type_key: Optional[str] = None, nbits: int = 16) -> None:
        
        """Set the session bit width for a specific channel/type (or default if type_key is None)."""
        ALLOWED_BITS = (16, 20, 24, 32)
        if nbits not in ALLOWED_BITS:
            return
        info = self.channels.get(name)
        if not info:
            return
        if type_key:
            if info.runtime_bit_width_map is None:
                info.runtime_bit_width_map = {}
            info.runtime_bit_width_map[type_key] = nbits
        else:
            info.runtime_bit_width = nbits

    def get_runtime_bit_width(self, name: str, type_key: Optional[str] = None) -> Optional[int]:
        """Get the session bit width for a channel/type, falling back to the default for that channel 16 bits."""
        info = self.channels.get(name)
        if not info:
            return None
        if type_key and info.runtime_bit_width_map and type_key in info.runtime_bit_width_map:
            return info.runtime_bit_width_map[type_key]
        return info.runtime_bit_width



    # --------------------------
    # Channel Access
    # --------------------------

    def get_all_channels(self) -> Dict[str, ChannelInfo]:
        return self.channels

    def add_channel(self, name: str, type_options: Optional[Tuple[str, ...]] = None,
                    plot_config: Optional[Dict[str, int]] = None):
        if name in self.channels:
            raise ValueError(f"Channel '{name}' already exists.")
        self.channels[name] = ChannelInfo(type=type_options, plot_config=plot_config)

    # --------------------------
    # Type Handling
    # --------------------------
    def set_selected_type(self, name: str, type_value: str):
        if name in self.channels and self.channels[name].type:
            self.channels[name].selected_type = type_value
            self._emit('type_selected', name, type_value) 

    def get_selected_type(self, name: str) -> Optional[str]:
        info = self.channels.get(name)
        return info.selected_type if info else None
    
    def get_type(self, name: str) -> Optional[Tuple[str, ...]]:
        info = self.channels.get(name)
        return info.type if info else None          

    # --------------------------
    # Selection Logic
    # --------------------------   

    def select(self, name: str):
        if name in self.channels:
            self.channels[name].selected = True
            # print(f"Selected Channel {name}")
            self._emit('channel_selected', name)
            # notify any tab‐panel listeners
            # for fn in self._listeners:
            #     fn(name, True)            

    def unselect(self, name: str):
        if name in self.channels:
            self.channels[name].selected = False
            # print(f"UNSelected Channel {name}")
            self._emit('channel_unselected', name)
            # notify any tab‐panel listeners
            # for fn in self._listeners:
            #     fn(name, False)

    def toggle(self, name: str):
        if name in self.channels:
            self.channels[name].selected = not self.channels[name].selected

    def is_selected(self, name: str) -> bool:
        return self.channels.get(name).selected if name in self.channels else False

    def get_selected_channels(self) -> Dict[str, ChannelInfo]:
        return {name: info for name, info in self.channels.items() if info.selected and name!="SHELL"}

    def get_default_config(self, channel_name: str) -> str:
        """
        Returns the “default_configp” (i.e. the default type key)
        for the given channel.
        """
        if channel_name not in self.channels:
            raise KeyError(f"Unknown channel: {channel_name!r}")
        return self.channels[channel_name].default_configp
    
    def get_label_config(self, channel_name: str) -> str:
        """
        Returns the “label-config” (i.e. the plot labels for the selecetd signal type)
        """
        if channel_name not in self.channels:
            raise KeyError(f"Unknown channel: {channel_name!r}")
        return self.channels[channel_name].label_config
    
    
    def get_max_record(self, name: str, type_key: Optional[str] = None) -> Optional[int]:
        """
        Return the max-segment duration (in seconds) for channel `name`.
        If `type_key` is given and a per-type map exists, use that;
        otherwise fall back to the default `max_segment_duration`.
        """
        info = self.channels[name]
        if type_key and info.max_record_map:
            return info.max_record_map.get(type_key,
                        info.max_record)
        return info.max_record

    def get_window_size(self, name: str, type_key: Optional[str] = None) -> int:
        info = self.channels[name]
        if type_key and info.window_size_map:
            return info.window_size_map.get(type_key, info.window_size)
        return info.window_size

    def get_data_header(self, name: str, type_key: Optional[str] = None):
        info = self.channels[name]
        if type_key and info.nibble_map:
            return info.nibble_map[type_key]
        else: 
            return info.nibble    


    def get_subplot_labels(self,
                           name: str,
                           type_key: Optional[str] = None
                          ) -> List[List[str]]:
        info = self.channels[name]
        """get labels for each plots to show accordng to the plot map definitions"""

        # 1) if provided an explicit map, use it:
        if info.subplot_labels_map and type_key in info.subplot_labels_map:
            return info.subplot_labels_map[type_key]

        # 2) otherwise, fall back to an even split:
        labels = info.label_config[type_key] \
                 if (type_key and info.label_config) \
                 else info.label_config[info.default_configp]

        n_plots = info.plot_config[type_key] \
                  if (type_key and info.plot_config) \
                  else info.plot_config[info.default_configp]

        chunk = len(labels) // n_plots
        groups = []
        for i in range(n_plots):
            start = i*chunk
            end   = (i+1)*chunk if i < n_plots-1 else len(labels)
            groups.append(labels[start:end])
        return groups


    def get_data_rate(self, name: str, type_key: Optional[str] = None) -> Optional[int]:
        """
        Return the sampling rate (in Hz) for channel `name`.
        If `type_key` is provided and a per-type map exists, use that;
        otherwise fall back to the default `data_rate`.
        """
        info = self.channels[name]
        if type_key and info.data_rate_map:
            return info.data_rate_map.get(type_key, info.data_rate)
        return info.data_rate

    def get_plotduration(self, name: str, type_key: Optional[str] = None) -> Optional[int]:
        """
        Return the plot duration (in seconds) for channel `name`.
        If `type_key` is provided and a per-type map exists, use that;
        otherwise fall back to the default `plot duration`.
        """
        info = self.channels[name]
        if type_key and info.plotduration_map:
            return info.plotduration_map.get(type_key, info.plotduration)
        return info.plotduration    


    def get_window_for_duration(self,
                                name: str,
                                type_key: Optional[str] = None,
                                duration_sec: float = 30.0
                               ) -> int:
        """
        Compute how many samples fit in `duration_sec` seconds
        at this channel's data rate.
        """
        rate = self.get_data_rate(name, type_key)
        return int(rate * duration_sec)  
    
    ## Methdos to get and set filter options
    ## Works for generic filters and the NK filters

    def get_filter_mode(self, name):
        return self.channels[name].filter_mode or "Raw"
    
    def set_filter_mode(self, name, mode): 
        self.channels[name].filter_mode = "Filtered" if mode == "Filtered" else "Raw"

    def get_filter_type(self, name): 
        return self.channels[name].filter_type or "None"
    
    def set_filter_type(self, name, ftype):
        allowed = {"MovingAvg","Lowpass","Highpass","None"}  # NK2 names are accepted later via registry
        self.channels[name].filter_type = ftype if (ftype in allowed or ftype.startswith("NK2:")) else "None"

    def get_filter_params(self, name): 
        return self.channels[name].filter_params or {}
    
    def set_filter_params(self, name, **kwargs):
        cfg = dict(self.channels[name].filter_params or {})
        # accept ints/floats/strings (NK2 params like "method")
        cfg.update({k: v for k, v in kwargs.items() if isinstance(v, (int, float, str)) or v is None})
        self.channels[name].filter_params = cfg




    #def get_channel_manager():
    #    return _channel_manager

    # Shared instance
# _channel_manager = ChannelManager()
#global _channel_manager
_channel_manager: Optional[ChannelManager] = None

def get_channel_manager() -> ChannelManager:
    global _channel_manager
    if _channel_manager is None:
        _channel_manager = ChannelManager()
    return _channel_manager

# Shared instance
#_channel_manager = ChannelManager()
