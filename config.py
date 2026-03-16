# config.py

# This file holds all the configuration variables for the application.
# This makes it easy to change settings without touching the main app logic.

DATABASE_FILE = 'university_schedule.db'
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'csv'}
SECRET_KEY = 'super_secret_key_change_this_later'

# Secure credentials for the Admin Panel
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'Nahary12' # <-- CHANGE THIS

# Mappings for Data Transformation
DAY_MAP = {
    'Mon': 'Monday', 'Tue': 'Tuesday', 'Wed': 'Wednesday',
    'Thu': 'Thursday', 'Fri': 'Friday', 'Sat': 'Saturday', 'Sun': 'Sunday'
}
TIME_MAP = {
    '1': '09:00 - 10:15', '2': '10:25 - 11:40', '3': '12:40 - 13:55',
    '4': '14:05 - 15:20', '5': '15:30 - 16:45', '6': '16:55 - 18:10'
}