#!/usr/bin/env python3
"""
Simulate Anomaly - Generate synthetic anomalies for testing
"""

import json
import numpy as np
from pathlib import Path
from datetime import datetime


class AnomalySimulator:
    """Generate synthetic anomalies for ECG, PIEZO, and TEMP"""
    
    def __init__(self, anomaly_logs_dir="anomaly_logs"):
        self.anomaly_logs_dir = Path(anomaly_logs_dir)
        self.anomaly_logs_dir.mkdir(exist_ok=True)
    
    def generate_ecg_sample_data(self, num_points=100):
        """
        Generate realistic ECG-like waveform data
        Simulates P-QRS-T complex pattern
        """
        sample_data = []
        
        # Baseline
        baseline = 100
        
        for i in range(num_points):
            # Normalize position (0 to 1)
            pos = i / num_points
            
            # P wave (small bump at start)
            if 0.1 < pos < 0.2:
                value = baseline + 10 * np.sin((pos - 0.1) * np.pi / 0.1)
            # QRS complex (sharp spike in middle)
            elif 0.35 < pos < 0.55:
                if 0.35 < pos < 0.4:  # Q dip
                    value = baseline - 20
                elif 0.4 < pos < 0.45:  # R peak
                    value = baseline + 280 * ((pos - 0.4) / 0.05)
                elif 0.45 < pos < 0.5:  # S dip
                    value = baseline - 20
                else:  # Return to baseline
                    value = baseline + 40 * (1 - (pos - 0.5) / 0.05)
            # T wave (broader bump after QRS)
            elif 0.6 < pos < 0.8:
                value = baseline + 30 * np.sin((pos - 0.6) * np.pi / 0.2)
            else:
                value = baseline
            
            # Add small random noise
            value += np.random.normal(0, 2)
            sample_data.append(int(value))
        
        return sample_data
    
    def generate_piezo_sample_data(self, num_points=100):
        """
        Generate realistic PIEZO sensor data
        Simulates pressure wave pattern
        """
        sample_data = []
        
        baseline = 450
        
        for i in range(num_points):
            pos = i / num_points
            
            # Main pressure wave (asymmetric bell curve)
            if 0.1 < pos < 0.6:
                # Rise (fast)
                if pos < 0.3:
                    amplitude = ((pos - 0.1) / 0.2) ** 2
                    value = baseline + 1100 * amplitude
                # Fall (slower)
                else:
                    amplitude = 1 - ((pos - 0.3) / 0.3) ** 1.5
                    value = baseline + 1100 * amplitude
            else:
                value = baseline
            
            # Add noise
            value += np.random.normal(0, 5)
            sample_data.append(int(value))
        
        return sample_data
    
    def calculate_severity(self, anomaly_type, temperature):
        """
        Calculate severity based on temperature and anomaly type
        
        Args:
            anomaly_type: 'hypothermia' or 'hyperthermia'
            temperature: float temperature value
            
        Returns:
            str: 'mild', 'moderate', or 'severe'
        """
        if anomaly_type == "hypothermia":
            if temperature < 32.0:
                return "severe"
            elif temperature < 34.0:
                return "moderate"
            else:
                return "mild"
        else:  # hyperthermia
            if temperature > 40.0:
                return "severe"
            elif temperature > 39.0:
                return "moderate"
            else:
                return "mild"
    
    def simulate_ecg_anomaly(self, reconstruction_error=None, threshold=0.1):
        """
        Generate a synthetic ECG anomaly
        
        Args:
            reconstruction_error: float (if None, random value above threshold)
            threshold: float (default 0.1)
            
        Returns:
            dict: Anomaly data
        """
        now = datetime.now()
        
        # Generate reconstruction error if not provided
        if reconstruction_error is None:
            # Random error between threshold and 2x threshold
            reconstruction_error = threshold + np.random.uniform(0, threshold)
        
        anomaly = {
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S.%f")[:-3],
            "reconstruction_error": round(reconstruction_error, 4),
            "threshold": threshold,
            "sample_data": self.generate_ecg_sample_data()
        }
        
        return anomaly
    
    def simulate_piezo_anomaly(self, reconstruction_error=None, threshold=0.1):
        """
        Generate a synthetic PIEZO anomaly
        
        Args:
            reconstruction_error: float (if None, random value above threshold)
            threshold: float (default 0.1)
            
        Returns:
            dict: Anomaly data
        """
        now = datetime.now()
        
        # Generate reconstruction error if not provided
        if reconstruction_error is None:
            reconstruction_error = threshold + np.random.uniform(0, threshold)
        
        anomaly = {
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S.%f")[:-3],
            "reconstruction_error": round(reconstruction_error, 4),
            "threshold": threshold,
            "sensor": "PIEZO",
            "sample_data": self.generate_piezo_sample_data()
        }
        
        return anomaly
    
    def simulate_temp_anomaly(self, anomaly_type, temperature, threshold=35.0, 
                             duration_readings=5, severity=None):
        """
        Generate a synthetic TEMP anomaly
        
        Args:
            anomaly_type: 'hypothermia' or 'hyperthermia'
            temperature: float temperature value
            threshold: float (default 35.0)
            duration_readings: int (default 5)
            severity: str ('mild', 'moderate', 'severe') or None to auto-calculate
            
        Returns:
            dict: Anomaly data
        """
        now = datetime.now()
        
        # Calculate severity if not provided
        if severity is None:
            severity = self.calculate_severity(anomaly_type, temperature)
        
        anomaly = {
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "anomaly_type": anomaly_type,
            "temperature": round(temperature, 2),
            "threshold": threshold,
            "duration_readings": duration_readings,
            "severity": severity
        }
        
        return anomaly
    
    def save_anomaly(self, anomaly_type, anomaly_data):
        """
        Save anomaly to appropriate JSON file
        
        Args:
            anomaly_type: 'ecg', 'piezo', or 'temp'
            anomaly_data: dict with anomaly data
            
        Returns:
            bool: True if successful
        """
        today = datetime.now().strftime("%Y%m%d")
        
        # Determine filename
        if anomaly_type == 'ecg':
            filename = f"anomalies_{today}.json"
        elif anomaly_type == 'piezo':
            filename = f"piezo_anomalies_{today}.json"
        elif anomaly_type == 'temp':
            filename = f"temp_anomalies_{today}.json"
        else:
            raise ValueError(f"Invalid anomaly type: {anomaly_type}")
        
        filepath = self.anomaly_logs_dir / filename
        
        # Load existing anomalies
        if filepath.exists():
            with open(filepath, 'r') as f:
                anomalies = json.load(f)
        else:
            anomalies = []
        
        # Append new anomaly
        anomalies.append(anomaly_data)
        
        # Save back to file
        with open(filepath, 'w') as f:
            json.dump(anomalies, f, indent=2)
        
        print(f"[Simulator] Saved {anomaly_type.upper()} anomaly to {filename}")
        return True


def main():
    """Test the simulator"""
    simulator = AnomalySimulator()
    
    # Test ECG
    print("Generating ECG anomaly...")
    ecg_anomaly = simulator.simulate_ecg_anomaly(reconstruction_error=0.1847)
    simulator.save_anomaly('ecg', ecg_anomaly)
    print(json.dumps(ecg_anomaly, indent=2))
    
    # Test PIEZO
    print("\nGenerating PIEZO anomaly...")
    piezo_anomaly = simulator.simulate_piezo_anomaly(reconstruction_error=0.1678)
    simulator.save_anomaly('piezo', piezo_anomaly)
    print(json.dumps(piezo_anomaly, indent=2))
    
    # Test TEMP
    print("\nGenerating TEMP anomaly...")
    temp_anomaly = simulator.simulate_temp_anomaly(
        anomaly_type='hypothermia',
        temperature=22.78,
        duration_readings=9
    )
    simulator.save_anomaly('temp', temp_anomaly)
    print(json.dumps(temp_anomaly, indent=2))


if __name__ == '__main__':
    main()