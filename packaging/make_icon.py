from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SIZE = 1024
BACK = (38, 39, 43, 255)
WOOD = (214, 176, 128, 255)
CUT = (181, 86, 28, 255)


def draw():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle([40, 40, SIZE - 40, SIZE - 40], radius=210, fill=BACK)
    pen.rectangle([150, 560, SIZE - 150, 860], fill=WOOD)
    pen.polygon([(512, 860), (330, 560), (694, 560)], fill=BACK)
    pen.polygon([(512, 760), (392, 560), (632, 560)], fill=CUT)
    pen.rectangle([452, 170, 572, 430], fill=WOOD)
    pen.polygon([(512, 640), (392, 430), (632, 430)], fill=WOOD)
    return image


def main():
    image = draw()
    image.save(HERE / "downcut.png")
    image.save(HERE / "downcut.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote downcut.png and downcut.ico")


if __name__ == "__main__":
    main()
