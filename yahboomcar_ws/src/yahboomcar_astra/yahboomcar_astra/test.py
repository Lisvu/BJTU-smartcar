    
import cv2 as cv
import numpy as np


def red_obj_follow(rgb_img):
        image = rgb_img
        hsv_image = cv.cvtColor(image, cv.COLOR_BGR2HSV)
        # 定义红色在HSV空间中的范围
        # 红色的HSV范围分为两部分，因为它跨越了HSV圆环的0度
        lower_red1 = np.array([0, 100, 60])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([156, 100, 60])
        upper_red2 = np.array([180, 255, 255])


        # 根据阈值获取红色区域的掩膜
        mask1 = cv.inRange(hsv_image, lower_red1, upper_red1)
        mask2 = cv.inRange(hsv_image, lower_red2, upper_red2)
        mask = cv.bitwise_or(mask1, mask2)

        # 将掩膜应用到原图像上，得到红色区域
        red_region = cv.bitwise_and(image, image, mask=mask)

        kernel = np.ones((5, 5), np.uint8)
        mask = cv.morphologyEx(mask, cv.MORPH_OPEN, kernel)
        contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        centerx, centery, r = 0,0,0
        for contour in contours:
            x, y, w, h = cv.boundingRect(contour)
            area1 = w * h
            rotate_rect = cv.minAreaRect(contour)
            box = cv.boxPoints(rotate_rect)
            box = np.int0(box)
            area2 = cv.contourArea(box)
            if (area1 > 1000):
                cv.rectangle(image, (x, y), (x + w, y + h), (0, 255, 0), 2)
            if (area1 > 1000 and area2/area1 > 0.75):
                centerx = x
                centery = y
                r = (w+h)/4.
                cv.drawContours(image, [box], 0, (0, 0, 255), 2)
                break
        gray = cv.cvtColor(image, cv.COLOR_RGB2GRAY)
        return image, gray,(centerx, centery, r)


cap = cv.VideoCapture(0)

while True:
     ret, frame = cap.read()
     image, gray, (centerx, centery, r) = red_obj_follow(frame)
     cv.imshow('frame', image)
     cv.imshow('gray', gray)
     key = cv.waitKey(1)
     if key == ord('q'):
         break
cap.release()
cv.destroyAllWindows()
     