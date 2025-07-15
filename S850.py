#!/usr/bin/env python3
import pigpio
import time
import datetime

# ——— Configuration ——————————————————————————————————————————————————
STATUS_PIN         = 17        # BCM pin number connected to the S850 status line
ARMED_MAX_US       =   75      # ≤ 75 µs → ARMED (you observed ~60 µs)
UNARMED_MAX_US     =  150      # 76–150 µs → UNARMED (you observed ~90 µs)
ALARM_TIMEOUT_US   =1_000_000  # no pulse for >1 s → ALARM
# ————————————————————————————————————————————————————————————————

# Module‐level state
last_fall_tick  = None
last_pulse_tick = None
current_state   = None

def edge_cb(gpio, level, tick):
    global last_fall_tick, last_pulse_tick, current_state

    if level == 0:  # falling edge: start of low pulse
        last_fall_tick = tick
        return

    # rising edge: end of low pulse
    if level == 1 and last_fall_tick is not None:
        width = pigpio.tickDiff(last_fall_tick, tick)  # in µs
        last_pulse_tick = tick

        if   width <= ARMED_MAX_US:
            new_state = "ARMED"
        elif width <= UNARMED_MAX_US:
            new_state = "UNARMED"
        else:
            new_state = "UNKNOWN"

        if new_state != current_state:
            current_state = new_state
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] Stand state → {current_state} (pulse {width} µs)")

def main():
    global last_pulse_tick

    pi = pigpio.pi()
    if not pi.connected:
        print("❌ pigpiod not running? Start with: sudo systemctl start pigpiod")
        return

    # Configure input with pull-up
    pi.set_mode(STATUS_PIN, pigpio.INPUT)
    pi.set_pull_up_down(STATUS_PIN, pigpio.PUD_UP)

    # Monitor both edges
    pi.callback(STATUS_PIN, pigpio.EITHER_EDGE, edge_cb)

    print(f"✅ Monitoring S850 status on GPIO{STATUS_PIN}. Ctrl-C to quit.")

    try:
        while True:
            time.sleep(1)
            # If no pulse for >1 s → ALARM
            if last_pulse_tick is not None and pigpio.tickDiff(last_pulse_tick, pi.get_current_tick()) > ALARM_TIMEOUT_US:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}] *** ALARM CONDITION: no pulses for >1 s ***")
                last_pulse_tick = None
    except KeyboardInterrupt:
        print("\n🛑 Stopping monitor.")
    finally:
        pi.stop()

if __name__ == "__main__":
    main()
