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
__date__ = "2022/10/01"
__deprecated__ = False
__email__ = "rbausdorf@gmail.com"
__license__ = "GPLv3"
__status__ = "Production"
__version__ = "1.0"

import timezonefinder
import pytz
import time
from astral import LocationInfo
from astral import Observer
from astral import sun
from astral import SunDirection
from datetime import datetime


class IrLocation:
    zone_finder = None
    date_array = None
    found_zone = None
    timezone = None
    observer = None
    location_info = None
    times_sunset = None
    times_sunrise = None

    def __init__(self, local_date_str, latitude, longitude, elevation, city, region):
        sun.solar_depression = 'civil'
        self.zone_finder = timezonefinder.TimezoneFinder()

        self.date_array = local_date_str.split('-')
        self.found_zone = self.zone_finder.timezone_at(lat=latitude, lng=longitude)
        self.timezone = pytz.timezone(self.found_zone)

        self.observer = Observer(latitude, longitude, elevation)
        print('Location: ', self.observer)

        self.location_info = LocationInfo(city, region, self.found_zone, latitude, longitude)
        print('LocationInfo: ', self.location_info)

        local_date = self.datetime_no_zone("7200")
        self.times_sunset = self.twilight(local_date, SunDirection.SETTING)
        self.times_sunrise = self.twilight(local_date, SunDirection.RISING)

        print("sunrise start  " + str(self.times_sunrise[0]))
        print("sunrise end    " + str(self.times_sunrise[1]))
        print("sunset start " + str(self.times_sunset[0]))
        print("sunset end   " + str(self.times_sunset[1]))

    def datetime_no_zone(self, time_of_day: str) -> datetime:
        local_time = time.localtime(float(time_of_day))

        # Create a datetime object WITHOUT timezone info, so it can be localized to the tracks timezone
        return datetime(int(self.date_array[0]), int(self.date_array[1]), int(self.date_array[2]),
                        local_time.tm_hour, local_time.tm_min, local_time.tm_sec)

    def solar_elevation(self, local_date_time: datetime):
        localized_time = self.timezone.localize(local_date_time)
        print("local time: ", localized_time)

        return sun.elevation(self.observer, localized_time)

    def twilight(self, local_datetime, direction):
        return sun.twilight(self.observer, self.timezone.localize(local_datetime), direction, self.timezone)


if __name__ == '__main__':
    latitude = 27.450094
    longitude = -81.351871
    elevation = 41
    city = "Braselton"
    country = "USA"
    iracing_date_str = "2023 - 03 - 18"
    tod = "66922.0"

    ir_location = IrLocation(iracing_date_str, latitude, longitude, elevation, city, country)
    date_time_no_zone = ir_location.datetime_no_zone(tod)
    angle = ir_location.solar_elevation(date_time_no_zone)
    print('solar elevation: ' + str(angle))

    print(datetime.strftime(date_time_no_zone.astimezone(pytz.timezone('CET')), "%Y-%m-%dT%H:%M:%S%z"))
