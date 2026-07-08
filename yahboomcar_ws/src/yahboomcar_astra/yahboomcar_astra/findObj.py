#ros lib
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from geometry_msgs.msg import Twist
from sensor_msgs.msg import CompressedImage, LaserScan, Image
from icar_msgs.msg import Position
#common lib
import os
import threading
import math
import time
import numpy as np
import socket
from icar_astra.astra_common import *
from icar_msgs.msg import Position
print("import finish")
cv_edition = cv.__version__
print("cv_edition: ",cv_edition)
class ObjIdentify(Node):
    def __init__(self,name):
        super().__init__(name)
        self.server_ip = "192.168.8.101"
        self.server_port = 12345
        self.server_addr = (self.server_ip, self.server_port)
        self.conn_status = False
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        #create a publisher
        self.pub_position = self.create_publisher(Position,"/Current_point", 10)
        self.pub_cmdVel = self.create_publisher(Twist, '/cmd_vel', 10)
        self.windows_name = 'frame'
        self.Track_state = 'identify'
        self.capture = cv.VideoCapture(0)
        if cv_edition[0]=='3': self.capture.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*'XVID'))
        else: self.capture.set(cv.CAP_PROP_FOURCC, cv.VideoWriter.fourcc('M', 'J', 'P', 'G'))
        self.timer = self.create_timer(0.04, self.on_timer)
    
    def on_timer(self):
        ret, frame = self.capture.read()
        start = time.time()
        frame =self.process(frame)
        end = time.time()
        fps = 1 / (end - start)
        text = "FPS : " + str(int(fps))
        cv.putText(frame, text, (30, 30), cv.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 1)       
        cv.imshow('frame', frame)
        cv.waitKey(1)

    def process(self, rgb_img):
        if self.conn_status == False:
            try:
                self.socket.connect(self.server_addr)
                self.conn_status = True
            except socket.timeout:
                print("Connect to server failed!")
                self.conn_status = False
            except ConnectionRefusedError:
                print("Connection refused!")
                self.conn_status = False
            except Exception as e:
                print("Socket connection error:", e)
                self.conn_status = False
        try:
            rgb_img = cv.resize(rgb_img, (640, 480))
            _, encoded_img = cv.imencode('.jpg', rgb_img, [int(cv.IMWRITE_JPEG_QUALITY), 90])
            data = np.array(encoded_img)
            data = data.tobytes()
            self.socket.sendall(f"{len(data):<{16}}".encode())
            self.socket.sendall(data)
            reply = self.socket.recv(128)
            self.result = reply.decode('utf-8').split(',')
        except Exception as e:
            print("Socket error:", e)
            self.result = []
            self.socket.close()
            self.conn_status = False

        if (len(self.result) > 0):
            

            print("Result:", self.result)
            self.centerx = int(float(self.result[0]))
            self.centery = int(float(self.result[1]))
            self.label = self.result[2]
            cv.putText(rgb_img, self.label, (self.centerx+30,self.centery), cv.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
            cv.circle(rgb_img, (self.centerx,self.centery), 5, (0,0,255), -1)
            direction = 0
            if self.label == "turnleft":
                direction = 0
            elif self.label == "turnright":
                direction = 1
            elif self.label == "turnaround":
                direction = 2

            threading.Thread(target=self.execute, args=(self.centerx, self.centery, direction)).start()
         
        return rgb_img
    def execute(self, x, y, z):
        position = Position()
        position.anglex = x * 1.0
        position.angley = y * 1.0
        position.distance = z * 1.0
        self.pub_position.publish(position)   

def main():
    rclpy.init()
    obj_identify = ObjIdentify("ObjIdentify")
    print("start it")
    rclpy.spin(obj_identify)
