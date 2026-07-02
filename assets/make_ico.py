"""重写 app_icon.ico —— 手动构造多分辨率 ICO 二进制。

PIL 的 save(ICO, sizes=..., append_images=...) 在某些版本上只会写入单帧，
本脚本绕过 PIL 的 ICO writer，直接拼接每帧 PNG（ICO 容器支持 PNG 嵌入，
Win7+ 自动识别）。

CLI:
    python make_ico.py
"""
from pathlib import Path
import struct
from PIL import Image
from io import BytesIO


def build_png_paylod(rgba_img: Image.Image) -> bytes:
    """把 RGBA 图编码为 PNG bytes（ICO 内部 PNG 帧格式）。"""
    img = rgba_img.convert("RGBA")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def write_ico(out_path: Path, images: list) -> int:
    """images: list of (PIL.Image RGBA, size_int) 元组"""

    # 1) 先把所有帧编码为 PNG payload
    payloads = [build_png_paylod(im) for im, _ in images]

    # 2) ICO 头
    ICONDIR = struct.Struct("<HHH")  # reserved(2) + type(2) + count(2)
    ICONDIRENTRY = struct.Struct(
        "<BBBBHHII"  # width(1)+height(1)+colors(1)+reserved(1)+planes(2)+bpp(2)+bytes(4)+offset(4)
    )
    header = ICONDIR.pack(0, 1, len(images))  # type=1 (ICO)
    entry_size = ICONDIRENTRY.size

    # 3) 目录紧跟 header；图数据在目录之后
    data_offset = len(header) + entry_size * len(images)
    entries = b""
    for (im, _size), payload in zip(images, payloads):
        w, h = im.size
        b_w = 0 if w >= 256 else w
        b_h = 0 if h >= 256 else h
        entries += ICONDIRENTRY.pack(
            b_w, b_h, 0, 0,       # width(0=256), height(0=256), colors=0, reserved=0
            1, 32,                 # planes=1, bpp=32
            len(payload), data_offset,
        )
        data_offset += len(payload)

    blob = header + entries + b"".join(payloads)
    out_path.write_bytes(blob)
    return out_path.stat().st_size


def main():
    here = Path(__file__).parent
    src_path = here / "app_icon.png"
    out_path = here / "app_icon.ico"

    src = Image.open(src_path).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    images = [(src.resize(s, Image.LANCZOS), s[0]) for s in sizes]

    out_bytes = write_ico(out_path, images)

    # 验证
    import PIL.Image as IM
    verify = IM.open(out_path)
    print(f"output  : {out_path}")
    print(f"size    : {out_bytes} bytes")
    print(f"frames  : {sorted(verify.ico.sizes())}")
    # 用 file 命令交叉验证
    import subprocess
    res = subprocess.run(["file", str(out_path)], capture_output=True, text=True, check=False)
    print(f"file(1) : {res.stdout.strip()}")


if __name__ == "__main__":
    main()
