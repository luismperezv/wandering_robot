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

from firmware.hardware.ultrasonic import MultiUltrasonic, UltrasonicSensor
from firmware import config

def test_sensors():
    print("Initializing ultrasonic sensors...")
    
    # Sensor configuration
    sensor_config = {
        'front': (config.FRONT_TRIG, config.FRONT_ECHO),
        'left': (config.LEFT_TRIG, config.LEFT_ECHO),
        'right': (config.RIGHT_TRIG, config.RIGHT_ECHO)
    }
    
    # Initialize MultiUltrasonic with all sensors
    try:
        sensors = MultiUltrasonic(
            config=sensor_config,
            max_distance_m=config.MAX_DISTANCE_M,
            samples=3
        )
    except RuntimeError as e:
        print(f"Error initializing sensors: {e}")
        print("Make sure the pigpio daemon is running (sudo pigpiod -g -l)")
        return
    
    try:
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
                    distance_str = f"{distance:.1f}" if isinstance(distance, (int, float)) and distance != float('inf') else 'N/A'
                    print(f"{name.upper():<10} | {distance_str:<15} | {status}")
                
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
        print("Cleaning up...")
        try:
            sensors.cleanup()
        except Exception as e:
            print(f"Error cleaning up sensors: {e}")
        print("Test completed.")

if __name__ == "__main__":
    test_sensors()
