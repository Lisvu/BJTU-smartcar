import os
import time
import sys

from Rosmaster_Lib import Rosmaster

g_cmd=1
g_speed=100

if len(sys.argv) > 2:
    g_cmd = int(sys.argv[1])
    g_speed = int(sys.argv[2])
else:
    print("usage:python3 drive.py 1/2/3/4/5/6/7 100/90/...")
    os._exit(-1)

print(f'cmd:{g_cmd}')
print(f'speed:{g_speed}')

g_bot = Rosmaster(debug=True)
g_bot.create_receive_threading()

if __name__ == '__main__':
    g_bot.set_car_run(g_cmd, g_speed, adjust=False)
    if (g_cmd == 7):
        print("stop running")
        os._exit(-1)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Existing...")
        g_bot.set_car_run(7,100,adjust=False)
