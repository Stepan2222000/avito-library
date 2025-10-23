"""Utility functions for Geetest solver."""

from __future__ import annotations

from hashlib import sha512

import cv2
import numpy as np


def calculate_hash(back_content: bytes, pi_content: bytes) -> str:
    """Build cache key from background and puzzle images."""

    return sha512(back_content + pi_content).hexdigest()


def calculate_offset(
    back_content: bytes,
    pi_content: bytes,
    pi_top: float,
    threshold: float = 0.5,
) -> int:
    """Compute slider offset using OpenCV template matching."""

    template_image = cv2.imdecode(np.frombuffer(pi_content, np.uint8), cv2.IMREAD_COLOR)
    main_image = cv2.imdecode(np.frombuffer(back_content, np.uint8), cv2.IMREAD_COLOR)
    main_image = main_image[int(pi_top) : int(pi_top + 80.4757), :]
    main_gray = cv2.cvtColor(main_image, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template_image, cv2.COLOR_BGR2GRAY)
    _, template_thresh = cv2.threshold(template_gray, 50, 255, cv2.THRESH_BINARY_INV)
    _, main_thresh = cv2.threshold(main_gray, 127, 255, cv2.THRESH_BINARY)
    result = cv2.matchTemplate(main_thresh, template_thresh, cv2.TM_CCOEFF_NORMED)
    _, x_coords = np.where(result >= threshold)
    return int(x_coords[-1]) + 37 if len(x_coords) else 0
