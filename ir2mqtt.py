#!python3

import irsdk
import time
import paho.mqtt.client as mqtt
import configparser
import yaml
import serial

debug = False
useSerial = False

# this is our State class, with some helpful variables
class State:
    ir_connected = False
    date_time = -1
    pitFlags = -1
    pitFuel = -1
    tick = 0
 
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

        if useSerial:
            ser.close()

        # we are shut down ir library (clear all internal variables)
        ir.shutdown()
        print('irsdk disconnected')
        mqtt_publish('state', 0)
    elif not state.ir_connected:
        if config.has_option('global', 'simulate'):
            is_startup = ir.startup(test_file=config['global']['simulate'])
            print('starting up using dump file: ' + str(config['global']['simulate']))
        else:
            is_startup = ir.startup()
            print('DEBUG: starting up with simulation')

        if is_startup and ir.is_initialized and ir.is_connected:
            state.ir_connected = True
            if useSerial:
                ser.open()
            print('irsdk connected')
            mqtt_publish('state', 1)

def publishSessionTime():
    sToD = ir['SessionTimeOfDay']

    tod = time.strftime("%H:%M:%S", time.gmtime(float(sToD)))
    date = time.strftime("%Y-%m-%d")

    we = ir['WeekendInfo']['WeekendOptions']
        
    if we:
        date = we['Date']

    state.date_time = str(date) + 'T' + tod + '+0200'
    print('session ToD:', state.date_time)
    mqtt_publish('ToD', state.date_time)

def writeSerialData():
    pit_svflags = ir['PitSvFlags']
    if state.pitFlags != pit_svflags:
        ser.write(('#PFL=' + str(pit_svflags) + '*').encode('ascii'))
        state.pitFlags = pit_svflags
        if debug:
            print('DEBUG: serial(' + '#PFL=' + str(pit_svflags) + '*' + ')')

    pit_svfuel = ir['PitSvFuel']
    if state.pitFuel != pit_svfuel:
        ser.write(('#PFU=' + str(pit_svfuel) + '*').encode('ascii'))
        state.pitFuel = pit_svfuel
        if debug:
            print('DEBUG: serial(' + '#PFU=' + str(pit_svfuel) + '*' + ')')

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
    if state.tick % 60 == 1:
        publishSessionTime()
    
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
    
    if ser.is_open:
        writeSerialData()

    # and just as an example
    # you can send commands to iracing
    # like switch cameras, rewind in replay mode, send chat and pit commands, etc
    # check pyirsdk.py library to see what commands are available
    # https://github.com/kutu/pyirsdk/blob/master/irsdk.py#L332
    # when you run this script, camera will be switched to P1
    # and very first camera in list of cameras in iracing
    # while script is running, change camera by yourself in iracing
    # and how it changed back every 1 sec
    #ir.cam_switch_pos(0, 1)

def mqtt_publish(topic, data):
    top = config['mqtt']['baseTopic'] + '/' + topic
    mqttClient.publish(top, data)
    if debug:
        print('DEBUG mqtt_publish(' + top + ', ' + str(data) + ')')
    
def on_connect(client, userdata, flags, rc):
    print('MQTT: ' + mqttRC[rc])
    if rc==0:
        if state.ir_connected:
            mqtt_publish('state', 1)
            if state.date_time != -1:
                mqtt_publish('ToD', state.date_time) 
    else:
        print("Bad connection Returned code=",rc)

def on_disconnect(client, userdata, rc):
    if rc==0:
        print('MQTT: connection terminated')
    else:
        print('MQTT: connection terminated unexpectedly')
        
def banner():
    print("=============================")
    print("|         IR2MQTT           |")
    print("=============================")
    print("MQTT host: " + config['mqtt']['host'])
    print("MQTT port: " + config['mqtt']['port'])
    print("MQTT base: " + config['mqtt']['baseTopic'])

if __name__ == '__main__':
    config = configparser.ConfigParser()    
    try: 
        config.read('ir2mqtt.ini')
    except Exception:
        print('unable to read configuration: ' + Exception.__cause__)

    banner()
    if config.has_option('global', 'debug'):
        debug = config.getboolean('global', 'debug')
        print('Debug output enabled')

    ir = irsdk.IRSDK()
    # initializing ir and state
    state = State()
    
    mqttClient = mqtt.Client("irClient")
    mqttClient.on_connect=on_connect
    mqttClient.on_disconnect=on_disconnect
    mqttClient.loop_start()
    try:
        mqttClient.connect(config['mqtt']['host'], int(config['mqtt']['port']))
    except Exception:
        print('unable to connect to mqtt broker')

    ser = serial.Serial()
    if config.has_option('global', 'serial'):
        ser.port =  config['global']['serial']
        ser.baudrate = 9600
        useSerial = True
        print('using COM port: ' + str(ser.port))

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