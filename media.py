# 图片处理（从 main.py 抽出）
import os


def compress_image(input_path, output_path):
    from PIL import Image, ImageOps
    import shutil
    ext = input_path.rsplit(".", 1)[-1].lower() if "." in input_path else ""
    try:
        size = os.path.getsize(input_path)
    except OSError:
        size = 0
    if ext in ("jpg", "jpeg") and size > 0 and size < 300 * 1024:
        try:
            with Image.open(input_path) as probe:
                w, h = probe.size
            if max(w, h) <= 1200:
                if os.path.abspath(input_path) != os.path.abspath(output_path):
                    shutil.copyfile(input_path, output_path)
                return
        except Exception as e:
            print(f"⚠ compress_image 探测失败，走压缩路径: {e}")
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > 1200:
            ratio = 1200 / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        img.save(output_path, "JPEG", quality=80, optimize=True)

def crop_avatar(input_path, output_path):
    from PIL import Image, ImageOps
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((200, 200), Image.LANCZOS)
        img.save(output_path, "JPEG", quality=82, optimize=True)

def compress_feed_bg(input_path, output_path):
    from PIL import Image, ImageOps
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > 1920:
            ratio = 1920 / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        img.save(output_path, "JPEG", quality=80, optimize=True)


__all__ = ["compress_image", "crop_avatar", "compress_feed_bg"]
