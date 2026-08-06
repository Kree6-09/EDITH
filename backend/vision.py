"""Face detection and recognition engine for G.R.A.C.E.

Uses OpenCV's Haar cascade for detection and LBPH for recognition. Both are
lightweight (no dlib/face_recognition dependency) so the whole system runs
without GPU or heavyweight ML installs.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import cv2
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
KNOWN_FACES_DIR = os.path.join(DATA_DIR, "known_faces")
MODEL_PATH = os.path.join(DATA_DIR, "model.yml")
LABELS_PATH = os.path.join(DATA_DIR, "labels.json")

FACE_SIZE = (200, 200)
# LBPH confidence is a distance: lower is a better match.
MATCH_THRESHOLD = 75.0

os.makedirs(KNOWN_FACES_DIR, exist_ok=True)


def _write_image(path: str, img: np.ndarray) -> bool:
    """cv2.imwrite silently fails on Windows for non-ASCII paths (accents, ñ,
    etc.) since it uses the local codepage internally rather than Unicode.
    Encode in-memory and write via Python's own (Unicode-safe) file I/O."""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return False
    with open(path, "wb") as fh:
        fh.write(buf.tobytes())
    return True


def _read_gray_image(path: str) -> np.ndarray | None:
    """Unicode-safe counterpart to cv2.imread(path, IMREAD_GRAYSCALE)."""
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


@dataclass
class Sighting:
    box: tuple[int, int, int, int]
    name: str
    confidence: float
    known: bool


@dataclass
class FaceEngine:
    detector: cv2.CascadeClassifier = field(init=False)
    recognizer: object = field(init=False)
    labels: dict[int, str] = field(init=False, default_factory=dict)
    trained: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.detector = cv2.CascadeClassifier(cascade_path)
        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.labels = {}
        self.trained = False
        self._load()

    def _load(self) -> None:
        if os.path.exists(MODEL_PATH) and os.path.exists(LABELS_PATH):
            self.recognizer.read(MODEL_PATH)
            with open(LABELS_PATH, "r", encoding="utf-8") as fh:
                self.labels = {int(k): v for k, v in json.load(fh).items()}
            self.trained = True

    def known_names(self) -> list[str]:
        return sorted(self.labels.values())

    def detect_faces(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = self.detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        return [tuple(int(v) for v in f) for f in faces]

    def recognize(self, frame_bgr: np.ndarray) -> list[Sighting]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        boxes = self.detect_faces(frame_bgr)
        results = []
        for (x, y, w, h) in boxes:
            crop = cv2.resize(gray[y:y + h, x:x + w], FACE_SIZE)
            if self.trained:
                label_id, confidence = self.recognizer.predict(crop)
                if confidence <= MATCH_THRESHOLD and label_id in self.labels:
                    results.append(Sighting((x, y, w, h), self.labels[label_id], float(confidence), True))
                    continue
            results.append(Sighting((x, y, w, h), "Unknown", 999.0, False))
        return results

    def enroll(self, name: str, frame_bgr: np.ndarray) -> tuple[bool, str]:
        boxes = self.detect_faces(frame_bgr)
        if not boxes:
            return False, "No face detected in frame."
        x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        crop = cv2.resize(gray[y:y + h, x:x + w], FACE_SIZE)

        person_dir = os.path.join(KNOWN_FACES_DIR, name)
        os.makedirs(person_dir, exist_ok=True)
        existing = len(os.listdir(person_dir))
        if not _write_image(os.path.join(person_dir, f"{existing:04d}.png"), crop):
            return False, "Failed to save the face image."

        self.train()
        return True, f"Enrolled sample #{existing + 1} for {name}."

    def train(self) -> None:
        samples: list[np.ndarray] = []
        label_ids: list[int] = []
        labels: dict[int, str] = {}

        if not os.path.isdir(KNOWN_FACES_DIR):
            return

        for name in sorted(os.listdir(KNOWN_FACES_DIR)):
            person_dir = os.path.join(KNOWN_FACES_DIR, name)
            if not os.path.isdir(person_dir):
                continue
            person_samples = []
            for fname in os.listdir(person_dir):
                img = _read_gray_image(os.path.join(person_dir, fname))
                if img is None:
                    continue
                person_samples.append(cv2.resize(img, FACE_SIZE))
            if not person_samples:
                # Directory exists but every image failed to decode — don't
                # list this person as "known" with zero actual training data.
                continue
            idx = len(labels)
            labels[idx] = name
            samples.extend(person_samples)
            label_ids.extend([idx] * len(person_samples))

        if not samples:
            self.trained = False
            self.labels = {}
            if os.path.exists(MODEL_PATH):
                os.remove(MODEL_PATH)
            if os.path.exists(LABELS_PATH):
                os.remove(LABELS_PATH)
            return

        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.recognizer.train(samples, np.array(label_ids))
        self.recognizer.write(MODEL_PATH)
        with open(LABELS_PATH, "w", encoding="utf-8") as fh:
            json.dump(labels, fh)
        self.labels = labels
        self.trained = True

    def remove_person(self, name: str) -> bool:
        person_dir = os.path.join(KNOWN_FACES_DIR, name)
        if not os.path.isdir(person_dir):
            return False
        for fname in os.listdir(person_dir):
            os.remove(os.path.join(person_dir, fname))
        os.rmdir(person_dir)
        self.train()
        return True
