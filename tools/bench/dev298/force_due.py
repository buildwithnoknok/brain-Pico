# DEV-298 bench: make the next boot run the daily update pass, then hard reset.
import microcontroller
microcontroller.nvm[1:5] = bytes(4)
print("ota timestamp cleared - hard reset")
microcontroller.reset()
