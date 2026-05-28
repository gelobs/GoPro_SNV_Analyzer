#!/usr/bin/env python
#
# 17/02/2019 
# Juan M. Casillas <juanm.casillas@gmail.com>
# https://github.com/juanmcasillas/gopro2gpx.git
#
# Released under GNU GENERAL PUBLIC LICENSE v3. (Use at your own risk)
#
import time
from datetime import datetime

from gopro2gpx.fourCC import XYZData, SYSTData, LabelGPSF, GPSData, KARMAGPSData
from gopro2gpx.gpshelper import GPSPoint


def BuildGPSPoints(data, skip=False):
    """
    Data comes UNSCALED so we have to do: Data / Scale.
    Do a finite state machine to process the labels.
    GET
     - SCAL     Scale value
     - GPSF     GPS Fix
     - GPSU     GPS Time
     - GPS5     GPS Data
    """

    points = []
    SCAL = XYZData(1.0, 1.0, 1.0)
    GPSU = None    
    SYST = SYSTData(0, 0)

    stats = { 
        'ok': 0,
        'badfix': 0,
        'badfixskip': 0,
        'empty' : 0
    }

    GPSFIX = 0 # no lock.
    for d in data:
        if d.fourCC == 'SCAL':
            SCAL = d.data
        elif d.fourCC == 'GPSU':
            GPSU = d.data
        elif d.fourCC == 'GPSF':
            if d.data != GPSFIX:
                print("GPSFIX change to %s [%s]" % (d.data, LabelGPSF.xlate[d.data]))
            GPSFIX = d.data
        elif d.fourCC == 'GPS5':
            if d.data.lon == d.data.lat == d.data.alt == 0:
                print("Warning: Skipping empty point")
                stats['empty'] += 1
                continue

            if GPSFIX == 0:
                stats['badfix'] += 1
                if skip:
                    print("Warning: Skipping point due GPSFIX==0")
                    stats['badfixskip'] += 1
                    continue                    

            data = [ float(x) / float(y) for x,y in zip( d.data._asdict().values() ,list(SCAL) ) ]
            gpsdata = GPSData._make(data)
            p = GPSPoint(gpsdata.lat, gpsdata.lon, gpsdata.alt, datetime.fromtimestamp(time.mktime(GPSU)), gpsdata.speed)
            points.append(p)
            stats['ok'] += 1

        elif d.fourCC == 'SYST':
            data = [ float(x) / float(y) for x,y in zip( d.data._asdict().values() ,list(SCAL) ) ]
            if data[0] != 0 and data[1] != 0:
                SYST = SYSTData._make(data)


        elif d.fourCC == 'GPRI':
            # KARMA GPRI info

            if d.data.lon == d.data.lat == d.data.alt == 0:
                print("Warning: Skipping empty point")
                stats['empty'] += 1
                continue

            if GPSFIX == 0:
                stats['badfix'] += 1
                if skip:
                    print("Warning: Skipping point due GPSFIX==0")
                    stats['badfixskip'] += 1
                    continue
                    
            data = [ float(x) / float(y) for x,y in zip( d.data._asdict().values() ,list(SCAL) ) ]
            gpsdata = KARMAGPSData._make(data)
            
            if SYST.seconds != 0 and SYST.miliseconds != 0:
                p = GPSPoint(gpsdata.lat, gpsdata.lon, gpsdata.alt, datetime.fromtimestamp(SYST.miliseconds), gpsdata.speed)
                points.append(p)
                stats['ok'] += 1

    print("-- stats -----------------")
    total_points =0
    for i in stats.keys():
        total_points += stats[i]
    print("- Ok:              %5d" % stats['ok'])
    print("- GPSFIX=0 (bad):  %5d (skipped: %d)" % (stats['badfix'], stats['badfixskip']))
    print("- Empty (No data): %5d" % stats['empty'])
    print("Total points:      %5d" % total_points)
    print("--------------------------")
    return(points)
