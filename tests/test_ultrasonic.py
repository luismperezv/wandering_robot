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

from firmware.hardware.ultrasonic import PigpioUltrasonic
from firmware import config

def test_sensors():
    # Initialize each sensor separately
    print("Initializing ultrasonic sensors...")
    
    # Front sensor
    front_sensor = PigpioUltrasonic(
        trig=config.FRONT_TRIG,
        echo=config.FRONT_ECHO,
        max_distance_m=config.MAX_DISTANCE_M,
        samples=3
    )
    
    # Left sensor
    left_sensor = PigpioUltrasonic(
        trig=config.LEFT_TRIG,
        echo=config.LEFT_ECHO,
        max_distance_m=config.MAX_DISTANCE_M,
        samples=3
    )
    
    # Right sensor
    right_sensor = PigpioUltrasonic(
        trig=config.RIGHT_TRIG,
        echo=config.RIGHT_ECHO,
        max_distance_m=config.MAX_DISTANCE_M,
        samples=3
    )
    
    sensors = {
        'front': front_sensor,
        'left': left_sensor,
        'right': right_sensor
    }
    
    try:
        print("Ultrasonic sensor test started. Press Ctrl+C to exit.")
        print("-" * 50)
        
        while True:
            try:
                # Get distances from all sensors
                distances = {}
                for name, sensor in sensors.items():
                    distances[name] = sensor.distance_cm()
                
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
        for sensor in sensors.values():
            try:
                sensor.close()
            except Exception as e:
                print(f"Error cleaning up sensor: {e}")
        print("Test completed.")

if __name__ == "__main__":
    test_sensors()
