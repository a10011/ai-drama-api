"""
Face beautification post-processing using OpenCV.
Called after seedream generates the portrait.
"""

import cv2
import numpy as np
import os
import tempfile
import requests
from pathlib import Path


def beautify_portrait(image_path_or_url: str, strength: float = 1.0, output_path: str = None) -> str:
    """
    Apply beautification to a portrait. Effects:
    - Skin smoothing (bilateral filter)
    - Eye area brightening and dark circle reduction
    - Under-eye correction
    - Overall skin tone improvement

    Args:
        image_path_or_url: Local file path or URL
        strength: 0.0-1.0, how strong to apply beautification
        output_path: Where to save; auto-generated if None
    """
    # Load image
    if image_path_or_url.startswith(('http://', 'https://')):
        resp = requests.get(image_path_or_url, timeout=30)
        img_array = np.asarray(bytearray(resp.content), dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    else:
        img = cv2.imread(image_path_or_url)

    if img is None:
        raise ValueError(f"Cannot load image: {image_path_or_url}")

    h, w = img.shape[:2]
    result = img.copy()

    # 1. Skin smoothing with bilateral filter (preserves edges)
    smooth = cv2.bilateralFilter(result, d=9, sigmaColor=75, sigmaSpace=75)
    result = cv2.addWeighted(smooth, 0.3 * strength, result, 1.0 - 0.3 * strength, 0)

    # 2. Face/eye detection using Haar cascades
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')

    gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(200, 200))

    for (fx, fy, fw, fh) in faces:
        # Upper face (eye region)
        upper_y = fy
        upper_h = fh // 2
        upper_roi = result[upper_y:upper_y+upper_h, fx:fx+fw]
        upper_gray = gray[upper_y:upper_y+upper_h, fx:fx+fw]

        eyes = eye_cascade.detectMultiScale(upper_gray, 1.1, 4)
        for (ex, ey, ew, eh) in eyes:
            # Eye brightening
            eye_roi = upper_roi[ey:ey+eh, ex:ex+ew]
            eye_lab = cv2.cvtColor(eye_roi, cv2.COLOR_BGR2LAB)
            eye_lab[:, :, 0] = cv2.add(eye_lab[:, :, 0], int(20 * strength))
            eye_bright = cv2.cvtColor(eye_lab, cv2.COLOR_LAB2BGR)

            # Eye sharpening
            kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
            eye_sharp = cv2.filter2D(eye_bright, -1, kernel)
            upper_roi[ey:ey+eh, ex:ex+ew] = eye_sharp

        # 3. Under-eye dark circle correction
        middle_y = upper_y + upper_h
        under_eye_h = fh // 3
        under_eye_roi = result[middle_y:middle_y+under_eye_h, fx:fx+fw]
        if under_eye_roi.size > 0:
            under_lab = cv2.cvtColor(under_eye_roi, cv2.COLOR_BGR2LAB)
            under_lab[:, :, 0] = cv2.add(under_lab[:, :, 0], int(12 * strength))
            under_lab[:, :, 1] = cv2.multiply(under_lab[:, :, 1], 0.8).astype(np.uint8)
            under_lab[:, :, 2] = cv2.multiply(under_lab[:, :, 2], 0.9).astype(np.uint8)
            under_correct = cv2.cvtColor(under_lab, cv2.COLOR_LAB2BGR)
            result[middle_y:middle_y+under_eye_h, fx:fx+fw] = cv2.addWeighted(under_correct, 0.5, under_eye_roi, 0.5, 0)

    # 4. Overall skin tone improvement (brightness + chroma)
    result_lab = cv2.cvtColor(result, cv2.COLOR_BGR2LAB)
    result_lab[:, :, 0] = cv2.add(result_lab[:, :, 0], int(5 * strength))
    result = cv2.cvtColor(result_lab, cv2.COLOR_LAB2BGR)

    # Save
    if output_path is None:
        base = os.path.splitext(image_path_or_url if '://' not in image_path_or_url else 'portrait.jpg')[0]
        output_path = f"{base}_beautified.jpg"

    cv2.imwrite(output_path, result, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return output_path
