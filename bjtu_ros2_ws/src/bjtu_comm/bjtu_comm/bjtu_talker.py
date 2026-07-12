import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from bjtu_comm.messages import format_chatter_message


class BjtuTalker(Node):
    def __init__(self):
        super().__init__('bjtu_talker')
        self.publisher_ = self.create_publisher(String, 'bjtu_chatter', 10)
        self.timer = self.create_timer(1.0, self.timer_callback)
        self._counter = 0

    def timer_callback(self):
        msg = String()
        msg.data = format_chatter_message(self._counter)
        self.publisher_.publish(msg)
        self.get_logger().info(f'Published: "{msg.data}"')
        self._counter += 1


def main(args=None):
    rclpy.init(args=args)
    node = BjtuTalker()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
