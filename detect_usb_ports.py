#!/usr/bin/env python3
"""
USB Port Detection - Cross-platform
Rileva tutte le porte seriali disponibili su Windows, macOS e Linux
"""

import serial.tools.list_ports
import json
from pathlib import Path


def get_available_ports():
    """
    Rileva tutte le porte seriali USB disponibili
    
    Returns:
        list: Lista di dizionari con informazioni sulle porte
              [{'device': '/dev/tty...', 'description': '...', 'hwid': '...'}]
    """
    ports = serial.tools.list_ports.comports()
    
    available_ports = []
    for port in ports:
        port_info = {
            'device': port.device,
            'description': port.description if port.description else 'Unknown Device',
            'hwid': port.hwid if port.hwid else 'N/A'
        }
        available_ports.append(port_info)
    
    return available_ports


def load_port_config(config_file='usb_ports_config.json'):
    """
    Carica la configurazione delle porte USB dal file JSON
    
    Args:
        config_file: Nome del file di configurazione
        
    Returns:
        dict: Configurazione con 'shell_port' e 'data_port'
    """
    config_path = Path(config_file)
    
    # Default configuration (macOS USB modem)
    default_config = {
        'shell_port': '/dev/tty.usbmodem1201',
        'data_port': '/dev/tty.usbmodem1203'
    }
    
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                return config
        except Exception as e:
            print(f"[Config] Errore lettura {config_file}: {e}")
            print(f"[Config] Uso configurazione default")
            return default_config
    else:
        # Crea file con configurazione default
        save_port_config(default_config['shell_port'], default_config['data_port'], config_file)
        return default_config


def save_port_config(shell_port, data_port, config_file='usb_ports_config.json'):
    """
    Salva la configurazione delle porte USB nel file JSON
    
    Args:
        shell_port: Porta per shell/comandi
        data_port: Porta per dati
        config_file: Nome del file di configurazione
        
    Returns:
        bool: True se salvataggio riuscito
    """
    config = {
        'shell_port': shell_port,
        'data_port': data_port
    }
    
    try:
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"[Config] Configurazione salvata in {config_file}")
        return True
    except Exception as e:
        print(f"[Config] Errore salvataggio {config_file}: {e}")
        return False


def main():
    """Test del rilevamento porte"""
    print("=== Rilevamento Porte USB ===")
    
    ports = get_available_ports()
    
    if not ports:
        print("Nessuna porta USB rilevata")
    else:
        print(f"\nPorte USB disponibili ({len(ports)}):")
        for i, port in enumerate(ports, 1):
            print(f"\n{i}. Device: {port['device']}")
            print(f"   Descrizione: {port['description']}")
            print(f"   HWID: {port['hwid']}")
    
    print("\n=== Test Configurazione ===")
    
    # Carica configurazione
    config = load_port_config()
    print(f"\nConfigurazione attuale:")
    print(f"  Shell Port: {config['shell_port']}")
    print(f"  Data Port:  {config['data_port']}")


if __name__ == '__main__':
    main()