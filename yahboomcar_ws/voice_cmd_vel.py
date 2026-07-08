import argparse
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from Speech_Lib import Speech


COMMANDS = {
    0: "stop",
    2: "stop",
    4: "forward",
    5: "backward",
    6: "left",
    7: "right",
}


class VoiceCmdVel(Node):
    def __init__(self, linear_speed, angular_speed, hold_time):
        super().__init__("voice_cmd_vel")
        self.pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.speech = Speech()
        self.linear_speed = linear_speed
        self.angular_speed = angular_speed
        self.hold_time = hold_time
        self.last_code = None
        self.last_command_time = 0.0
        self.timer = self.create_timer(0.1, self.tick)
        self.get_logger().info("Voice control started. Say forward/stop commands to the speech module.")

    def tick(self):
        try:
            code = self.speech.speech_read()
        except ValueError as exc:
            self.get_logger().debug("ignore invalid speech data: %s" % exc)
            return
        except Exception as exc:
            self.get_logger().warning("speech read failed: %s" % repr(exc))
            self.publish_stop()
            return
        if code not in COMMANDS:
            if time.time() - self.last_command_time > self.hold_time:
                self.publish_stop()
            return

        command = COMMANDS[code]
        if code != self.last_code:
            self.get_logger().info("voice command: %s code=%s" % (command, code))

        if command == "forward":
            self.publish_twist(self.linear_speed, 0.0, 0.0)
        elif command == "backward":
            self.publish_twist(-self.linear_speed, 0.0, 0.0)
        elif command == "left":
            self.publish_twist(0.0, 0.0, self.angular_speed)
        elif command == "right":
            self.publish_twist(0.0, 0.0, -self.angular_speed)
        else:
            self.publish_stop()

        self.speech.void_write(code)
        self.last_code = code
        self.last_command_time = time.time()

    def publish_twist(self, x, y, z):
        msg = Twist()
        msg.linear.x = x
        msg.linear.y = y
        msg.angular.z = z
        self.pub.publish(msg)

    def publish_stop(self):
        self.publish_twist(0.0, 0.0, 0.0)


def main():
    parser = argparse.ArgumentParser(description="Voice control for Yahboom car using Speech_Lib.")
    parser.add_argument("--linear", type=float, default=0.12, help="Forward/backward speed in m/s")
    parser.add_argument("--angular", type=float, default=0.45, help="Turn speed in rad/s")
    parser.add_argument("--hold", type=float, default=1.5, help="Stop if no command is heard for this many seconds")
    args = parser.parse_args()

    rclpy.init()
    node = VoiceCmdVel(args.linear, args.angular, args.hold)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.publish_stop()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
