import requests
from PIL import Image
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime
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

# GPIO configuration
GPIO.setmode(GPIO.BCM)  # Use BCM pin numbering
BUTTON_PINS = [17, 27, 22, 23, 24]  # GPIO pins for buttons
CONTACT_IDS = ["1", "2", "3", "4", "5"]  # Map buttons to Contact IDs

# Set up GPIO pins as inputs with pull-up resistors
for pin in BUTTON_PINS:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Authenticate and initialize the Sheets API
def authenticate_sheets():
    print("Authenticating with Google Sheets API...")
    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return build("sheets", "v4", credentials=credentials)

# Read values from the Credentials sheet for JWT token and base URL
def read_credentials():
    print(f"Reading credentials from range: {CREDENTIALS_RANGE}")
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
    print(f"Read JWT token: {jwt_token}, Base URL: {base_url}")
    return jwt_token, base_url

# Read the alarm station table from the Credentials sheet
def load_alarm_table():
    print("Loading alarm station table...")
    sheets = authenticate_sheets()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=ALARM_TABLE_RANGE
    ).execute()
    rows = result.get('values', [])
    alarm_table = {row[0]: row[1:] for row in rows if len(row) >= 7}  # Map Contact ID to remaining values
    print(f"Loaded {len(alarm_table)} alarm stations.")
    return alarm_table

# Fetch and resize the camera image
def fetch_and_resize_image(base_url, jwt_token, device_id):
    url = f"https://{base_url}/api/v3.0/media/liveImage.jpeg?deviceId={device_id}&type=preview"
    print(f"Fetching image from URL: {url}")
    headers = {
        "accept": "image/jpeg",
        "authorization": f"Bearer {jwt_token}"
    }
    response = requests.get(url, headers=headers)
    print(f"Image fetch response status: {response.status_code}")
    if response.status_code != 200:
        raise Exception(f"Failed to fetch image. Status code: {response.status_code}")
    
    # Resize the image to approximately 10cm width (600px at 96 DPI)
    image = Image.open(BytesIO(response.content))
    target_width = 600
    width_percent = (target_width / float(image.size[0]))
    target_height = int((float(image.size[1]) * float(width_percent)))
    image = image.resize((target_width, target_height), Image.ANTIALIAS)

    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    print(f"Image fetched and resized to {target_width}x{target_height} pixels.")
    return buffer, target_height

# Upload image to Google Drive
def upload_image_to_drive(image_buffer, file_name):
    print("Uploading image to Google Drive...")
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
    print(f"Uploaded file ID: {file_id}")

    # Make the file public
    drive_service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"}
    ).execute()

    # Get the shareable link
    file_url = f"https://drive.google.com/uc?id={file_id}"
    print(f"File URL: {file_url}")
    return file_url

# Get the next available row in the sheet
def get_next_available_row():
    sheets = authenticate_sheets()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{ALARM_LOG_SHEET_NAME}!A:A"
    ).execute()
    rows = result.get('values', [])
    next_row = len(rows) + 1  # Next row index
    print(f"Next available row is: {next_row}")
    return next_row

# Append data to the Alarm Log sheet
def append_row_to_sheet(data, image_buffer, image_height, device_id):
    sheets = authenticate_sheets()

    # Get the next available row
    next_row = get_next_available_row()

    # Upload the image to Google Drive
    image_url = upload_image_to_drive(image_buffer, "alarm_snapshot.jpg")
    print(f"Image uploaded with URL: {image_url}")

    # Create the clickable URL
    # video_url = f"https://c028.eagleeyenetworks.com/live/index.html?id={device_id}&shortcut_override=false"
    video_url = f"https://webapp.eagleeyenetworks.com/#/videoext/{device_id}"

    # Add row data and a clickable image
    requests = [
        # Add data to the row
        {
            "updateCells": {
                "rows": [
                    {
                        "values": [
                            {"userEnteredValue": {"stringValue": str(data[0])}},  # Site
                            {"userEnteredValue": {"stringValue": str(data[1])}},  # Location
                            {"userEnteredValue": {"stringValue": str(data[2])}},  # Floor
                            {"userEnteredValue": {"stringValue": str(data[3])}},  # Zone
                            {"userEnteredValue": {"stringValue": str(data[4])}},  # Alarm Station
                            {"userEnteredValue": {"stringValue": str(data[5])}},  # Timestamp
                            {
                                "userEnteredValue": {
                                    "formulaValue": f'=HYPERLINK("{video_url}", IMAGE("{image_url}"))'
                                }  # Clickable image
                            }
                        ]
                    }
                ],
                "start": {"sheetId": 0, "rowIndex": next_row - 1, "columnIndex": 0},
                "fields": "userEnteredValue"
            }
        },
        # Resize row height to match image height
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

    # Execute the batchUpdate request
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body={"requests": requests}
    ).execute()
    print("Row and clickable image added successfully.")

# Main loop to handle GPIO inputs
def main():
    # Load your station table and creds as before
    alarm_table = load_alarm_table()
    jwt_token, base_url = read_credentials()

    # Track which pins have already been ?handled? while LOW
    handled = {cid: False for cid in CONTACT_IDS}

    print("Monitoring GPIO pins for alarms? Press Ctrl+C to stop.")
    try:
        while True:
            for pin, contact_id in zip(BUTTON_PINS, CONTACT_IDS):
                state = GPIO.input(pin)

                # 1) LOW and never handled ? trigger once
                if state == GPIO.LOW and not handled[contact_id]:
                    handled[contact_id] = True
                    print(f"Alarm on Contact ID {contact_id}")

                    # your existing per-alarm logic:
                    row_data = alarm_table.get(contact_id)
                    if not row_data:
                        print(f"No config for ID {contact_id}")
                        continue

                    device_id = row_data[-1]
                    row_to_log = row_data[:-1] + [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]

                    img_buf, img_h = fetch_and_resize_image(base_url, jwt_token, device_id)
                    append_row_to_sheet(row_to_log, img_buf, img_h, device_id)
                    print("Logged snapshot for Contact ID", contact_id)

                # 2) HIGH and was handled ? reset so next LOW will fire again
                elif state == GPIO.HIGH and handled[contact_id]:
                    handled[contact_id] = False

            # short pause to debounce & avoid 100% CPU
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopping?")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()
