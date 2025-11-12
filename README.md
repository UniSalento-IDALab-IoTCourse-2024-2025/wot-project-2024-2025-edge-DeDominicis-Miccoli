# **Instructions**
## Required Files

1. **IITdata_acq.py**
2. **channel_manager.py**
3. **handler_data.py**
4. **serial_threads**

## How to Use

1. Clone the REPO and make sure all dependencies are installed and present 
2. Make sure Device is switched on (IIT Electronic device)
3. Make sure the USB gateway is connected to the PC 
4. Choose the correct COM Port  - modify SHELL_PORT and DATA_PORT variables in IITdata_acq.py and insert the correct values
5. Run **IITdata_acq.py**

The device should go through the connection procedure, initialize the ECG, ADC, and Temperature sensor

This is an example of the expecetd messages you may see in the terminal, the messages may not be exatly as shown. 
<img width="678" height="839" alt="Messages1" src="https://github.com/user-attachments/assets/8ffb8d8c-1422-4651-bac8-110ebf5f6795" />
<img width="402" height="416" alt="Messages2" src="https://github.com/user-attachments/assets/ab54eb61-b0c2-4144-b2c9-8aedf2106805" />

Once the procedure is finished, data acquisition will commence and a Matplotlib figure will pop up plotting the data preprogrammed into the device 

This is an example of the expected window 
<img width="1920" height="1023" alt="ExpectedData" src="https://github.com/user-attachments/assets/d7b66619-82c6-4828-8f68-1509c8d4b89a" />

Close the MatplotLib figure to stop the data acquisition and disconnect, the device.

These messages will appear in the terminal

<img width="319" height="326" alt="Messages4" src="https://github.com/user-attachments/assets/ebf2cdac-5af8-4e4e-a83c-743aed8f6e55" />
