#!/usr/bin/env python3
from Speech_Lib import Speech
from Rosmaster_Lib import Rosmaster

_original_speech_read = Speech.speech_read

def safe_speech_read(self):
    try:
        return _original_speech_read(self)
    except Exception as exc:
        print(f"Ignored invalid speech data: {exc}", flush=True)
        return -1

Speech.speech_read = safe_speech_read

_original_get_version = Rosmaster.get_version

def safe_get_version(self):
    try:
        return float(_original_get_version(self))
    except Exception as exc:
        print(f"Ignored invalid version data: {exc}", flush=True)
        return 0.0

Rosmaster.get_version = safe_get_version

from yahboomcar_voice_ctrl.Voice_Ctrl_Mcnamu_driver_X3 import main

if __name__ == "__main__":
    main()
