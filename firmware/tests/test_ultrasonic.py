#!/usr/bin/env python3
"""
Test script for the three ultrasonic sensors.
Reads from each sensor sequentially and prints the distance in cm.
"""
import time
import sys
import os

# Add parent directory to path to import from firmware
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from firmware.hardware.ultrasonic import MultiUltrasonic
from firmware import config

def test_sensors():
    # Configure all three ultrasonic sensors
    sensor_config = {
        'front': (config.FRONT_TRIG, config.FRONT_ECHO),
        'left': (config.LEFT_TRIG, config.LEFT_ECHO),
        'right': (config.RIGHT_TRIG, config.RIGHT_ECHO)
    }
    
    try:
        # Initialize the multi-sensor system
        print("Initializing ultrasonic sensors...")
        sensors = MultiUltrasonic(
            config=sensor_config,
            max_distance_m=config.MAX_DISTANCE_M,
            samples=3  # Take 3 samples per reading for better accuracy
        )
        
        print("Ultrasonic sensor test started. Press Ctrl+C to exit.")
        print("-" * 50)
        
        while True:
            try:
                # Get distances from all sensors
                distances = sensors.get_distances()
                
                # Print header
                print("\n" + "=" * 50)
                print(f"{'Sensor':<10} | {'Distance (cm)':<15} | Status")
                print("-" * 50)
                
                # Print each sensor's reading
                for name, distance in distances.items():
                    status = "OK" if distance != float('inf') else "NO ECHO"
                    print(f"{name.upper():<10} | {distance if distance != float('inf') else 'N/A':<15.1f} | {status}")
                
                print("=" * 50)
                
                # Wait before next reading
                time.sleep(3)
                
            except KeyboardInterrupt:
                print("\nTest stopped by user.")
                break
                
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        # Clean up
        if 'sensors' in locals():
            print("Cleaning up...")
            sensors.cleanup()
        print("Test completed.")

if __name__ == "__main__":
    test_sensors()
