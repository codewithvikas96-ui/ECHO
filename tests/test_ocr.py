from io import BytesIO
import unittest

from PIL import Image, ImageDraw, ImageFont

from echo.ocr import extract_image, find_tesseract


@unittest.skipUnless(find_tesseract(), "local Tesseract engine required")
class ImageRecognitionTests(unittest.TestCase):
    def test_transparent_and_rotated_labels(self):
        image = Image.new("RGBA", (360, 80))
        ImageDraw.Draw(image).text((15, 12), "DREAM STRONGER", fill="white", font=ImageFont.load_default(size=30))
        for rotation in (None, Image.Transpose.ROTATE_90):
            with self.subTest(rotation=rotation):
                sample = image if rotation is None else image.transpose(rotation)
                data = BytesIO()
                sample.save(data, format="PNG")
                self.assertIn("dream stronger", extract_image(data.getvalue()).casefold())

    def test_unreadable_input(self):
        blank = BytesIO()
        Image.new("RGB", (120, 80), "white").save(blank, format="PNG")
        with self.assertRaisesRegex(ValueError, "No readable text"):
            extract_image(blank.getvalue())
        with self.assertRaisesRegex(ValueError, "could not open"):
            extract_image(b"invalid image")
