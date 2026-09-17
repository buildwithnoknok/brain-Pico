# bench_rpc_product.py — the smallest possible product, for DEV-34 bench runs.
#
# Put on the bench Pico as /data/product.py (`pico.py put bench_rpc_product.py
# /data/product.py`). It polls the LED Button like a real product and lights
# it while pressed; it has NO idea the app channel exists — that is the point:
# the Conductor's drivers service /rpc between reads, so a `hello` from the
# Pi must be answered while this loop runs. Beeps once at start.
from noknok import Conductor
import time

c = Conductor()
c.enumerate_all()
c.load_roles()
btn = c.ledbutton[0] if c.ledbutton else None
if c.buzzer:
    c.buzzer[0].play(660, 80, 40)
print("bench product up: button=%s" % (btn is not None))

n = 0
while True:
    st = btn.read() if btn else None
    if st is not None:
        if st.pressed:
            btn.set_color(0, 40, 0)
        elif n % 33 == 0:
            btn.led_off()
    n += 1
    c.sleep(0.03)          # cooperative sleep: also pumps the app channel
