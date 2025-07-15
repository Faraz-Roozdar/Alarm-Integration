#!/usr/bin/env python3
import threading
import serial
import requests
from PIL import Image
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime
import RPi.GPIO as GPIO
import pigpio
import time

# ——— Configuration ——————————————————————————————————————————————————
SERVICE_ACCOUNT_FILE = "/home/roozdar/Desktop/projects/uplifted-record-443616-e6-63005fbd7104.json"
SPREADSHEET_ID = "1JLkeTBw8zTdtnHyQ5vTcsVsgxsdKuNBJoyjHQ3v-ThA"
ALARM_LOG_SHEET_NAME = "Alarm Log"
CREDENTIALS_SHEET_NAME = "Credentials"
CREDENTIALS_RANGE = f"{CREDENTIALS_SHEET_NAME}!B1:B2"
ALARM_TABLE_RANGE = f"{CREDENTIALS_SHEET_NAME}!A5:H"  # 8 columns: Contact ID, Site, Location, Floor, Zone, Table, Alarm Unit, Camera ID
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 115200

# GPIO configuration for hardware triggers
GPIO.setmode(GPIO.BCM)
HW_BUTTONS = {
    "1": 27,  # Example mapping, update as needed
    "2": 22,
    "5": 24
}
for pin in HW_BUTTONS.values():
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# S850 configuration
S850_CONTACT_ID = "6"
S850_STATUS_PIN = 17
ARMED_MAX_US = 75
UNARMED_MAX_US = 150
ALARM_TIMEOUT_US = 1_000_000

# Alarm node device IDs for Contact IDs 3 and 4
ALARM_NODE_MAP = {
    "3": "29FF",
    "4": "14A0"
}

# ——— Google Sheets/Drive Functions ————————————————————————————————
def authenticate_sheets():
    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return build("sheets", "v4", credentials=credentials)

def read_credentials():
    sheets = authenticate_sheets()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=CREDENTIALS_RANGE
    ).execute()
    values = result.get('values', [])
    if len(values) < 2:
        raise ValueError("Missing credentials in the 'Credentials' sheet.")
    jwt_token = values[0][0].strip()
    base_url = values[1][0].strip()
    return jwt_token, base_url

def load_alarm_table():
    sheets = authenticate_sheets()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=ALARM_TABLE_RANGE
    ).execute()
    rows = result.get('values', [])
    # Map Contact ID to all columns (Site, Location, Floor, Zone, Table, Alarm Unit, Camera ID)
    alarm_table = {row[0]: row[1:] for row in rows if len(row) >= 8}
    return alarm_table

def fetch_and_resize_image(base_url, jwt_token, device_id):
    url = f"https://{base_url}/api/v3.0/media/liveImage.jpeg?deviceId={device_id}&type=preview"
    headers = {
        "accept": "image/jpeg",
        "authorization": f"Bearer {jwt_token}"
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch image. Status code: {response.status_code}")
    image = Image.open(BytesIO(response.content))
    target_width = 600
    width_percent = (target_width / float(image.size[0]))
    target_height = int((float(image.size[1]) * float(width_percent)))
    image = image.resize((target_width, target_height), Image.ANTIALIAS)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    return buffer, target_height

def upload_image_to_drive(image_buffer, file_name):
    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    drive_service = build("drive", "v3", credentials=credentials)
    file_metadata = {"name": file_name, "mimeType": "image/jpeg"}
    media = MediaIoBaseUpload(image_buffer, mimetype="image/jpeg")
    uploaded_file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()
    file_id = uploaded_file.get("id")
    drive_service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"}
    ).execute()
    file_url = f"https://drive.google.com/uc?id={file_id}"
    return file_url

def get_next_available_row():
    sheets = authenticate_sheets()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{ALARM_LOG_SHEET_NAME}!A:A"
    ).execute()
    rows = result.get('values', [])
    next_row = len(rows) + 1
    return next_row

def append_row_to_sheet(row_data, image_buffer, image_height, device_id):
    sheets = authenticate_sheets()
    next_row = get_next_available_row()
    image_url = upload_image_to_drive(image_buffer, "alarm_snapshot.jpg")
    video_url = f"https://webapp.eagleeyenetworks.com/#/videoext/{device_id}"
    # row_data: [Site, Location, Floor, Zone, Table, Alarm Unit]
    requests_body = [
        {
            "updateCells": {
                "rows": [
                    {
                        "values": [
                            {"userEnteredValue": {"stringValue": str(row_data[0])}},  # Site
                            {"userEnteredValue": {"stringValue": str(row_data[1])}},  # Location
                            {"userEnteredValue": {"stringValue": str(row_data[2])}},  # Floor
                            {"userEnteredValue": {"stringValue": str(row_data[3])}},  # Zone
                            {"userEnteredValue": {"stringValue": str(row_data[4])}},  # Table
                            {"userEnteredValue": {"stringValue": str(row_data[5])}},  # Alarm Unit
                            {"userEnteredValue": {"stringValue": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}},  # Timestamp
                            {
                                "userEnteredValue": {
                                    "formulaValue": f'=HYPERLINK("{video_url}", IMAGE("{image_url}"))'
                                }
                            }
                        ]
                    }
                ],
                "start": {"sheetId": 0, "rowIndex": next_row - 1, "columnIndex": 0},
                "fields": "userEnteredValue"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": 0,
                    "dimension": "ROWS",
                    "startIndex": next_row - 1,
                    "endIndex": next_row,
                },
                "properties": {"pixelSize": image_height},
                "fields": "pixelSize"
            }
        },
    ]
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={"requests": requests_body}
    ).execute()

# ——— Alarm Handlers ————————————————————————————————————————————————
def handle_alarm(contact_id, alarm_table, jwt_token, base_url):
    row_data = alarm_table.get(contact_id)
    if not row_data:
        print(f"No config for Contact ID {contact_id}")
        return
    # row_data: [Site, Location, Floor, Zone, Table, Alarm Unit, Camera ID]
    device_id = row_data[-1]
    row_to_log = row_data[:-1]  # Exclude Camera ID
    try:
        img_buf, img_h = fetch_and_resize_image(base_url, jwt_token, device_id)
        append_row_to_sheet(row_to_log, img_buf, img_h, device_id)
        print(f"Logged snapshot for Contact ID {contact_id}")
    except Exception as e:
        print(f"Error handling alarm for Contact ID {contact_id}: {e}")

# Hardware GPIO monitoring (Contact IDs 1, 2, 5)
def gpio_monitor(alarm_table, jwt_token, base_url):
    handled = {cid: False for cid in HW_BUTTONS}
    try:
        while True:
            for cid, pin in HW_BUTTONS.items():
                state = GPIO.input(pin)
                if state == GPIO.LOW and not handled[cid]:
                    handled[cid] = True
                    print(f"Hardware alarm on Contact ID {cid}")
                    handle_alarm(cid, alarm_table, jwt_token, base_url)
                elif state == GPIO.HIGH and handled[cid]:
                    handled[cid] = False
            time.sleep(0.1)
    except Exception as e:
        print(f"GPIO monitoring error: {e}")
    finally:
        GPIO.cleanup()

# Serial/alarm node monitoring (Contact IDs 3, 4)
def serial_monitor(alarm_table, jwt_token, base_url):
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        print(f"Cannot open serial port {SERIAL_PORT}: {e}")
        return
    try:
        while True:
            line = ser.read_until(b'\r')
            if not line:
                continue
            try:
                text = line.decode('ascii', errors='ignore').strip().rstrip('\r\n')
                parts = text.split(',')
                if len(parts) != 2:
                    continue
                payload, flag = parts
                if flag != '1':
                    continue
                if len(payload) < 9:
                    continue
                device_id = payload[:8]
                for cid, node_id in ALARM_NODE_MAP.items():
                    if node_id in device_id:
                        print(f"Alarm node trigger for Contact ID {cid}")
                        handle_alarm(cid, alarm_table, jwt_token, base_url)
            except Exception as e:
                print(f"Serial parse error: {e} | Raw: {line!r}")
    except Exception as e:
        print(f"Serial monitoring error: {e}")
    finally:
        ser.close()

# S850 logic (Contact ID 6)
class S850Monitor:
    def __init__(self, alarm_table, jwt_token, base_url):
        self.pi = pigpio.pi()
        self.last_fall_tick = None
        self.last_pulse_tick = None
        self.current_state = None
        self.alarm_table = alarm_table
        self.jwt_token = jwt_token
        self.base_url = base_url
        if not self.pi.connected:
            raise RuntimeError("pigpiod not running? Start with: sudo systemctl start pigpiod")
        self.pi.set_mode(S850_STATUS_PIN, pigpio.INPUT)
        self.pi.set_pull_up_down(S850_STATUS_PIN, pigpio.PUD_UP)
        self.pi.callback(S850_STATUS_PIN, pigpio.EITHER_EDGE, self.edge_cb)

    def edge_cb(self, gpio, level, tick):
        if level == 0:
            self.last_fall_tick = tick
            return
        if level == 1 and self.last_fall_tick is not None:
            width = pigpio.tickDiff(self.last_fall_tick, tick)
            self.last_pulse_tick = tick
            if width > UNARMED_MAX_US:
                new_state = "UNKNOWN"
            elif width > ARMED_MAX_US:
                new_state = "UNARMED"
            else:
                new_state = "ARMED"
            if new_state != self.current_state:
                self.current_state = new_state
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}] S850 state → {self.current_state} (pulse {width} µs)")

    def monitor(self):
        try:
            while True:
                time.sleep(1)
                if self.last_pulse_tick is not None and pigpio.tickDiff(self.last_pulse_tick, self.pi.get_current_tick()) > ALARM_TIMEOUT_US:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{ts}] *** S850 ALARM CONDITION: no pulses for >1 s ***")
                    handle_alarm(S850_CONTACT_ID, self.alarm_table, self.jwt_token, self.base_url)
                    self.last_pulse_tick = None
        except KeyboardInterrupt:
            print("\n🛑 Stopping S850 monitor.")
        finally:
            self.pi.stop()

# ——— Main ————————————————————————————————————————————————————————
def main():
    alarm_table = load_alarm_table()
    jwt_token, base_url = read_credentials()
    threads = []
    threads.append(threading.Thread(target=gpio_monitor, args=(alarm_table, jwt_token, base_url), daemon=True))
    threads.append(threading.Thread(target=serial_monitor, args=(alarm_table, jwt_token, base_url), daemon=True))
    s850_monitor = S850Monitor(alarm_table, jwt_token, base_url)
    threads.append(threading.Thread(target=s850_monitor.monitor, daemon=True))
    for t in threads:
        t.start()
    print("Monitoring all alarm sources. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping all monitoring.")
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main() 