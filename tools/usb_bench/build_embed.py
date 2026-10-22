# SPDX-License-Identifier: MIT
# Bench host: build a self-contained ota_loop for a Pico whose filesystem cannot be written.
# Embeds noknok_usb.py (exec'd into a namespace object) and the app image (base64).
#   python3 build_embed.py noknok_usb.py noknok_leds.bin ota_loop.py > ota_embedded.py
import sys, base64
usb_src = open(sys.argv[1]).read()
img = open(sys.argv[2], "rb").read()
loop = open(sys.argv[3]).read()
assert "import noknok_usb\n" in loop and 'data = open(IMG, "rb").read()' in loop
loop = loop.replace("import noknok_usb\n", "")
loop = loop.replace('data = open(IMG, "rb").read()', "data = _IMG")
out = []
out.append("import binascii")
out.append("_g = {}")
out.append("exec(%r, _g)" % usb_src)
out.append("class noknok_usb: pass")
out.append("for _k in _g: setattr(noknok_usb, _k, _g[_k])")
out.append("_IMG = binascii.a2b_base64(%r)" % base64.b64encode(img))
out.append(loop)
print("\n".join(out))
