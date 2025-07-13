#!/usr/bin/env python3
import serial
import datetime

# ??? Configuration ?????????????????????????????????????????????????????????????
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE   = 115200
# ????????????????????????????????????????????????????????????????????????????????

def parse_message(raw_bytes):
    """
    Parse a raw byte message from the Alarm Node into a human-readable string.
    Expected format: <8-char DeviceID><hex SensorID>,<0|1>\r
    """
    try:
        # Decode bytes ? string, strip CR/LF
        text = raw_bytes.decode('ascii', errors='ignore').strip().rstrip('\r\n')
        parts = text.split(',')
        if len(parts) != 2:
            return None

        payload, flag = parts
        # payload = DeviceID(8) + SensorID(variable)
        if len(payload) < 9:
            return None

        device_id = payload[:8]
        sensor_id = payload[8:].upper()
        state = "? ALARM ON" if flag == '1' else "? ALARM OFF"
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        return f"[{timestamp}] Device {device_id} | Sensor 0x{sensor_id} | {state}"
    except Exception as e:
        # In case of unexpected format
        return f"Parse Error: {e} ? Raw: {raw_bytes!r}"

def main():
    print(f"? Listening on {SERIAL_PORT} at {BAUD_RATE} baud...\n")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        print(f"? Cannot open serial port {SERIAL_PORT}: {e}")
        return

    try:
        while True:
            line = ser.read_until(b'\r')
            if not line:
                continue
            entry = parse_message(line)
            if entry:
                print(entry)
    except KeyboardInterrupt:
        print("\n? Logging stopped.")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
