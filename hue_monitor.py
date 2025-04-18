#!/usr/bin/env python3

import requests
import json
import sys
import time
import smtplib
import socket
import fcntl
import struct
import os
import signal
#import base64
#import ssl

import pandas as pd

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

#
# Handle SIGTERM signal
#
def sigterm_handler(_signo, _stack_frame):
    sys.exit(0)

signal.signal(signal.SIGINT, sigterm_handler)
signal.signal(signal.SIGTERM, sigterm_handler)

#
# Motion profile has 24 hrs. with a 15 min. time grid
# ==> 96 discrete values (on/off) per day
#
low_chr  = chr(0x2581)
high_chr = chr(0x2588)
plot     = list(96*low_chr)
timeline = (10*' ').join(['0', '03', '06', '09', '12', '15', '18', '21', '24'])

#
# HTML header to be used in HTML formatted report
#
HTMLheader = """
  <head>
    <style>
      table {{
        border-collapse: collapse;
        width: 100%;
      }}

      th, td {{
        text-align: left;
        padding: 8px;
      }}

      tr:nth-child(even) {{
        background-color: #D6EEEE;
      }}
    </style>
  </head>
"""

#
# Date format used by sensor
#
date_in_format  = "%Y-%m-%dT%H:%M:%S.%fZ"

#
# Date format used for reporting & logging
#
day_format      = "%d.%m.%y"
day_format_long = "%a, %d.%m.%y"
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
    "motion_detected":  "Motion detected by sensor {}",
    "msg_sent":         "Message sent",
    "msg_failed":       "Message delivery failed: {}",
    "msg_suspended ":   "Message delivery suspended at specified time interval",
    "cfg_not_found":    "Configuration file not found: {}",
    "cfg_write_error":  "Error writing configuration: {}",
    "cfg_read_error":   "Error reading configuration: {}",
    "invalid_response": "Invalid response from {}",
    "timeout":          "Connection to {} timed out",
    "exception":        "Unexpected error: {}",
    "suspended":        "Service '{}' temporarily disabled",
    "enabled":          "Service '{}' re-enabled",
    "no_config":        "No configuration",
    "no_response":      "No response or no IP address"
}

REPORTsettings = {
    "report_subject":   "Daily Report",
    "report_to":        [],
    "report_header":    "Sensor data of {}",
    "bridge_ip":        "Hue bridge IP address: {}",
    "notify_on_motion": "Send a notification when movement is detected: {}",
    "suspend_services": "Suspend services while notifications are suppressed: {}",
    "suppress_period":  "Suppress notifications (Period): {}",
    "suppress_daily":   "Suppress notifications (Daily): {}",
    "on":               "On",
    "off":              "Off",
    "attach":		True,
    "store":		None
}

SMTPsettings = {
    "user":             "user@mail.com",
    "name":             "Hue Bridge",
    "password":         "password",
    "server":           "smtp.mail.com",
    "port":             587
}

HUEsettings = {
    "ip":               "192.168.178.100",
    "key":              None
}

MOTIONsettings = {
    "notify":           False,
    "notify_to":        "",
    "notify_subject":   "Motion Alert",
    "notify_text":      "Sensor \"{}\" detected a motion at {}.",
    "except":           "",
    "except_daily":     "",
    "suspend":          True
}


def get_ip_address(ifname): #ifname = 'eth0' or 'wlan0'
    ip = ''

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        ip = socket.inet_ntoa(fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack('256s', bytes(ifname[:15], 'utf-8'))
        )[20:24])
    except:
        pass

    finally:
        s.close()

    return ip


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
        HUEsettings["ip"]  = config.get("Hue Bridge", "ip")
        HUEsettings["key"] = config.get("Hue Bridge", "key")

        #
        # Set notify on motion events to True if you want alert meesages on motion detection
        # except dates specify periods when no alert is sent
        #
        if config.has_section("Motion Alert"):
            #for option in ("notify", "suspend", "except", "except_daily"):
            for option in config.options("Motion Alert"):
                value = config.get("Motion Alert", option)
                if value:
                    if option == "notify" or option == "suspend":
                        MOTIONsettings[option] = config.getboolean("Motion Alert", option)
                    elif option == "notify_to" and "@" in value:
                        MOTIONsettings[option] = [ r.strip() for r in value.split(',') ]
                    else:
                        MOTIONsettings[option] = value
        #
        # except dates must be specified as comma separated intervals in the format
        # "%d.%m.%y %H:%M:%S - %d.%m.%y %H:%M:%S" (date_out_format) in the ini file
        # with %H, %M, or %S being optional parameters
        #

        #
        # Report Settings
        # If receivers ("report_to") list is empty, no E-mail will be sent
        #
        for option in config.options("Reporting"):
            value = config.get("Reporting", option)
            if value:
                if option == "attach":
                    REPORTsettings[option] = config.getboolean("Reporting", option)
                elif option == "report_to":
                    REPORTsettings[option] = [ r.strip() for r in value.split(',') ]
                else:
                    REPORTsettings[option] = value

    except Exception as e:
        log("cfg_read_error", argument=e)

    return config


def read_sensor_config(sensor_name):
    #if not os.path.exists(config_file):
    #    log("cfg_not_found", argument=config_file)
    #    return None

    #
    # Set defaults
    #
    settings = MOTIONsettings

    try:
        config = ConfigParser()
        config.read([os.path.abspath(config_file)])

        #
        # Set notify on motion events to True if you want alert meesages on motion detection
        # except dates specify periods when no alert is sent
        #
        if config.has_section(sensor_name):
            #for option in ("notify", "suspend", "except", "except_daily"):
            for option in config.options(sensor_name):
                value = config.get(sensor_name, option)
                if value:
                    if option == "notify" or option == "suspend":
                        settings[option] = config.getboolean(sensor_name, option)
                    else:
                        settings[option] = value
        #
        # except dates must be specified as comma separated intervals in the format
        # "%d.%m.%y %H:%M:%S - %d.%m.%y %H:%M:%S" (date_out_format) in the ini file
        # with %H, %M, or %S being optional parameters
        #
    except Exception as e:
        log("cfg_read_error", argument=e)

    return settings


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


def check_date(date, range, daily=False):
    if daily:
        format = time_format
    else:
        format = date_out_format

    try:
        interval = [ (x.strip(), y.strip()) for x, y in [ tuple(x.split("-")) for x in [ x.strip() for x in range.split(",") if x != "" ] ] ]

        compare = datetime.strptime(date, format)

        for first, last in interval:
            start = datetime.strptime(first, format[:len(first)])
            end = datetime.strptime(last, format[:len(last)])

            if start <= compare <= end:
                return True
    except:
        pass

    return False


def html_report(bridge, date=today):
    try:
        html = \
f"""
<html>{HTMLheader}
  <body>
    <h1>{REPORTsettings["report_header"].format(date)}</h1>
    <p>{REPORTsettings['bridge_ip'].format(get_ip_address('wlan0'))}</p>
"""

        for sensor in bridge.sensors:
            power_service = [s for s in sensor.services if s.name == "device_power"][0]
            power_service.update()

            html += \
f"""
    <h2>{sensor.product_name}: {sensor.name}</h2>
    <p>{REPORTsettings['notify_on_motion'].format(REPORTsettings['on'] if sensor.settings['notify'] else REPORTsettings['off'])}</p>
    <p>{REPORTsettings['suspend_services'].format(REPORTsettings['on'] if sensor.settings['suspend'] else REPORTsettings['off'])}</p>
    <p>{REPORTsettings['suppress_period'].format(sensor.settings['except'])}</p>
    <p>{REPORTsettings['suppress_daily'].format(sensor.settings['except_daily'])}</p>
    <p>{power_service.description}: {power_service.data[-1][1]}{power_service.unit}</p>
    {{}}
"""

        html += \
f"""
  </body>
</html>
"""

    except Exception as e:
        log(str(e))
        return None

    return html


def sendmail(recipients, subject, msg_body, subtype=None, attachments=None):
    #assert isinstance(recipients, list)

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

    for attachment in attachments or []:
        if not "path" in attachment:
            continue

        filename = os.path.basename(attachment["path"])

        if os.path.isfile(attachment["path"]):
            with open(attachment["path"], "rb") as f:
                data = f.read()
        elif "data" in attachment:
            data = attachment["data"]
        else:
            continue

        if data:
            msg.add_attachment(
                data,
                filename=filename,
                maintype='text',
                subtype='csv'
            )

    #context = ssl.create_default_context()
    with smtplib.SMTP(SMTPsettings["server"], port=SMTPsettings["port"]) as server:
        #server.starttls(context=context)
        server.starttls()
        server.login(SMTPsettings["user"], SMTPsettings["password"])
        server.sendmail(SMTPsettings["user"], recipients, msg.as_string())


def report(bridge, reset=False):
    global plot

    # Plot the motion profile of the passed day
    log(today)
    log("".join(plot))
    log(timeline)

    # Show current power status of all sensors
    for sensor in bridge.sensors:
        for service in sensor.services:
            if service.name == "device_power":
                service.update()
                log(service.prompt())
                break

    # Send the daily report
    log("report", argument=today)

    html_body = html_report(bridge)
    html_tables = []

    # Start with an empty list of attachments
    attachments = []

    # Transform collected sensor data into DataFrame, CSV format
    for sensor in bridge.sensors:
        service_dict = {}

        for service in sensor.services:
            if service.name == "device_power":
                continue

            #service_dict[service.description] = [f"{changed.strftime(time_format)} {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']}{service.unit}" for changed, value in service.data]
            service_dict[service.description] = [f"{changed.strftime(date_out_format)} {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']}{service.unit}" for changed, value in service.data]

        df = pd.DataFrame({key:pd.Series(value) for key, value in service_dict.items()})

        html_table = df.to_html(index=False, header=True, na_rep='', border=0)
        html_tables.append(html_table)

        # Attch sensor data or save as file?
        if REPORTsettings["attach"] or REPORTsettings["store"]:
            attachment = {}

            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            file_path = f"{bridge.name}_{sensor.name}_{timestamp}.csv"

            # Save sensor data locally?
            if REPORTsettings["store"]:
                try:
                    # Is it the name of an existing file?
                    if os.path.isfile(REPORTsettings["store"]):
                        file_path = REPORTsettings["store"]
                    # Is it the name of an existing directory?
                    elif os.path.isdir(REPORTsettings["store"]):
                        file_path = os.path.join(REPORTsettings["store"], file_path)
                    # Let's assume that a "." in the basename specifies the name of (not yet existing) file.
                    elif "." in  os.path.basename(REPORTsettings["store"]):
                        file_path = REPORTsettings["store"]
                        # Create the parent directory if neccessary.
                        if os.path.sep in REPORTsettings["store"] and not os.path.isdir(REPORTsettings["store"].rsplit(os.path.sep, 1)[0]):
                            os.makedirs(REPORTsettings["store"].rsplit(os.path.sep, 1)[0])
                    # If it's neither a file nor an exisitng directory we'll create a directory with the specified name
                    else:
                        os.makedirs(REPORTsettings["store"])
                        file_path = os.path.join(REPORTsettings["store"], file_path)

                    if os.path.isfile(file_path):
                        df.to_csv(file_path, mode='a', sep='\t', index=False, header=False)
                    else:
                        df.to_csv(file_path, sep='\t', index=False, header=True)

                except Exception as e:
                    log(str(e))
            else:
                attachment["data"] = df.to_csv(sep='\t', index=False, header=True).encode("utf-8")

            if REPORTsettings["attach"]:
                attachment["path"] = file_path
                attachments.append(attachment)

    if html_body:
        # Insert html_tables into html_body
        html_body = html_body.format(*html_tables)

        try:
            sendmail(REPORTsettings["report_to"], REPORTsettings["report_subject"], html_body, subtype="html", attachments=attachments)
            log("msg_sent")

        except Exception as e:
            log("msg_failed", argument=e)

    # Reset the motion profile
    if datetime.now().strftime(day_format) != today or reset:
        plot = list(96*low_chr)

    # Reset the data store of all services
    if reset:
        for sensor in bridge.sensors:
            for service in sensor.services:
                service.reset()


def on_change(bridge, sensor, service, changed, value):
    if service.name == 'motion' and value:
        log("motion_detected", argument=sensor.name)
        if sensor.settings["notify"]:

            # Send alert message if no exceptions apply
            if not check_date(changed.strftime(date_out_format), sensor.settings["except"]) and not check_date(changed.strftime(time_format), sensor.settings["except_daily"], daily=True):
                msg_text = sensor.settings["notify_text"].format(sensor.name, changed.strftime(time_format))

                try:
                    if sensor.settings["notify_to"] and not isinstance(sensor.settings["notify_to"], list):
                        headers = {
                            "Title": sensor.settings["notify_subject"]
                        }
                        #auth_string= f"{username}:{password}"
                        #headers["Authorization"] = "Basic " + base64.b64encode(auth_string.encode()).decode()
                        r = requests.post(sensor.settings["notify_to"], data=msg_text, headers=headers)
                        r.raise_for_status()
                    elif sensor.settings["notify_to"]:
                        sendmail(sensor.settings["notify_to"], sensor.settings["notify_subject"], msg_text)
                    elif REPORTsettings["report_to"]:
                        sendmail(REPORTsettings["report_to"], sensor.settings["notify_subject"], msg_text)

                    log("msg_sent")

                except Exception as e:
                    log("msg_failed", argument=e)

            else:
                log("msg_restricted")

        # Update the motion profile
        h = int(changed.strftime("%H"))
        m = int(changed.strftime("%M"))
        plot_index = h*4 + m//15
        plot[plot_index] = high_chr


class Bridge():

    def __init__(self, ip_address, username=None, onchange=None):
        self.ip            = ip_address
        self.onchange      = onchange

        try:
            self.username      = username or self.__username()
            self.devices       = self.__devices()

            self.sensors       = [ Sensor(device["id"], device["name"], self) for device in self.devices if device["product_name"] == "Hue motion sensor" ]
        except:
            raise

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

            except:
                raise

        return username

    def __devices(self):
        url = f"https://{self.ip}/clip/v2/resource/device"
        headers = { "hue-application-key": self.username }

        device_parms = []

        try:
            response = requests.get(url, headers=headers, timeout=3, verify=False)

            if response.status_code == 200:
                devices = response.json()["data"]

                for device in devices:
                    if "product_data" and "metadata" in device.keys():
                        device_parm = dict(zip(["id", "product_name", "name"], [device["id"], device["product_data"]["product_name"], device["metadata"]["name"]]))
                        device_parms.append(device_parm)
            else:
                log("invalid_response", argument=url)
                response.raise_for_status()

        except:
            raise

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

                                    if service.value_name in service_data.keys():
                                        value = service_data[service.value_name]
                                    else:
                                        value = None

                                    if "changed" in service_data.keys():
                                        changed = utc2local(datetime.strptime(service_data["changed"], date_in_format))
                                    else:
                                        changed = datetime.now()

                                    if self.onchange:
                                        self.onchange(self, sensor, service, changed, value)

                                    service.update(changed, value)
                                    log(service.prompt())

                    else:
                        log("invalid_response", argument=url)

                except requests.exceptions.ConnectionError:
                    raise

                except requests.exceptions.Timeout as e:
                    log("timeout", argument=e)
                    continue

                except requests.exceptions.RequestException as e:
                    if "timed out" in str(e):
                        log("timeout", argument=url)
                        continue
                    else:
                        raise

                except KeyError:
                    pass

                except KeyboardInterrupt:
                    break


class Sensor():

    def __init__(self, id, name, owner):
        self.id           = id

        # This is the invidual name the sensor was given in the Hue app
        self.name         = name

        # The Hue bridge the sensor  belongs to
        self.owner        = owner

        # We'll need to customize this if name changes
        self.product_name = "Hue motion sensor"

        self.__ip         = owner.ip
        self.__username   = owner.username

        self.services     = self.__services()

        # Read indivisual settings from config file - else use dafaults
        self.settings     = read_sensor_config(self.name)

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

        self.data         = []
        self.update()

    def prompt(self):
        if not self.data:
            return f"{datetime.now().strftime(date_out_format)} {self.owner.name} {self.description}: N/A"

        changed, value = self.data[-1]
        return f"{changed.strftime(date_out_format)} {self.owner.name} {self.description}: {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']}{self.unit}"

    def reset(self):
        self.data = []
        self.update()

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

    def update(self, changed=None, value=None):
        # query latest knwon state or set state if specified
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

        return


class MyTimer(Timer):
    def run(self):
         while not self.finished.wait(self.interval):
             self.function(*self.args, **self.kwargs)


def timer_event(bridge):
    global  today

    # Let's see if a day has passed. It's time to send a new report and set the date
    if datetime.now().strftime(day_format) != today:
        report(bridge, reset=True)
        today = datetime.now().strftime(day_format)

    for sensor in bridge.sensors:
        if sensor.settings["suspend"]:
            if check_date(datetime.now().strftime(date_out_format), sensor.settings["except"]) or check_date(datetime.now().strftime(time_format), sensor.settings["except_daily"], daily=True):
                for service in sensor.services:
                    if service.enabled:
                       if service.enable(False):
                            log("suspended", argument=service.name)
            else:
                for service in sensor.services:
                    if service.enabled is False:
                        if service.enable():
                            log("enabled", argument=service.name)


def check(ip):
    try:
        requests.head(f"http://{ip}", timeout=1)
        return True
    except requests.ConnectionError:
        return False


if __name__ == "__main__":
    # Read settings from config file
    cfg = read_config()
    if not cfg:
        log("no_config")
        sys.exit(0)

    time.sleep(15) # wait 15 secs. for bridge to initalize and obtain ip address after reboot

    if not check(HUEsettings["ip"]):
        try:
            response = requests.get("https://discovery.meethue.com/", verify=False)

            if response and response.status_code == 200:
                data = response.json()[0]
                if "internalipaddress" in data.keys():
                    HUEsettings["ip"] = data["internalipaddress"]

        except:
            pass

    if not check(HUEsettings["ip"]):
        log("no_response")
        sys.exit(1)

    try:
        # Instantiate our bridge
        bridge = Bridge(HUEsettings["ip"], username=HUEsettings["key"], onchange=on_change)

        # If a new key was created, save it in the ini file
        if not HUEsettings["key"]:
            save_key(cfg, bridge.username)
            HUEsettings["key"] = bridge.username

        # Print the current status of all connected sensors & services
        if bridge.sensors:
            for sensor in bridge.sensors:
                status = []
                log(f"{sensor.product_name}: {sensor.name}")

                for service in sensor.services:
                    status.append(service.prompt())
                    if service.enabled is False:
                        log("suspended", argument=service.name)
                        # Enable all suspended services if "suspend" option is set to "no"
                        if not sensor.settings["suspend"]:
                            if service.enable():
                                log("enabled", argument=service.name)

                for line in sorted(status):
                    log(line)

        timer = MyTimer(60, timer_event, args=(bridge,))
        timer.start()

        # Listen for events
        bridge.events()

    except Exception as e:
        log("exception", argument=e)
        sys.exit(1)

    finally:
        try:
            # Send report
            report(bridge)

            timer.cancel()
        except:
            pass

