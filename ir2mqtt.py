#!python3
""" Daemon that can publishes iRacing telemetry values at MQTT topics.

Configure what telemery values from iRacing you would like to publish at which
MQTT topic.
Calculate the geographical and astronomical correct light situation on track. 
Send pit service flags and refuel amount to and receive pit commands from
a buttonbox using a serial connection.
 
This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.
This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
"""

__author__ = "Robert Bausdorf"
__contact__ = "rbausdorf@gmail.com"
__copyright__ = "2019, bausdorf engineering"
#__credits__ = ["One developer", "And another one", "etc"]
__date__ = "2019/06/01"
__deprecated__ = False
__email__ =  "rbausdorf@gmail.com"
__license__ = "GPLv3"
#__maintainer__ = "developer"
__status__ = "Production"
__version__ = "1.2"

import irsdk
import time
import paho.mqtt.client as mqtt
import configparser
import yaml
import serial
import astral
import timezonefinder
import pytz
from datetime import datetime
from datetime import date
from datetime import time as dttime
from datetime import tzinfo
from irsdk import PitCommandMode

debug = False

# this is our State class, with some helpful variables
class State:
    ir_connected = False
    date_time = -1
    pitFlags = -1
    pitFuel = -1
    tick = 0
    latitude = -1
    longitude = -1
    elevation = -1

# Possible states given in Paho MQTT client callbacks 
mqttRC = ['Connection successful', 
          'Connection refused - incorrect protocol version', 
          'Connection refused - invalid client identifier', 
          'Connection refused - server unavailable', 
          'Connection refused - bad username or password', 
          'Connection refused - not authorised']

# here we check if we are connected to iracing
# so we can retrieve some data
def check_iracing():        
    
    if state.ir_connected and not (ir.is_initialized and ir.is_connected):
        state.ir_connected = False
        # don't forget to reset all your in State variables
        state.date_time = -1
        state.pitFlags = -1
        state.pitFuel = -1
        state.tick = 0
        state.latitude = -1
        state.longitude = -1
        state.elevation = -1
        state.timezone = -1

        # Close serial port to buttonbox
        if ser.is_open:
            ser.close()

        # we are shut down ir library (clear all internal variables)
        ir.shutdown()
        print('irsdk disconnected')
        mqtt_publish('state', 0)
    elif not state.ir_connected:
        # Check if a dump file should be used to startup IRSDK
        if config.has_option('global', 'simulate'):
            is_startup = ir.startup(test_file=config['global']['simulate'])
            print('starting up using dump file: ' + str(config['global']['simulate']))
        else:
            is_startup = ir.startup()
            if debug:
                print('DEBUG: starting up with simulation')

        if is_startup and ir.is_initialized and ir.is_connected:
            state.ir_connected = True
            # Check need and open serial connection
            try:
                if config.has_option('global', 'serial'):
                    ser.open()
            except Exception:
                print('Unable to open port ' + ser.port + '. Serial communication is disabled')
                    
            print('irsdk connected')
            mqtt_publish('state', 1)
            
            # Get geographical track information and track timezone for
            # astronomical calculations
            state.latitude = float(str(ir['WeekendInfo']['TrackLatitude']).rstrip(' m'))
            state.longitude = float(str(ir['WeekendInfo']['TrackLongitude']).rstrip(' m'))
            state.elevation = float(str(ir['WeekendInfo']['TrackAltitude']).rstrip(' m'))
            state.timezone = pytz.timezone(timeZoneFinder.certain_timezone_at(lng=state.longitude, lat=state.latitude))

def publishSessionTime():
    
    # Get the simulated time of day from IRSDK
    sToD = ir['SessionTimeOfDay']
    tod = time.localtime(float(sToD)-3600)
    dat = ir['WeekendInfo']['WeekendOptions']['Date'].split('-')

    # Create a datetime object WITHOUT timezone info so it can be localized to
    # the tracks timezone
    state.date_time = state.timezone.localize(datetime(int(dat[0]), int(dat[1]), int(dat[2]), tod.tm_hour, tod.tm_min, tod.tm_sec))
    # Display the current time in that time zone
    print('session ToD:', state.date_time.isoformat('T'))

    # Publish using the timezone from configuration
    mqtt_publish('ToD', datetime.strftime(state.date_time.astimezone(pytz.timezone(config['mqtt']['timezone'])), "%Y-%m-%dT%H:%M:%S%z"))
    publishLightInfo(state.date_time)

def publishLightInfo(dateAndTime):

    if state.timezone is None:
        print("Could not determine the time zone")
    else:
        # Calculate solar elevation and twilight start and end times
        angle = geoTime.solar_elevation(dateAndTime, state.latitude, state.longitude)
        print('solar elevation: ' + str(angle))
        mqtt_publish('solarElevation', str(angle))
        
        times_setting = geoTime.twilight_utc(astral.SUN_SETTING, dateAndTime, state.latitude, state.longitude, state.elevation)
        times_rising = geoTime.twilight_utc(astral.SUN_RISING, dateAndTime, state.latitude, state.longitude, state.elevation)
        if debug:
            print("DEBUG: rising start  " + str(times_rising[0].astimezone(state.timezone)))
            print("DEBUG: rising end    " + str(times_rising[1].astimezone(state.timezone)))
            print("DEBUG: setting start " + str(times_setting[0].astimezone(state.timezone)))
            print("DEBUG: setting end   " + str(times_setting[1].astimezone(state.timezone)))

        # Classify and publish the current light situation on track as one of
        # night, dawn, day or dusk  
        lightinfo = 'day'
        if dateAndTime < times_rising[0].astimezone(state.timezone):
            lightinfo = 'night'
        elif dateAndTime < times_rising[1].astimezone(state.timezone):
            lightinfo = 'dawn'
        elif dateAndTime < times_setting[0].astimezone(state.timezone):
            lightinfo = 'day'
        elif dateAndTime < times_setting[1].astimezone(state.timezone):
            lightinfo = 'dusk'
        else:
            lightinfo = 'night'

        print('lightinfo: ' + lightinfo)
        mqtt_publish('lightinfo', lightinfo)


def writeSerialData():
    # Read pit flags from telemetry if it has changed and
    # send it to serial port
    pit_svflags = ir['PitSvFlags']
    if state.pitFlags != pit_svflags:
        ser.write(('#PFL=' + str(pit_svflags) + '*').encode('ascii'))
        state.pitFlags = pit_svflags
        print('SERIAL> ' + '#PFL=' + str(pit_svflags) + '*')

    # Read pit refuel amount from telemetry if it has changed and
    # send it to serial port
    pit_svfuel = ir['PitSvFuel']
    if state.pitFuel != pit_svfuel:
        ser.write(('#PFU=' + str(pit_svfuel)[0:3] + '*').encode('ascii'))
        state.pitFuel = pit_svfuel
        print('SERIAL> ' + '#PFU=' + str(pit_svfuel)[0:3] + '*')

def readSerialData():
    try: 
        # Check if data is available on serial port
        telegram = str(ser.readline())
    
        try:
            # Determine the telegram part in serial data
            start = telegram.index('#')
            end = telegram.index('*')
            telegram = telegram[start + 1:end]
            print('SERIAL< ' + telegram)
            keyvalue = telegram.split('=')
    
            # Check if telegram key and send the appropriate pit command to iRacing
            if len(keyvalue) == 2:
                if keyvalue[0] == 'PFU':
                    if debug:
                        print('DEBUG: send fuel pit command ' + int(keyvalue[1]) + ' l')
                    ir.pit_command(PitCommandMode.fuel , int(keyvalue[1]))
                if keyvalue[0] == 'PCM':
                    if debug:
                        print('DEBUG: send pit command ' + int(keyvalue[1]))
                    ir.pit_command(int(keyvalue[1]))
        except Exception as e:
            if debug:
                print('DEBUG: error processing telegram ' + telegram + ' : ' + str(e))
    except Exception as e:
        print('Error processing telegram: ' + str(e))

# our main loop, where we retrieve data
# and do something useful with it
def loop():
    # on each tick we freeze buffer with live telemetry
    # it is optional, useful if you use vars like CarIdxXXX
    # in this way you will have consistent data from this vars inside one tick
    # because sometimes while you retrieve one CarIdxXXX variable
    # another one in next line of code can be changed
    # to the next iracing internal tick_count
    ir.freeze_var_buffer_latest()

    state.tick += 1
    # publish session time and configured telemetry values every minute
    if state.tick % 60 == 1:
        publishSessionTime()
    
        # read and publish configured telemetry values
        if config.has_section('iracing'):
            for top in config['iracing']:
                ind = config.get('iracing', top).split('/')
                val = ir
                for key in ind:
                    if val != None:
                        if isinstance(val, list):
                            val = val[0].__getitem__(key)
                        else:
                            val = val.__getitem__(key)
    
                if val != None:
                    mqtt_publish(top, val)

    # Read/Write serial data as needed
    if useSerial and ser.is_open:
        writeSerialData()
        readSerialData()


def mqtt_publish(topic, data):
    top = config['mqtt']['baseTopic'] + '/' + topic
    mqttClient.publish(top, data)
    if debug:
        print('DEBUG mqtt_publish(' + top + ', ' + str(data) + ')')

# Paho MQTT callback 
def on_connect(client, userdata, flags, rc):
    print('MQTT: ' + mqttRC[rc])
    if rc==0:
        if state.ir_connected:
            mqtt_publish('state', 1)
            if state.date_time != -1:
                mqtt_publish('ToD', state.date_time) 
    else:
        print("Bad connection Returned code=",rc)

# Paho MQTT callback
def on_disconnect(client, userdata, rc):
    if rc==0:
        print('MQTT: connection terminated')
    else:
        print('MQTT: connection terminated unexpectedly')
        
def banner():
    print("=============================")
    print("|         IR2MQTT           |")
    print("|           " + str(__version__) + "             |")
    print("=============================")
    print("MQTT host: " + config['mqtt']['host'])
    print("MQTT port: " + config['mqtt']['port'])
    print("MQTT base: " + config['mqtt']['baseTopic'])

# Here is our main program entry
if __name__ == '__main__':
    # Read configuration file
    config = configparser.ConfigParser()    
    try: 
        config.read('ir2mqtt.ini')
    except Exception:
        print('unable to read configuration: ' + Exception.__cause__)

    # Print banner an debug output status
    banner()
    if config.has_option('global', 'debug'):
        debug = config.getboolean('global', 'debug')

    if debug:
        print('Debug output enabled')

    # initializing ir and state
    ir = irsdk.IRSDK()
    state = State()

    # Initialize and connect MQTT client to configured broker
    mqttClient = mqtt.Client("irClient")
    mqttClient.on_connect=on_connect
    mqttClient.on_disconnect=on_disconnect
    mqttClient.loop_start()
    try:
        mqttClient.connect(config['mqtt']['host'], int(config['mqtt']['port']))
    except Exception:
        print('unable to connect to mqtt broker')

    # Initialize and configure serial port
    ser = serial.Serial()
    useSerial = False
    if config.has_option('global', 'serial'):
        ser.port =  config['global']['serial']
        ser.baudrate = 9600
        ser.timeout = 1
        useSerial = True
        print('using COM port: ' + str(ser.port))

    # Initialize astronomical calculator and timezone finder
    geoTime = astral.Astral()
    geoTime.solar_depression = 'civil'
    timeZoneFinder = timezonefinder.TimezoneFinder()

    try:
        # infinite loop
        while True:
            # check if we are connected to iracing
            check_iracing()
                
            # if we are, then process data
            if state.ir_connected:
                loop()

            # sleep for 1 second
            # maximum you can use is 1/60
            # cause iracing update data with 60 fps
            time.sleep(1)
    except KeyboardInterrupt:
        # press ctrl+c to exit
        print('exiting')
        if state.ir_connected:
            mqtt_publish('state', 0)
        
        if useSerial and ser.is_open:
            ser.close()

        mqttClient.loop_stop()
        mqttClient.disconnect()
        time.sleep(2)
        pass