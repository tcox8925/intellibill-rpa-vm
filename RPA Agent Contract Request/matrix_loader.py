import os
import time
import json
import pandas as pd
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
import db_connection

def get_process_matrix():
    """
    Fetches the full process matrix from the database and returns it as a DataFrame.

    :return: Pandas DataFrame containing the entire ops_acr_process_matrix table.
    """
    try:
        # Connect to database
        conn = db_connection.get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = "SELECT * FROM wpo.ops_acr_process_matrix"

        # Load data into a Pandas DataFrame
        df = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        df = df.astype(str)
        df = df.fillna("")  # Replace NaN with empty strings

        print(f"Successfully fetched {len(df)} records from ops_acr_process_matrix.")
        return df

    except Exception as e:
        print(f"Error reading RPA matrix: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def get_load_matrix():
    """
    Fetches the full process matrix from the database and returns it as a DataFrame.

    :return: Pandas DataFrame containing the entire ops_acr_load_matrix table.
    """
    try:
        # Connect to database
        conn = db_connection.get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = "SELECT * FROM wpo.ops_acr_load_matrix"

        # Load data into a Pandas DataFrame
        df = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        df = df.astype(str)
        df = df.fillna("")  # Replace NaN with empty strings

        print(f"Successfully fetched {len(df)} records from ops_acr_load_matrix.")
        return df

    except Exception as e:
        print(f"Error reading RPA matrix: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

def get_error_matrix():
    """
    Fetches the full process matrix from the database and returns it as a DataFrame.

    :return: Pandas DataFrame containing the entire ops_acr_error_matrix table.
    """
    try:
        # Connect to database
        conn = db_connection.get_postgres_connection()
        if not conn:
            print("Failed to establish database connection.")
            return None

        query = "SELECT * FROM wpo.ops_acr_error_matrix"

        # Load data into a Pandas DataFrame
        df = pd.read_sql(query, conn)

        # Ensure all columns are treated as strings to avoid type issues
        df = df.astype(str)
        df = df.fillna("")  # Replace NaN with empty strings

        print(f"Successfully fetched {len(df)} records from ops_acr_error_matrix.")
        return df

    except Exception as e:
        print(f"Error reading RPA matrix: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed.")

# use matrix_loader.py load matrices into dataframes
process_matrix = None
load_matrix = None
error_matrix = None
# login to crm
# start handling 