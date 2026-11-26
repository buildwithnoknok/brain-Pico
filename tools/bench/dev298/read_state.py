# DEV-298 bench: versions, the Store "usb" record and the last events (read-only).
import microcontroller
import noknok
import noknok_usb
try:
    import noknok_main as m
    code = m.CODE_VERSION
except Exception as e:
    code = "?(%s)" % e
print("code", code, "noknok", noknok.__version__, "noknok_usb", noknok_usb.__version__)
print("ota last check nvm[1:5] =", bytes(microcontroller.nvm[1:5]))
print("usb record:", noknok.store().get("usb"))
ev = noknok.store().get("events") or []
for line in ev[-25:]:
    print("  ev", line)
