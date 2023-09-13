#!/usr/bin/env python3

import requests
import json
import sys
from datetime import datetime
import time
from urllib3.exceptions import InsecureRequestWarning

from email.utils import formataddr
from email.header import Header
from email.message import EmailMessage
import smtplib
#import ssl

# Suppress only the single warning from urllib3 needed.
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

low_chr = chr(0x2581)
high_chr = chr(0x2588)
timeline = (10*' ').join(['0', '03', '06', '09', '12', '15', '18', '21', '24'])

day_format      = "%d.%m.%y"
time_format     = "%H:%M:%S"
date_in_format  = "%Y-%m-%dT%H:%M:%S.%fZ"
date_out_format = f"{day_format} {time_format}"

#
# Hue Bridge IP Address (required)
#
HueIP = "192.168.178.x"

#
# Hue Bridge User Name/API Key
#
# Sepcifiy if user has been already created, else set to 'None'.
# Remeber you muss have physcal access to the Hue Bridge to create
# a new user/API Key
#
HueAPIKey = "aequenceoflettersandnumbers"

#
# Customize service descriptions and to suit your preference/language
#
HueServices = [
    {
        "name":         "device_power",
        "description":  "Ladezustand der 2x AAA Batterien",
        "section":      "power_state",
        "value":        "battery_level",
        "unit":         "%"
    },
    {
        "name":         "light_level",
        "description":  "Lichtsensor",
        "section":      "light",
        "value":        "light_level",
        "unit":         " Lux"
    },
    {
        "name":         "temperature",
        "description":  "Temperatursensor",
        "section":      "temperature",
        "value":        "temperature",
        "unit":         "°C"
    },
    {
        "name":         "motion",
        "description":  "Bewegungssensor",
        "section":      "motion",
        "value":        "motion",
        "unit":         ""
    }
]


status_msg = " - Letzter Wert"
event_msg  = " - Neuer Wert"

#
# E-Mail Settings (requires customization)
#
# If HueReceivers list is empty, no E-mail will be sent
#

HTMLtitle    = "Sensordaten vom {}"

HueSubject   = "Täglicher Bericht - Sensordaten"
HueReceivers = [ "me@mail.com" ]

SMTPuser     = "me@mail.com"
SMTPname     = "HueBridge"
SMTPpassword = "mypassword"
SMTPserver   = "smtp.mail.com"
SMTPport     = 587



def log(message):
    print(message)


def utc2local(utc):
    epoch  = time.mktime(utc.timetuple())
    offset = datetime.fromtimestamp(epoch) - datetime.utcfromtimestamp(epoch)
    return utc + offset


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

    heading = HTMLtitle.format(date)
    html = f"<html>{HTMLheader}\n\t<body>\n\t\t<h1>{heading}</h1>\n"

    for sensor in bridge.sensors:
        heading = f"{sensor.product_name}: {sensor.name}"
        html +=  f"\t\t<h2>{heading}</h2>\n"

        power_service = ([ s for s in sensor.services if s.name == "device_power" ] or [None])[0]
        if power_service:
            power_service.update()
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


def sendmail(subject, html_body, recipients):
    if not recipients:
        return

    msg = EmailMessage()

    if SMTPname:
        msg['From']  = formataddr((str(Header(SMTPname, 'utf-8')), SMTPuser))
    else:
        msg['From']  = SMTPuser
    msg['To']      = ", ".join(recipients)
    msg['Subject'] = subject

    msg.set_content(html_body, subtype="html")

    try:
        #context = ssl.create_default_context()
        with smtplib.SMTP(SMTPserver, port=SMTPport) as server:
            #server.starttls(context=context)
            server.starttls()
            server.login(SMTPuser, SMTPpassword)
            server.sendmail(SMTPuser, recipients, msg.as_string())

        log("Mail sent")

    except:
        log("Unable to send mail")


class Bridge():

    def __init__(self, ip_address, username=None):
        self.ip            = ip_address
        self.username      = username or self.__username()

        self.devices       = self.__devices()
        self.sensors       = [ Sensor(device["id"], device["name"], self) for device in self.devices if device["product_name"] == "Hue motion sensor" ] or None

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
                response = requests.post(url, json = my_obj, timeout=3, verify=False)

                if response and response.status_code == 200:
                    data = response.json()[0]

                    if "error" in data.keys():
                        input(data["error"]["description"])
                    elif "success" in data.keys():
                        username = data["success"]["username"]

                else:
                    log("Invalid Response")

            except Exception as e:
                log(e)
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
                log("Invalid Response")

        except Exception as e:
            log(e)

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

        today = datetime.now().strftime(day_format)
        plot = list(96*low_chr)

        with requests.Session() as session:
            while(True):
                try:
                    response = session.get(url, headers=headers, timeout=86400, stream=True, verify=False)

                    if response and response.status_code == 200:
                        for line in response.iter_lines():
                            if line:
                                line = line.decode('utf-8')

                                t = datetime.now().strftime(day_format)

                                # Let's see if a day has passed. It's time to send a new report then
                                if t != today:
                                    for sensor in self.sensors:
                                        # Once a day we must check the battery level
                                        power_service = ([ s for s in sensor.services if s.name == "device_power" ] or [None])[0]
                                        if power_service:
                                            power_service.update()
                                            log(power_service.prompt(status_msg))

                                    # Plot the motion profile of the passed day
                                    log(today)
                                    log("".join(plot))
                                    log(timeline)

                                    # Reset the motion profile
                                    plot = list(96*low_chr)

                                    # Send the daily report
                                    html = html_report(self, today)
                                    sendmail(HueSubject, html, HueReceivers)

                                    today = t

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

                                    value = service_data[service.value_name]

                                    if "changed" in service_data.keys():
                                        changed = utc2local(datetime.strptime(service_data["changed"], date_in_format))
                                    else:
                                        changed = datetime.now()

                                    service.update(value=value, changed=changed)

                                    log(service.prompt(event_msg))

                                    # If motion was detected we update the motion profile for that interval
                                    if service.name == 'motion' and value:
                                        h = int(changed.strftime("%H"))
                                        m = int(changed.strftime("%M"))
                                        plot_index = h*4 + m//15
                                        plot[plot_index] = high_chr
                    else:
                        log("Invalid Response")

                except requests.exceptions.RequestException as e:
                    if "timed out" in str(e):
                        # if the request has timed out (i.e. after a day with no events)
                        # send a report - even if it's empty
                        html = html_report(self, today)
                        sendmail(HueSubject, html, HueReceivers)
                        pass
                    else:
                        log(e)
                        break
                except KeyError:
                    pass
                except KeyboardInterrupt:
                    break
                #except Exception as e:
                #    log(e)



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
                log("Invalid Response")

        except Exception as e:
            log(e)
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

        self.value        = None
        self.changed      = None
        self.data         = []
        self.update()

    def prompt(self, msg):
        return f"{self.changed.strftime(date_out_format)} {self.owner.name} {self.description}{msg}: {self.value}{self.unit}"

    def update(self, value=None, changed=None):
        if changed is None or value is None:
            url = f"https://{self.__ip}/clip/v2/resource/{self.name}/{self.id}"
            headers = { "hue-application-key": self.__username }

            try:
                response = requests.get(url, headers=headers, timeout=3, verify=False)

                if response and response.status_code == 200:
                    data = response.json()["data"][0]

                    if self.report_name in data[self.section_name].keys():
                        service_data = data[self.section_name][self.report_name]
                    else:
                        service_data = data[self.section_name]

                    value = service_data[self.value_name]

                    if "changed" in service_data.keys():
                        changed = utc2local(datetime.strptime(service_data["changed"], date_in_format))
                    else:
                        changed = datetime.now()

                else:
                    log("Invalid Response")
                    return

            except Exception as e:
                log(e)
                return

        # Reset data at each new day:
        if self.changed and changed.strftime("%d") != self.changed.strftime("%d"):
            self.data = []

        self.data.append((changed, value))

        self.value   = value
        self.changed = changed

        return


if __name__ == "__main__":
    # Instantiate our bridge
    bridge = Bridge(HueIP, username=HueAPIKey)

    # Print the last status of all connected sensors & services
    for sensor in bridge.sensors:
        status = []
        log(f"{sensor.product_name}: {sensor.name}")

        for service in sensor.services:
            status.append(service.prompt(status_msg))

        for line in sorted(status):
            log(line)

    # Listen for events
    bridge.events()
