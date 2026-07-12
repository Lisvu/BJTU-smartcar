import argparse
import shutil
import subprocess
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Float32

from battery_status import classify_battery


class BatteryMonitor(Node):
    def __init__(self, low_voltage, critical_voltage, interval, voice):
        super().__init__("battery_monitor")
        self.low_voltage = low_voltage
        self.critical_voltage = critical_voltage
        self.interval = interval
        self.voice = voice and shutil.which("spd-say") is not None
        self.last_warn_time = 0.0
        self.last_level = "ok"

        self.buzzer_pub = self.create_publisher(Bool, "/Buzzer", 1)
        self.create_subscription(Float32, "/voltage", self.on_voltage, 10)

        self.get_logger().info(
            "Battery monitor started. low=%.2fV critical=%.2fV voice=%s"
            % (self.low_voltage, self.critical_voltage, self.voice)
        )

    def on_voltage(self, msg):
        voltage = float(msg.data)
        now = time.time()

        level, text = classify_battery(
            voltage,
            self.low_voltage,
            self.critical_voltage,
        )

        if level == "ok":
            if self.last_level != "ok":
                self.get_logger().info(text)
            self.last_level = level
            return

        if level != self.last_level or now - self.last_warn_time >= self.interval:
            self.get_logger().warning(text)
            self.alert(level, text)
            self.last_warn_time = now

        self.last_level = level

    def alert(self, level, text):
        if self.voice:
            subprocess.Popen(["spd-say", text])

        count = 5 if level == "critical" else 3
        for _ in range(count):
            self.buzzer_pub.publish(Bool(data=True))
            time.sleep(0.08)
            self.buzzer_pub.publish(Bool(data=False))
            time.sleep(0.08)


def main():
    parser = argparse.ArgumentParser(description="Warn when car battery voltage is low.")
    parser.add_argument("--low", type=float, default=11.0, help="Low battery warning voltage")
    parser.add_argument("--critical", type=float, default=10.6, help="Critical battery warning voltage")
    parser.add_argument("--interval", type=float, default=60.0, help="Seconds between repeated warnings")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice prompt")
    args = parser.parse_args()

    rclpy.init()
    node = BatteryMonitor(args.low, args.critical, args.interval, not args.no_voice)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
