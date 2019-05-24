#!python3

import irsdk
import time
import paho.mqtt.client as mqtt
import configparser
import yaml

debug = False
simulate = False

# this is our State class, with some helpful variables
class State:
    ir_connected = False
    last_we_setup_tick = -1
    date_time = -1

mqttRC = ['Connection successful', 
          'Connection refused - incorrect protocol version', 
          'Connection refused - invalid client identifier', 
          'Connection refused - server unavailable', 
          'Connection refused - bad username or password', 
          'Connection refused - not authorised']

class SimData(dict):
    yamlData = None
    telemetryData = None

    def __init__(self, name):
        self.name = name
        
    def load(self, sessionDataFile, telemetryDataFile):
        with open(sessionDataFile, 'r') as stream:
            try:
                self.yamlData = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print(exc)
        with open(telemetryDataFile, 'r') as stream:
            try:
                self.telemetryData = stream.readlines()
                for line in self.telemetryData:
                    property = line.split(' ', 1)
                    self.yamlData[property[0].strip()] = property[1].strip() 
            except Exception as e:
                print(e)

simData = SimData('simData')

# here we check if we are connected to iracing
# so we can retrieve some data
def check_iracing():
    if state.ir_connected and not (ir.is_initialized and ir.is_connected):
        state.ir_connected = False
        # don't forget to reset all your in State variables
        state.last_car_setup_tick = -1
        # we are shut down ir library (clear all internal variables)
        ir.shutdown()
        print('irsdk disconnected')
        mqtt_publish('state', 0)
       

    elif not state.ir_connected and ir.startup() and ir.is_initialized and ir.is_connected:
        state.ir_connected = True
        print('irsdk connected')
        mqtt_publish('state', 1)

def publishSessionTime():
    if not simulate:
        sToD = ir['SessionTimeOfDay']
    else:
        sToD = simData.yamlData['SessionTimeOfDay']

    tod = time.strftime("%H:%M:%S", time.gmtime(float(sToD)))
    date = time.strftime("%Y-%m-%d")

    if not simulate:
        we = ir['WeekendInfo']['WeekendOptions']
    else:
        we = simData.yamlData['WeekendInfo']['WeekendOptions']
        
    if we:
        if not simulate:
            we_tick = ir.get_session_info_update_by_key('WeekendOptions')
            if we_tick != state.last_we_setup_tick:
                date = we['Date']
        else:
            date = simData.yamlData['WeekendInfo']['WeekendOptions']['Date']

    state.date_time = str(date) + 'T' + tod + '+0200'
    print('session ToD:', state.date_time)
    mqtt_publish('ToD', state.date_time)
    
# our main loop, where we retrieve data
# and do something useful with it
def loop():
    # on each tick we freeze buffer with live telemetry
    # it is optional, useful if you use vars like CarIdxXXX
    # in this way you will have consistent data from this vars inside one tick
    # because sometimes while you retrieve one CarIdxXXX variable
    # another one in next line of code can be changed
    # to the next iracing internal tick_count
    if not simulate:
        ir.freeze_var_buffer_latest()

    publishSessionTime()
    
    # retrieve live telemetry data
    # check here for list of available variables
    # https://github.com/kutu/pyirsdk/blob/master/vars.txt
    # this is not full list, because some cars has additional
    # specific variables, like break bias, wings adjustment, etc

    if config.has_section('iracing'):
        for top in config['iracing']:
            ind = config.get('iracing', top).split('/')
            if not simulate:
                val = ir
                for key in ind:
                    if val != None:
                        val = val.__getitem__(key)
            else:
                val = simData.yamlData
                for key in ind:
                    if val != None:
                        if isinstance(val, list):
                            val = val[0].get(key)
                        else:
                            val = val.get(key)

            if val != None:
                mqtt_publish(top, val)
                

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
        print('DEBUG mqtt_publish(' + top + ', ' + data + ')')
    
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

    if config.has_option('global', 'sessionData'):
        simulate = True
        simData.load(config['global']['sessionData'], config['global']['telemetryData'])
        
    # initializing ir and state
    ir = irsdk.IRSDK()
    state = State()
    
    mqttClient = mqtt.Client("irClient")
    mqttClient.on_connect=on_connect
    mqttClient.on_disconnect=on_disconnect
    mqttClient.loop_start()
    try:
        mqttClient.connect(config['mqtt']['host'], int(config['mqtt']['port']))
    except Exception:
        print('unable to connect to mqtt broker')


    try:
        # infinite loop
        while True:
            # check if we are connected to iracing
            if not simulate:
                check_iracing()
            else:
                state.ir_connected = True
                
            # if we are, then process data
            if state.ir_connected:
                loop()
            # sleep for 1 second
            # maximum you can use is 1/60
            # cause iracing update data with 60 fps
            time.sleep(int(config['global']['loopDelay']))
    except KeyboardInterrupt:
        # press ctrl+c to exit
        print('exiting')
        if state.ir_connected:
            mqtt_publish('state', 0)
        
        mqttClient.loop_stop()
        mqttClient.disconnect()
        time.sleep(2)
        pass