"""
A module for reading game time (clock) at the top of the Dota 2 screen.
"""
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import mss
import mss.tools
import pytesseract
from PIL import Image, ImageOps

from config.settings import ScreenRegion

logger = logging.getLogger(__name__)


class ClockOcrReader:
    def __init__(self, tesseract_cmd: str, region: ScreenRegion):
        self.tesseract_cmd = tesseract_cmd
        self.region = region
        if self.tesseract_cmd and self.tesseract_cmd.strip():
            if not Path(self.tesseract_cmd).exists():
                logger.warning("Путь к Tesseract не найден: %s", self.tesseract_cmd)
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
        self._ocr_configs = (
            r"--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789:-",
            r"--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789:-",
        )
        self.sct = mss.mss()
        self._logged_ocr_error = False
        self._debug_dir = Path("logs") / "clock_ocr"
        self._debug_dir.mkdir(parents=True, exist_ok=True)

    def read_game_seconds(self) -> Optional[int]:
        """
        Takes a screenshot of the clock area and returns the time in seconds.
        If the time is negative (pregame, e.g. -1:15), a negative number will be returned.
        """
        monitor = {
            "top": self.region.y,
            "left": self.region.x,
            "width": self.region.width,
            "height": self.region.height,
        }
        
        try:
            sct_img = self.sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            preprocessed_img = self._preprocess(img)

            raw_texts = []
            for config in self._ocr_configs:
                text = pytesseract.image_to_string(preprocessed_img, config=config).strip()
                raw_texts.append(text)
                parsed = self._parse_time_string(text)
                if parsed is not None:
                    logger.debug("Clock OCR success: %r -> %d", text, parsed)
                    return parsed

            self._save_debug_images(img, preprocessed_img)
            logger.debug("Clock OCR parse failed. region=%s raw=%s", self.region, raw_texts)
            return None
        except Exception as exc:
            if not self._logged_ocr_error:
                logger.warning("Ошибка OCR при чтении часов (показывается один раз): %s", exc)
                self._logged_ocr_error = True
            else:
                logger.debug("Ошибка OCR при чтении часов: %s", exc)
            return None

    def _preprocess(self, image: Image.Image) -> Image.Image:
        gray = ImageOps.grayscale(image)
        upscaled = gray.resize((gray.width * 4, gray.height * 4), Image.Resampling.LANCZOS)
        contrasted = ImageOps.autocontrast(upscaled)
        threshold_lut = [0 if i <= 150 else 255 for i in range(256)]
        return contrasted.point(threshold_lut, mode="1")

    def _save_debug_images(self, raw_image: Image.Image, preprocessed_image: Image.Image) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        raw_path = self._debug_dir / f"{stamp}_raw.png"
        prep_path = self._debug_dir / f"{stamp}_preprocessed.png"
        try:
            raw_image.save(raw_path)
            preprocessed_image.save(prep_path)
            logger.info("Clock OCR debug screenshots saved: raw=%s, preprocessed=%s", raw_path, prep_path)
        except Exception as exc:
            logger.debug("Не удалось сохранить Clock OCR debug-скриншоты: %s", exc)

    def _parse_time_string(self, time_str: str) -> Optional[int]:
        """Конвертирует '12:30' или '-1:15' в секунды с жесткой проверкой на адекватность."""
        # Оставляем только цифры, двоеточие и минус
        clean_str = "".join(c for c in time_str if c in "0123456789:-")
        
        # Если нет двоеточия - это не время, а мусор (например счет игры)
        if ":" not in clean_str:
            return None
            
        try:
            is_negative = clean_str.startswith('-')
            clean_str = clean_str.replace('-', '')
            
            parts = clean_str.split(":")
            if len(parts) == 2:
                minutes, seconds = int(parts[0]), int(parts[1])
            elif len(parts) == 3:
                minutes = int(parts[0]) * 60 + int(parts[1])
                seconds = int(parts[2])
            else:
                return None
            
            if seconds > 59:
                return None
                
            total_seconds = minutes * 60 + seconds
            result = -total_seconds if is_negative else total_seconds
            

            if result < -150:
                logger.debug("Отклонено OCR время %d (слишком большое отрицательное значение)", result)
                return None
                
            return result
            
        except (ValueError, IndexError):
            return None