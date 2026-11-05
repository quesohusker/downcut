from kerfline.textvec import list_fonts

PREFERRED = ("Arial Bold", "Liberation Sans Bold", "DejaVu Sans Bold", "Arial")


def outline_font() -> str:
    fonts = {row["name"]: row for row in list_fonts() if row["path"].lower().endswith((".ttf", ".otf"))}
    for name in PREFERRED:
        if name in fonts:
            return fonts[name]["path"]
    if fonts:
        return next(iter(fonts.values()))["path"]
    raise RuntimeError("These tests need one TrueType or OpenType font on the system.")


ARIAL = outline_font()
