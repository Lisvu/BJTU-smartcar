#! /bin/bash

###############################################################################
# 1.add Additional startup programs
# start_rosmaster_app
# bash /home/jetson/Rosmaster/rosmaster/start_app.sh
# start app program
# 
# 2.add Additional startup programs
# Resolution
# xrandr --fb 1024x600
# set Resolution when no screen
###############################################################################


sleep 8
cd ~/Rosmaster-App/rosmaster/
gnome-terminal -- bash -c "python3 ~/Rosmaster-App/rosmaster/app.py;exec bash"

wait
exit 0
