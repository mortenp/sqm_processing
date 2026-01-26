from fastapi import FastAPI, UploadFile, File, Query, APIRouter, Form
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse, HTMLResponse
from astropy.time import Time
from astropy.coordinates import get_sun, get_body, EarthLocation, AltAz, SkyCoord #get_moon, 
from astropy import units as u
from datetime import datetime
import numpy as np
from collections import deque
import io
import re
import os
import traceback
from collections import deque
import matplotlib.pyplot as plt
from scipy.interpolate import make_interp_spline, interp1d

#import astropy.visualization
import pandas as pd
from datetime import datetime
from pathlib import Path

import random
import math

import logging
import mysql.connector
from mysql.connector import Error
import json

import matplotlib
matplotlib.rcParams['font.family'] = 'DejaVu Sans'  # or 'Liberation Sans', 'Arial', etc.
matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']

# startup:
# source /srv/www/d9.pihl.net/public_html/sqm_processing/venv/bin/activate
# uvicorn my_sqm_service:app --host 127.0.0.1 --port 8090
# uvicorn my_sqm_service:app --host 127.0.0.1 --port 8090 --reload --log-level debug


#%matplotlib inline

#logging.basicConfig(filename="/tmp/sqm_service.log", level=logging.DEBUG)
logging.basicConfig(filename="/srv/www/d9.pihl.net/public_html/sqm_processing/logs/sqm_service.log", level=logging.DEBUG)
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

app = FastAPI(title="SQM Processing Service")

# Initialize cache on startup
@app.on_event("startup")
async def startup_event():
    """Initialize database cache on application startup"""
    init_cache_db()

UPLOAD_DIR = "/srv/www/d9.pihl.net/public_html/sqm_processing/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

DOWNLOAD_DIR = "/srv/www/d9.pihl.net/public_html/sqm_processing/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ---------------- CONFIGURATION DEFAULTS ----------------
DEFAULT_ROLL_DURATION_MIN = 15
DEFAULT_STDEV_THRESHOLD = 0.05
SUN_LIMIT_DEG = -20
MOON_LIMIT_DEG = -10
MPSAS_LIMIT = 18
debug = 0
TESTMODE = 0

DEFAULT_LAT = 56.04
DEFAULT_LONG = 10.87  

# Milky Way calc - parameters (make configurable)
BASE_MW_SB = 20.5                  # mag/arcsec^2 at zenith (tune to your site)
BASE_MW_SB_AT_PLANE = 20.0         # mag/arcsec² for zenith lying exactly on galactic plane
PLANE_TO_POLE_FADE = 2.5           # additional mag from b=0 -> |b|=90 (tune to site)
EXTINCTION_COEFF = 0.15            # mag per airmass (typical site value)
MW_SB_THRESHOLD = 21.5             # max mag/arcsec^2 to consider "Milky Way visible"

LIMIT_SERIALS = 0
# --------------------------------------------------------

# MySQL Caching Configuration
CACHE_ENABLED = True  # Set to False to disable caching
CACHE_TIME_BUCKET_MIN = 20  # Cache granularity: 20 minutes

DB_CONFIG = {
    'host': 'localhost',
    'user': 'morten',
    'password': 'mp120mp120',  # Change this
    'database': 'sqm_cache',
    'raise_on_warnings': False
}

lat = None
lon = None
location_name = None
serial_number = None

ALLOWED_SERIALS = "2586,2588,6849,3387,6362,6860,6852,6859,6851,6857,6854,LANGELAND:,7118,7110,7115,7116,7122,7108,7109,7107"



def scale_series(series, new_min, new_max):
    arr = np.array(series)
    scaled = (arr - arr.min()) / (arr.max() - arr.min())  # scale to 0–1
    return scaled * (new_max - new_min) + new_min
    
    
def airmass_kasten(z_deg):
    """Kasten & Young (1989) airmass approximation. z_deg = zenith angle in degrees."""
    if z_deg >= 90:
        return float('inf')
    z = z_deg
    return 1.0 / (math.cos(math.radians(z)) + 0.50572 * (96.07995 - z) ** -1.6364)

def estimate_mw_surface_brightness(airmass, base_sb=20.5, extinction_coeff=0.15):
    """
    Simple MW surface brightness proxy in mag/arcsec^2.
    base_sb: assumed MW surface brightness at zenith (mag/arcsec^2). Smaller = brighter.
    extinction_coeff: mag lost per airmass (typical 0.12-0.4 depending on site).
    Returns estimated SB (mag/arcsec^2).
    """
    # extra extinction relative to zenith
    extra_mag = extinction_coeff * (airmass - 1.0)
    return base_sb + extra_mag


# ==================== CACHING FUNCTIONS ====================

def round_location(lat, lon):
    """
    Round latitude and longitude for cache key optimization.
    Latitude: rounded to nearest whole degree
    Longitude: rounded to nearest 0.5 degree
    
    This reduces cache fragmentation while maintaining sufficient precision
    for astronomical calculations (input precision is 0.001 degree).
    """
    if lat is None or lon is None:
        return None, None
    
    # Round latitude to nearest 1.0 degree
    lat_rounded = round(float(lat), 0)
    
    # Round longitude to nearest 0.5 degree
    lon_rounded = round(float(lon) * 2) / 2
    
    return lat_rounded, lon_rounded


def init_cache_db():
    """Initialize MySQL database for caching celestial calculations"""
    try:
        conn = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password']
        )
        cursor = conn.cursor()
        
        # Create database if it doesn't exist
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']}")
        cursor.execute(f"USE {DB_CONFIG['database']}")
        
        # Create cache table
        create_table_query = """
        CREATE TABLE IF NOT EXISTS celestial_cache (
            id INT AUTO_INCREMENT PRIMARY KEY,
            lat DECIMAL(10, 6) NOT NULL,
            lon DECIMAL(10, 6) NOT NULL,
            time_bucket DATETIME NOT NULL,
            sun_alt FLOAT,
            moon_alt FLOAT,
            mw_brightness FLOAT,
            milky_way_visible BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_calc (lat, lon, time_bucket)
        )
        """
        cursor.execute(create_table_query)
        conn.commit()
        cursor.close()
        conn.close()
        logging.info("Cache database initialized successfully")
        return True
    except Error as e:
        logging.warning(f"Cache DB initialization failed: {e}. Caching disabled.")
        return False


def get_time_bucket(t_astropy, bucket_minutes=CACHE_TIME_BUCKET_MIN):
    """Round time to nearest bucket for caching"""
    dt = t_astropy.datetime
    bucket_seconds = bucket_minutes * 60
    epoch = datetime(1970, 1, 1)
    diff = (dt - epoch).total_seconds()
    rounded_diff = (diff // bucket_seconds) * bucket_seconds
    return epoch + __import__('datetime').timedelta(seconds=rounded_diff)


def get_cache(lat, lon, t_astropy):
    """Retrieve cached celestial values for location and time"""
    if not CACHE_ENABLED:
        return None
    
    try:
        # Round location for cache lookup
        lat_rounded, lon_rounded = round_location(lat, lon)
        if lat_rounded is None or lon_rounded is None:
            return None
        
        ## fixed lat lon for testing
        lat_rounded = 55
        lon_rounded = 12.5
        
        time_bucket = get_time_bucket(t_astropy)
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        query = """
        SELECT sun_alt, moon_alt, mw_brightness, milky_way_visible 
        FROM celestial_cache 
        WHERE lat = %s AND lon = %s AND time_bucket = %s
        """
        cursor.execute(query, (lat_rounded, lon_rounded, time_bucket))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            logging.debug(f"Cache HIT: {lat_rounded}, {lon_rounded}, {time_bucket}")
            return result
        else:
            logging.debug(f"Cache MISS: {lat_rounded}, {lon_rounded}, {time_bucket}")
            return None
    except Error as e:
        logging.warning(f"Cache retrieval failed: {e}")
        return None


def set_cache(lat, lon, t_astropy, sun_alt, moon_alt, mw_brightness, milky_way_visible):
    """Store calculated celestial values in cache"""
    if not CACHE_ENABLED:
        return False
    
    try:
        # Round location for cache storage
        lat_rounded, lon_rounded = round_location(lat, lon)
        if lat_rounded is None or lon_rounded is None:
            return False
        
        time_bucket = get_time_bucket(t_astropy)
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        query = """
        INSERT INTO celestial_cache (lat, lon, time_bucket, sun_alt, moon_alt, mw_brightness, milky_way_visible)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE 
            sun_alt = VALUES(sun_alt), 
            moon_alt = VALUES(moon_alt),
            mw_brightness = VALUES(mw_brightness),
            milky_way_visible = VALUES(milky_way_visible)
        """
        cursor.execute(query, (lat_rounded, lon_rounded, time_bucket, sun_alt, moon_alt, mw_brightness, milky_way_visible))
        conn.commit()
        cursor.close()
        conn.close()
        logging.debug(f"Cache stored: {lat_rounded}, {lon_rounded}, {time_bucket}")
        return True
    except Error as e:
        logging.warning(f"Cache storage failed: {e}")
        return False

    


def parse_header(file, max_lines=50):
    """Extract latitude, longitude, and header line count from the first lines"""
    
    global lat
    global lon
    global location_name
    global serial_number

    
    print(f"parse_header file: {file}")
    header_lines = []
    for _ in range(max_lines):
        line = file.readline()
        if not line:
            break
        header_lines.append(line)
        lat_match = re.search(
            r'Position \(lat, lon, elev\(m\)\): ([\d\.-]+), ([\d\.-]+), (\d+)', line
        )
        if lat_match:
            lat, lon, _ = map(float, lat_match.groups())
            
        location_match = re.search(
            r'Location name:\s+(.*)$', line
        )
        if location_match:
            location_name = location_match.group(1)
        
        serial_match = re.search(
            r'SQM serial number:\s+(.*)$', line
        )
        if serial_match:
            serial_number = serial_match.group(1)
            logging.debug(f"Found serial from header: {serial_number}")

   
                 
    if lat is None or lon is None:
        logging.warning("Could not extract location from header, using default")
        location = EarthLocation(lat=DEFAULT_LAT*u.deg, lon=DEFAULT_LONG*u.deg)
    else:
        logging.debug(f"Parsed location: {lat}, {lon}")
        location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)

    if location_name is None:
        location_name = "Unknown location"
        
# Location name: Møns Klint

    return lat, lon, location_name, serial_number, len(header_lines)

def parse_time(tstr):
    """Parse UTC timestamp from file"""
    match = re.search(r'(\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)', tstr)
    if not match:
        #return None
        return Time(datetime(2025, 10, 15, 10, 20, 30), scale='utc')
    y, m, d, H, M, S = map(int, match.groups())
    return Time(datetime(y, m, d, H, M, S), scale='utc')


def process_stream(file_path, output_file_path, mpsas_limit, sun_max_alt=SUN_LIMIT_DEG, moon_max_alt=MOON_LIMIT_DEG,
                   roll_duration_min=DEFAULT_ROLL_DURATION_MIN,
                   stdev_threshold=DEFAULT_STDEV_THRESHOLD, mw_sb_threshold=MW_SB_THRESHOLD, testmode=0):
    from astropy.time import Time
    from astropy.coordinates import EarthLocation, AltAz, get_sun, get_body
    import astropy.units as u
    import numpy as np
    from collections import deque
    from datetime import timedelta, datetime
    
    # Using module-level configuration constants



    
    output = "output:\n"
    #print(f"process_stream file: {file_path}")
    logging.debug(f"process_stream file: {file_path}")
    #output = output + f"processing file: {file_path} \nwith params mpsas_limit {mpsas_limit} sun_max_alt {sun_max_alt} moon_max_alt {moon_max_alt} \nroll_duration_min {roll_duration_min} stdev_threshold {stdev_threshold}\n"
    output = output + f"Processing file with params: \nmpsas_limit {mpsas_limit} \nsun_max_alt {sun_max_alt} \nmoon_max_alt {moon_max_alt} \nroll_duration_min {roll_duration_min} \nstdev_threshold {stdev_threshold}\n"
    
    buffer = deque()  # stores (Time, MPSAS)
    linecounter = 0
    last_altitude_time = None
    sun_alt = moon_alt = None
    roll_duration_td = timedelta(minutes=roll_duration_min)
    total_mpsas = 0
    used_lines = 0
    
    last_timestamp = None
    last_time_diff_min = 0
    
    last_milky_way_visible = False
    
    line_limit = 10000000
    
    max_mpsas = 0;
    
    if testmode > 0:
        file_path = "/srv/www/d9.pihl.net/public_html/sqm_processing/uploads/20240522_220724_DSMN-2.dat"
        logging.debug(f"testmode: {testmode}")
    
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f, \
        open(output_file_path, "w") as out:
        logging.debug(f"reading file: {file_path}")
        # write header
        out.write("UTC_TIME;LOCAL_TIME;SUN_ALT;MOON_ALT;MPSAS;MW_BRIGHTNESS;MW_VISIBLE;ROLL_STDEV\n")
        ###                out.write(f"{utc_str};{local_str};{sun_alt:.3f};{moon_alt:.3f};{mpsas:.3f};{mw_sb:.2f};{milky_way_visible};{roll_stdev:.4f}\n")

        # parse header for location
        lat, lon, location_name, serial_number, header_len = parse_header(f)
        if lat is None or lon is None:
            logging.debug(f"Could not extract location from header, using default 55N/12E")
            #print("Could not extract location from header, using default 55N/12E")
            location = EarthLocation(lat=55*u.deg, lon=12*u.deg)
            output = output + f"<strong>Missing location, using default: {lat}:{lon}\n"
            output = output + f"Sunrise, Sunset, Moonrise and Moonset times will not be precise</strong>\n"
        else:
            location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
            logging.debug(f"Location from header: {lat}:{lon}")
            output = output + f"Location: {lat}:{lon}\n"
            
        
        output = output + f"Location name: {location_name}\n"
        logging.debug(f"serial_number: {serial_number}")
        
            #print(f"Location: {lat}, {lon}")
        
        
        output = output + f"Header lines: {header_len}\n"
        
            
            
        logging.debug(f"header_len: {header_len}")
        
        # skip remaining header lines#
#        for _ in range(header_len):
#            next(f)


        if serial_number not in ALLOWED_SERIALS:
            line_limit = 99
            logging.debug(f"serial_number {serial_number} not in registered, line_limit {line_limit} ")
        else:
            line_limit = 100000000
            logging.debug(f"serial_number {serial_number} is registered, line_limit {line_limit} ")
        # only limit if LIMIT_SERIALS is set
        if (LIMIT_SERIALS < 1):
            line_limit = 100000000

        logging.debug(f"skipping headers: {header_len}")
        prev_times = deque(maxlen=5)
        logging.debug(f"Processing lines")
        for line in f:
            linecounter += 1
            line = line.strip()
            #logging.debug(f"Line: {linecounter}: {line}")
            
            if not line:
                continue
            parts = line.split(";")
            if len(parts) < 6:
                logging.warning(f"Skipping malformed line {linecounter}: {line}")
                continue
                #UTC Date & Time, Local Date & Time, Temperature, Voltage, MSAS, Record type
                #2024-03-19T16:07:05.000;
                #2024-03-19T17:07:05.000;
                #5.8;
                #4.96;
                #0.00;
                #1
            utc_str, local_str, temp, volt, mpsas_str, dtype = parts[:6]

            try:
                mpsas = float(mpsas_str)
                #logging.debug(f"mpsas {mpsas}")
                t = parse_time(local_str)
                if t is None:
                    logging.exception(f"Error parsing time in line {linecounter}")
                    continue
            except Exception:
                logging.exception(f"Error parsing line {linecounter}")
                continue
            
            # append to rolling buffer
            #logging.debug(f"appending to buffer t mpsas {t} {mpsas}")
            buffer.append((t, mpsas))
            #logging.debug(f"cutoff calc")
            cutoff = t - (roll_duration_min * u.min).to(u.day)
            #cutoff = t - roll_duration_td.to(u.day)
            #logging.debug(f"cutoff: {cutoff}")
            buffer = deque([(tt, mm) for tt, mm in buffer if tt > cutoff])
            #logging.debug(f"done appending to buffer and deque")
            #logging.debug(f"going to check last_altitude_time and roll_duration_td")
            
            # update sun/moon altitudes only if elapsed > roll_duration_min
            #logging.debug(f"checking last_altitude_time {last_altitude_time} ")
            
 


    # parse timestamp into a datetime object
            #ts = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%S")
            if last_timestamp is not None:
                time_diff_min = (t.datetime - last_timestamp.datetime).total_seconds() / 60.0
                logging.debug(f"time_diff_min: {time_diff_min}")
                output = output + f"Measurement interval: {time_diff_min}\n"
                #if(time_diff_min != last_time_diff_min):
                roll_duration_min = 3 * time_diff_min
                #logging.debug(f"time_diff_min={time_diff_min:.2f}, roll_duration_min={roll_duration_min:.2f}")
            else:
                roll_duration_min = DEFAULT_ROLL_DURATION_MIN
            
            if (debug > 0):
                logging.debug(f"last_time_diff_min: {last_time_diff_min}")

#             if (last_time_diff_min != time_diff_min):
#              logging.debug(f"{time_diff_min} != {last_time_diff_min}")
#                 time_diff_min = (t.datetime - last_timestamp.datetime).total_seconds() / 60.0
#                 output = output + f"<strong>Changed measurement interval: {time_diff_min}</strong>\n"
#                 #if(time_diff_min != last_time_diff_min):
#                 roll_duration_min = 3 * time_diff_min
            
#             last_timestamp = t
#             last_time_diff_min = time_diff_min

#             if last_timestamp is not None:
#                 time_diff_min = (t - last_timestamp).total_seconds() / 60
#                 roll_duration_min = 3 * time_diff_min
#                 logging.debug(f"time_diff_min: {time_diff_min:.2f}, roll_duration_min: {roll_duration_min:.2f}")
#             else:
#                 logging.debug("first timestamp, skipping diff")
# 
#             last_timestamp = t            
#             #roll_duration_td = (roll_duration_min * u.min).to(u.day)
# 
#             logging.debug(f"roll_duration_td: {roll_duration_td}")
#             roll_duration_min = (time_diff * u.min * 3).to(u.day)
#             logging.debug(f"set roll_duration_min: {roll_duration_min}")
            
            if last_altitude_time is None or (t - last_altitude_time) > (roll_duration_min * u.min).to(u.day):
                # First calculate sun altitude to check if it's below horizon
                altaz = AltAz(obstime=t, location=location)
                sun_alt = get_sun(t).transform_to(altaz).alt.deg
                
                # Only process when sun is below horizon (sun_alt < 0)
                if sun_alt < 0:
                    cache_result = get_cache(lat, lon, t)
                    
                    if cache_result:
                        # Use cached values
                        moon_alt = cache_result['moon_alt']
                        mw_sb = cache_result['mw_brightness']
                        milky_way_visible = cache_result['milky_way_visible']
                        logging.debug(f"Using cached values: sun_alt={sun_alt:.2f}, moon_alt={moon_alt:.2f}, mw_sb={mw_sb:.2f}")
                    else:
                        # Calculate remaining values
                        moon_alt = get_body("moon", t, location=location).transform_to(altaz).alt.deg
                        
                        # compute zenith direction and its galactic latitude
                        zen_altaz = AltAz(obstime=t, location=location, alt=90*u.deg, az=0*u.deg)  # az arbitrary at zenith
                        zenith = SkyCoord(zen_altaz)                     # create SkyCoord in AltAz then transform
                        zenith_gal = zenith.transform_to('galactic')
                        b_deg = abs(zenith_gal.b.deg)
                        
                        # scale base surface brightness by galactic latitude.
                        # simple linear fade: at plane b=0 -> BASE_MW_SB_AT_PLANE
                        # at poles b=90 -> BASE_MW_SB_AT_PLANE + PLANE_TO_POLE_FADE
                        mw_sb_plane = BASE_MW_SB_AT_PLANE + (PLANE_TO_POLE_FADE * (b_deg / 90.0))
                        
                        # compute airmass for zenith direction (zenith angle = 90 - alt = 0 for zenith)
                        # airmass at zenith is 1.0, but keep formula for completeness if you sample off-zenith
                        zen_alt = 90.0 - 90.0   # zero
                        airmass = 1.0
                        
                        # apply extinction
                        mw_sb = mw_sb_plane + EXTINCTION_COEFF * (airmass - 1.0)
                        
                        # visible boolean
                        milky_way_visible = (mw_sb <= mw_sb_threshold)
                        
                        # Store in cache
                        set_cache(lat, lon, t, sun_alt, moon_alt, mw_sb, milky_way_visible)
                        
                        # logging
                        if (debug > 0):
                            logging.debug(f"zenith b={b_deg:.2f}°, mw_sb_plane={mw_sb_plane:.2f}, mw_sb={mw_sb:.2f}, visible={milky_way_visible}")
                    
                    if (milky_way_visible != last_milky_way_visible):
                        last_milky_way_visible = milky_way_visible
                        logging.debug(f"change: milky_way_visible: {milky_way_visible}")

                    if (debug > 0):
                        logging.debug(f"moon_alt: {moon_alt}")
                        logging.debug(f"sun_alt: {sun_alt}")
                else:
                    # Daytime (sun_alt >= 0): skip all calculations
                    logging.debug(f"Daytime (sun_alt={sun_alt:.2f}), skipping sky quality calculations")
                
                last_altitude_time = t
                    
                    
            # only calculate roll_stdev if both sun/moon are below limits
            if sun_alt is not None and moon_alt is not None and \
            sun_alt < sun_max_alt and moon_alt < moon_max_alt:
                roll_stdev = np.std([mm for _, mm in buffer]) if len(buffer) >= 2 else np.nan
                if not np.isnan(roll_stdev) and roll_stdev < stdev_threshold:
                    if (mpsas > mpsas_limit):
                        #logging.debug(f"mpsas: {mpsas} > {mpsas_limit}")
                        out.write(f"{utc_str};{local_str};{sun_alt:.3f};{moon_alt:.3f};{mpsas:.3f};{mw_sb:.2f};{milky_way_visible};{roll_stdev:.4f}\n")
                    #print(f"Output line: {utc_str};{local_str};{sun_alt:.3f};{moon_alt:.3f};{mpsas:.3f};{roll_stdev:.4f}")
                        total_mpsas = total_mpsas + mpsas
                        used_lines = used_lines + 1;
                        
                        # keep maximum mpsas in file
                        if mpsas > max_mpsas:
                            max_mpsas = mpsas
                        
                        
            if (used_lines > line_limit):
                logging.info(f"break after {linecounter} lines, used_lines {used_lines}")
                output = output + f"Ending after {used_lines} good lines, because your device is not registered\n"
                break
                
                
    print(f"Finished processing {linecounter} lines, {used_lines} saved to {output_file_path}")
    logging.info(f"Finished processing {linecounter} lines, {used_lines} saved to {output_file_path}")
    if (used_lines > 0):
        average_mpsas = total_mpsas/used_lines
        logging.info(f"average_mpsas {average_mpsas}")
    else:
        average_mpsas = 0
        logging.info(f"no lines for average_mpsas {average_mpsas}")
    output = output + f"Finished processing {linecounter} lines\n{used_lines} saved\n"
    output = output + f"Average MPSAS {average_mpsas:.2f} \n"
    output = output + f"Maximum MPSAS {max_mpsas:.2f} \n"

    return location_name, average_mpsas, serial_number, output


@app.post("/process")
async def process_file(
    file: UploadFile = File(...),
    roll_duration: int = Form(DEFAULT_ROLL_DURATION_MIN),
    stdev_threshold: float = Form(DEFAULT_STDEV_THRESHOLD),
    moon_max_alt: int = Form(-10),  # default matches your HTML
    sun_max_alt: int = Form(-20),
    mpsas_limit: float = Form(MPSAS_LIMIT),
    mw_sb_threshold: float = Form(MW_SB_THRESHOLD),
    testmode: int = Form(TESTMODE)
):   

#    global testmode
    
# async def process_file(file: UploadFile = File(...), sun_max_alt: int = Query(SUN_LIMIT_DEG, description="Sun max altitude"), moon_max_alt: int = Query(MOON_LIMIT_DEG, description="Moon max altitude"), roll_duration: int = Query(DEFAULT_ROLL_DURATION_MIN, description="Rolling window duration in minutes"), stdev_threshold: float = Query(DEFAULT_STDEV_THRESHOLD, description="Max rolling stdev for MPSAS"), mpsas_limit: float = Query(MPSAS_LIMIT, description="Minimum MPSAS") ):
    
    logging.debug(f"/process mpsas_limit {mpsas_limit} sun_max_alt {sun_max_alt} testmode {testmode}")
    try:
        save_path = os.path.join(UPLOAD_DIR, file.filename)
        processed_filename = f"processed_{file.filename}"
        processed_path = os.path.join(DOWNLOAD_DIR, processed_filename)
        #processed_path = os.path.join(DOWNLOAD_DIR, f"processed_{file.filename}")
        # Stream file to disk in chunks
        with open(save_path, "wb") as f:
            while chunk := await file.read(1024*1024):  # 1 MB chunks
                f.write(chunk)

        # Debug: confirm upload
        size = os.path.getsize(save_path)
        res = f"Received file: {file.filename}, size={size} bytes\n"
        #print(f"Received file: {file.filename}, size={size} bytes, saved at {save_path}")
        
#process_stream(file_path, output_file_path, mpsas_limit=MPSAS_LIMIT, sun_max_alt=SUN_LIMIT_DEG, moon_max_alt=MOON_LIMIT_DEG,
#                   roll_duration_min=DEFAULT_ROLL_DURATION_MIN,
#                   stdev_threshold=DEFAULT_STDEV_THRESHOLD):
        # TODO: replace with your SQM processing logic
        # You can open save_path and process line by line or in chunks
        
        
        location_name, average_mpsas, serial_number, result = process_stream(save_path, processed_path, mpsas_limit, sun_max_alt, moon_max_alt, roll_duration, stdev_threshold, mw_sb_threshold, testmode)
        res = res + result
        logging.debug(f"location_name {location_name}")
        logging.debug(f"average_mpsas {average_mpsas:.2f}")
        logging.debug(f"serial_number {serial_number}")
        
        logging.debug(f"making plot")
        # Path to processed file
        processed_file = f"/srv/www/d9.pihl.net/public_html/sqm_processing/downloads/{processed_filename}"
        png_file = f"/srv/www/d9.pihl.net/public_html/sqm_processing/downloads/{processed_filename}.png"
        randomnumber = random.randint(10, 2000)
        png_url = f"/sqm_processing/downloads/{processed_filename}.png?{randomnumber}"
        
        if testmode > 0:
            logging.debug(f"testmode {testmode}")
            processed_file = '/srv/www/d9.pihl.net/public_html/sqm_processing/downloads/processed_20240522_220724_DSMN-2.dat'
            png_file = f"/srv/www/d9.pihl.net/public_html/sqm_processing/downloads/test.png"
            png_url = f"/sqm_processing/downloads/test.png?{randomnumber}"
            
#out.write("UTC_TIME;;SUN_ALT;MOON_ALT;MPSAS;ROLL_STDEV\n")
# Load data
        logging.debug(f"reading data {processed_file}")
        csv_file = Path(processed_file)
        
        
        
        try:
            my_abs_path = csv_file.resolve(strict=True)
        except FileNotFoundError:
            logging.debug(f"cant find {csv_file}")
    # doesn't exist
        else:
        
            df = pd.read_csv(processed_file, sep=";", parse_dates=["LOCAL_TIME"])
            logging.debug(f"has read data {processed_file}")
# Plot

# scale MOON_ALT to 15–22
#            df["MOON_ALT_SCALED"] = scale_series(df["MOON_ALT"], mpsas_limit, 22)
#            df["SUN_ALT_SCALED"] = scale_series(df["SUN_ALT"], mpsas_limit, 22)
#            df["MW_BRIGHTNESS_SCALED"] = scale_series(df["MW_BRIGHTNESS"], 20, 21)

# x = np.array([1, 2, 3, 4, 5, 6, 7, 8])
# y = np.array([20, 30, 5, 12, 39, 48, 50, 3])

#            X_Y_Spline = make_interp_spline(df["LOCAL_TIME"], df["MW_BRIGHTNESS_SCALED"])
#            cubic_interpolation_model = interp1d(df["LOCAL_TIME"], df["MW_BRIGHTNESS_SCALED"], kind = "cubic")
#            Y_=cubic_interpolation_model(X_)
#            plt.plot(X_, Y_,label='MW brightness')

# ax1.plot(x, x)
# ax1.set_yscale('asinh')
# ax1.grid()
# ax1.set_title('asinh')


            logging.debug(f"has scaled data")
            plt.figure(figsize=(10, 10))
#            set_yscale('asinh')
#             facecolor=dodgerblue
#             skyblue
#             gold
#             orange
            plt.plot(df["LOCAL_TIME"], df["MPSAS"], marker="o", linestyle="dotted", color="skyblue", label='MPSAS')
            logging.debug(f"has plotted mpsas data")
            #plt.plot(df["LOCAL_TIME"], df["MOON_ALT_SCALED"], marker="o", linestyle="none", color="orange", label='Moon alt')
            #plt.plot(df["LOCAL_TIME"], df["SUN_ALT_SCALED"], marker="o", linestyle="none", color="gold", label='Sun alt')
            plt.plot(df["LOCAL_TIME"], df["MW_BRIGHTNESS"], marker="o", linestyle="dotted", color="orange", label='MW brightness')
#             logging.debug(f"has plotted mw data")
            plt.xlabel("Local Time")
            plt.ylabel("MPSAS")
            plt.title(f"SQM MPSAS over time at {location_name}")
            
            #scale_to_range(arr, new_min=0, new_max=1):
            
            # ({lat}, {lon})
            #leg = ax.legend(loc="lower left")
            
            #plt.legend(["MPSAS", "MOON_ALT", "SUN_ALT"], loc="lower right")
            
            plt.grid(True)
            plt.tight_layout()
            plt.legend(loc="lower right")
            
            logging.debug(f"saving plot {png_file}")
# Save figure
            plt.savefig(png_file)
            plt.close()


        download_url = f"/sqm_processing/downloads/{processed_filename}"
        randomnumber = random.randint(10, 2000)
        #({lat}, {lon})
        html_content = f"""
        <html>
            <head><title>SQM Processing Result</title></head>
            <body>
                
                <h2>SQM MPSAS processing results for {location_name} </h2>
                <h4>Average MPSAS for the period: {average_mpsas:.2f}</h4>
                <strong>Serial number: {serial_number}</strong>
                <pre>{res}<pre>
                <p>
                <img src="{png_url}">
                </p>
                <p>File saved as: <strong>{processed_filename}</strong></p>
                <p><a href="{download_url}" target="_blank">Download processed file</a></p>
            </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)


      
#        return {"Hello": "World"}
#         return PlainTextResponse(
#             output_io,
#             status_code=200,
#             headers={"Content-Type": "text/plain"},
# #            content={"status": "OK", "detail": str(e), "traceback": tb}
#             #media_type="text/plain",
#             #headers={"Content-Type": "text/plain"}
#         )
#         
        
#         return StreamingResponse(
#             output_io,
#             media_type="text/html",
#             headers={"Content-Type: text/html; charset=utf-8", "Pragma: no-cache"}
#             #Location: http://www.w3.org/pub/WWW/People.html
#             #media_type="text/csv",
#             #headers={"Content-Disposition": f"attachment; filename=processed_{file.filename}"}
#             
#     )

        return {"status": "ok", "filename": file.filename, "size_bytes": size, "file: ": save_path}

    except Exception as e:
        # Return full traceback for debugging
        tb = traceback.format_exc()
        print(tb)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e), "traceback": tb}
        )


