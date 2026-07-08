import math
import os
import select
import sys
import termios
import threading
import time
import tty

import serial

from Rosmaster_Lib import Rosmaster


CMD_FORWARD = 1
CMD_BACKWARD = 2
CMD_LEFT = 3
CMD_RIGHT = 4
CMD_LEFT_SPIN = 5
CMD_RIGHT_SPIN = 6
CMD_STOP = 7

DEFAULT_SPEED = 30
MIN_SPEED = 25
MAX_SPEED = 100
SPEED_STEP = 5

LIDAR_PORT = "/dev/rplidar"
LIDAR_BAUDRATE = 115200

# Tune these first. Most RPLidar installs use 0 degrees as the forward direction.
FRONT_CENTER_DEG = 0
FRONT_HALF_WIDTH_DEG = 35
STOP_DISTANCE_M = 0.30
CLEAR_DISTANCE_M = 0.60
LIDAR_DATA_TIMEOUT = 1.0

CHECK_INTERVAL = 0.03
COMMAND_HOLD_TIMEOUT = 0.60
STOP_REPEAT_INTERVAL = 0.25
STATUS_PRINT_INTERVAL = 0.5


KEY_TO_CMD = {
    "w": CMD_FORWARD,
    "s": CMD_BACKWARD,
    "a": CMD_LEFT,
    "d": CMD_RIGHT,
    "j": CMD_LEFT_SPIN,
    "l": CMD_RIGHT_SPIN,
    "x": CMD_STOP,
    " ": CMD_STOP,
    "1": CMD_FORWARD,
    "2": CMD_BACKWARD,
    "3": CMD_LEFT,
    "4": CMD_RIGHT,
    "5": CMD_LEFT_SPIN,
    "6": CMD_RIGHT_SPIN,
    "7": CMD_STOP,
}

CMD_NAMES = {
    CMD_FORWARD: "forward",
    CMD_BACKWARD: "backward",
    CMD_LEFT: "left",
    CMD_RIGHT: "right",
    CMD_LEFT_SPIN: "left spin",
    CMD_RIGHT_SPIN: "right spin",
    CMD_STOP: "stop",
}


def angle_in_front(angle_deg):
    diff = (angle_deg - FRONT_CENTER_DEG + 180.0) % 360.0 - 180.0
    return abs(diff) <= FRONT_HALF_WIDTH_DEG


class RPLidarGuard:
    def __init__(self, port=LIDAR_PORT, baudrate=LIDAR_BAUDRATE):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
        self.lock = threading.Lock()
        self.running = False
        self.thread = None
        self.front_min_m = math.inf
        self.last_scan_time = 0
        self.error = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.serial:
            try:
                self._send_cmd(0x25)  # stop
                time.sleep(0.05)
                self.serial.close()
            except Exception:
                pass

    def snapshot(self):
        with self.lock:
            age = time.time() - self.last_scan_time if self.last_scan_time else math.inf
            return self.front_min_m, age, self.error

    def blocked(self):
        front_min_m, age, error = self.snapshot()
        if error is not None or age > LIDAR_DATA_TIMEOUT:
            return True, front_min_m, age, error
        return front_min_m <= STOP_DISTANCE_M, front_min_m, age, error

    def clear(self):
        front_min_m, age, error = self.snapshot()
        return error is None and age <= LIDAR_DATA_TIMEOUT and front_min_m >= CLEAR_DISTANCE_M

    def _send_cmd(self, cmd):
        self.serial.write(bytes([0xA5, cmd]))
        self.serial.flush()

    def _run(self):
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=0.2)
            self.serial.setDTR(False)
            time.sleep(1.0)
            self._send_cmd(0x25)  # stop any previous scan
            time.sleep(0.1)
            self._send_cmd(0x20)  # start scan
            self.serial.read(7)   # response descriptor

            current_front_min = math.inf
            last_rotation_time = time.time()
            last_start_flag = False

            while self.running:
                packet = self.serial.read(5)
                if len(packet) != 5:
                    continue

                b0, b1, b2, b3, b4 = packet
                start_flag = b0 & 0x01
                inverse_start_flag = (b0 >> 1) & 0x01
                check_bit = b1 & 0x01

                if start_flag == inverse_start_flag or check_bit != 1:
                    continue

                if start_flag and not last_start_flag:
                    with self.lock:
                        self.front_min_m = current_front_min
                        self.last_scan_time = time.time()
                        self.error = None
                    current_front_min = math.inf
                    last_rotation_time = time.time()

                last_start_flag = bool(start_flag)

                angle_q6 = ((b1 >> 1) | (b2 << 7))
                distance_q2 = b3 | (b4 << 8)
                angle_deg = angle_q6 / 64.0
                distance_m = (distance_q2 / 4.0) / 1000.0

                if 0.03 <= distance_m <= 6.0 and angle_in_front(angle_deg):
                    current_front_min = min(current_front_min, distance_m)

                # Publish a partial value if the start flag is delayed.
                if time.time() - last_rotation_time > 0.5:
                    with self.lock:
                        self.front_min_m = current_front_min
                        self.last_scan_time = time.time()
                        self.error = None
                    current_front_min = math.inf
                    last_rotation_time = time.time()

        except Exception as exc:
            with self.lock:
                self.error = repr(exc)


def stop_car(bot):
    bot.set_car_run(CMD_STOP, 100, adjust=False)


def send_command(bot, cmd, speed):
    if cmd == CMD_STOP:
        stop_car(bot)
    else:
        bot.set_car_run(cmd, speed, adjust=False)
    print("command:", CMD_NAMES.get(cmd, str(cmd)), "speed:", speed)


def read_key(timeout):
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    return sys.stdin.read(1)


def print_help(speed):
    print("Lidar guarded manual drive")
    print("Controls: w/1 forward, s/2 backward, a/3 left, d/4 right")
    print("          j/5 left spin, l/6 right spin, x/7/space stop")
    print("          +/- speed, q quit")
    print("Current speed:", speed)
    print("Hold a movement key to keep moving. Release it and the car stops automatically.")
    print("Forward is blocked when lidar front distance <= %.2fm." % STOP_DISTANCE_M)


def main():
    speed = DEFAULT_SPEED
    debug = False

    if len(sys.argv) >= 2:
        if sys.argv[1] == "debug":
            debug = True
        else:
            speed = int(sys.argv[1])

    if len(sys.argv) >= 3 and sys.argv[2] == "debug":
        debug = True

    bot = Rosmaster(debug=False)
    bot.create_receive_threading()

    lidar = RPLidarGuard()
    lidar.start()

    current_cmd = CMD_STOP
    blocked = False
    last_motion_key_time = 0
    last_stop_time = 0
    last_status_print_time = 0

    old_settings = termios.tcgetattr(sys.stdin)

    try:
        tty.setcbreak(sys.stdin.fileno())
        print_help(speed)

        while True:
            now = time.time()
            is_blocked, front_min_m, data_age, lidar_error = lidar.blocked()

            if debug and now - last_status_print_time >= STATUS_PRINT_INTERVAL:
                if lidar_error:
                    print("lidar error:", lidar_error)
                elif math.isinf(front_min_m):
                    print("front distance: inf, data age:", round(data_age, 2))
                else:
                    print("front distance:", round(front_min_m, 3), "m")
                last_status_print_time = now

            if is_blocked and current_cmd == CMD_FORWARD:
                if not blocked:
                    if lidar_error:
                        print("Lidar error. Forward stopped:", lidar_error)
                    elif data_age > LIDAR_DATA_TIMEOUT:
                        print("Lidar data timeout. Forward stopped.")
                    else:
                        print("Obstacle detected. Forward stopped. distance:", round(front_min_m, 3), "m")
                if now - last_stop_time >= STOP_REPEAT_INTERVAL:
                    stop_car(bot)
                    last_stop_time = now
                current_cmd = CMD_STOP
                last_motion_key_time = 0
                blocked = True
            elif blocked and lidar.clear():
                print("Path clear. You can drive forward again.")
                blocked = False

            if current_cmd != CMD_STOP and now - last_motion_key_time >= COMMAND_HOLD_TIMEOUT:
                stop_car(bot)
                current_cmd = CMD_STOP

            key = read_key(CHECK_INTERVAL)
            if key is None:
                continue

            if key == "q":
                break

            if key in ("+", "="):
                speed = min(MAX_SPEED, speed + SPEED_STEP)
                print("speed:", speed)
                continue

            if key in ("-", "_"):
                speed = max(MIN_SPEED, speed - SPEED_STEP)
                print("speed:", speed)
                continue

            if key not in KEY_TO_CMD:
                continue

            requested_cmd = KEY_TO_CMD[key]

            if requested_cmd == CMD_FORWARD:
                is_blocked, front_min_m, data_age, lidar_error = lidar.blocked()
                if is_blocked:
                    if lidar_error:
                        print("Forward blocked: lidar error:", lidar_error)
                    elif data_age > LIDAR_DATA_TIMEOUT:
                        print("Forward blocked: lidar data timeout")
                    else:
                        print("Forward blocked by obstacle. distance:", round(front_min_m, 3), "m")
                    stop_car(bot)
                    current_cmd = CMD_STOP
                    last_motion_key_time = 0
                    blocked = True
                    continue

            send_command(bot, requested_cmd, speed)
            current_cmd = requested_cmd
            if requested_cmd == CMD_STOP:
                last_motion_key_time = 0
                blocked = False
            else:
                last_motion_key_time = time.time()

    except KeyboardInterrupt:
        print("Exit, stop car")

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        stop_car(bot)
        lidar.stop()


if __name__ == "__main__":
    main()
