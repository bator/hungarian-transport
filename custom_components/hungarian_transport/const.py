"""Constants for Hungarian transport."""

DOMAIN = "hungarian_transport"
SERVICE_ELVIRA_BETWEEN = "elvira_between"
SERVICE_FUTAR = "futar"
SERVICE_REALTIME = "realtime"
SERVICE_REBUILD_MAV = "rebuild_mav_index"
CONF_API_KEY = "api_key"
CONF_DAILY_MAV = "daily_mav_index"
DEFAULT_DAILY_MAV = True
# Update notifications (timetable_notify.py) and the daily check's time.
CONF_NOTIFY_UPDATES = "notify_updates"
DEFAULT_NOTIFY_UPDATES = False
CONF_NOTIFY_SERVICE = "notify_service"
CONF_DAILY_TIME = "daily_time"
DEFAULT_DAILY_TIME = "06:00:00"
# The daily MÁV index installs call the notification service too (otherwise
# only the persistent notification): MÁV publishes almost every day.
CONF_NOTIFY_DAILY = "notify_daily_installs"
DEFAULT_NOTIFY_DAILY = False
