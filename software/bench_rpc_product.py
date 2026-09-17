# bench_rpc_product.py — the smallest possible product, for DEV-34 bench runs.
#
# Put on the bench Pico as /data/product.py (`pico.py put bench_rpc_product.py
# /data/product.py`). It shows the whole c.settings loop on one LED Button:
#   - settings: on (toggle), color (#RRGGBB), brightness (0-255)
#   - the button toggles `on` (device-side change → persisted after 5 s idle)
#   - the app (rpc_call.py … settings.set) changes colour/brightness → the
#     on_change callback repaints the LED at the next module read
# It has NO idea the app channel exists: the Conductor's drivers service /rpc
# between reads and deliver the callback. Beeps once at start.
from noknok import Conductor
import time

c = Conductor()
c.enumerate_all()
c.load_roles()
btn = c.ledbutton[0] if c.ledbutton else None
if c.buzzer:
    c.buzzer[0].play(660, 80, 40)

s = c.settings
s.defaults({"on": True, "color": "#00FF40", "brightness": 40})

def apply(changed=None):
    """Paint the LED from the current settings."""
    if btn is None:
        return
    if not s.get("on"):
        btn.led_off()
        return
    hexc = str(s.get("color", "#00FF40")).lstrip("#")
    try:
        r, g, b = int(hexc[0:2], 16), int(hexc[2:4], 16), int(hexc[4:6], 16)
    except Exception:
        r, g, b = 0, 255, 64
    k = max(0, min(255, int(s.get("brightness", 40)))) / 255
    btn.set_color(int(r * k), int(g * k), int(b * k))
    print("bench product: applied", s.all(), "changed:", changed)

s.on_change(apply)          # app-side changes land here
apply()
print("bench product up: button=%s settings=%s" % (btn is not None, s.all()))

was = False
while True:
    st = btn.read() if btn else None
    pressed = bool(st and st.pressed)
    if pressed and not was:                 # press edge → toggle on/off
        s.set("on", not s.get("on"))
        apply()
    was = pressed
    c.sleep(0.03)          # cooperative sleep: also pumps the app channel
