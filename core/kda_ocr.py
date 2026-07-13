"""KDA OCR reader for replay HUD capture regions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import cv2
import mss
import numpy as np
import pytesseract

from config.settings import KDAOcrConfig

logger = logging.getLogger(__name__)

_KDA_PATTERN = re.compile(r"(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})")


@dataclass(frozen=True)
class KDAReading:
    kills: int
    deaths: int
    assists: int


class RegionNotConfiguredError(RuntimeError):
    """KDA capture region is not configured."""


class KDAOcrReader:
    """Capture KDA region and parse values with OCR."""

    def __init__(self, config: KDAOcrConfig):
        self.config = config
        if config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = config.tesseract_cmd
        self._sct = mss.mss()

    def capture_raw(self) -> np.ndarray:
        """Capture configured screen region as a BGRA numpy array."""
        if not self.config.region.is_configured():
            raise RegionNotConfiguredError(
                "Область экрана для чтения KDA не настроена. Откройте настройки и "
                "выделите область над HUD выбранного героя."
            )
        shot = self._sct.grab(self.config.region.as_mss_dict())
        return np.array(shot)

    def preprocess(self, raw_image: np.ndarray) -> np.ndarray:
        """Preprocess image for OCR with scaling, grayscale, and thresholding."""
        gray = cv2.cvtColor(raw_image, cv2.COLOR_BGRA2GRAY)
        scale = max(1.0, self.config.ocr_upscale_factor)
        resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh

    def read_kda(self) -> Optional[KDAReading]:
        """Return parsed KDA or None when OCR parsing fails."""
        raw = self.capture_raw()
        processed = self.preprocess(raw)
        text = pytesseract.image_to_string(
            processed,
            config="--psm 7 -c tessedit_char_whitelist=0123456789/",
        )
        reading = self._parse(text)
        if reading is None:
            logger.debug("Не удалось распознать KDA из текста OCR: %r", text.strip())
        return reading

    def _parse(self, text: str) -> Optional[KDAReading]:
        match = _KDA_PATTERN.search(text)
        if not match:
            return None
        kills, deaths, assists = (int(g) for g in match.groups())
        limit = self.config.max_reasonable_kill_value
        if kills > limit or deaths > limit or assists > limit:
            return None
        return KDAReading(kills=kills, deaths=deaths, assists=assists)

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:  # noqa: BLE001 - resource cleanup should not crash the application
            pass