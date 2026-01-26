#!/usr/bin/env python3
"""
Prepopulate cache database with astronomical calculations for a year and location.
This enables instant cache hits when processing files from that location and time period.

Usage:
    python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025
    python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025 --month 3
    python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --start 2025-01-01 --end 2025-12-31
"""

import argparse
import sys
from datetime import datetime, timedelta
from astropy.time import Time
from astropy.coordinates import get_sun, get_body, EarthLocation, AltAz, SkyCoord
from astropy import units as u
import logging

# Try importing MySQL connector
try:
    import mysql.connector
    from mysql.connector import Error
except ImportError:
    print("ERROR: mysql-connector-python not installed")
    print("Install with: pip install mysql-connector-python")
    sys.exit(1)

# Import cache functions from main service
sys.path.insert(0, '/Users/morten/sqm_process2/sqm_processing')
try:
    from my_sqm_service import (
        DB_CONFIG, CACHE_TIME_BUCKET_MIN, 
        round_location, get_time_bucket,
        BASE_MW_SB_AT_PLANE, PLANE_TO_POLE_FADE, EXTINCTION_COEFF, MW_SB_THRESHOLD
    )
except ImportError as e:
    print(f"ERROR: Could not import from my_sqm_service: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def calculate_celestial_values(lat, lon, t):
    """Calculate sun_alt, moon_alt, mw_brightness, milky_way_visible for a given time."""
    try:
        location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg)
        altaz = AltAz(obstime=t, location=location)
        
        # Sun altitude
        sun_alt = get_sun(t).transform_to(altaz).alt.deg
        
        # Moon altitude (using get_body)
        moon_alt = get_body("moon", t, location=location).transform_to(altaz).alt.deg
        
        # Milky Way brightness calculation
        zen_altaz = AltAz(obstime=t, location=location, alt=90*u.deg, az=0*u.deg)
        zenith = SkyCoord(zen_altaz)
        zenith_gal = zenith.transform_to('galactic')
        b_deg = abs(zenith_gal.b.deg)
        
        mw_sb_plane = BASE_MW_SB_AT_PLANE + (PLANE_TO_POLE_FADE * (b_deg / 90.0))
        airmass = 1.0
        mw_sb = mw_sb_plane + EXTINCTION_COEFF * (airmass - 1.0)
        
        milky_way_visible = bool(mw_sb <= MW_SB_THRESHOLD)
        
        # Convert numpy types to Python types for database compatibility
        sun_alt = float(sun_alt)
        moon_alt = float(moon_alt)
        mw_sb = float(mw_sb)
        
        return sun_alt, moon_alt, mw_sb, milky_way_visible
    except Exception as e:
        logger.warning(f"Error calculating values for {t}: {e}")
        return None, None, None, None


def store_in_cache(lat_rounded, lon_rounded, time_bucket, sun_alt, moon_alt, mw_sb, milky_way_visible):
    """Store calculated values in cache database."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Convert datetime to string format for MySQL
        time_bucket_str = time_bucket.strftime('%Y-%m-%d %H:%M:%S')
        
        query = """
        INSERT INTO celestial_cache (lat, lon, time_bucket, sun_alt, moon_alt, mw_brightness, milky_way_visible)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE 
            sun_alt = VALUES(sun_alt), 
            moon_alt = VALUES(moon_alt),
            mw_brightness = VALUES(mw_brightness),
            milky_way_visible = VALUES(milky_way_visible)
        """
        cursor.execute(query, (lat_rounded, lon_rounded, time_bucket_str, sun_alt, moon_alt, mw_sb, milky_way_visible))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Error as e:
        logger.error(f"Cache storage failed: {e}")
        return False


def prepopulate(lat, lon, start_date, end_date):
    """Prepopulate cache for location and date range."""
    
    # Round location
    lat_rounded, lon_rounded = round_location(lat, lon)
    logger.info(f"Prepopulating cache for location: ({lat_rounded}, {lon_rounded})")
    logger.info(f"Date range: {start_date.date()} to {end_date.date()}")
    
    print(f"Prepopulating cache for location: ({lat_rounded}, {lon_rounded})")
    print(f"Date range: {start_date.date()} to {end_date.date()}")

    # Generate time buckets
    current = start_date
    bucket_seconds = CACHE_TIME_BUCKET_MIN * 60
    total_buckets = 0
    stored = 0
    errors = 0
    
    while current <= end_date:
        # Round to nearest bucket
        epoch = datetime(1970, 1, 1)
        diff = (current - epoch).total_seconds()
        rounded_diff = (diff // bucket_seconds) * bucket_seconds
        bucket_time = epoch + timedelta(seconds=rounded_diff)
        
        # Create astropy Time object
        t = Time(bucket_time, scale='utc')
        
        # Calculate values
        sun_alt, moon_alt, mw_sb, mw_visible = calculate_celestial_values(lat, lon, t)
        print(f"sun_alt: {sun_alt}")
        
        # Only cache when sun is below horizon (sun_alt < 0)
        if sun_alt is not None and sun_alt < 0:
            print(f"store_in_cache lat={lat_rounded}, lon={lon_rounded}, dates {bucket_time} moon_alt: {moon_alt}")
            if store_in_cache(lat_rounded, lon_rounded, bucket_time, sun_alt, moon_alt, mw_sb, mw_visible):
                stored += 1
            else:
                errors += 1
                print(f"error storing in cache for {bucket_time}")
        elif sun_alt is not None:
            # Skip storing when sun is above horizon
            pass
        else:
            errors += 1
        
        total_buckets += 1
        
        # Log progress every 1000 buckets
        if total_buckets % 1000 == 0:
            logger.info(f"Progress: {total_buckets} buckets processed, {stored} stored, {errors} errors")
        
        # Move to next bucket
        current += timedelta(minutes=CACHE_TIME_BUCKET_MIN)
    
    logger.info(f"Prepopulation complete!")
    logger.info(f"Total buckets: {total_buckets}")
    logger.info(f"Successfully stored: {stored}")
    logger.info(f"Errors: {errors}")
    
    return total_buckets, stored, errors


def main():
    parser = argparse.ArgumentParser(
        description='Prepopulate cache database for a year/month and location',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Prepopulate entire year
  python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025
  
  # Prepopulate specific month
  python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --year 2025 --month 3
  
  # Prepopulate custom date range
  python3 prepopulate_cache.py --lat 56.04 --lon 10.87 --start 2025-03-01 --end 2025-03-31
        """
    )
    
    parser.add_argument('--lat', type=float, required=True, help='Latitude')
    parser.add_argument('--lon', type=float, required=True, help='Longitude')
    parser.add_argument('--year', type=int, help='Year to prepopulate (entire year)')
    parser.add_argument('--month', type=int, help='Month (only with --year)')
    parser.add_argument('--start', type=str, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='End date (YYYY-MM-DD)')
    
    args = parser.parse_args()
    
    # Determine date range
    if args.year:
        if args.month:
            # Specific month
            start_date = datetime(args.year, args.month, 1)
            if args.month == 12:
                end_date = datetime(args.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = datetime(args.year, args.month + 1, 1) - timedelta(days=1)
        else:
            # Entire year
            start_date = datetime(args.year, 1, 1)
            end_date = datetime(args.year, 12, 31, 23, 59, 59)
    elif args.start and args.end:
        try:
            start_date = datetime.strptime(args.start, '%Y-%m-%d')
            end_date = datetime.strptime(args.end, '%Y-%m-%d')
            end_date = end_date.replace(hour=23, minute=59, second=59)
        except ValueError as e:
            print(f"Error parsing dates: {e}")
            sys.exit(1)
    else:
        print("Error: Please specify either --year or both --start and --end")
        parser.print_help()
        sys.exit(1)
    
    # Prepopulate
    try:
        print(f"starting prepopulation for lat={args.lat}, lon={args.lon}, dates {start_date} to {end_date}")
        total, stored, errors = prepopulate(args.lat, args.lon, start_date, end_date)
        
        if errors > 0:
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
