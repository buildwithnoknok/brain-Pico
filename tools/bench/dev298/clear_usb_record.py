# DEV-298 bench: forget what this brain knows about its USB modules (Store key "usb":
# type, bootloader version "bl", visit counters). Use after a module was re-flashed
# over SWD to the SAME app version (the brain cannot tell), before a fresh test run.
import noknok
print("before:", noknok.store().get("usb"))
noknok.store().delete("usb")
print("after:", noknok.store().get("usb"))
