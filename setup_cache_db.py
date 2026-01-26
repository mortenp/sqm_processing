#!/usr/bin/env python3
"""
Setup script for SQM Processing Cache Database
Helps create MySQL user and database for the caching system
"""

import mysql.connector
from mysql.connector import Error
import sys
import getpass

def setup_cache_database():
    """Interactive setup for cache database"""
    
    print("=" * 60)
    print("SQM Processing - Cache Database Setup")
    print("=" * 60)
    print()
    
    # Get MySQL root credentials
    print("First, we need to connect as MySQL root to create the cache user.")
    print()
    
    mysql_host = input("MySQL host [localhost]: ").strip() or "localhost"
    mysql_user = input("MySQL root user [root]: ").strip() or "morten"
    mysql_password = getpass.getpass("MySQL root password: ")
    
    # Try to connect
    try:
        print("\nConnecting to MySQL...")
        root_conn = mysql.connector.connect(
            host=mysql_host,
            user=mysql_user,
            password=mysql_password
        )
        print("✓ Connection successful")
    except Error as e:
        print(f"✗ Connection failed: {e}")
        return False
    
    try:
        cursor = root_conn.cursor()
        
        # Create cache user
        cache_user = "sqm_cache"
        cache_password = input("\nEnter password for 'sqm_cache' database user: ")
        
        if not cache_password:
            print("✗ Password cannot be empty")
            return False
        
        print(f"\nCreating user '{cache_user}'@'localhost'...")
        
        # Drop if exists
        cursor.execute(f"DROP USER IF EXISTS '{cache_user}'@'localhost'")
        
        # Create user
        cursor.execute(
            f"CREATE USER '{cache_user}'@'localhost' IDENTIFIED BY %s",
            (cache_password,)
        )
        print("✓ User created")
        
        # Create database
        print("Creating database 'sqm_cache'...")
        cursor.execute("CREATE DATABASE IF NOT EXISTS sqm_cache")
        print("✓ Database created")
        
        # Grant privileges
        print("Granting privileges...")
        cursor.execute(
            f"GRANT ALL PRIVILEGES ON sqm_cache.* TO '{cache_user}'@'localhost'"
        )
        cursor.execute("FLUSH PRIVILEGES")
        print("✓ Privileges granted")
        
        root_conn.commit()
        cursor.close()
        root_conn.close()
        
        # Test connection with new user
        print("\nTesting cache user connection...")
        try:
            test_conn = mysql.connector.connect(
                host=mysql_host,
                user=cache_user,
                password=cache_password,
                database="sqm_cache"
            )
            test_conn.close()
            print("✓ Cache user connection successful")
        except Error as e:
            print(f"✗ Cache user connection failed: {e}")
            return False
        
        # Show configuration to use
        print("\n" + "=" * 60)
        print("Configuration to add to my_sqm_service.py:")
        print("=" * 60)
        print(f"""
DB_CONFIG = {{
    'host': '{mysql_host}',
    'user': '{cache_user}',
    'password': '{cache_password}',
    'database': 'sqm_cache',
    'raise_on_warnings': False
}}
""")
        
        print("=" * 60)
        print("Setup complete! ✓")
        print("=" * 60)
        
        return True
        
    except Error as e:
        print(f"✗ Setup error: {e}")
        return False
    finally:
        if root_conn.is_connected():
            cursor.close()
            root_conn.close()


if __name__ == "__main__":
    success = setup_cache_database()
    sys.exit(0 if success else 1)
