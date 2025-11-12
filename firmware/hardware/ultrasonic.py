import time
import statistics
from typing import Dict, List, Optional, Tuple

import pigpio

SOUND_SPEED = 343.0  # m/s @ ~20C


class UltrasonicSensor:
    def __init__(self, pi: pigpio.pi, trig: int, echo: int, name: str, max_distance_m: float = 2.5, samples: int = 3):
        """Initialize a single ultrasonic sensor.
        
        Args:
            pi: pigpio instance
            trig: GPIO pin number for trigger
            echo: GPIO pin number for echo
            name: Name of the sensor (e.g., 'front', 'left', 'right')
            max_distance_m: Maximum distance to measure in meters
            samples: Number of samples to take for each reading
        """
        self.pi = pi
        self.trig = trig
        self.echo = echo
        self.name = name
        self.max_distance_m = max_distance_m
        self.timeout_s = (2 * max_distance_m) / SOUND_SPEED
        self.samples = max(1, samples)

        # Setup GPIO
        self.pi.set_mode(self.trig, pigpio.OUTPUT)
        self.pi.set_mode(self.echo, pigpio.INPUT)
        self.pi.write(self.trig, 0)
        self.pi.set_pull_up_down(self.echo, pigpio.PUD_DOWN)

        # Edge detection
        self._rise = None
        self._fall = None
        self._cb = self.pi.callback(self.echo, pigpio.EITHER_EDGE, self._edge)

    def _edge(self, gpio: int, level: int, tick: int) -> None:
        if level == 1:
            self._rise = tick
        elif level == 0:
            self._fall = tick

    @staticmethod
    def _ticks_to_s(start: int, end: int) -> float:
        if end < start:
            end += (1 << 32)
        return (end - start) / 1_000_000.0

    def _pulse(self) -> None:
        """Send a 10µs pulse to the trigger pin."""
        self._rise = None
        self._fall = None
        self.pi.gpio_trigger(self.trig, 10, 1)  # 10 µs HIGH

    def distance_cm(self) -> float:
        """Get the distance in cm, or inf if no echo is detected."""
        readings = []
        for _ in range(self.samples):
            self._pulse()
            start = time.time()
            while (time.time() - start) < self.timeout_s:
                if self._rise is not None and self._fall is not None and self._fall > self._rise:
                    duration = self._ticks_to_s(self._rise, self._fall)
                    distance = (duration * SOUND_SPEED * 100) / 2  # cm
                    if distance < (self.max_distance_m * 100):
                        readings.append(distance)
                    break
                time.sleep(0.001)  # 1ms delay between checks

        if not readings:
            return float('inf')
        return statistics.median(readings)

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        if hasattr(self, '_cb') and self._cb is not None:
            self._cb.cancel()
            self._cb = None


class MultiUltrasonic:
    def __init__(self, config: dict, max_distance_m: float = 2.5, samples: int = 3):
        """Initialize multiple ultrasonic sensors.
        
        Args:
            config: Dictionary with sensor names as keys and (trig, echo) tuples as values
            max_distance_m: Maximum distance to measure in meters
            samples: Number of samples to take for each reading
        """
        self.pi = pigpio.pi()  # needs pigpiod running
        if not self.pi.connected:
            raise RuntimeError("pigpio daemon not running (start with: sudo pigpiod -g -l)")
            
        self.sensors = {}
        for name, (trig, echo) in config.items():
            self.sensors[name] = UltrasonicSensor(
                pi=self.pi,
                trig=trig,
                echo=echo,
                name=name,
                max_distance_m=max_distance_m,
                samples=samples
            )
    
    def get_distances(self) -> Dict[str, float]:
        """Get distances from all sensors."""
        return {name: sensor.distance_cm() for name, sensor in self.sensors.items()}
    
    def get_distance(self, name: str) -> float:
        """Get distance from a specific sensor by name."""
        if name not in self.sensors:
            raise ValueError(f"No sensor named '{name}'. Available sensors: {list(self.sensors.keys())}")
        return self.sensors[name].distance_cm()
    
    def cleanup(self) -> None:
        """Clean up all sensors and GPIO resources."""
        for sensor in self.sensors.values():
            sensor.cleanup()
        self.pi.stop()

    def close(self):
        if self._cb:
            self._cb.cancel()
        if self.pi and self.pi.connected:
            self.pi.stop()

