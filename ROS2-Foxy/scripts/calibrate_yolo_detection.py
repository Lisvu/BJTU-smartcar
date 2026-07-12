#!/usr/bin/env python3
"""Interactively measure traffic-sign YOLO detection versus range and bearing."""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from detect_headless import YOLO_ROOT, detect_frame, open_camera
from self_detect import YoloDetecter


DEFAULT_WEIGHTS = YOLO_ROOT / "traffic_sign_yolov5s.pt"
DEFAULT_DATA = YOLO_ROOT / "traffic_signs.yaml"


@dataclass
class Station:
    distance_m: float
    bearing_deg: float
    end_time: float
    rows: list[dict] = field(default_factory=list)


class CalibrationRunner:
    def __init__(self, args, output):
        self.args = args
        self.output = output
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.station: Station | None = None
        self.latest_text = "warming up"
        self.error: Exception | None = None

    def run(self):
        cap = None
        try:
            detector = YoloDetecter(
                weights=self.args.weights,
                data=self.args.data,
                imgsz=(self.args.imgsz, self.args.imgsz),
                conf_thres=self.args.conf_thres,
                iou_thres=self.args.iou_thres,
                device=self.args.device,
                classes=None,
            )
            names = detector.names if isinstance(detector.names, dict) else dict(enumerate(detector.names))
            stop_ids = [int(index) for index, name in names.items() if str(name).lower() == self.args.target_class.lower()]
            if not stop_ids:
                raise RuntimeError(f"class {self.args.target_class!r} not found; model names={names}")
            detector.classes = stop_ids
            cap = open_camera(self.args.source, self.args.width, self.args.height)
            for _ in range(self.args.warmup_frames):
                cap.read()
            last_status = 0.0
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f"camera read failed from {self.args.source}")
                detections = [d for d in detect_frame(detector, frame)
                              if str(d["name"]).lower() == self.args.target_class.lower()]
                best = max(detections, key=lambda d: float(d["confidence"]), default=None)
                now = time.time()
                if best:
                    cx, cy = best["center"]
                    bw, bh = best["box"]
                    pixel_bearing = (float(cx) - frame.shape[1] / 2.0) / frame.shape[1] * self.args.hfov_deg
                    status = (f"STOP conf={float(best['confidence']):.3f} cx={cx} "
                              f"pixel_bearing={pixel_bearing:+.1f}deg box={bw}x{bh}")
                else:
                    cx = cy = bw = bh = None
                    pixel_bearing = None
                    status = "STOP not detected"
                with self.lock:
                    self.latest_text = status
                    station = self.station
                    if station is not None and now <= station.end_time:
                        row = {
                            "t": now,
                            "distance_m": station.distance_m,
                            "bearing_deg": station.bearing_deg,
                            "visible": 1,
                            "detected": int(best is not None),
                            "conf": float(best["confidence"]) if best else 0.0,
                            "pixel_bearing_deg": pixel_bearing,
                            "cx": int(cx) if cx is not None else None,
                            "cy": int(cy) if cy is not None else None,
                            "bw": int(bw) if bw is not None else None,
                            "bh": int(bh) if bh is not None else None,
                            "image_w": int(frame.shape[1]),
                            "image_h": int(frame.shape[0]),
                        }
                        self.output.write(json.dumps(row, separators=(",", ":")) + "\n")
                        self.output.flush()
                        station.rows.append(row)
                self.ready_event.set()
                if now - last_status >= 0.5:
                    print(f"\rLIVE {status:<80}", end="", flush=True)
                    last_status = now
                if self.args.preview:
                    color = (0, 220, 0) if best else (0, 0, 220)
                    if best:
                        x1, y1, x2, y2 = best["xyxy"]
                        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, status, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
                    cv2.imshow("BJTU YOLO calibration", frame)
                    if cv2.waitKey(1) & 0xFF == 27:
                        self.stop_event.set()
        except Exception as exc:
            self.error = exc
            self.stop_event.set()
        finally:
            if cap is not None:
                cap.release()
            if self.args.preview:
                cv2.destroyAllWindows()


def summarize(station: Station, hfov_deg: float):
    rows = station.rows
    detected = [row for row in rows if row["detected"]]
    rate = len(detected) / len(rows) if rows else 0.0
    mean = lambda key: float(np.mean([row[key] for row in detected])) if detected else float("nan")
    mean_cx = mean("cx")
    image_w = detected[0]["image_w"] if detected else 0
    inferred = (mean_cx - image_w / 2.0) / image_w * hfov_deg if detected else float("nan")
    result = {"distance_m": station.distance_m, "bearing_deg": station.bearing_deg,
              "frames": len(rows), "detected_frames": len(detected), "rate": rate,
              "mean_conf": mean("conf"), "mean_cx": mean_cx,
              "mean_pixel_bearing_deg": inferred, "mean_bw": mean("bw"), "mean_bh": mean("bh")}
    print("\nSTATION " + " ".join(f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value}"
                                   for key, value in result.items()))
    return result


def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--source", default="/dev/video0")
    p.add_argument("--output", default="/home/jetson/bjtu_ai/yolo_calib.jsonl")
    p.add_argument("--target-class", default="stop")
    p.add_argument("--duration", type=float, default=6.0)
    p.add_argument("--hfov-deg", type=float, default=60.0)
    p.add_argument("--conf-thres", type=float, default=0.5)
    p.add_argument("--iou-thres", type=float, default=0.45)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--device", default="")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--warmup-frames", type=int, default=8)
    p.add_argument("--preview", action="store_true", help="Show an OpenCV window on Jetson DISPLAY.")
    p.add_argument("--overwrite", action="store_true")
    return p


def main():
    args = parser().parse_args()
    for path in (args.weights, args.data):
        if not Path(path).is_file():
            raise SystemExit(f"required file missing: {path}")
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if args.overwrite else "a"
    summaries = []
    with output_path.open(mode) as output:
        runner = CalibrationRunner(args, output)
        thread = threading.Thread(target=runner.run, daemon=True)
        thread.start()
        print("Loading model and opening camera; measurement prompt will appear after the first inference...")
        deadline = time.time() + 120.0
        while not runner.ready_event.wait(0.2):
            if runner.error:
                runner.stop_event.set()
                thread.join(timeout=2)
                raise SystemExit(f"startup failed: {runner.error}")
            if time.time() >= deadline:
                runner.stop_event.set()
                raise SystemExit("startup timed out before the first camera inference")
        print("Ready. Enter: distance_m [bearing_deg]. Enter done to finish.")
        while not runner.stop_event.is_set():
            try:
                command = input("\nmeasurement> ").strip()
            except (EOFError, KeyboardInterrupt):
                command = "done"
            if command.lower() in {"done", "quit", "q"}:
                break
            fields = command.split()
            try:
                distance = float(fields[0])
                bearing = float(fields[1]) if len(fields) > 1 else 0.0
                if distance <= 0 or len(fields) > 2:
                    raise ValueError
            except (ValueError, IndexError):
                print("Use: distance_m [bearing_deg], for example: 1.5 -15. Enter done to finish.")
                continue
            station = Station(distance, bearing, time.time() + args.duration)
            with runner.lock:
                runner.station = station
            print(f"Recording {args.duration:.1f}s at distance={distance:.2f}m bearing={bearing:+.1f}deg...")
            while time.time() < station.end_time and not runner.stop_event.is_set():
                time.sleep(0.1)
            with runner.lock:
                runner.station = None
            summaries.append(summarize(station, args.hfov_deg))
        runner.stop_event.set()
        thread.join(timeout=5)
        if runner.error:
            raise SystemExit(f"calibration stopped: {runner.error}")
    print(f"\nSaved per-frame samples to {output_path}")
    print("distance(m) bearing(deg) frames detect_rate mean_conf")
    for row in summaries:
        print(f"{row['distance_m']:10.2f} {row['bearing_deg']:12.1f} {row['frames']:6d} "
              f"{row['rate']:11.1%} {row['mean_conf']:9.3f}")


if __name__ == "__main__":
    main()
