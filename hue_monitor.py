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
import datetime
import io

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from urllib3.exceptions import InsecureRequestWarning

from threading import Timer
from configparser import ConfigParser
from mimetypes import guess_type

from email.utils import formataddr, make_msgid
from email.header import Header
from email.message import EmailMessage

#
# Suppress only the single warning from urllib3 needed.
#
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

#
# Set default font sizes for plots (via matplotlib)
#
SMALL_SIZE = 8
MEDIUM_SIZE = 10
BIGGER_SIZE = 12

plt.rc('font', size=SMALL_SIZE)          # controls default text sizes
plt.rc('axes', titlesize=SMALL_SIZE)     # fontsize of the axes title
plt.rc('axes', labelsize=MEDIUM_SIZE)    # fontsize of the x and y labels
plt.rc('xtick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('ytick', labelsize=SMALL_SIZE)    # fontsize of the tick labels
plt.rc('legend', fontsize=SMALL_SIZE)    # legend fontsize
plt.rc('figure', titlesize=BIGGER_SIZE)  # fontsize of the figure title

mtime_format = mdates.DateFormatter("%H:%M")

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
#plot     = list(96*low_chr)
#plot     = [high_chr if x else low_chr for x in lista] when lista = [False, True, True, False, False, ...]
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
day_format      = "%d.%m.%y" # must not contain spaces
time_format     = "%H:%M:%S" # must not contain spaces
date_out_format = f"{day_format} {time_format}"

day_format_long = "%a, %d.%m.%y"

today = datetime.datetime.now().strftime(day_format)

#
# Read configuration from matching ini file in current directory
#
config_file = os.path.splitext(os.path.basename(__file__))[0] + '.ini'

#
# Initialize settings. Customize in config file
#
HueServices = {
    "device_power": {
        "description":   "Battery Level",
        "section":       "power_state",
        "value":         "battery_level",
        "unit":          "%"
    },
    "light_level": {
        "description":   "Licht Sensor",
        "section":       "light",
        "value":         "light_level",
        "unit":          "Lux"
    },
    "temperature": {
        "description":   "Temperature Sensor",
        "section":       "temperature",
        "value":         "temperature",
        "unit":          "°C"
    },
    "motion": {
        "description":   "Motion Sensor",
        "section":       "motion",
        "value":         "motion",
        "unit":          ""
    }
}

LOGsettings = {
    "report":            "Sending daily report for date {}",
    "motion_detected":   "Motion detected by sensor {}",
    "msg_sent":          "Message sent",
    "msg_failed":        "Message delivery failed: {}",
    "msg_suspended ":    "Message delivery suspended at specified time interval",
    "cfg_not_found":     "Configuration file not found: {}",
    "cfg_write_error":   "Error writing configuration: {}",
    "cfg_read_error":    "Error reading configuration: {}",
    "invalid_response":  "Invalid response from {}",
    "timeout":           "Connection to {} timed out",
    "exception":         "Unexpected error: {}",
    "suspended":         "Service '{}' temporarily disabled",
    "enabled":           "Service '{}' re-enabled",
    "no_config":         "No configuration",
    "no_response":       "No response or no IP address",
    "data_read_success": "Successfully read saved data for service {}",
    "data_read_failed":  "Reading saved data for service {} failed",
    "no_data":           "No saved data for service {}"
}

REPORTsettings = {
    "report_subject":    "Daily Report",
    "report_header":     "Sensor data of {}",
    "bridge_ip":         "Hue bridge/Gateway IP address: {}/{}",
    "notify_on_motion":  "Send a notification when movement is detected: {}",
    "suspend_services":  "Suspend services while notifications are suppressed: {}",
    "suppress_period":   "Suppress notifications (Period): {}",
    "suppress_daily":    "Suppress notifications (Daily): {}",
    "motion_profile":    "Motion Detection Profile (All Sensors)",
    "source":            "Source",
    "on":                "On",
    "off":               "Off"
}

DATAsettings = {
    "report_to":         [],
    "attach":		 True,
    "store":		 None
}

SMTPsettings = {
    "user":              "user@mail.com",
    "name":              "Hue Bridge",
    "password":          "password",
    "server":            "smtp.mail.com",
    "port":              587
}

HUEsettings = {
    "ip":                "10.1.1.2",
    "key":               None
}

MOTIONsettings = {
    "notify":            False,
    "notify_to":         "",
    "notify_subject":    "Motion Alert",
    "notify_text":       "Sensor \"{}\" detected a motion at {}.",
    "except":            "",
    "except_daily":      "",
    "suspend":           True
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


def isOpen(ip, port, timeout=3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)

    try:
        s.connect((ip, int(port)))
        # or
        #if s.connect_ex((ip, int(port))) == 0: # True if open, False if not
        s.shutdown(socket.SHUT_RDWR)
        return True

    except:
        return False

    finally:
        s.close()

def read_config():
    if not os.path.exists(config_file):
        log("cfg_not_found", argument=config_file)
        return None

    config = None

    try:
        config = ConfigParser()
        config.read([os.path.abspath(config_file)])

        #
        # Hue Bridge IP and User Name/API Key
        #
        HUEsettings["ip"]  = config.get("Hue Bridge", "ip")
        HUEsettings["key"] = config.get("Hue Bridge", "key")

        #
        # Mail account settings
        #
        for option in config.options("Mail Account"):
            value = config.get("Mail Account", option)
            if value:
                SMTPsettings[option] = value

        #
        # Data handling settings
        # If receivers ("report_to") list is empty, no E-mail will be sent
        #
        for option in config.options("Data Handling"):
            value = config.get("Data Handling", option)
            if value:
                if option == "attach":
                    DATAsettings[option] = config.getboolean("Data Handling", option)
                elif option == "report_to" and "@" in value:
                    DATAsettings[option] = [ r.strip() for r in value.split(',') ]
                else:
                    DATAsettings[option] = value

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
        # Customized service descriptions
        #
        for service in HueServices:
            value = config.get("Service Descriptions", service)
            if value:
                HueServices[service]["description"] = value

        #
        # Customized settings for logging
        #
        for option in config.options("Logging"):
            value = config.get("Logging", option)
            if value:
                LOGsettings[option] = value

        #
        # Customized settings for reporting
        #
        for option in config.options("Reporting"):
            value = config.get("Reporting", option)
            if value:
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
    offset = datetime.datetime.fromtimestamp(epoch) - datetime.datetime.utcfromtimestamp(epoch)
    return utc + offset


def check_date(date, range, daily=False):
    if daily:
        format = time_format
    else:
        format = date_out_format

    try:
        interval = [ (x.strip(), y.strip()) for x, y in [ tuple(x.split("-")) for x in [ x.strip() for x in range.split(",") if x != "" ] ] ]

        compare = datetime.datetime.strptime(date, format)

        for first, last in interval:
            start = datetime.datetime.strptime(first, format[:len(first)])
            end = datetime.datetime.strptime(last, format[:len(last)])

            if start <= compare <= end:
                return True
    except:
        pass

    return False


def html_report(bridge, datestr, imageid=None):
    try:
        html = \
f"""
<html>{HTMLheader}
  <body>
    <h1>{REPORTsettings["report_header"].format(datestr)}</h1>
    <p>{REPORTsettings['bridge_ip'].format(HUEsettings["ip"], get_ip_address('wlan0'))}</p>
"""

        if imageid:
            html += \
f"""
    <p><img src="cid:{imageid[1:-1]}" alt="img" /></p>
"""

        for sensor in bridge.sensors:
            for service in sensor.services:
                if service.name == "device_power":
                    power_service = service
                    break

            power_service.update()

            html += \
f"""
    <h2>{sensor.product_name}: {sensor.name}</h2>
    <p>{REPORTsettings['notify_on_motion'].format(REPORTsettings['on'] if sensor.settings['notify'] else REPORTsettings['off'])}</p>
    <p>{REPORTsettings['suspend_services'].format(REPORTsettings['on'] if sensor.settings['suspend'] else REPORTsettings['off'])}</p>
    <p>{REPORTsettings['suppress_period'].format(sensor.settings['except'])}</p>
    <p>{REPORTsettings['suppress_daily'].format(sensor.settings['except_daily'])}</p>
    <p>{power_service.description}: {power_service.data[-1][1]} {power_service.unit}</p>
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

        if "maintype" in attachment and "subtype" in attachment:
            maintype = attachment["maintype"]
            subtype = attachment["subtype"]
        else:
            maintype, subtype = guess_type(filename)[0].split('/')

        if os.path.isfile(attachment["path"]):
            with open(attachment["path"], "rb") as f:
                data = f.read()
        elif "data" in attachment:
            data = attachment["data"]
        else:
            continue

        if data:
            if "cid" in attachment:
                msg.add_related(
                    data,
                    maintype=maintype,
                    subtype=subtype,
                    cid=attachment["cid"]
                )
            else:
                msg.add_attachment(
                    data,
                    filename=filename,
                    maintype=maintype,
                    subtype=subtype
                )

    #context = ssl.create_default_context()
    with smtplib.SMTP(SMTPsettings["server"], port=SMTPsettings["port"]) as server:
        #server.starttls(context=context)
        server.starttls()
        server.login(SMTPsettings["user"], SMTPsettings["password"])
        server.sendmail(SMTPsettings["user"], recipients, msg.as_string())


def service_profile(service_data, title, filename=None):
    # Create temperature profile in PNG format

    if len(service_data) < 2:
        raise Exception("insufficient data")

    x_values = [x for x, _ in service_data]
    y_values = [y for _, y in service_data]

    # Set figure height to 2.2 inches only
    plt.figure().set_figheight(2.2)
    plt.plot(x_values, y_values)

    # Use fixed limits on y-axis / autoscale off
    left = datetime.datetime.strptime(today, day_format)
    right = left.replace(hour=23, minute=59, second=59, microsecond=0)
    plt.xlim(left=left, right=right)

    # Use no margins
    #plt.margins(x=0, y=0, tight=False)
    plt.autoscale(enable=None, axis="x", tight=True)

    #plt.xlabel("Time", size=SMALL_SIZE)
    #plt.ylabel("Temperature", size=SMALL_SIZE)
    if title:
        plt.title(title, size=MEDIUM_SIZE, pad=20)

    # Display grid
    plt.grid(visible=True, which="both", axis="both")

    ax = plt.gca()

    # Don't show the top and right border
    #ax.spines["top"].set_visible(False)
    #ax.spines["right"].set_visible(False)

    # Set axis aspect ratio
    top, bottom = plt.ylim()
    range = float(bottom - top)
    aspect = .25/range
    ax.set_aspect(aspect)

    # Use custom time format
    ax.xaxis.set_major_formatter(mtime_format)

    # Save as PNG file (or write to IOBuffer)
    if filename:
        plt.savefig(filename)
        img_data = None

    else:
        with io.BytesIO() as buf:  # use buffer memory
            plt.savefig(buf, format='png')
            buf.seek(0)
            img_data = buf.getvalue()

    plt.close()

    return img_data


def motion_profile(plotdata, filename=None):
    # Create motion profile (all sensors) in PNG format

    today0 = datetime.datetime.strptime(today, day_format)

    x_labels = [(today0 + datetime.timedelta(minutes=15*n)).strftime("%H:%M") for n in range(0, 96)]
    x_pos    = [n for n in range(0, 96)]

    y_values = [1 if c == high_chr else 0 for c in plotdata]

    # Add 24:00 data point for better readability
    #x_labels.append("24:00")
    #x_pos.append(96)
    #y_values.append(0)

    # Set figure height to 1.6 inches only
    plt.figure().set_figheight(1.6)
    plt.bar(x_pos, y_values, width=1, align="edge")

    # Print labels below x-axis, eevry 3 hrs (96/24 * 3 = 12)
    plt.xticks(x_pos[0::12], x_labels[0::12], size=SMALL_SIZE)

    # Use fixed limits on y-axis / autoscale off
    plt.ylim(bottom=0, top=1)

    # Y-axis labels should only be "0" and "1" (or "off" and "on")
    y_labels = ["0", "1"]
    y_pos    = [0, 1]

    # Print labels left beside y-axis
    plt.yticks(y_pos, y_labels, size=SMALL_SIZE)

    # Use no margins
    #plt.margins(x=0, y=0, tight=False)
    plt.autoscale(enable=None, axis="x", tight=True)

    #plt.xlabel("Time", size=SMALL_SIZE)
    #plt.ylabel("Motion", size=SMALL_SIZE)
    plt.title(REPORTsettings["motion_profile"], size=MEDIUM_SIZE, pad=20)

    ax = plt.gca()

    # Don't show the top and right border
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Set axis aspect ratio
    ax.set_aspect(12.0)

    # Save as PNG file (or write to IOBuffer)
    if filename:
        plt.savefig(filename)
        img_data = None

    else:
        with io.BytesIO() as buf:  # use buffer memory
            plt.savefig(buf, format='png')
            buf.seek(0)
            img_data = buf.getvalue()

    plt.close()

    return img_data


def sensor_data2df(sensor, update=False):
    service_dict = {}
    maxlen = 0

    for service in sensor.services:
        if service.name == "device_power":
            continue

        if update and service.last_saved:
            if service.unit:
                service_dict[service.description] = [f"{changed.strftime(date_out_format)} {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']} {service.unit}" for changed, value in service.data if changed > service.last_saved]
            else:
                service_dict[service.description] = [f"{changed.strftime(date_out_format)} {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']}" for changed, value in service.data if changed > service.last_saved]

        else:
            if service.unit:
                service_dict[service.description] = [f"{changed.strftime(date_out_format)} {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']} {service.unit}" for changed, value in service.data]
            else:
                service_dict[service.description] = [f"{changed.strftime(date_out_format)} {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']}" for changed, value in service.data]

        # Cleanup: use only items of today
        service_dict[service.description] = [item for item in service_dict[service.description] if item.startswith(today)]

        if len(service_dict[service.description]) > maxlen:
             maxlen = len(service_dict[service.description])

    service_dict[REPORTsettings["source"]] = [sensor.name for i in range(0, maxlen)]

    df = pd.DataFrame({key:pd.Series(value) for key, value in service_dict.items()})

    return df


def report(bridge, reset=False):
    # Start with an empty list of attachments
    attachments = []

    log(today)

    plot = list(96*low_chr)
    #plot = [high_chr if x else low_chr for x in lista] when lista = [False, True, True, False, False, ...]

    for sensor in bridge.sensors or []:
        for service in sensor.services:
            # Show current power status of all sensors
            if service.name == "device_power":
                service.update()
                log(service.prompt())

            # Get the motion profile of the passed day (all sensors)
            if service.name == "motion":
                for changed, value in service.data:
                    if value and changed.strftime(day_format) == today:
                        h = int(changed.strftime("%H"))
                        m = int(changed.strftime("%M"))
                        plot_index = h*4 + m//15   # plot_index = h*n//24 + m//(60//(n//24)), n = 96, 288, 720 (for 15min, 5min, 1min interval)
                        plot[plot_index] = high_chr

    # Plot the motion profile
    log("".join(plot))
    log(timeline)

    filename = "motion.png"
    cid = make_msgid() #or f"<{os.path.basename(filename)}>"

    #img_data = motion_profile(plot, filename) # returns None if filename != None
    img_data = motion_profile(plot)

    attachment = {
        "maintype": "image",
        "subtype":  "png",
        "cid":      cid,
        "path":     filename,
        "data":     img_data
    }
    attachments.append(attachment)

    # Send the daily report
    log("report", argument=today)

    html_body = html_report(bridge, today, imageid=cid)
    html_tables = []

    # Transform collected sensor data into DataFrame, CSV format
    for sensor in bridge.sensors:
        cid = None

        df = sensor_data2df(sensor)

        columns = [column for column in list(df) if column != REPORTsettings["source"]]
        html_table = df.to_html(index=False, header=True, na_rep='', border=0, columns=columns)

        for service in sensor.services:
            if service.name == "temperature":
                try:
                    filename = f"{sensor.name} {service.description}.png"
                    cid = make_msgid() #or f"<{os.path.basename(filename)}>"

                    #img_data = service_profile(service.data, f"{sensor.name}: {service.description} ({service.unit.strip()})", filename)
                    img_data = service_profile(service.data, f"{sensor.name}: {service.description} ({service.unit.strip()})")

                    attachment = {
                        "maintype": "image",
                        "subtype":  "png",
                        "cid":      cid,
                        "path":     filename,
                        "data":     img_data
                    }
                    attachments.append(attachment)

                except:
                    cid = None

        if cid:
            html_table += \
f"""
    <p><img src="cid:{cid[1:-1]}" alt="img" /></p>
"""

        html_tables.append(html_table)

        # Attach sensor data or save as file?
        if DATAsettings["attach"] or DATAsettings["store"]:
            attachment = {
                "maintype": "text",
                "subtype": "csv"
            }

            ts = datetime.datetime.strptime(today, day_format)
            timestamp = ts.strftime("%y") + ts.strftime("%m") + ts.strftime("%d") # reverse today's date
            file_path = f"{bridge.name}_{sensor.name}_{timestamp}.csv"

            # Save sensor data locally?
            if DATAsettings["store"]:
                try:
                    # Is it the name of an existing file?
                    if os.path.isfile(DATAsettings["store"]):
                        file_path = DATAsettings["store"]
                    # Is it the name of an existing directory?
                    elif os.path.isdir(DATAsettings["store"]):
                        file_path = os.path.join(DATAsettings["store"], file_path)
                    # Let's assume that a "." in the basename specifies the name of (not yet existing) file.
                    elif "." in  os.path.basename(DATAsettings["store"]):
                        file_path = DATAsettings["store"]
                        # Create the parent directory if neccessary.
                        if os.path.sep in DATAsettings["store"] and not os.path.isdir(DATAsettings["store"].rsplit(os.path.sep, 1)[0]):
                            os.makedirs(DATAsettings["store"].rsplit(os.path.sep, 1)[0])
                    # If it's neither a file nor an exisitng directory we'll create a directory with the specified name
                    else:
                        os.makedirs(DATAsettings["store"])
                        file_path = os.path.join(DATAsettings["store"], file_path)

                    if os.path.isfile(file_path):
                        df = sensor_data2df(sensor, update=True)
                        df.to_csv(file_path, mode='a', sep='\t', index=False, header=False)

                    else:
                        df.to_csv(file_path, sep='\t', index=False, header=True)

                except Exception as e:
                    log(str(e))
            else:
                attachment["data"] = df.to_csv(sep='\t', index=False, header=True).encode("utf-8")

            if DATAsettings["attach"]:
                attachment["path"] = file_path
                attachments.append(attachment)

    if html_body:
        # Insert html_tables into html_body
        html_body = html_body.format(*html_tables)

        try:
            sendmail(DATAsettings["report_to"], REPORTsettings["report_subject"], html_body, subtype="html", attachments=attachments)
            log("msg_sent")

        except Exception as e:
            log("msg_failed", argument=e)

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
                    elif DATAsettings["report_to"]:
                        sendmail(DATAsettings["report_to"], sensor.settings["notify_subject"], msg_text)

                    log("msg_sent")

                except Exception as e:
                    log("msg_failed", argument=e)

            else:
                log("msg_restricted")


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

    def reset(self):
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
                                        changed = utc2local(datetime.datetime.strptime(service_data["changed"], date_in_format))
                                    else:
                                        changed = datetime.datetime.now()

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
                    if service["rtype"] in HueServices:
                        s = Service(service["rid"], service["rtype"], HueServices[service["rtype"]], self)
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

    def __init__(self, id, name, properties, owner):
        self.id = id
        self.name = name

        self.description  = properties["description"]
        self.section_name = properties["section"]
        self.report_name  = properties["value"] + "_report"
        self.value_name   = properties["value"]
        self.unit         = properties["unit"]

        self.owner        = owner

        self.__ip         = owner.owner.ip # bridge.ip()
        self.__username   = owner.owner.username # bridge.username()

        self.__url        = f"https://{self.__ip}/clip/v2/resource/{self.name}/{self.id}"
        self.__headers    = {"hue-application-key": self.__username}

        self.enabled      = self.is_enabled()

        self.data         = []
        self.last_saved   = None
        self.update()

    def prompt(self):
        if not self.data:
            return f"{datetime.datetime.now().strftime(date_out_format)} {self.owner.name} {self.description}: N/A"

        changed, value = self.data[-1]

        if self.unit:
            return f"{changed.strftime(date_out_format)} {self.owner.name} {self.description}: {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']} {self.unit}"
        else:
            return f"{changed.strftime(date_out_format)} {self.owner.name} {self.description}: {value if not isinstance(value, bool) else REPORTsettings['on'] if value else REPORTsettings['off']}"

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
                        changed = utc2local(datetime.datetime.strptime(service_data["changed"], date_in_format))
                    else:
                        changed = datetime.datetime.now()

                else:
                    log("invalid_response", argument=self.__url)
                    return

            except Exception as e:
                log("exception", argument=e)
                return

        if not self.data or changed > self.data[-1][0]:
            self.data.append((changed, value))

        return


class MyTimer(Timer):
    def run(self):
         while not self.finished.wait(self.interval):
             self.function(*self.args, **self.kwargs)


def timer_event(bridge):
    global  today

    # Let's see if a day has passed. It's time to send a new report and set the date
    if datetime.datetime.now().strftime(day_format) != today:
        report(bridge, reset=True)
        today = datetime.datetime.now().strftime(day_format)

    for sensor in bridge.sensors:
        if sensor.settings["suspend"]:
            if check_date(datetime.datetime.now().strftime(date_out_format), sensor.settings["except"]) or check_date(datetime.datetime.now().strftime(time_format), sensor.settings["except_daily"], daily=True):
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

    except (requests.ConnectionError, requests.Timeout):
        return False


def read_csv(bridge):
    spos = len(date_out_format.split())

    timestamp = today[6:8] + today[3:5] + today[:2] # reverse today's date
    file_path = None

    if os.path.isfile(DATAsettings["store"]):
        file_path = DATAsettings["store"]

    for sensor in bridge.sensors or []:

        if not file_path and os.path.isdir(DATAsettings["store"]):
            file_path = f"{bridge.name}_{sensor.name}_{timestamp}.csv"
            file_path = os.path.join(DATAsettings["store"], file_path)

            if not os.path.isfile(file_path):
                return

        df = pd.read_csv(file_path, sep="\t", na_filter=False)

        for service in sensor.services:
            if service.name == "device_power":
                continue

            try:
                # Filter the rows which match criteria for the specific service: only today's data and source is sensor
                df_service = df[df[service.description].str.startswith(today) & (df[REPORTsettings["source"]] == sensor.name)]
                # Select the filtered column for this specific service
                service_data = list(df_service[service.description])

            except:
                log("data_read_failed", argument=f"{sensor.name}:{service.description}")
                continue

            if service_data:
                if service.name == "motion":
                    service.data = [(datetime.datetime.strptime(" ".join(x.split()[:spos]), date_out_format), True if x.split()[spos] == REPORTsettings["on"] else False) for x in service_data]

                elif service.name == "temperature":
                    service.data = [(datetime.datetime.strptime(" ".join(x.split()[:spos]), date_out_format), float(x.split()[spos])) for x in service_data]

                elif service.name == "light_level":
                    service.data = [(datetime.datetime.strptime(" ".join(x.split()[:spos]), date_out_format), int(x.split()[spos])) for x in service_data]

                service.last_saved = service.data[-1][0]

                log("data_read_success", argument=f"{sensor.name}:{service.description}")

            else:
                log("no_data", argument=f"{sensor.name}:{service.description}")


if __name__ == "__main__":
    # Read settings from config file
    cfg = read_config()
    if not cfg:
        log("no_config")
        sys.exit(0)

    start_time = time.time()
    while int(time.time() - start_time) < 30:
        if isOpen(HUEsettings["ip"], 80, 1):
            break
        else:
            time.sleep(5)

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

        # Wait a minute before exiting with error
        time.sleep(60)

        sys.exit(1)

    try:
        # Instantiate our bridge
        bridge = Bridge(HUEsettings["ip"], username=HUEsettings["key"], onchange=on_change)

        # If a new key was created, save it in the ini file
        if not HUEsettings["key"]:
            save_key(cfg, bridge.username)
            HUEsettings["key"] = bridge.username

        # Read today's saved data from csv file(s) - if exist:
        if DATAsettings["store"]:
            read_csv(bridge)

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

