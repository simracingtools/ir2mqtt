# ir2mqtt

This application can publish iRacing session an telemetry data on mqtt topics. 
So yor're able to integrate iRacing into many smart home solutions. In addition 
I implemented a small serial protocol which enables you to integrate with an 
Arduino-/Teensy-based buttonbox to reflect pit flags (tyre change, re-fuel etc.)
and refueling amount.

In addition the astronomical twilight times and sun elevation angles are 
calculated by using the geographical track locations from the irsdk information.

My first appliance is to control room lighting to correspond to the simulations 
TimeOfDay so it switches off during the night and is dimmed during sunrise/sunset.

## Prerequisites

You need to install 

* [Python iRacing SDK](https://github.com/kutu/pyirsdk#install), 
* [Python Paho MQTT library](https://pypi.org/project/paho-mqtt/#installation) and 
* [PySerial library](https://pythonhosted.org/pyserial/pyserial.html#installation).
* [Astral](https://astral.readthedocs.io/en/latest/index.html)
* [TimezoneFinder](https://pypi.org/project/timezonefinder/)
* [pytz](http://pytz.sourceforge.net/)

Optionally if you wand to create a self contained executable you may install 
[PyInstaller](https://www.pyinstaller.org/).

## Building

No build steps are necessary to execute the python script:

    python ir2mqtt.py
    
To create a self-contained executable:

    pyinstaller -F ir2mqtt.py

## Configuration

Configuration is done by a configuration file ir2mqtt.ini which is expected in
the same directory as the script/application is executed.

	[DEFAULT]
	
	[global]
	# Generates additional debug output. All MQTT and serial communication will be 
	# logged. Comment out or set to no/false to disable
	debug = yes
	
	# Serial communication port to send data to. Comment out to disable serial
	# communication
	serial = COM3
	
	# Uncomment to start the application using a data dump file from irsdk for 
	# testing/development purposes. The dump file can be created
	# issuing the command 'irsdk --dump data.dmp'
	;simulate = data.dump
	
	[mqtt]
	# Hostname of a MQTT broker to connect to. Comment out to disable MQTT 
	# communication.
	host = localhost
	
	# TCP port on which a MQTT broker is listening. Default is 1883.
	port = 1883
	
	# MQTT topic prefix to use when publishing values - see section [iracing]
	baseTopic = /sensors/iRacing
	
	# Timezone used to publish the simulations TimeOfDay
	timezone=+0200

	[iracing]
	# Mapping of irsdk values to MQTT topics.
	# Format: mqttTopic = irsdkField
	# The configuration key (mqtt topic is prepended by the baseTopic configuration 
	# value). So a configuration line
	# eventType = WeekendInfo/EventType
	# will post the EventType value from the iRacing WeekendInfo data structure on
	# the MQTT topic '/sensors/iRacing/eventType'
	eventType = WeekendInfo/EventType
	sessionType = SessionInfo/Sessions/SessionType

## State and TimeOfDay publishing

Indepentently from the configuration to the application the IRSDK connection 
state is published on the topic 

	<baseTopic>/state 
	
as numerical value:

* 0 means irsdk is disconnected from the simulation
* 1 means irsdk is connected to the simulation

The date and time of day information is translated from the original track 
timezone to the timezone specified in the configuration and  published on the 
topic 

	<baseTopic>/ToD 

in ISO format:

	%Y-%m-%dT%H:%M:%S<+/-ffff>

For example Noon at 2nd of March 2019 in the CEST timezone is published as

	2019-03-02T12:00:00+0200

## Light information publishing

Independently from the configuration a lightinfo information is calculated which
can be one of the following values: dusk, day, dawn, night. This value is
published on the MQTT topic:

	<baseTopic>/lightinfo

The current elevation angle of the sun for the simulation time of day at the 
track location is published as a float value on the topic:

	<baseTopic>/solarElevation

Negative value means the sun is below the horizon, positive value means it's
above.

## Serial telegram protocol

The protocol is string-based to enable best integration an interoperability 
possibilities.

The basic form is 

	*KEY=VALUE#

There is no length assumption on the KEY and VALUE part but I decided to use
short 3-letter keys because of the limited memory capacities for a microcontroller.

Currently three telegrams are supported:

	*PFL=<int>#   (outbound)
	*PFU=<int>#   (inbound/outbound)
	*PCM=<int>#   (inbound)

Outbound means communication from application to microcontroller, inbound means
microcontroller to application.

### PFL telegram

This telegram submits the PitSvFlags telemetry value. The Number submitted
can be evaluated by bit comparison defined in irsdk.py:

    lf_tire_change     = 0x01
    rf_tire_change     = 0x02
    lr_tire_change     = 0x04
    rr_tire_change     = 0x08
    fuel_fill          = 0x10
    windshield_tearoff = 0x20
    fast_repair        = 0x40

### PFU telegram

This telegram submits the PitSvFuel telemetry value. The number submitted is the
current amount of fuel to be added during next pit stop. The unit of the value
depends on the simulation configuration and is not taken into account in any way.

On the receiving side this telegram triggers the 

	irsdk.pit_command(PitCommandMode.fuel, <telegramValue>)

function, submitting the telegram's VALUE as fuel amount to add. As implemented
in IRSDK this amount is always taken in liters as unit.

### PCM telegram

This telegram receives a pit command and triggers the

	irsdk.pit_command(<telegramValue>)

function. The usable parameters are:

    clear       =  0 # Clear all pit checkboxes
    ws          =  1 # Clean the winshield, using one tear off
    fuel        =  2 # Add fuel, optionally specify the amount to add in liters or pass '0' to use existing amount
    lf          =  3 # Change the left front tire, optionally specifying the pressure in KPa or pass '0' to use existing pressure
    rf          =  4 # right front
    lr          =  5 # left rear
    rr          =  6 # right rear
    clear_tires =  7 # Clear tire pit checkboxes
    fr          =  8 # Request a fast repair
    clear_ws    =  9 # Uncheck Clean the winshield checkbox
    clear_fr    = 10 # Uncheck request a fast repair
    clear_fuel  = 11 # Uncheck add fuel

Please note that a second parameter is not supported in the current implementation,
so there is currently to way to change tyre pressures. For changing the refuel
amount see PFU telegram.
 
### Reading/writing telegrams on Arduino/Teensy

For your convenience here a function you can use in a sketch to read the 
telegrams from the serial port on an Arduino/Teensy microcontroller:

	String readTelegramFromSerial() {
	  char buff[10];
	  int dataCount = 0;
	  boolean startData = false;
	  while(Serial.available()) {
	    char c = Serial.read();
	    if( c == '#' ) {
	      startData = true;
	    } else if( startData && dataCount < 10) {
	      if( c != '*') {
	        buff[dataCount++] = c;
	      } else {  
	        break;
	      }
	    } else if(dataCount >= 10) {
	      return String();
	    }
	  }
	  if( startData || dataCount > 0 ) {
	    return String(buff);
	  }
	  return String();
	}

The function returns the telegram content excluding the start and end marker
characters '#' and '*'. For example the serial telegram '#PFU=10*' is returned
as PFU=10. You can separate the key and value with simple string operations:

	void processTelegram(String* telegram) {
	  int idx = telegram->indexOf('=');
	  if( idx > 0 ) {
	    String key = telegram->substring(0, idx);
	    String val = telegram->substring(idx+1);
	    if( key.equals("PFL") ){
	      processFlags(val.toInt());
	    } else if( key.equals("PFU") ) {
	      processFuel(val.toFloat());
	    }
	  }
	}

You have to provide the processFlags and processFuel functions to handle your
controller specific actions.

To evaluate the pit service flags, this structure may be helpful:

	struct PitSvFlags {
	    byte lf_tire_change;
	    byte rf_tire_change;
	    byte lr_tire_change;
	    byte rr_tire_change;
	    byte fuel_fill;
	    byte windshield_tearoff;
	    byte fast_repair;
	};
	    
	PitSvFlags pitFlags = {0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40};

On the sending site the pit command numbers may be helpful:

	struct PitCommand {
	    clear;       // Clear all pit checkboxes
	    ws;          // Clean the winshield, using one tear off
	    fuel;        // Add fuel, optionally specify the amount to add in liters or pass '0' to use existing amount
	    lf;          // Change the left front tire, optionally specifying the pressure in KPa or pass '0' to use existing pressure
	    rf;          // right front
	    lr;          // left rear
	    rr;          // right rear
	    clear_tires; // Clear tire pit checkboxes
	    fr;          // Request a fast repair
	    clear_ws;    // Uncheck Clean the winshield checkbox
	    clear_fr;    // Uncheck request a fast repair
	    clear_fuel;  // Uncheck add fuel
	}
	
	PitCommand pitCmd = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};

A sending function can be implemented as follows:

	void sendPitCmd(int cmd) {
	  Serial.print("#PCM=" + String(cmd) + "*\n");
	}

	void sendPitFuelCmd(int amount) {
	  Serial.print("#PFU=" + String(amount) + "*\n");
	}

Please not the trailing newline character. It is required as ir2mqtt uses
the serial readline() function to receive telegrams.
