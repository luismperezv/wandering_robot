import time
import statistics

import pigpio

SOUND_SPEED = 343.0  # m/s @ ~20C


class PigpioUltrasonic:
    def __init__(self, trig: int, echo: int, max_distance_m: float = 2.5, samples: int = 3):
        self.pi = pigpio.pi()  # needs pigpiod running
        if not self.pi.connected:
            raise RuntimeError("pigpio daemon not running (start with: sudo pigpiod -g -l)")
        self.trig = trig
        self.echo = echo
        self.max_distance_m = max_distance_m
        self.timeout_s = (2 * max_distance_m) / SOUND_SPEED
        self.samples = max(1, samples)

        self.pi.set_mode(self.trig, pigpio.OUTPUT)
        self.pi.set_mode(self.echo, pigpio.INPUT)
        self.pi.write(self.trig, 0)
        self.pi.set_pull_up_down(self.echo, pigpio.PUD_DOWN)

        self._rise = None
        self._fall = None
        self._cb = self.pi.callback(self.echo, pigpio.EITHER_EDGE, self._edge)

    def _edge(self, gpio, level, tick):
        if level == 1:
            self._rise = tick
        elif level == 0:
            self._fall = tick

    @staticmethod
    def _ticks_to_s(start, end):
        if end < start:
            end += (1 << 32)
        return (end - start) / 1_000_000.0

    def _pulse(self):
        self._rise = None
        self._fall = None
        self.pi.gpio_trigger(self.trig, 10, 1)  # 10 µs HIGH

    def distance_cm(self):
        readings = []
        for _ in range(self.samples):
            self._pulse()

            t0 = time.time()
            while self._rise is None and (time.time() - t0) < self.timeout_s:
                time.sleep(0.00005)
            if self._rise is None:
                continue

            t1 = time.time()
            while self._fall is None and (time.time() - t1) < self.timeout_s:
                time.sleep(0.00005)
            if self._fall is None:
                continue

            dt = self._ticks_to_s(self._rise, self._fall)
            d_m = (dt * SOUND_SPEED) / 2.0
            if 0.0 < d_m <= self.max_distance_m:
                readings.append(d_m * 100.0)  # cm
            time.sleep(0.01)

        if not readings:
            return float('inf')
        return statistics.median(readings)

    def close(self):
        if self._cb:
            self._cb.cancel()
        if self.pi and self.pi.connected:
            self.pi.stop()


