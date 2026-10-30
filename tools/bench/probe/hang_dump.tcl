# SPDX-FileCopyrightText: 2026 noknok (Christopher Houben)
# SPDX-License-Identifier: MIT
# DEV-172/173: snapshot both RP2350 cores of a frozen brain over SWD (Raspberry Pi Debug Probe).
# OpenOCD links the two cores (SMP), so every step halts BOTH, reads both, resumes both.
# PC/LR sampling (busy loop = same few addresses), full regs, SCB fault registers, top of stack.
# Always ends by clearing C_HALT in DHCSR on both cores, even if a read failed.
set CORES {rp2350.cm0 rp2350.cm1}
proc rv {r} { return [lindex [reg $r] end] }
proc halt_both {} { global CORES; foreach t $CORES { targets $t; catch {halt 300} } ; foreach t $CORES { targets $t; poll } }
proc resume_both {} {
    global CORES
    foreach t $CORES { targets $t; mww 0xE000EDF0 0xA05F0001 }
}
proc sample {i} {
    global CORES
    halt_both
    foreach t $CORES {
        targets $t
        if {[catch {echo [format "%s sample %2d  pc %s  lr %s  sp %s  xpsr %s" $t $i [rv pc] [rv lr] [rv sp] [rv xpsr]]} e]} {
            echo "!!! $t sample $i: $e"
        }
    }
    resume_both
}
proc full {} {
    global CORES
    halt_both
    foreach t $CORES {
        targets $t
        echo "===== $t registers"
        catch {echo [reg]}
        echo "----- $t SCB: VTOR, then CFSR HFSR DFSR MMFAR BFAR"
        catch {echo [mdw 0xE000ED08]}
        catch {echo [mdw 0xE000ED28 5]}
        set sp [rv sp]
        set n [expr {(0x20082000 - $sp) / 4}]
        if {$n > 256} { set n 256 }
        if {$n > 0} {
            echo "----- $t stack ($n words from sp $sp)"
            catch {echo [mdw $sp $n]}
        }
    }
    resume_both
}

# USB state (CP 10.3.1 pico2_w addresses from our rebuild; valid because only ulab differs from the
# official image and the code literal pools are identical). Read twice, 1 s apart: does anything move?
proc usb_state {tag} {
    halt_both
    targets rp2350.cm0
    echo "===== USB $tag"
    echo "--- _usbd_dev 0x2000cdf0 (0x6c)";        catch {echo [mdw 0x2000cdf0 27]}
    echo "--- _cdcd_itf 0x2000be5c (first 0x100)";  catch {echo [mdw 0x2000be5c 64]}
    echo "--- hw_endpoints 0x20010d74 (0x300)";     catch {echo [mdw 0x20010d74 192]}
    echo "--- _usbd_q / qdef_buf";                  catch {echo [mdw 0x2000ce60 1]}; catch {echo [mdw 0x2000ce64 48]}
    echo "--- _serial_console_write_disabled";      catch {echo [mdb 0x20014092 1]}
    echo "--- USBCTRL_REGS 0x50110000";             catch {echo [mdw 0x50110000 40]}
    echo "--- DPRAM ep ctrl 0x50100000 + buf ctrl 0x50100080"; catch {echo [mdw 0x50100000 32]}; catch {echo [mdw 0x50100080 32]}
    resume_both
}

for {set i 0} {$i < 12} {incr i} { sample $i; sleep 37 }
usb_state A
sleep 1000
usb_state B
if {[catch {full} e]} { echo "!!! full dump: $e" }
resume_both
foreach t $CORES { targets $t; echo "$t DHCSR [lindex [mdw 0xE000EDF0] end]" }
