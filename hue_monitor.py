#!/usr/bin/env python3

import requests
import json
import sys
import time
import smtplib
import os
#import ssl

from datetime import datetime
from urllib3.exceptions import InsecureRequestWarning

from threading import Timer

from email.utils import formataddr
from email.header import Header
from email.message import EmailMessage

from configparser import ConfigParser

#
# Suppress only the single warning from urllib3 needed.
#
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

low_chr  = chr(0x2581)
high_chr = chr(0x2588)

plot     = list(96*low_chr)
timeline = (10*' ').join(['0', '03', '06', '09', '12', '15', '18', '21', '24'])

date_in_format  = "%Y-%m-%dT%H:%M:%S.%fZ"

day_format      = "%d.%m.%y"
time_format     = "%H:%M:%S"
date_out_format = f"{day_format} {time_format}"

today = datetime.now().strftime(day_format)

#
# Read configuration from matching ini file in current directory
#
config_file = os.path.splitext(os.path.basename(__file__))[0] + '.ini'

#
# Initialize settings. Customize in config file
#
HueServices = [
    {
        "name":         "device_power",
        "description":  "Battery Level",
        "section":      "power_state",
        "value":        "battery_level",
        "unit":         "%"
    },
    {
        "name":         "light_level",
        "description":  "Licht Sensor",
        "section":      "light",
        "value":        "light_level",
        "unit":         " Lux"
    },
    {
        "name":         "temperature",
        "description":  "Temperature Sensor",
        "section":      "temperature",
        "value":        "temperature",
        "unit":         "°C"
    },
    {
        "name":         "motion",
        "description":  "Motion Sensor",
        "section":      "motion",
        "value":        "motion",
        "unit":         ""
    }
]

LOGsettings = {
    "report":           "Sending daily report for date {}",
    "motion_detected":  "Motion detected by {} sensor",
    "mail_sent":        "Message sent",
    "mail_failed":      "Message delivery failed: {}",
    "mail_restricted":  "Message delivery restricted",
    "cfg_not_found":    "Configuration file not found: {}",
    "cfg_write_error":  "Error reading configuration: {}",
    "cfg_read_error":   "Error writing configuration: {}",
    "invalid_response": "Invalid response from {}",
    "timeout":          "Connection to {} timed out",
    "exception":        "Unexpected error: {}",
    "suspended":        "Service {} temporarily disabled",
    "enabled":          "Service {} re-enabled"
}

SMTPsettings = {
    "user":       "user@mail.com",
    "name":       "Hue Bridge",
    "password":   "password",
    "server":     "smtp.mail.com",
    "port":       587
}

HUEsettings = {
    "ip":         "192.168.178.100",
    "key":        None
}

MOTIONsettings = {
    "notify":       True,
    "except":       [],
    "except_daily": [],
    "suspend":      True
}

MSGsettings = {
    "report_subject": "Daily Reportt",
    "alert_subject":  "Motion Alert",
    "report_text":    "Sensor data of {}",
    "alert_text":     "Sensor \"{}\" detcted a motion at {}.",
    "recipients":     []
}


def read_config():
    if not os.path.exists(config_file):
        log("cfg_not_found", argument=config_file)
        return None

    config = None

    try:
        config = ConfigParser()
        config.read([os.path.abspath(config_file)])

        #
        # Customize service descriptions to suit your preference/language
        #
        for service in HueServices:
            value = config.get("Service Descriptions", service["name"])
            if value:
                service["description"] = value

        #
        # Customize settings for logging
        #
        for option in config.options("Logging"):
            value = config.get("Logging", option)
            if value:
                LOGsettings[option] = value

        #
        # Mail account settings
        #
        for option in config.options("Mail Account"):
            value = config.get("Mail Account", option)
            if value:
                SMTPsettings[option] = value

        #
        # Hue Bridge IP and User Name/API Key
        #
        # Specifiy if user has been already created, else set to 'None'.
        # Remeber you muss have physcal access to the Hue Bridge to create
        # a new user/API Key
        #
        HUEsettings["ip"]  = config.get("Hue Bridge", "ip")
        HUEsettings["key"] = config.get("Hue Bridge", "key")

        #
        # Setnotify on motion events to True if you want alert meesages on motion detection
        # except dates specify periods when no alert is sent
        #
        for option in config.options("Motion Alert"):
            value = config.get("Motion Alert", option)
            if value:
                if option == "notify" or option == "suspend":
                    MOTIONsettings[option] = config.getboolean("Motion Alert", option)
                else:
                    MOTIONsettings[option] = [ (x.strip(), y.strip()) for x, y in [ tuple(x.split("-")) for x in [ x.strip() for x in value.split(",") if x != "" ] ] ]
        #
        # except dates must be specified as comma separated intervals in the format
        # "%d.%m.%y %H:%M:%S - %d.%m.%y %H:%M:%S" (date_out_format) in the ini file
        # with %H, %M, or %S being optional parameters
        #

        #
        # E-Mail Settings (requires customization)
        # If HueReceivers list is empty, no E-mail will be sent
        #
        for option in config.options("Messaging"):
            value = config.get("Messaging", option)
            if value:
                if option == "recipients":
                    MSGsettings[option] = [ r.strip() for r in value.split(',') ]
                else:
                    MSGsettings[option] = value

    except Exception as e:
        log("cfg_read_error", argument=e)

    return config


def save_key(config, key):
    config.set("Hue Bridge", "key", key)

    try:
        with open(config_file, 'w') as configfile:
            config.write(configfile)
    except Exception as e:
        log("cfg_write_error", argument=e)


def log(message, argument=None):
    if message in LOGsettings.keys():
        if argument and "{}" in LOGsettings[message]:
            print(LOGsettings[message].format(argument))
        else:
            print(LOGsettings[message])
    else:
        print(message)


def utc2local(utc):
    epoch  = time.mktime(utc.timetuple())
    offset = datetime.fromtimestamp(epoch) - datetime.utcfromtimestamp(epoch)
    return utc + offset


def check_date(date, interval, daily=False):
    if daily:
        format = time_format
    else:
        format = date_out_format

    try:
        #compare = datetime.now()
        compare = datetime.strptime(date, format)

        for first, last in interval:
            start = datetime.strptime(first, format[:len(first)])
            end = datetime.strptime(last, format[:len(last)])

            if start <= compare <= end:
                return True
    except:
        pass

    return False


def html_report(bridge, date):
    HTMLheader = """
	<head>
		<style>
			table {
				border-collapse: collapse;
				width: 100%;
			}

			th, td {
				text-align: left;
				padding: 8px;
			}

			tr:nth-child(even) {
				background-color: #D6EEEE;
			}
		</style>
	</head>"""

    heading = MSGsettings["report_text"].format(date)
    html = f"<html>{HTMLheader}\n\t<body>\n\t\t<h1>{heading}</h1>\n"

    for sensor in bridge.sensors:
        heading = f"{sensor.product_name}: {sensor.name}"
        html +=  f"\t\t<h2>{heading}</h2>\n"

        power_service = ([ s for s in sensor.services if s.name == "device_power" ] or [None])[0]
        if power_service:
            power_service.update()
            log(power_service.prompt())

            html +=  f"\t\t<p>{power_service.description}: {power_service.value}{power_service.unit}</p>\n"

        maxrows = 0
        rows = []

        for service in sensor.services:
            if service.name == "device_power":
                continue
            if len(service.data) > maxrows:
                maxrows = len(service.data)

        for i in range(maxrows + 1):
            rows.append("\t\t\t\t")

        for service in sensor.services:
            if service.name == "device_power":
                continue

            rows[0] = rows[0] + f"<th>{service.description}</th>"
            i = 1

            for changed, value in service.data:
                if changed.strftime(day_format) == date:
                    #rows[i] = rows[i] + f"<td><tt>{changed.strftime(time_format)} {value:>5}{service.unit}</tt></td>"
                    rows[i] = rows[i] + f"<td>{changed.strftime(time_format)} {value:}{service.unit}</td>"
                    i = i + 1

            while(i <=  maxrows):
                rows[i] = rows[i] + "<td></td>"
                i = i + 1

        for i in range(maxrows + 1):
            rows[i] = "\t\t\t<tr>\n" + rows[i] + "\n\t\t\t</tr>\n"

        rows[0] = "\t\t<table>\n" + rows[0]
        rows[-1] = rows[-1] + "\t\t</table>\n"

        for row in rows:
            html += row

    html += "\t</body>\n</html>"

    return html


def sendmail(recipients, subject, msg_body, subtype=None):
    if not recipients:
        return

    msg = EmailMessage()

    if SMTPsettings["name"]:
        msg['From']  = formataddr((str(Header(SMTPsettings["name"], 'utf-8')), SMTPsettings["user"]))
    else:
        msg['From']  = SMTPsettings["user"]

    msg['To']      = ", ".join(recipients)
    msg['Subject'] = subject

    if subtype == "html":
        msg.set_content(msg_body, subtype="html")
    else:
        msg.set_content(msg_body)

    try:
        #context = ssl.create_default_context()
        with smtplib.SMTP(SMTPsettings["server"], port=SMTPsettings["port"]) as server:
            #server.starttls(context=context)
            server.starttls()
            server.login(SMTPsettings["user"], SMTPsettings["password"])
            server.sendmail(SMTPsettings["user"], recipients, msg.as_string())

        log("mail_sent")

    except Exception as e:
        log("mail_failed", argument=e)


def on_change(bridge, sensor, service, value, changed):
    global today, plot

    # Let's see if a day has passed. It's time to send a new report then
    date = datetime.now().strftime(day_format)
    #date = changed.strftime(day_format)
    if date != today:
        # Plot the motion profile of the passed day
        log(today)
        log("".join(plot))
        log(timeline)

        # Reset the motion profile
        plot = list(96*low_chr)

        # Send the daily report
        log("report", argument=today)
        html = html_report(bridge, today)
        sendmail(MSGsettings["recipients"], MSGsettings["report_subject"], html, subtype="html")

        # The first event on a new day will reset the data store of all services
        for sensor in bridge.sensors:
            for service in sensor.services:
                service.reset()

        today = date

    if service.name == 'motion' and value:
        log("motion_detected", argument=sensor.name)
        if MOTIONsettings["notify"]:
            # Send alert message if no exceptions apply
            if not check_date(changed.strftime(date_out_format), MOTIONsettings["except"]) and not check_date(changed.strftime(time_format), MOTIONsettings["except_daily"], daily=True):
                sendmail(MSGsettings["recipients"], MSGsettings["alert_subject"], MSGsettings["alert_text"].format(sensor.name, changed.strftime(time_format)))
            else:
                log("mail_restricted")

        # Update the motion profile
        h = int(changed.strftime("%H"))
        m = int(changed.strftime("%M"))
        plot_index = h*4 + m//15
        plot[plot_index] = high_chr


class Bridge():

    def __init__(self, ip_address, username=None, onchange=None):
        self.ip            = ip_address
        self.username      = username or self.__username()
        self.onchange      = onchange

        self.devices       = self.__devices()
        self.sensors       = [ Sensor(device["id"], device["name"], self) for device in self.devices if device["product_name"] == "Hue motion sensor" ]

        # We'll need to customize this if name changes
        self.product_name  = "Hue Bridge"
        self.id, self.name = ([ (device["id"], device["name"]) for device in self.devices if device["product_name"] == self.product_name ] or [ (None, None) ])[0]

    def __username(self):
        # if no user name /API key is specified, we'll create one
        url = f"https://{self.IP}/api"
        my_obj = {
            "devicetype": "app_name#instance_name",
            "generateclientkey": True
            }

        username = None

        # do this endlessly until successful (i.e. someone pressed the button)
        while(username is None):
            try:
                response = requests.post(url, json=my_obj, timeout=3, verify=False)

                if response and response.status_code == 200:
                    data = response.json()[0]

                    if "error" in data.keys():
                        input(data["error"]["description"])
                    elif "success" in data.keys():
                        username = data["success"]["username"]

                else:
                    log("invalid_response", argument=url)

            except Exception as e:
                log("exception", argument=e)
                break

        return username

    def __devices(self):
        url = f"https://{self.ip}/clip/v2/resource/device"
        headers = { "hue-application-key": self.username }

        device_parms = []

        try:
            response = requests.get(url, headers=headers, timeout=3, verify=False)

            if response and response.status_code == 200:
                devices = response.json()["data"]

                for device in devices:
                    if "product_data" and "metadata" in device.keys():
                        device_parm = dict(zip(["id", "product_name", "name"], [device["id"], device["product_data"]["product_name"], device["metadata"]["name"]]))

                        device_parms.append(device_parm)
            else:
                log("invalid_response", argument=url)

        except Exception as e:
            log("exception", argument=e)

        return device_parms

    def update(self):
        self.devices = self.__devices()

        for sensor in self.sensors:
           for service in sensor.services:
                del service
           del sensor

        self.sensors = [ Sensor(device["id"], device["name"], self) for device in self.devices if device["product_name"] == "Hue motion sensor" ]

    def events(self):
        url = f"https://{self.ip}/eventstream/clip/v2"
        headers = {
            "hue-application-key": self.username,
            "Accept": "text/event-stream"
            }

        with requests.Session() as session:
            while(True):
                try:
                    response = session.get(url, headers=headers, timeout=86400, stream=True, verify=False)

                    if response and response.status_code == 200:
                        for line in response.iter_lines():
                            if line:
                                line = line.decode('utf-8')

                                if line.startswith("data:"):
                                    data = json.loads(line.split(":", 1)[1].strip())
                                    if not "update" == data[0]["type"]:
                                        continue

                                    event_data = data[0]["data"][0]
                                    if not "owner" in event_data.keys():
                                        continue

                                    owner_id = event_data["owner"]["rid"]
                                    sensor = ([ s for s in self.sensors if s.id == owner_id ] or [None])[0]
                                    if not sensor:
                                        continue

                                    service_name = event_data["type"]
                                    service = ([ s for s in sensor.services if s.name == service_name ] or [None])[0]
                                    if not service:
                                        continue

                                    if service.report_name in event_data[service.section_name]:
                                        service_data = event_data[service.section_name][service.report_name]
                                    else:
                                        service_data = event_data[service.section_name]

                                    #value = service_data[service.value_name]
                                    if service.value_name in service_data.keys():
                                        value = service_data[service.value_name]
                                    else:
                                        value = None

                                    if "changed" in service_data.keys():
                                        changed = utc2local(datetime.strptime(service_data["changed"], date_in_format))
                                    else:
                                        changed = datetime.now()

                                    if self.onchange:
                                        self.onchange(self, sensor, service, value, changed)

                                    service.update(value=value, changed=changed)
                                    log(service.prompt())

                    else:
                        log("invalid_response", argument=url)

                except requests.exceptions.RequestException as e:
                    if "timed out" in str(e):
                        log("timeout", argument=url)
                    else:
                        log("exception", argument=e)
                        #sys.exit(1)
                #except requests.exceptions.ChunkedEncodingError as e:
                #    log("exception", argument=e)
                except KeyError:
                    pass
                except KeyboardInterrupt:
                    break
                #except Exception as e:
                #    log("exception", argument=e)


class Sensor():

    def __init__(self, id, name, owner):
        self.id           = id
        self.name         = name
        self.owner        = owner

        # We'll need to customize this if name changes
        self.product_name = "Hue motion sensor"

        self.__ip         = owner.ip
        self.__username   = owner.username

        self.services     = self.__services()

    def __services(self):
        url = f"https://{self.__ip}/clip/v2/resource/device/{self.id}"
        headers = { "hue-application-key": self.__username }

        service_list = []

        try:
            response = requests.get(url, headers=headers, timeout=3, verify=False)
            if response and response.status_code == 200:
                device = response.json()["data"][0]
                for service in device["services"]:
                    index = ([ i for i, svc in enumerate(HueServices) if svc["name"] == service["rtype"] ] or [None])[0]

                    if index is not None:
                        s = Service(service["rid"], HueServices[index], self)
                        service_list.append(s)
                    else:
                        continue

            else:
                log("invalid_response", argument=url)

        except Exception as e:
            log("exception", e)
            pass

        return service_list


class Service():

    def __init__(self, id, service, owner):
        self.id = id

        self.name         = service["name"]
        self.description  = service["description"]
        self.section_name = service["section"]
        self.report_name  = service["value"] + "_report"
        self.value_name   = service["value"]
        self.unit         = service["unit"]

        self.owner        = owner

        self.__ip         = owner.owner.ip # bridge.ip()
        self.__username   = owner.owner.username # bridge.username()

        self.__url        = f"https://{self.__ip}/clip/v2/resource/{self.name}/{self.id}"
        self.__headers    = {"hue-application-key": self.__username}

        self.enabled      = self.is_enabled()

        self.value        = None
        self.changed      = None
        self.data         = []
        self.update()

    def prompt(self):
        #timestamp = (self.changed or datetime.now()).strftime(date_out_format)
        return f"{self.changed.strftime(date_out_format)} {self.owner.name} {self.description}: {self.value}{self.unit}"

    def reset(self):
        self.data = []

    def is_enabled(self):
        enabled = None

        try:
            response = requests.get(self.__url, headers=self.__headers, timeout=3, verify=False)
            if response and response.status_code == 200:
                data = response.json()["data"][0]
                if "enabled" in data.keys():
                    enabled = data["enabled"]
            else:
                log("invalid_response", argument=self.__url)
        except Exception as e:
            log("exception", argument=e)

        return enabled

    def enable(self, set=True):
        if self.enabled is None: # service == device_power?
            return False

        try:
            response = requests.put(self.__url, json={"enabled": set}, headers=self.__headers, timeout=3, verify=False)
            if response and response.status_code == 200:
                self.enabled = set
                return True
            else:
                log("invalid_response", argument=self.__url)

        except Exception as e:
            log("exception", argument=e)

        return False

    def update(self, value=None, changed=None):
        if changed is None or value is None:

            try:
                response = requests.get(self.__url, headers=self.__headers, timeout=3, verify=False)

                if response and response.status_code == 200:
                    data = response.json()["data"][0]

                    if self.report_name in data[self.section_name].keys():
                        service_data = data[self.section_name][self.report_name]
                    else:
                        service_data = data[self.section_name]

                    if self.value_name in service_data.keys():
                        value = service_data[self.value_name]
                    else:
                        value = None

                    if "changed" in service_data.keys():
                        changed = utc2local(datetime.strptime(service_data["changed"], date_in_format))
                    else:
                        changed = datetime.now()

                else:
                    log("invalid_response", argument=self.__url)
                    return

            except Exception as e:
                log("exception", argument=e)
                return

        self.data.append((changed, value))

        self.value   = value
        self.changed = changed

        return


class SuspendTimer(Timer):
    def run(self):
         while not self.finished.wait(self.interval):
             self.function(*self.args, **self.kwargs)


def check_exceptions(bridge):
    if check_date(datetime.now().strftime(date_out_format), MOTIONsettings["except"]) or check_date(datetime.now().strftime(time_format), MOTIONsettings["except_daily"], daily=True):
         for sensor in bridge.sensors:
             for service in sensor.services:
                 if service.enabled:
                     response = service.enable(False)
                     if response:
                         log("suspended", argument=service.name)
    else:
         for sensor in bridge.sensors:
             for service in sensor.services:
                 if service.enabled is not None and not service.enabled:
                     response = service.enable()
                     if response:
                         log("enabled", argument=service.name)


if __name__ == "__main__":
    # Read settings from config file
    cfg = read_config()
    if not cfg:
       log("No config")
       sys.exit(1)

    # Instantiate our bridge
    bridge = Bridge(HUEsettings["ip"], username=HUEsettings["key"], onchange=on_change)
    if not bridge:
       log("Hue Bridge not accessible")
       sys.exit(1)

    # If a new key was created, save it in the ini file
    if not HUEsettings["key"]:
       save_key(cfg, bridge.username)

    # Print the last status of all connected sensors & services
    if bridge.sensors:
        for sensor in bridge.sensors:
            status = []
            log(f"{sensor.product_name}: {sensor.name}")

            for service in sensor.services:
                status.append(service.prompt())

            for line in sorted(status):
                log(line)

    if MOTIONsettings["suspend"]:
        timer = SuspendTimer(60, check_exceptions, args=(bridge,))
        timer.start()
    else:
        timer = None

    # Listen for events
    bridge.events()

    if timer:
        timer.cancel()
