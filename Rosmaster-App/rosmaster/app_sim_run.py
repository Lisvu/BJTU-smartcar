from app_sim import RobotControlApp
import tkinter as tk
import cv2


root = tk.Tk()
app = RobotControlApp(root)
root.mainloop()
# 当窗口关闭时释放摄像头
if app.cap.isOpened():
    app.cap.release()   # 释放摄像头资源
    cv2.destroyAllWindows()   # 关闭所有 OpenCV 窗口

