import pytz
from datetime import datetime

# Define the timezone
cst_tz = pytz.timezone("America/Chicago")

# Get the current date in the correct timezone
today = datetime.now(cst_tz).date()

print(today)
