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
__version__ = "1.4"

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
    tick = 0
    latitude = -1
    longitude = -1
    elevation = -1
    mqttdict = {}
    mqttConnected = False

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
        state.tick = 0
        state.latitude = -1
        state.longitude = -1
        state.elevation = -1
        state.timezone = -1
        state.mqttdict = {}

        # Close serial port to buttonbox
        for ind in ser:
            if ser[ind].is_open:
                ser[ind].close()

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
            if config.has_option('global', 'serial'):
                for ind in ser:
                    try:
                        ser[ind].open()
                        print('Serial port ' + ind + ' open')
                        if debug:
                            print('DEBUG: ' + str(ser[ind]))
                    except Exception:
                        print('Unable to open port ' + ser[ind].port + '. Serial communication is disabled')
                    
            print('irsdk connected')
            if state.mqttConnected:
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

def readSerialData(connection):
        # Check if data is available on serial port
        if connection.in_waiting:
            telegram = connection.readline().decode('ascii')
        
            try:
                if len(telegram) > 0:
                    # Determine the telegram part in serial data
                    start = telegram.index('#')
                    end = telegram.index('*')
                    telegram = telegram[start + 1:end]
                    print('SERIAL[' + str(connection.port) + ']< ' + telegram)
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
            

def getIrsdkValue(top):
    ind = top.split('/')
    val = ir
    for key in ind:
        if val != None:
            listkey = key.split('[', 1)
            if len(listkey) == 2:
                listindex = listkey[1].rstrip(']')
                idx = 0
                if listindex == 'last':
                    idx = len(val.__getitem__(listkey[0])) - 1
                elif listindex[0] == '$':
                    try:
                        indirection = state.mqttdict[listindex.lstrip('$')]
                    except KeyError as e:
                        print('value of "' + listindex.lstrip('$') + '" not avilable at this time')
                elif listindex[0] == '#':
                    indirection = getIrsdkValue(listindex.lstrip('#').replace('&', '/'))
                    if not isinstance(indirection, int):
                        ixd = int(indirection)
                    else:
                        idx = indirection
                     
                else:
                    idx = int(listindex)

                val = val.__getitem__(listkey[0])[idx]
            else:
                val = val.__getitem__(key)
    
    return val
    
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
    if state.tick % 60 == 1 and state.mqttConnected:
        publishSessionTime()
    
    # read and publish configured telemetry values every second - but only
    # if the value has changed in telemetry
    if config.has_section('iracing'):
        for top in config['iracing']:
            try:
                ind = config.get('iracing', top)
                val = getIrsdkValue(ind)
                if val != None:
                    try:
                        lastVal = state.mqttdict[top]
                        if lastVal == val:
                            continue
                        elif state.mqttConnected:
                            state.mqttdict[top] = val
                            mqtt_publish(top, val)
                    except KeyError:
                        if state.mqttConnected:
                            state.mqttdict[top] = val
                            mqtt_publish(top, val)
                    
            except Exception as e:
                print('error getting value of ' + str(ind) + str(e))
                
    # Read/Write serial data as needed
    if useSerial:
        if config.has_section('serial'):
            for top in config['serial']:
                try:
                    ind = config.get('serial', top)
                    val = getIrsdkValue(ind)
                    if val != None:
                        try:
                            lastVal = state.mqttdict[top]
                            if lastVal == val:
                                continue
                            else:
                                state.mqttdict[top] = val
                        except KeyError:
                            state.mqttdict[top] = val
                        
                        for ind in ser:
                            if ser[ind].is_open:
                                telegram = '#' + top.upper() + '=' + str(val) + '*';
                                print('SERIAL[' + str(ser[ind].port) + ']> ' + telegram);
                                ser[ind].write(telegram.encode('ascii'))

                except Exception as e:
                    print('error getting value of ' + str(ind) + str(e))

        for ind in ser:
            try: 
                if ser[ind].is_open:
                    readSerialData(ser[ind])
                elif useSerial:
                    ser[ind].open()
            except serial.serialutil.SerialException as e:
                print('Error on serial connection ' + ind + ': ' + str(e))
                if useSerial:
                    try:
                        ser[ind].close()
                    except serial.serialutil.SerialException:
                        print('Error re-opening serial connection ' + ind + ': ' + str(ser[ind]))


def mqtt_publish(topic, data):
    top = config['mqtt']['baseTopic'] + '/' + topic
    mqttClient.publish(top, str(data))
    if debug:
        print('DEBUG mqtt_publish(' + top + ', ' + str(data) + ')')

# Paho MQTT callback 
def on_connect(client, userdata, flags, rc):
    print('MQTT: ' + mqttRC[rc])
    if rc==0:
        state.mqttConnected = True
        if state.ir_connected:
            mqtt_publish('state', 1)
            if state.date_time == -1:
                publishSessionTime() 
    else:
        print("Bad connection Returned code=",rc)

# Paho MQTT callback
def on_disconnect(client, userdata, rc):
    state.mqttConnected = False
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
    ser = {} #serial.Serial()
    useSerial = False
    if config.has_option('global', 'serial'):
        ports = config['global']['serial'].split(',')
        useSerial = True
        if debug:
            print('DEBUG: pySerial version: ' + serial.__version__)
        for port in ports:
            ser[port] = serial.Serial()
            ser[port].port = port
            ser[port].baudrate = 9600
            ser[port].timeout = 1

    for ind in ser:
        print('using COM port: ' + str(ser[ind].port))
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
        
        if useSerial:
            for ind in ser:
                if ser[ind].is_open:
                    ser[ind].close()

        mqttClient.loop_stop()
        mqttClient.disconnect()
        time.sleep(2)
        pass