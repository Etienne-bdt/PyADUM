import datetime
import re

import icalendar
import requests
from bs4 import BeautifulSoup as bs
from bs4 import Tag


class adum(requests.Session):
    def __init__(self, email: str, password: str):
        super().__init__()
        self.email = email
        self.password = password
        self.login_url = "https://adum.fr/index.pl"
        self.landing = self.login()

    def login(self):
        """Logs into the ADUM platform using provided credentials.
        Returns the response object after login.

        Raises:
            ValueError: If email or password is not provided.
        """

        if not self.email or not self.password:
            raise ValueError("Email and password must be provided")

        data = {
            "action": "login",
            "email": self.email,
            "password": self.password,
            "matFormation": "",
        }

        _ = self.get(self.login_url)
        # This sets the CGESID cookie
        response = self.post(self.login_url, data=data)

        return response

    def get_status(self):
        """Fetches the current status from the ADUM platform.

        Returns:
            str: The current status text.
        """
        soup = bs(self.landing.text, "html.parser")
        procedures = soup.find(id="zone_procedures")
        status = procedures.find_all("b")[-1].text
        return status

    def get_formations(self):
        """Fetches the list of formations that the user is enrolled in.
        It fetches formations that the user is :
            - Trying to enroll in
            - Currently enrolled in
            - Completed
        Returns:
            list[str]: A list of formation URLs.
        """
        formation_url = "https://adum.fr/phd/formation/formation_encours.pl"
        response = self.get(formation_url)
        soup = bs(response.text, "html.parser")
        formations = soup.find(id="zone_formulaire")
        tables = formations.find_all("table")
        formations_list = [
            tables[i].find_all("a", href=True) for i in range(len(tables))
        ]
        return [a["href"] for sublist in formations_list for a in sublist]

    def _flatten_contents(self, node):
        result = []
        if isinstance(node, Tag):
            if len(node.contents) == 0:
                for n in node.next_elements:
                    if n.name == "br" or n.name == "hr":
                        continue
                    elif n.name == "b" or n.name == "td" or n.name == "span":
                        break
                    else:
                        result.extend(self._flatten_contents(n))
            else:
                for child in node.contents:
                    if isinstance(child, Tag):
                        result.extend(self._flatten_contents(child))
                    else:
                        result.append(str(child))
        else:
            result.append(str(node))        
        return result

    def get_formation_info(self, formation_url: str):
        """Fetches detailed information about a specific formation.

        Args:
            formation_url (str): The URL of the formation to fetch details for.

        Returns:
            dict: A dictionary containing formation details such as title, status, and dates.
        """
        base_url = "https://adum.fr"
        if not formation_url.startswith("http"):
            formation_url = base_url + formation_url
        response = self.get(formation_url)
        soup = bs(response.text, "html.parser")
        title = soup.find("h2").text.strip()
        # Crop soup to relevant section, it has no parent everything is laid parallel
        # Find the <b> tag with the text "Calendrier :"
        # If found, get the next element in the soup until </td> is encountered.
        # Fetch all relevant info (plain text with NO HTML tags) separated by <br> tags.
        formation_section = soup.find("b", string="Calendrier :")
        if not formation_section:
            # No set time for this formation
            return []
        else:
            sessions = []
            # Different sessions are in <b> tags
            b_tags = formation_section.find_all_next("b")
            for b_tag in b_tags:
                session_info = {}
                # If there is only Séance 1, there is only one session so no need to add it to the title
                if len(b_tags) == 1:
                    session_info["title"] = title
                else:
                    session_info["title"] = title + " - " + b_tag.text.strip()
                next_sibling = b_tag.find_next()
                to_parse = self._flatten_contents(next_sibling)
                to_parse.append("Lien : " + formation_url)
                session_info.update(self._parse_session_info(to_parse))
                sessions.append(session_info)
        return sessions

    def _parse_session_info(self, session) -> dict:
        """Helper method to parse session details list into key-value pairs and extract details.

        Args:
            session (list): The list containing session info.

        Returns:
            dict: A dictionary containing the parsed session details.
        """
        info = {}
        for strings in session:
            if ":" in strings:
                key, value = strings.split(":", 1)
                info.update({key.strip(): value.strip()})
        return info

    def _create_event(self, session, start_date, startend_time):
        """Helper method to create an iCal event from session.
        If an event has two time slots, we take each time slot as an input here and create two events.

        Args:
            session (dict): The session information.
        """
        event = icalendar.Event()

        uid = f"{session.get('title','').replace(' ','_')}_{start_date.strftime('%Y%m%d')}_{startend_time[0].replace('h','').replace(' ','')}"
        event.add("uid", uid)
        event.add("summary", session.get("title", ""))
        event.add("dtstamp", datetime.datetime.now())
        start_time_str, end_time_str = startend_time
        start_time = datetime.datetime.strptime(
            start_time_str.replace("h", ":").replace(" ", ""), "%H:%M"
        ).time()
        end_time = datetime.datetime.strptime(
            end_time_str.replace("h", ":").replace(" ", ""), "%H:%M"
        ).time()
        event.add(
            "dtstart",
            datetime.datetime.combine(start_date.date(), start_time),
        )
        event.add(
            "dtend", datetime.datetime.combine(start_date.date(), end_time)
        )
        event.add("location", session.get("Lieu", ""))
        description = "\n".join(f"{k}: {v}" for k, v in session.items() if k not in ["Date", "title", "Lieu", "Horaire"])
        event.add("description", description)
        return event


    def _convert_session_to_ical(self, session) -> icalendar.Event:
        """Convert a single session dictionary to an iCal event.

        Args:
            session (dict): The session information.

        Returns:
            icalendar.Event: The iCal event object.
        """
        event = icalendar.Event()
        # session already contains parsed info, no need to parse again

        # Example of parsing dates, assuming 'Date' key exists in info
        if "Date" in session:
            date_str = session["Date"]
            try:
                start_date = datetime.datetime.strptime(date_str, "%d-%m-%Y")
                # Horaires stores time as a string of multiples times and sometimes gibberish like : 9h00 12h30 or 9h30 à 12h30 or 9h-12h
                # If there are four xxhxx we should split into two events
                # also handle if the time is 9h and a space after h to escape the date
                startend_time = re.findall(
                    r"(\d{1,2}h\d{0,2})", session.get("Horaire", "")
                )
                #If format is HHh or HHhMM without minutes, add 00 minutes
                for i in range(len(startend_time)):
                    if re.match(r"^\d{1,2}h$", startend_time[i]):
                        startend_time[i] = startend_time[i] + "00"
                if len(startend_time) == 2:
                    return self._create_event(session, start_date, startend_time)
                elif len(startend_time) == 4:
                    event = self._create_event(
                        session, start_date, startend_time[0:2]
                    )
                    event2 = self._create_event(
                        session, start_date, startend_time[2:4]
                    )
                    return [event, event2]
                else:
                    return None  # Unable to parse times
            except ValueError:
                print(f"Invalid date format for session '{session.get('title','')}' with date '{date_str}'")  # Handle invalid date format if necessary
                pass
        # Add other details as description
        
        return event

    def get_icalendar(self) -> str:
        """Get formations data and convert directly to iCal format"""
        formations = self.get_formations() # Your scraping method
        if not formations:
            print("No formations found.")
            return None
        if isinstance(formations, str):
            formations = [formations]
        cal = icalendar.Calendar()
        cal.add("prodid", "-//ADUM Formaitions//mxm.dk//")
        cal.add("version", "2.0")
        cal.add("TZID", "Europe/Paris")

        for formation_url in formations:
            sessions = self.get_formation_info(formation_url)
            for session in sessions:
                event = self._convert_session_to_ical(session)
                if event:
                    if isinstance(event, list):
                        for ev in event:
                            cal.add_component(ev)
                    else:
                        cal.add_component(event)
                else:
                    print(f"Could not convert session to iCal: {session}")
                    continue
        return cal

    def get_course_hours(self):
        """Fetches the total course hours from the ADUM platform.

        Returns:
            str: The total course hours text.
        """
        soup = bs(self.landing.text, "html.parser")
        procedures = soup.find(id="zone_formations")
        hours = procedures.find_next("span").text
        regex = r"/^[^\d]*(\d+)/"
        #Find first number in the string
        match = re.search(r"(\d+)", hours)
        if match:
            return match.group(1)
        return "0"

    def is_course_open(self, formation_url: str):
        """
        Watches for a enrollment re-opening in a course.
        
        :param self: Description
        """
        #Find this <input type="button" value="Demande d'inscription" name="button_1" id="button_1" onclick="_button_1_1()">
        base_url = "https://adum.fr"
        if not formation_url.startswith("http"):
            formation_url = base_url + formation_url
        response = self.get(formation_url)
        soup = bs(response.text, "html.parser")
        name = soup.find("h2").text.strip()
        button = soup.find("input", {"type": "button", "value": "Demande d'inscription"})
        if button:
            return name, True
        return name, False