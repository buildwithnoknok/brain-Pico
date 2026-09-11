## Script to enumerate all devices (I2C or USB) connected to the Pico
from noknok import Conductor
c = Conductor()
c.enumerate_all()