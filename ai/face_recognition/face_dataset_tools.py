"""
face_dataset_tools.py - Utilities for listing and cleaning face datasets.

Usage:
  python ai/face_recognition/face_dataset_tools.py list
  python ai/face_recognition/face_dataset_tools.py stats
  python ai/face_recognition/face_dataset_tools.py clean --dry-run
  python ai/face_recognition/face_dataset_tools.py clean --apply --remove-occluded
"""

import os
import sys
import argparse
import cv2
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config

EYE_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml"
eye_cascade = cv2.CascadeClassifier(EYE_CASCADE_PATH)


def _iter_images(dataset_dir: str):
    for person_name in sorted(os.listdir(dataset_dir)):
        person_dir = os.path.join(dataset_dir, person_name)
        if not os.path.isdir(person_dir):
            continue
        images = [
            os.path.join(person_dir, f)
            for f in os.listdir(person_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        ]
        yield person_name, images


def _blur_score(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _brightness(gray: np.ndarray) -> float:
    return float(gray.mean())


def _eye_count(gray: np.ndarray) -> int:
    eyes = eye_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20))
    return len(eyes)


def list_dataset():
    dataset_dir = config.FACE_DATASET_DIR
    print("[Dataset] Listing...")
    for person_name, images in _iter_images(dataset_dir):
        print(f"- {person_name}: {len(images)} images")


def stats_dataset():
    dataset_dir = config.FACE_DATASET_DIR
    print("[Dataset] Stats...")
    for person_name, images in _iter_images(dataset_dir):
        blur_scores = []
        brightness_scores = []
        occluded = 0
        total = 0
        for path in images:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            total += 1
            blur_scores.append(_blur_score(img))
            brightness_scores.append(_brightness(img))
            if _eye_count(img) < 1:
                occluded += 1
        if total == 0:
            continue
        blur_avg = float(np.mean(blur_scores)) if blur_scores else 0.0
        bright_avg = float(np.mean(brightness_scores)) if brightness_scores else 0.0
        occ_pct = (occluded / total) * 100
        print(f"- {person_name}: total={total}, blur_avg={blur_avg:.1f}, bright_avg={bright_avg:.1f}, occluded~={occ_pct:.1f}%")


def clean_dataset(blur_threshold: float, bright_min: float, bright_max: float, remove_occluded: bool, apply: bool):
    dataset_dir = config.FACE_DATASET_DIR
    removed = 0
    scanned = 0

    print("[Dataset] Clean start...")
    for _, images in _iter_images(dataset_dir):
        for path in images:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            scanned += 1
            blur = _blur_score(img)
            bright = _brightness(img)
            eyes = _eye_count(img)

            bad_blur = blur < blur_threshold
            bad_bright = bright < bright_min or bright > bright_max
            bad_occ = remove_occluded and eyes < 1

            if bad_blur or bad_bright or bad_occ:
                if apply:
                    os.remove(path)
                    removed += 1
                else:
                    removed += 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[Dataset] {mode}: scanned={scanned}, to_remove={removed}")


def build_parser():
    parser = argparse.ArgumentParser(description="Face dataset utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List dataset counts")
    sub.add_parser("stats", help="Print dataset stats")

    clean = sub.add_parser("clean", help="Clean dataset by quality rules")
    clean.add_argument("--blur-threshold", type=float, default=config.FACE_BLUR_THRESHOLD)
    clean.add_argument("--bright-min", type=float, default=config.FACE_BRIGHTNESS_MIN)
    clean.add_argument("--bright-max", type=float, default=config.FACE_BRIGHTNESS_MAX)
    clean.add_argument("--remove-occluded", action="store_true")
    clean.add_argument("--apply", action="store_true")
    clean.add_argument("--dry-run", action="store_true")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "list":
        list_dataset()
        return

    if args.command == "stats":
        stats_dataset()
        return

    if args.command == "clean":
        apply = args.apply and not args.dry_run
        clean_dataset(
            blur_threshold=args.blur_threshold,
            bright_min=args.bright_min,
            bright_max=args.bright_max,
            remove_occluded=args.remove_occluded,
            apply=apply,
        )


if __name__ == "__main__":
    main()
