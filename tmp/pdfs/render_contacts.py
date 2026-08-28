import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw


source = Path(sys.argv[1])
output = Path(sys.argv[2])
first_page = int(sys.argv[3])
last_page = int(sys.argv[4])
output.mkdir(parents=True, exist_ok=True)
pdf = pdfium.PdfDocument(source)

for base in range(first_page - 1, last_page, 4):
    images = []
    for index in range(base, min(base + 4, last_page)):
        image = pdf[index].render(scale=1.35).to_pil().convert("RGB")
        canvas = Image.new("RGB", (image.width, image.height + 40), "white")
        canvas.paste(image, (0, 40))
        ImageDraw.Draw(canvas).text(
            (10, 10), f"PAGE {index + 1}", fill="red", stroke_width=1
        )
        images.append(canvas)
    width = max(image.width for image in images)
    height = max(image.height for image in images)
    sheet = Image.new("RGB", (width * 2, height * 2), (220, 220, 220))
    for offset, image in enumerate(images):
        sheet.paste(image, ((offset % 2) * width, (offset // 2) * height))
    sheet.save(
        output / f"pages_{base + 1:02d}_{base + len(images):02d}.jpg",
        quality=90,
    )
