import tkinter as tk 
from tkinter import ttk 
import subprocess 
import threading 
import time 
import cv2 
import os
import license
from PIL import Image, ImageTk 
from Rosmaster_Lib import Rosmaster  # 确保导入 Rosmaster 类

class RobotControlApp: 
    def __init__(self, root): 
        if (license.checkLicense("icar")):
            print("please contact with mail(7644070@qq.com) or tel(18351950186) to get valid license file.")
            os._exit(-1)
        self.root = root 
        self.root.title("机器人控制系统") 
        self.root.geometry("920x320")   # 适当增大窗口宽度以容纳摄像头画面 

        # 设置默认的移动速度 
        self.speed = 50 

        # 用于存储单选按钮的值 
        self.speed_var = tk.IntVar() 
        self.speed_var.set(self.speed)  

        # 设置默认持续时间 
        self.last_time = "持续" 
        # 用于存储持续时间单选按钮的值 
        self.last_time_var = tk.StringVar() 
        self.last_time_var.set(self.last_time)  

        # 进程控制相关变量 
        self.current_process = None 
        self.process_lock = threading.Lock() 

        # 设置默认的寻迹状态
        self.follow_line = tk.IntVar(value=0)  # 默认不勾选

        # 初始化 g_bot 对象
        self.g_bot = Rosmaster(debug=True)
        self.g_bot.create_receive_threading()

        # 创建界面 
        self.create_widgets()  

        # 初始化摄像头 
        self.cap = cv2.VideoCapture(0) 
        if not self.cap.isOpened():  
            print("无法打开摄像头") 
        else: 
            # 创建显示摄像头画面的 Label 
            self.image_label = ttk.Label(self.root)  
            self.image_label.place(x=480, y=10, width=400, height=300) 
            # 启动摄像头线程 
            threading.Thread(target=self.update_camera_frame, daemon=True).start() 

    def create_widgets(self): 
        # 主按钮框架 
        button_frame = ttk.Frame(self.root)  
        button_frame.place(x=10, y=90, width=480, height=200) 

        # 速度单选按钮框架 
        speed_radio_frame = ttk.Frame(self.root)  
        speed_radio_frame.place(x=10, y=10, width=450, height=30) 

        # 添加速度标签 
        speed_label = ttk.Label(speed_radio_frame, text="速度：") 
        speed_label.pack(side=tk.LEFT, padx=5) 

        # 速度单选按钮选项 
        speeds = [10, 20, 30, 50] 
        for speed in speeds: 
            radio_button = ttk.Radiobutton( 
                speed_radio_frame, 
                text=str(speed), 
                variable=self.speed_var,  
                value=speed, 
                command=self.update_speed  
            ) 
            radio_button.pack(side=tk.LEFT, padx=5) 

        # 持续时间单选按钮框架 
        duration_radio_frame = ttk.Frame(self.root)  
        duration_radio_frame.place(x=10, y=50, width=450, height=30) 

        # 添加持续时间标签 
        last_time_label = ttk.Label(duration_radio_frame, text="持续时间：") 
        last_time_label.pack(side=tk.LEFT, padx=10) 

        # 持续时间单选按钮选项 
        durations = ["持续", "0.5s", "1s"] 
        for duration in durations: 
            radio_button = ttk.Radiobutton( 
                duration_radio_frame, 
                text=duration, 
                variable=self.last_time_var,  
                value=duration, 
                command=self.update_last_time  
            ) 
            radio_button.pack(side=tk.LEFT, padx=5) 

        # 寻迹指令 Checkbutton
        follow_line_checkbutton = ttk.Checkbutton(
            self.root,
            text="寻迹指令",
            variable=self.follow_line,
            command=self.toggle_follow_line
        )
        follow_line_checkbutton.place(x=10, y=295)  # 根据布局调整位置

        # 按钮位置及命令设置 
        buttons = [ 
            ("前进", ["python3", "rosmaster_test.py", "1"], 150, 10), 
            ("后退", ["python3", "rosmaster_test.py", "2"], 150, 90), 
            ("右平移", ["python3", "rosmaster_test.py", "3"], 290, 50), 
            ("左平移", ["python3", "rosmaster_test.py", "4"], 10, 50), 
            ("右转", ["python3", "rosmaster_test.py", "5"], 290, 10), 
            ("左转", ["python3", "rosmaster_test.py", "6"], 10, 10), 
            ("停止", ["python3", "rosmaster_test.py", "7"], 150, 50) 
        ] 

        for text, base_command, x, y in buttons: 
            command = base_command + [str(self.speed)]  
            if text != "停止": 
                button = ttk.Button( 
                    button_frame, 
                    text=text, 
                    command=lambda cmd=command: self.execute_command_with_duration(cmd)  
                ) 
            else: 
                button = ttk.Button( 
                    button_frame, 
                    text=text, 
                    command=lambda cmd=command: self.execute_command(cmd)  
                ) 
            button.place(x=x, y=y, width=120, height=30) 

    def update_speed(self): 
        """更新速度值""" 
        self.speed = self.speed_var.get()  

    def update_last_time(self): 
        """更新持续时间值""" 
        duration = self.last_time_var.get()  
        if duration == "持续": 
            self.last_time = "持续" 
        elif duration == "0.5s": 
            self.last_time = 0.5 
        elif duration == "1s": 
            self.last_time = 1 

    def execute_command_with_duration(self, command): 
        """执行带有持续时间的命令""" 
        def command_worker(): 
            with self.process_lock:  
                if self.current_process and self.current_process.poll() is None: 
                    self.current_process.terminate()  
                    self.current_process.wait()  

                command[-1] = str(self.speed)  

                if self.last_time == "持续": 
                    try: 
                        self.current_process = subprocess.Popen( 
                            command, 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.STDOUT 
                        ) 
                    except Exception as e: 
                        print(f"命令执行失败: {str(e)}") 
                else: 
                    try: 
                        self.current_process = subprocess.Popen( 
                            command, 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.STDOUT 
                        ) 
                        time.sleep(self.last_time)  
                        if self.current_process and self.current_process.poll() is None: 
                            self.current_process.terminate()  
                            self.current_process.wait()  
                        stop_command = ["python3", "rosmaster_test.py", "7", str(self.speed)]  
                        subprocess.Popen( 
                            stop_command, 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.STDOUT 
                        ) 
                    except Exception as e: 
                        print(f"命令执行失败: {str(e)}") 
        threading.Thread(target=command_worker, daemon=True).start() 

    def toggle_follow_line(self):
        """根据Checkbutton的状态执行相应的寻迹命令"""
        if self.follow_line.get() == 1:
            self.g_bot.set_follow_line(1)  # 使用类内部的 g_bot 对象
        else:
            self.g_bot.set_follow_line(0)

    def execute_command(self, command): 
        """执行命令的核心方法""" 
        def command_worker(): 
            with self.process_lock:  
                if self.current_process and self.current_process.poll() is None: 
                    self.current_process.terminate()  
                    self.current_process.wait()  
                command[-1] = str(self.speed)  
                try: 
                    self.current_process = subprocess.Popen( 
                        command, 
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.STDOUT 
                    ) 
                except Exception as e: 
                    print(f"命令执行失败: {str(e)}") 
        threading.Thread(target=command_worker, daemon=True).start() 

    def update_camera_frame(self): 
        while True: 
            ret, frame = self.cap.read()  
            if ret: 
                # 调整图像大小以适应 Label 
                frame = cv2.resize(frame, (400, 300)) 
                # 将 BGR 格式转换为 RGB 格式 
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) 
                # 创建 Image 对象 
                image = Image.fromarray(frame)  
                # 创建 PhotoImage 对象 
                photo = ImageTk.PhotoImage(image=image) 
                # 更新 Label 的图像 
                self.image_label.config(image=photo)  
                self.image_label.image = photo 
            time.sleep(0.03)   # 控制帧率 

if __name__ == "__main__": 
    root = tk.Tk() 
    app = RobotControlApp(root) 
    root.mainloop()  
    # 当窗口关闭时释放摄像头
    if app.cap.isOpened():  
        app.cap.release()   # 释放摄像头资源 
        cv2.destroyAllWindows()   # 关闭所有 OpenCV 窗口
