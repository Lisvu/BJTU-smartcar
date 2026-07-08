import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from geometry_msgs.msg import Twist
from sensor_msgs.msg import CompressedImage, LaserScan, Image
from cv_bridge import CvBridge
import os
import threading
import math
import time
import cv2
import numpy as np  

class VisionTrack(Node):
    def __init__(self, name):
        super().__init__(name)
        self.sub_depth = self.create_subscription(Image,"/camera/depth/image_raw", self.depth_img_Callback, 1)
        self.capture = cv2.VideoCapture(0)
        self.capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc('M', 'J', 'P', 'G'))
        self.timer = self.create_timer(0.01, self.on_timer)
        self.bridge = CvBridge()
        self.encoding = ['16UC1', '32FC1']
        self.Center_x = 0
        self.Center_y = 0
        self.Center_r = 0
        self.dist = 0

    def on_timer(self):
        ret, frame = self.capture.read()
        action = cv2.waitKey(10) & 0xFF
        start = time.time()
        frame , binary = self.process(frame, action)
        end = time.time()
        fps = 1/(end - start)
        text = "FPS : " + str(int(fps))
        cv2.putText(frame, text, (30, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 200, 200), 1)

        cv2.imshow('frame', frame)
        if action == ord('q') or action == 113:
            self.capture.release()
            cv2.destroyAllWindows()
    def red_obj_follow(self, rgb_img):
        image = rgb_img
        hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        # 定义红色在HSV空间中的范围
        # 红色的HSV范围分为两部分，因为它跨越了HSV圆环的0度
        lower_red1 = np.array([0, 100, 60])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([156, 100, 60])
        upper_red2 = np.array([180, 255, 255])


        # 根据阈值获取红色区域的掩膜
        mask1 = cv2.inRange(hsv_image, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv_image, lower_red2, upper_red2)
        mask = cv2.bitwise_or(mask1, mask2)

        # 将掩膜应用到原图像上，得到红色区域
        red_region = cv2.bitwise_and(image, image, mask=mask)

        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        centerx, centery, r = 0,0,0
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area1 = w * h
            rotate_rect = cv2.minAreaRect(contour)
            box = cv2.boxPoints(rotate_rect)
            box = np.int0(box)
            area2 = cv2.contourArea(box)
            if (area1 > 1000):
                cv2.rectangle(image, (x, y), (x + w, y + h), (0, 255, 0), 2)
            if (area1 > 1000 and area2/area1 > 0.75):
                centerx = x
                centery = y
                r = (w+h)/4.
                cv2.drawContours(image, [box], 0, (0, 0, 255), 2)
                break
        self.Center_x = centerx
        self.Center_y = centery
        self.Center_r = r
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        return image, gray,(centerx, centery, r)

    def process(self, rgb_img, action):
        rgb_img = cv2.resize(rgb_img, (640, 480))
        if self.dist > 0:
            cv2.putText(rgb_img, "dist:{}".format(int(self.dist)), (60, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 200, 200), 1)
        rgb_img, binary, self.circle = self.red_obj_follow(rgb_img)
        return rgb_img, binary

    def depth_img_Callback(self, msg):
        if not isinstance(msg, Image): 
            return
        depthFrame = self.bridge.imgmsg_to_cv2(msg, desired_encoding=self.encoding[1])
        self.action = cv2.waitKey(1)
        if self.Center_r > 5:
            distance = [0, 0, 0, 0, 0]
            if 0 < int(self.Center_y - 3) and int(self.Center_y + 3) < 480 and 0 < int(
                self.Center_x - 3) and int(self.Center_x + 3) < 640:
                # print("depthFrame: ", len(depthFrame), len(depthFrame[0]))
                distance[0] = depthFrame[int(self.Center_y - 3)][int(self.Center_x - 3)]
                distance[1] = depthFrame[int(self.Center_y + 3)][int(self.Center_x - 3)]
                distance[2] = depthFrame[int(self.Center_y - 3)][int(self.Center_x + 3)]
                distance[3] = depthFrame[int(self.Center_y + 3)][int(self.Center_x + 3)]
                distance[4] = depthFrame[int(self.Center_y)][int(self.Center_x)]
                distance_ = 1000.0
                num_depth_points = 5
                for i in range(5):
                    if 40 < distance[i] < 80000: distance_ += distance[i]
                    else: num_depth_points -= 1
                if num_depth_points == 0: 
                    distance_ = 0.0
                else: 
                    distance_ /= num_depth_points
                self.dist = distance_
                print("_x: {}, _y: {}, _r: {}, distance_: {}".format(self.Center_x, self.Center_y, self.Center_r, int(distance_)))
        else:
            self.dist = 0.
            print("Center_r == 0, don't try to get depth info.")

def main():
    rclpy.init()
    visionTrack = VisionTrack("vision_track")
    print("start it")
    rclpy.spin(visionTrack)
