import time
from datetime import datetime
import datetime as dt
import pandas as db
from pandas import DataFrame
import matrix_loader as ml
import db_connection as dbc
import carrier_handlers as ch
from db_connection import reset_carrier_eod_flags

cst = dt.timezone(-dt.timedelta(hours=6),name='CST')
utc = dt.timezone(-dt.timedelta(hours=0),name='UTC')

def new_day_check(process_matrix):
    today_date = datetime.now().date()
    print("==Checking last EOD flag refresh dates...")
    last_date = process_matrix.loc[process_matrix['last_eod_refresh_date'] != 'None', ['last_eod_refresh_date']]
    last_date = last_date['last_eod_refresh_date'].values[0]
    last_date = datetime.strptime(last_date, '%Y-%m-%d').date()
    print(last_date)
    print(today_date)
    if last_date < today_date:
        print('==Resetting EOD flags...')
        reset_carrier_eod_flags(today_date.strftime('%Y-%m-%d'))
    else:
        print('==No new date detected, skipping EOD reset.')


def end_of_day_check():
    process_matrix = ml.get_process_matrix()
    try:
        new_day_check(process_matrix)
    except Exception as e:
        print('==Ran into an error during new_day_check. Stopping process.')
        raise Exception

    print("==Checking process matrix to see if we have passed an EOD time...")
    now_cst = datetime.now().time().replace(tzinfo=utc)
    print(now_cst)
    for _, carrier_row in process_matrix.iterrows():
        disabled_until = carrier_row['disabled_until']
        if disabled_until is not None and disabled_until != 'None':
            if datetime.strptime(disabled_until, '%Y-%m-%d').date() > dt.date.today():
                continue
        if carrier_row['eod_times'] is not None and carrier_row['eod_times'] != 'None'\
                and carrier_row['automatic_export'].lower() == 'yes':
            print(carrier_row['script_name'])
            print(carrier_row['eod_times'])
            eod_times = carrier_row['eod_times'].split(',')
            index = 0
            for target_time in eod_times:
                hour = int(target_time.split(':')[0])
                minute = int(target_time.split(':')[1])
                assembled_target_time = dt.time(hour=hour,minute=minute,tzinfo=cst)
                print(f'Target Time: {assembled_target_time}')
                if now_cst > assembled_target_time:
                    if int(carrier_row['eod_flag']) <= index:
                        print("==Program needs to perform an EOD process for this carrier. Exiting to EOD functions.")
                        return True
                index+=1
    print('==No carriers need to run an EOD function right now.')
    return False

def run_eods():
    print("==Running valid EOD functions...")
    process_matrix = ml.get_process_matrix()
    now_cst = datetime.now().time().replace(tzinfo=utc)
    for _, carrier_row in process_matrix.iterrows():
        script_name = carrier_row["script_name"] + "_eod"
        carrier_id = carrier_row["carrier_id"]
        company_id = carrier_row["company_id"]

        # SINGLE CARRIER DEBUG FILTER
        # if row.get("script_name", "") != 'ACR_Ambetter_RPA':
        #   print(f"==Entry for {script_name} is not the selected debug target. Skipping.")
        #   continue

        if carrier_row.get("active_flag", "").lower() != 'yes':
            print(f"==Entry for {script_name} is not marked active. Skipping.")
            continue

        if carrier_row['eod_times'] is not None and carrier_row['eod_times'] != 'None' \
                and carrier_row['automatic_export'].lower() == 'yes':
            print(carrier_row['script_name'])
            print(carrier_row['eod_times'])
            eod_times = carrier_row['eod_times'].split(',')
            index = 0
            highest_time = None
            for target_time in eod_times:
                hour = int(target_time.split(':')[0])
                minute = int(target_time.split(':')[1])
                assembled_target_time = dt.time(hour=hour, minute=minute, tzinfo=cst)
                print(f'Target Time: {assembled_target_time}')
                if now_cst > assembled_target_time:
                    highest_time = assembled_target_time
                    index += 1
            if highest_time is None:
                continue
            elif int(carrier_row['eod_flag']) <= index:
                print(f"==Running {script_name} for time {highest_time}")
                try:
                    print(f"Calling eod function for {script_name}...")
                    handler = ch.handler_map.get(script_name)
                    handler(carrier_row)
                    dbc.advance_carrier_eod_flag(carrier_row['carrier_id'], index)
                except Exception as e:
                    print(f"Error occurred during end of day processing for {script_name}")

#end_of_day_check()
