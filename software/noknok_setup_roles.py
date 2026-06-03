# noknok_setup_roles.py
# Run this ONCE from Thonny to assign role names to your physical modules.
# The result is saved to noknok_roles.json on the Pico's CIRCUITPY drive.
#
# Supported module types:
#   Buzzer   → plays a beep so you can identify it
#   Keyboard → flashes LED white so you can identify it
#
# After running this, all your apps can use:
#   c.enumerate()
#   c.load_roles()
#   c.role["ok_button"].set_color(0, 255, 0, 0)
#   c.role["alert_buzzer"].play(880, 200)
#
# Re-run any time you want to change role assignments or add new modules.

from noknok import Conductor

c = Conductor()
c.enumerate()
c.setup_roles()
