import os

import dotenv
import yaml
from gotify import Gotify

from adum import adum

dotenv.load_dotenv(".env")

if os.getenv("ADUM_EMAIL") is None or os.getenv("ADUM_PASSWORD") is None:
    print("Missing ADUM_EMAIL or ADUM_PASSWORD env vars")
    exit(1)

dotenv.load_dotenv(".env")
adum_client = adum(email=os.getenv("ADUM_EMAIL"), password=os.getenv("ADUM_PASSWORD"))

if os.getenv("GOTIFY_BASEURL") is None or os.getenv("GOTIFY_TOKEN") is None:
    use_gotify = False
    print("Skipping Gotify notification, missing env vars")
else:
    use_gotify = True
    gotify = Gotify(
        base_url=os.getenv("GOTIFY_BASEURL"), app_token=os.getenv("GOTIFY_TOKEN")
    )


status = adum_client.get_status()
course_hours = adum_client.get_course_hours()
ical = adum_client.get_icalendar()
icalendar_path = os.getenv("ICAL_OUTPUT_PATH", "./output/advancements.ics")
with open(icalendar_path, "wb") as f:
    f.write(ical.to_ical())
print(f"iCalendar saved to {icalendar_path}")
# Read the last status from the file (handle missing file)

course_status_changes = []
watch_urls = os.getenv("WATCH_URLS", "").split(",")
if not watch_urls or watch_urls == [""]:
    print("No WATCH_URLS provided, skipping course status checks")
else:
    for watch_url in watch_urls:
        watch_url = watch_url.strip()
        formation_name, is_open = adum_client.is_course_open(watch_url)
        if formation_name is None:
            print(f"Could not check course status for URL: {watch_url}")
            continue
        course_status_changes.append((formation_name, is_open))

new_status = {
    "status": status,
    "course_hours": course_hours,
    "courses_open": course_status_changes,
}


try:
    with open("status.yaml", "r") as f:
        last_status = yaml.safe_load(f)
except FileNotFoundError:
    last_status = {}

if last_status != new_status:
    with open("status.yaml", "w") as f:
        yaml.safe_dump(new_status, f)
    print("Status changed, updated status.yaml")
    if use_gotify:
        message_lines = ["ADUM status changed:"]
        if last_status.get("status") != new_status["status"]:
            gotify.create_message(
                title="Status d'inscription ADUM modifié",
                message=f"Statut changé de {last_status.get('status')} à {new_status['status']}",
                priority=5,
            )
        if last_status.get("course_hours") != new_status["course_hours"]:
            gotify.create_message(
                title="Nouvelles heures enregistrées",
                message=f"Heures changées de {last_status.get('course_hours')} à {new_status['course_hours']}",
                priority=5,
            )
        last_courses_open = {
            name: status for name, status in last_status.get("courses_open", [])
        }
        for formation_name, is_open in course_status_changes:
            if last_courses_open.get(formation_name) != is_open:
                status_str = "ouverte" if is_open else "fermée"
                gotify.create_message(
                    title=f"Changement de statut pour {formation_name}",
                    message=f"La formation '{formation_name}' est maintenant {status_str}.",
                    priority=5,
                )
