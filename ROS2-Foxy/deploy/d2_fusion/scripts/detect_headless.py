#!/usr/bin/env python3
"""Headless YOLOv5 camera detector for the BJTU car.

This script runs on the Jetson host. It intentionally avoids ROS2, GUI display,
and any motor-control topics. It imports the vendor YOLO helper but keeps all
project-specific behavior here.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

YOLO_ROOT = Path("/home/jetson/yolov5-7.0")
if str(YOLO_ROOT) not in sys.path:
    sys.path.insert(0, str(YOLO_ROOT))

from self_detect import YoloDetecter  # noqa: E402
from utils.augmentations import letterbox  # noqa: E402
from utils.general import non_max_suppression, scale_boxes  # noqa: E402

IMAGE_SUFFIXES = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def parse_classes(value: str | None) -> list[int] | None:
    if value is None or value.strip().lower() in {"", "all", "none"}:
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def source_value(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def open_camera(source: str, width: int | None, height: int | None) -> cv2.VideoCapture:
    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    cap = cv2.VideoCapture(source_value(source), backend)
    if width:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open camera source {source!r}")
    return cap


def is_image_source(source: str) -> bool:
    path = Path(source)
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


class DetectionSocketServer:
    def __init__(self, endpoint: str | None) -> None:
        self.endpoint = endpoint
        self.sock: socket.socket | None = None
        self.clients: list[socket.socket] = []

    def __enter__(self) -> "DetectionSocketServer":
        if not self.endpoint:
            return self
        host, port_text = self.endpoint.rsplit(":", 1)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, int(port_text)))
        self.sock.listen()
        self.sock.setblocking(False)
        print(f"serve listening {host}:{port_text}", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for client in self.clients:
            client.close()
        self.clients.clear()
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def accept_pending(self) -> None:
        if self.sock is None:
            return
        while True:
            try:
                client, addr = self.sock.accept()
            except BlockingIOError:
                return
            client.setblocking(False)
            self.clients.append(client)
            print(f"serve client connected {addr[0]}:{addr[1]}", flush=True)

    def send(self, payload: dict[str, object]) -> None:
        if self.sock is None:
            return
        self.accept_pending()
        if not self.clients:
            return
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        live_clients: list[socket.socket] = []
        for client in self.clients:
            try:
                client.sendall(line)
                live_clients.append(client)
            except OSError:
                client.close()
        self.clients = live_clients


def detect_frame(detector: YoloDetecter, frame: np.ndarray) -> list[dict[str, object]]:
    image = letterbox(frame, detector.imgsz, stride=detector.model.stride, auto=detector.model.pt)[0]
    image = image.transpose((2, 0, 1))[::-1]
    image = np.ascontiguousarray(image)

    tensor = torch.from_numpy(image).to(detector.device)
    tensor = tensor.half() if detector.model.fp16 else tensor.float()
    tensor /= 255.0
    if len(tensor.shape) == 3:
        tensor = tensor[None]

    with torch.no_grad():
        pred = detector.model(tensor)
        pred = non_max_suppression(
            pred,
            detector.conf_thres,
            detector.iou_thres,
            detector.classes,
            detector.agnostic_nms,
            max_det=detector.max_det,
        )

    detections: list[dict[str, object]] = []
    for det in pred:
        if not len(det):
            continue
        det[:, :4] = scale_boxes(tensor.shape[2:], det[:, :4], frame.shape).round()
        for *xyxy, conf, cls in det:
            class_id = int(cls.item())
            x1, y1, x2, y2 = [int(v.item()) for v in xyxy]
            width = max(0, x2 - x1)
            height = max(0, y2 - y1)
            center_x = x1 + width // 2
            center_y = y1 + height // 2
            name = detector.names[class_id] if isinstance(detector.names, list) else detector.names[class_id]
            detections.append(
                {
                    "name": name,
                    "class_id": class_id,
                    "confidence": float(conf.item()),
                    "center": (center_x, center_y),
                    "box": (width, height),
                    "xyxy": (x1, y1, x2, y2),
                }
            )
    return detections


def detection_payload(frame: np.ndarray, detections: list[dict[str, object]]) -> dict[str, object]:
    return {
        "t": time.time(),
        "w": int(frame.shape[1]),
        "h": int(frame.shape[0]),
        "dets": [
            {
                "cls": str(item["name"]),
                "conf": float(item["confidence"]),
                "cx": int(item["center"][0]),
                "cy": int(item["center"][1]),
                "bw": int(item["box"][0]),
                "bh": int(item["box"][1]),
            }
            for item in detections
        ],
    }


def print_detections(frame_no: int, fps: float, detections: list[dict[str, object]]) -> None:
    for item in detections:
        cx, cy = item["center"]
        bw, bh = item["box"]
        x1, y1, x2, y2 = item["xyxy"]
        print(
            f"frame={frame_no} fps={fps:.2f} class={item['name']} "
            f"conf={item['confidence']:.3f} pixel=({cx},{cy}) "
            f"box=({bw},{bh}) xyxy=({x1},{y1},{x2},{y2})",
            flush=True,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run headless YOLOv5 detection from a local camera.")
    parser.add_argument("--source", default="/dev/video0", help="Camera source, such as /dev/video0 or 0.")
    parser.add_argument("--weights", default=str(YOLO_ROOT / "yolov5s.pt"), help="YOLOv5 weights path.")
    parser.add_argument("--data", default=str(YOLO_ROOT / "data/coco128.yaml"), help="Dataset yaml with class names.")
    parser.add_argument("--classes", default="0", help="Comma-separated class ids, default 0=person. Use all for all classes.")
    parser.add_argument("--imgsz", type=int, default=640, help="Inference image size.")
    parser.add_argument("--conf-thres", type=float, default=0.25, help="Confidence threshold.")
    parser.add_argument("--iou-thres", type=float, default=0.45, help="NMS IoU threshold.")
    parser.add_argument("--device", default="", help="CUDA device string; empty lets YOLOv5 choose automatically.")
    parser.add_argument("--width", type=int, default=640, help="Requested camera width.")
    parser.add_argument("--height", type=int, default=480, help="Requested camera height.")
    parser.add_argument("--warmup-frames", type=int, default=5, help="Camera frames to drop before measuring.")
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after N frames; 0 means run until interrupted.")
    parser.add_argument("--print-empty-every", type=int, default=30, help="Print no_detection every N empty frames.")
    parser.add_argument("--serve", help="Listen on HOST:PORT and stream one JSON detection line per frame.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    classes = parse_classes(args.classes)
    detector = YoloDetecter(
        weights=args.weights,
        data=args.data,
        imgsz=(args.imgsz, args.imgsz),
        conf_thres=args.conf_thres,
        iou_thres=args.iou_thres,
        device=args.device,
        classes=classes,
    )

    with DetectionSocketServer(args.serve) as server:
        if is_image_source(args.source):
            frame = cv2.imread(args.source)
            if frame is None:
                raise RuntimeError(f"cannot read image source {args.source!r}")
            start = time.monotonic()
            detections = detect_frame(detector, frame)
            fps = 1.0 / max(time.monotonic() - start, 1e-6)
            print(
                f"started source={args.source} weights={args.weights} classes={args.classes} "
                f"cuda={torch.cuda.is_available()} device={detector.device}",
                flush=True,
            )
            if detections:
                print_detections(1, fps, detections)
            else:
                print(f"frame=1 fps={fps:.2f} no_detection", flush=True)
            server.send(detection_payload(frame, detections))
            return 0

        cap = open_camera(args.source, args.width, args.height)
        try:
            for _ in range(max(args.warmup_frames, 0)):
                cap.read()

            start = time.monotonic()
            processed = 0
            empty_streak = 0
            print(
                f"started source={args.source} weights={args.weights} classes={args.classes} "
                f"cuda={torch.cuda.is_available()} device={detector.device}",
                flush=True,
            )
            while args.max_frames <= 0 or processed < args.max_frames:
                ok, frame = cap.read()
                if not ok or frame is None:
                    print(f"frame_read_failed source={args.source}", flush=True)
                    return 2

                processed += 1
                detections = detect_frame(detector, frame)
                elapsed = max(time.monotonic() - start, 1e-6)
                fps = processed / elapsed
                if detections:
                    empty_streak = 0
                    print_detections(processed, fps, detections)
                else:
                    empty_streak += 1
                    if args.print_empty_every > 0 and empty_streak % args.print_empty_every == 0:
                        print(f"frame={processed} fps={fps:.2f} no_detection", flush=True)
                server.send(detection_payload(frame, detections))
        except KeyboardInterrupt:
            print("stopped_by_user", flush=True)
        finally:
            cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
