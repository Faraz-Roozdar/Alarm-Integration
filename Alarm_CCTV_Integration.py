import threading
import serial
import requests
from PIL import Image
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime as dt
import RPi.GPIO as GPIO
import time

# Path to your service account key file
SERVICE_ACCOUNT_FILE = "/home/roozdar/Desktop/projects/uplifted-record-443616-e6-63005fbd7104.json"  # Update with your Raspberry Pi file path

# Google Sheets and Drive details
SPREADSHEET_ID = "1JLkeTBw8zTdtnHyQ5vTcsVsgxsdKuNBJoyjHQ3v-ThA"  # Replace with your Google Sheet ID
ALARM_LOG_SHEET_NAME = "Alarm Log"
CREDENTIALS_SHEET_NAME = "Credentials"
CREDENTIALS_RANGE = f"{CREDENTIALS_SHEET_NAME}!B1:B2"  # Range for JWT token and base URL
ALARM_TABLE_RANGE = f"{CREDENTIALS_SHEET_NAME}!A5:G"  # Full range of the alarm table

# Serial configuration
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 115200

# GPIO configuration
GPIO.setmode(GPIO.BCM)  # Use BCM pin numbering
BUTTON_PINS = [27, 22, 23, 24]  # GPIO pins for hardware triggers (Contact IDs 2-5)
CONTACT_IDS = ["2", "3", "4", "5"]  # Map buttons to Contact IDs

# Set up GPIO pins as inputs with pull-up resistors
for pin in BUTTON_PINS:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

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
    alarm_table = {row[0]: row[1:] for row in rows if len(row) >= 7}  # Map Contact ID to remaining values
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

def append_row_to_sheet(data, image_buffer, image_height, device_id):
    sheets = authenticate_sheets()
    next_row = get_next_available_row()
    image_url = upload_image_to_drive(image_buffer, "alarm_snapshot.jpg")
    video_url = f"https://webapp.eagleeyenetworks.com/#/videoext/{device_id}"
    requests_body = [
        {
            "updateCells": {
                "rows": [
                    {
                        "values": [
                            {"userEnteredValue": {"stringValue": str(data[0])}},
                            {"userEnteredValue": {"stringValue": str(data[1])}},
                            {"userEnteredValue": {"stringValue": str(data[2])}},
                            {"userEnteredValue": {"stringValue": str(data[3])}},
                            {"userEnteredValue": {"stringValue": str(data[4])}},
                            {"userEnteredValue": {"stringValue": str(data[5])}},
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

def handle_trigger(contact_id, alarm_table, jwt_token, base_url):
    row_data = alarm_table.get(contact_id)
    if not row_data:
        print(f"No config for ID {contact_id}")
        return
    device_id = row_data[-1]
    row_to_log = row_data[:-1] + [dt.now().strftime("%Y-%m-%d %H:%M:%S")]
    try:
        img_buf, img_h = fetch_and_resize_image(base_url, jwt_token, device_id)
        append_row_to_sheet(row_to_log, img_buf, img_h, device_id)
        print(f"Logged snapshot for Contact ID {contact_id}")
    except Exception as e:
        print(f"Error handling trigger for Contact ID {contact_id}: {e}")

def gpio_monitor(alarm_table, jwt_token, base_url):
    handled = {cid: False for cid in CONTACT_IDS}
    try:
        while True:
            for pin, contact_id in zip(BUTTON_PINS, CONTACT_IDS):
                state = GPIO.input(pin)
                if state == GPIO.LOW and not handled[contact_id]:
                    handled[contact_id] = True
                    print(f"Hardware alarm on Contact ID {contact_id}")
                    handle_trigger(contact_id, alarm_table, jwt_token, base_url)
                elif state == GPIO.HIGH and handled[contact_id]:
                    handled[contact_id] = False
            time.sleep(0.1)
    except Exception as e:
        print(f"GPIO monitoring error: {e}")
    finally:
        GPIO.cleanup()

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
            # Parse for alarm ON (flag == '1')
            try:
                text = line.decode('ascii', errors='ignore').strip().rstrip('\r\n')
                parts = text.split(',')
                if len(parts) != 2:
                    continue
                payload, flag = parts
                if flag == '1':
                    print("Software alarm ON received (serial)")
                    handle_trigger("1", alarm_table, jwt_token, base_url)
            except Exception as e:
                print(f"Serial parse error: {e} | Raw: {line!r}")
    except Exception as e:
        print(f"Serial monitoring error: {e}")
    finally:
        ser.close()

def main():
    alarm_table = load_alarm_table()
    jwt_token, base_url = read_credentials()
    # Start GPIO and serial monitoring in separate threads
    gpio_thread = threading.Thread(target=gpio_monitor, args=(alarm_table, jwt_token, base_url), daemon=True)
    serial_thread = threading.Thread(target=serial_monitor, args=(alarm_table, jwt_token, base_url), daemon=True)
    gpio_thread.start()
    serial_thread.start()
    print("Monitoring GPIO pins and serial port for alarms. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping monitoring.")
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main() 