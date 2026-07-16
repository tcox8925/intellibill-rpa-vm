import requests
import pandas as pd
import azure_blob_utils
import ast
from datetime import datetime as dt
import db_connection as db
from datetime import datetime, date, timedelta
import pytz
import json
import zipfile
import time
from io import BytesIO
import numpy as np
import time
import os
import string_utils

zoho_auth_token = None
last_inserted_id = None
#token_location = 'C:\\Users\\actua\\Desktop\\work\\Tools\\ZohoCRMToken.txt'
token_location = 'C:\\Users\\myopsadmin\\Documents\\ZohoCRMToken.txt'


def get_zoho_authentication_token():
    print("==Checking for local Zoho CRM Access token...")
    if os.path.exists(token_location):
        print("==Token exists, reading token.")
        with open(token_location, 'r') as file:
            token = file.readline().strip()
            timestamp = float(file.readline().strip())
        # Check for token
        if time.time() < timestamp + 1500.0: # Expire token after 25 minutes
            print("==Unexpired token found, verifying against CRM")
            verify = True
            headers = {'Authorization': f'Zoho-oauthtoken {token}'}
            url = f'https://www.zohoapis.com/crm/v8/settings/modules'
            response = requests.get(url, headers=headers)
            print(f"==Received status code {response.status_code}")
            if 200 <= response.status_code <= 299:
                print("==Token verified, returning")
                return token
            else:
                print("==Token verification failed, fetching new token")
                return generate_new_zoho_auth_token()
        else:
            print("==Token has expired or is expiring soon, fetching new token")
            return generate_new_zoho_auth_token()
    else:
        print("==No token file found, fetching new token")
        return generate_new_zoho_auth_token()

def generate_new_zoho_auth_token():
    zoho_client_id, zoho_client_secret, zoho_refresh_token = azure_blob_utils.get_zoho_secrets()
    retries = 0
    while retries < 6:
        print("==Fetching access token...")
        authentication_url = (
            f"https://accounts.zoho.com/oauth/v2/token?"
            f"refresh_token={zoho_refresh_token}"
            f"&client_id={zoho_client_id}"
            f"&client_secret={zoho_client_secret}"
            f"&grant_type=refresh_token"
        )
        response = requests.post(authentication_url)
        if response.status_code == 200:
            print("==Token received.")
            with open(token_location, 'w') as file:
                file.write(response.json()["access_token"] + '\n' + str(time.time()))
            return response.json()["access_token"]
        else:
            print("==Token fetch failed, trying again in 4 minutes...")
            time.sleep(240)
            retries = retries + 1
    print("==Too many token fetch failures, stopping process.")
    return None

def get_contracts_by_carrier(carrier_id, status_str, use_test_npns, batch_size=15, appointment_type_limit=None, agent_type_limit=None):
    status_list = status_str.split(',')
    status_filter = 'AND ('
    for status in status_list:
        if status_list[0] != status:
            status_filter += ' OR '
        status_filter += f"(Status = '{status}')"
    status_filter += ')'

    if appointment_type_limit == 'Producer':
        # add producer filter
        print(f"==Limiting appointment type to: {appointment_type_limit}...")
        status_filter = f"AND (Appointment_Type = '{appointment_type_limit}' " + status_filter + ')'

    if agent_type_limit is not None:
        print(f"==Limiting agent type to: {agent_type_limit}...")
        status_filter = f"AND (Agent.Type = '{agent_type_limit}' " + status_filter + ')'

    zoho_auth = get_zoho_authentication_token()


    if use_test_npns == 'Yes':
        print("==Using 'test NPNs only' filter...")
        query_filter = f"""
            WHERE 
            --(
            (Carrier.id = '{carrier_id}')
            --{status_filter})
            --AND (((Agent.NPN = '101110110') OR (Agent.NPN = '101000114')) OR ((Agent.NPN = '101110001') OR ((Agent.NPN = '10100011') OR ((Agent.NPN = '1010001113') OR (Agent.NPN = '1010')))))\n
            --AND (((Agent.NPN = '20494167') OR (Agent.NPN = '19746687')) OR (Agent.NPN = '7268021'))
            AND (Agent.NPN = '17765163')
        """
    else:
        age_minimum_time = datetime.now() - timedelta(minutes=30)
        age_minimum_time_str = age_minimum_time.strftime('%Y-%m-%dT%H:%M:%S-05:00')
        print("==Using 'blacklist all test NPNs' filter...")
        query_filter = f"""
            WHERE 
            (
            (Carrier.id = '{carrier_id}')
            {status_filter})
            AND (((Agent.NPN != '101110110') AND (Agent.NPN != '101000114')) AND ((Agent.NPN != '101110001') AND ((Agent.NPN != '10100011') AND ( (Agent.NPN != '1010001113') AND ((Agent.NPN != '1010') AND ((Agent.NPN != '101000117') AND (Created_Time < '{age_minimum_time_str}') )) ))))\n
            --AND (Agent.NPN != '101110110')
        """

    headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
    query = {
        "select_query": 
            f"""SELECT Name
                        ,Carrier.id
                        ,Agent.id
                        ,Agent.Type
                        ,Agent.NPN
                        ,Agent.First_Name
                        ,Agent.Last_Name
                        ,Status
                        ,Agent_Status
                        ,Writing_Number
                        ,Agent.Contracting_Email
                        ,Agent.Email
                        ,Agent.Secondary_Email
                        ,Upline.id
                        ,Top_Upline.id
                        ,Source
                        ,Field_Sales_Director.id
                        ,Appointment_Type
                        ,id
                        ,Status_Date
                        ,Agent.FEIN
                        ,Agent.Phone
                        ,Agent.Other_Phone
                        ,Agent.Mobile
                        ,Parent_Contract.id
                        ,Requested_States
                        ,Agent.Resident_State
            FROM Agent_Contracts
            {query_filter}
            LIMIT {batch_size}
            """
    }
    print("==Posting COQL query...")
    print(query)
    response = requests.post('https://www.zohoapis.com/crm/v2/coql', headers=headers, json=query)
    print(f"==Received status code {response.status_code}")
    if response.status_code == 204:
        print("==No rows were found on the CRM.")
        return None
    print(response.json())
    data = str(response.json().get("data", []))
    df = pd.DataFrame(ast.literal_eval(data))
    df.rename(columns=format_mapping, inplace=True)
    # Ensure all columns are created and default values set correctly
    df['process_flag'] = 0
    df['retries'] = 0
    uid_list = db.generate_unique_id(db.get_last_inserted_id(), len(df.index))
    df['batch_id'] = db.generate_unique_id(db.get_last_inserted_id(), 1)[0]
    for i, contract in df.iterrows():
        df.loc[i, 'txn_id'] = uid_list[i]
    df['responsible_agent'] = None
    df['responsible_agent_contract_status'] = None
    df['email_address'] = None
    df['fail_status'] = None
    df['success_status'] = None
    df['error_message'] = None
    df['note_error'] = None
    df['write_to_crm'] = None
    df['update_status_date'] = None
    df['parent_npn'] = None
    df['parent_wn'] = None
    df['parent_full_name'] = None
    df['parent_first_name'] = None
    df['parent_last_name'] = None
    df['agency_full_name'] = None
    df['agency_npn'] = None
    df['selected_phone'] = None
    df['sibling_contract'] = None
    df['responsible_agent_email'] = None
    df['pk_id'] = None

    now = dt.now()
    df['load_date'] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")

    # Requested states Ambetter override, wipe value for all others
    if carrier_id == '2931751000020024159':
        print("==Ambetter detected; Performing requested_state & resident_state reformat...")
        for i, contract in df.iterrows():
            state_str = ''
            if contract['requested_states'] is not None:
                for k, state in enumerate(contract['requested_states']):
                    if string_utils.state_name_to_state_code(state) in state_str:
                        continue
                    if k > 0:
                        state_str += ','
                    state_str += string_utils.state_name_to_state_code(state)
            df.loc[i, 'requested_states'] = state_str

            full_state_list = string_utils.resident_state_list
            if contract.get('resident_state') is None:
                for field in full_state_list:
                    if contract.get(f"Agent." + field).lower() == 'resident':
                        contract['resident_state'] = string_utils.state_name_to_state_code(field)

    else:
        print("==Carrier is not Ambetter; Clearing requested_state lists...")
        df['requested_states'] = None
    print(f"Contract table after state adjustments:\n{df.to_string()}")

    return df

# Zoho's COQL Alias functionality wasn't working..
format_mapping = {
        "Name":"contract_id",
        "Carrier.id":"carrier_id",
        "Agent.id":"agent_id",
        "Agent.Type":"agent_type",
        "Agent.NPN":"npn",
        "Agent.First_Name":"agent_first_name",
        "Agent.Last_Name":"agent_last_name",
        "Status":"contract_status",
        "Agent_Status":"agent_status",
        "Writing_Number":"agent_writing_num",
        "Agent.Contracting_Email":"contracting_email",
        "Agent.Email":"email",
        "Agent.Secondary_Email":"secondary_email",
        "Upline.id":"upline_id",
        "Top_Upline.id":"top_upline_id",
        "Source":"contract_source",
        "Field_Sales_Director.id":"field_sales_director_id",
        "Appointment_Type":"appointment_type",
        "Status_Date":"old_status_date",
        "Agent.FEIN":"agency_fein",
        "Agent.Phone":"phone",
        "Agent.Other_Phone":"other_phone",
        "Agent.Mobile":"mobile_phone",
        "Parent_Contract.id":"parent_id",
        "Requested_States":"requested_states",
        "Agent.Resident_State":"resident_state"
        }

def bcbs_mi_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"==Handling firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    # Can't join deep enough for the main query, and have to avoid sending too many ORs through one query... have to fetch these one at a time
    zoho_auth = get_zoho_authentication_token()
    for i, contract in firm_contracts.iterrows():
        if (contract['upline_id'] is None) or (contract['top_upline_id'] is None):
            print("==Upline or Top Upline data is missing, skipping firm adjustments.")
            continue
        agent_df = None
        contract_df = None
        try:
            print(contract.to_string())
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            url = f'https://www.zohoapis.com/crm/v2/Contacts/{contract['agent_id']}/Responsible_Agents?fields=First_Name,Last_Name,NPN'
            print(url)
            response = requests.get(url, headers=headers)
            print(f"==Received status code {response.status_code}")
            if response.status_code == 204:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            print(response.json())
            data = str(response.json().get("data", []))
            agent_df = pd.DataFrame(ast.literal_eval(data))
            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            print(agent_df['id'])
            print(agent_df['id'].item())
            agent_id = agent_df['id'].item()
        except Exception as e:
            print("==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue

        try:
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            query = {
                "select_query":
                    f"""SELECT Name
                                    ,Carrier.id
                                    ,Agent.id
                                    ,Agent.Type
                                    ,Agent.NPN
                                    ,Agent.First_Name
                                    ,Agent.Last_Name
                                    ,Status
                                    ,Agent_Status
                                    ,Writing_Number
                                    ,Agent.Contracting_Email
                                    ,Agent.Email
                                    ,Agent.Secondary_Email
                                    ,Upline.id
                                    ,Top_Upline.id
                                    ,Source
                                    ,Field_Sales_Director.id
                                    ,Appointment_Type
                                    ,Agent.Responsible_Agent
                                    ,id
                        FROM Agent_Contracts
                        WHERE (Carrier.id = '{carrier_id}')
                        AND (Agent.id = '{agent_id}')
                        LIMIT {1}
                        """
            }

            print("==Posting COQL query...")
            response = requests.post('https://www.zohoapis.com/crm/v2/coql', headers=headers, json=query)
            print(f"==Received status code {response.status_code}")
            if response.status_code == 204:
                print("==No data in response, setting contract_df to None.")
                contract_df = None
            else:
                print(response.json())
                data = str(response.json().get("data", []))
                contract_df = pd.DataFrame(ast.literal_eval(data))
                print(contract_df.to_string())
        except Exception as e:
            print("==Error during firm adjustments at responsible agent contract fetch level: {e}")

        # If missing, create a new contract.. Done
        if contract_df is None:
            print("==No contract found for responsible agent, creating new contract")
            trimmed_df = agent_df[['id', 'NPN']]
            trimmed_df = trimmed_df.rename(columns={"id":"Agent"})
            trimmed_df['Carrier'] = contract['carrier_id']
            trimmed_df['Status_Date'] = dt.now().date()
            trimmed_df['Source'] = contract['contract_source']
            trimmed_df['Field_Sales_Director'] = contract['field_sales_director_id']
            trimmed_df['Appointment_Type'] = 'Producer'
            trimmed_df['Requested_States'] = [['Michigan']]
            trimmed_df['Schedule'] = '100%'
            trimmed_df['Parent_Contract'] = None
            trimmed_df['Upline'] = contract['upline_id']
            trimmed_df['Top_Upline'] = contract['top_upline_id']
            trimmed_df['Status'] = 'Requested'
            print(trimmed_df.to_string())
            upsert_success = upsert_contract(trimmed_df)
            if upsert_success:
                contract['Status'] = 'Sent to Agent'
                contract['Process_Flag'] = '2'
                contract['responsible_agent'] = trimmed_df['Agent'].item()
                contract['responsible_agent_contract_status'] = trimmed_df['Status'].item()
            else:
                contract['error_message'] = 'Contract creation for Responsible Agent failed'
                contract['Process_Flag'] = '1'
        else:
            print("==Contract found for responsible agent, setting responsible_agent field for firm_contract")
            contract['responsible_agent'] = contract_df['Agent.id'].item()
            contract['responsible_agent_contract_status'] = contract_df['Status'].item()

        firm_contracts.loc[i] = contract

    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def uhc_aca_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"\n==Handling firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    # Can't join deep enough for the main query, and have to avoid sending too many ORs through one query... have to fetch these one at a time
    zoho_auth = get_zoho_authentication_token()
    for i, contract in firm_contracts.iterrows():
        agent_df = None
        try:
            print(contract.to_string())
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            url = f'https://www.zohoapis.com/crm/v2/Contacts/{contract['agent_id']}/Responsible_Agents?fields=First_Name,Last_Name,NPN'
            print(url)
            response = requests.get(url, headers=headers)
            print(f"==Received status code {response.status_code}")
            if response.status_code == 204:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            print(response.json())
            data = str(response.json().get("data", []))
            agent_df = pd.DataFrame(ast.literal_eval(data))
            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            print(agent_df['id'])
            print(agent_df['id'].item())
            agent_id = agent_df['id'].item()
            contract['responsible_agent'] = agent_id
            contract['agency_full_name'] = contract['agent_first_name'] + " " + contract['agent_last_name']
            contract['agent_first_name'] = agent_df['First_Name'].item()
            contract['agent_last_name'] = agent_df['Last_Name'].item()
            contract['agency_npn'] = contract['npn']
            contract['npn'] = agent_df['NPN'].item()

        except Exception as e:
            print("==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue

        firm_contracts.loc[i] = contract

    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def ambetter_requestedstates_fallback(contract):
    print(contract.to_string())
    zoho_auth = get_zoho_authentication_token()

    try:
        headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
        query = {
            "select_query":
                f"""SELECT Name
                                    ,Carrier.id
                                    ,Agent.id
                                    ,Agent.NPN
                                    ,Agent.First_Name
                                    ,Agent.Last_Name
                                    ,Requested_States
                        FROM Agent_Contracts
                        WHERE (Carrier.id = '{contract['carrier_id']}')
                        AND (Name = '{contract['id']}')
                        LIMIT {1}
                        """
        }

        print("==Posting COQL query...")
        response = requests.post('https://www.zohoapis.com/crm/v2/coql', headers=headers, json=query)
        print(f"==Received status code {response.status_code}")
        if response.status_code == 204:
            print("==No contract found on Zoho.")
            contract_df = None
        else:
            print(response.json())
            data = str(response.json().get("data", []))
            contract_df = pd.DataFrame(ast.literal_eval(data))
            print(contract_df.to_string())
            return contract_df['Requested_States'].item()
    except Exception as e:
        print(f"==Error during requested_state zoho fallback for Ambetter: {e}")
        return None

def bcbstx_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"\n==Handling firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    # Can't join deep enough for the main query, and have to avoid sending too many ORs through one query... have to fetch these one at a time
    zoho_auth = get_zoho_authentication_token()
    for i, contract in firm_contracts.iterrows():
        agent_df = None
        try:
            print(contract.to_string())
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            url = f'https://www.zohoapis.com/crm/v2/Contacts/{contract['agent_id']}/Responsible_Agents?fields=First_Name,Last_Name,NPN,Contracting_Email,Email,Secondary_Email,Phone'
            print(url)
            response = requests.get(url, headers=headers)
            print(f"==Received status code {response.status_code}")
            if response.status_code == 204:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            print(response.json())
            data = str(response.json().get("data", []))
            agent_df = pd.DataFrame(ast.literal_eval(data))
            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            print(agent_df['id'])
            print(agent_df['id'].item())
            agent_id = agent_df['id'].item()
            contract['responsible_agent'] = agent_id
            contract['agency_full_name'] = contract['agent_first_name'] + " " + contract['agent_last_name']
            contract['agent_first_name'] = agent_df['First_Name'].item()
            contract['agent_last_name'] = agent_df['Last_Name'].item()
            contract['agency_npn'] = contract['npn']
            contract['npn'] = agent_df['NPN'].item()
            contract['responsible_agent_email'] = (agent_df['Contracting_Email'].item()
                                                   or agent_df['Email'].item()
                                                   or agent_df['Secondary_Email'].item())
            contract['selected_phone'] = agent_df['Phone'].item()
        except Exception as e:
            print(f"==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue

        firm_contracts.loc[i] = contract

    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def goldkidney_firm_adjustments(carrier_id, status_str, firm_contracts):
    print(f"\n==Handling firm adjustments...\n==Given data:\n{firm_contracts.to_string()}\n")
    if len(firm_contracts) == 0:
        print("==No firm contracts collected, skipping firm adjustments.")
    # Can't join deep enough for the main query, and have to avoid sending too many ORs through one query... have to fetch these one at a time
    zoho_auth = get_zoho_authentication_token()
    for i, contract in firm_contracts.iterrows():
        agent_df = None
        try:
            print(contract.to_string())
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            url = f'https://www.zohoapis.com/crm/v2/Contacts/{contract['agent_id']}/Responsible_Agents?fields=First_Name,Last_Name,NPN,Email'
            print(url)
            response = requests.get(url, headers=headers)
            print(f"==Received status code {response.status_code}")
            if response.status_code == 204:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            print(response.json())
            data = str(response.json().get("data", []))
            agent_df = pd.DataFrame(ast.literal_eval(data))
            if len(agent_df) == 0:
                print("==No Responsible Agent found, leaving field empty and continuing.")
                continue
            elif len(agent_df) == 1:
                print("==A single responsible agent was found, continuing.")
            elif len(agent_df) > 1:
                print("==More than one responsible agent was found, choosing first.")
                agent_df = agent_df.iloc[:1]
            print(agent_df['id'])
            print(agent_df['id'].item())
            contract['responsible_agent'] = agent_df['id'].item()
            contract['responsible_agent_email'] = agent_df['Email'].item()

        except Exception as e:
            print("==Error during firm adjustments at Responsible_Agents fetch level: {e}")
            continue

        firm_contracts.loc[i] = contract

    print(f"==After firm adjustments:\n==Given data:\n{firm_contracts.to_string()}")
    return firm_contracts

def uhc_aca_subproducer_adjustments(carrier_id, status_str, subproducer_contracts):
    print(f"==Handling subproducer adjustments...\n==Given data:\n{subproducer_contracts.to_string()}\n")
    if len(subproducer_contracts) == 0:
        print("==No subproducer contracts collected, skipping subproducer adjustments.")
    # Can't join deep enough for the main query, and have to avoid sending too many ORs through one query... have to fetch these one at a time
    zoho_auth = get_zoho_authentication_token()
    for i, contract in subproducer_contracts.iterrows():
        agent_df = None
        contract_df = None
        parent_id = contract['parent_id']
        try:
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            query = {
                "select_query":
                    f"""SELECT Name
                                        ,Carrier.id
                                        ,Agent.id
                                        ,Agent.NPN
                                        ,Agent.First_Name
                                        ,Agent.Last_Name
                                        ,Writing_Number
                                        ,Status
                                        ,id
                            FROM Agent_Contracts
                            WHERE (Carrier.id = '{carrier_id}')
                            AND (Agent.id = '{parent_id}')
                            LIMIT {1}
                            """
            }

            print("==Posting COQL query...")
            response = requests.post('https://www.zohoapis.com/crm/v2/coql', headers=headers, json=query)
            print(f"==Received status code {response.status_code}")
            if response.status_code == 204:
                print("==No data in response, setting contract_df to None.")
                contract_df = None
            else:
                print(response.json())
                data = str(response.json().get("data", []))
                contract_df = pd.DataFrame(ast.literal_eval(data))
                print(contract_df.to_string())
        except Exception as e:
            print("==Error during firm adjustments at responsible agent contract fetch level: {e}")

        if contract_df is None:
            print("==No parent contract found for parent agent.")
        else:
            print("==Parent contract found for parent agent.")
            contract['parent_npn'] = contract_df['Agent.NPN'].item()
            contract['parent_wn'] = contract_df['Writing_Number'].item()
            contract['parent_full_name'] = contract_df['Agent.First_Name'].item() + " " + contract_df['Agent.Last_Name'].item()
            contract['parent_first_name'] = contract_df['Agent.First_Name'].item()
            contract['parent_last_name'] = contract_df['Agent.Last_Name'].item()
            contract['responsible_agent'] = contract_df['Agent.id'].item()
            contract['responsible_agent_contract_status'] = contract_df['Status'].item()

        subproducer_contracts.loc[i] = contract

    print(f"==After subproducer adjustments:\n==Given data:\n{subproducer_contracts.to_string()}")
    return subproducer_contracts

def uhc_filter_to_agency_contracts(df_contracts: pd.DataFrame,
                                   appointment_type_values='Agency,Agency/Corporate') -> pd.DataFrame:
    """
    UHC-specific: filter to Agency-related Appointment_Type values.
    Accepts comma-separated string or list/tuple/set.
    """
    if df_contracts is None or len(df_contracts) == 0:
        return df_contracts
    if isinstance(appointment_type_values, str):
        accepted = {v.strip().lower() for v in appointment_type_values.split(',') if v.strip()}
    elif isinstance(appointment_type_values, (list, tuple, set)):
        accepted = {str(v).strip().lower() for v in appointment_type_values if str(v).strip()}
    else:
        accepted = {'agency'}
    def is_agency_like(val):
        s = str(val or '').strip().lower()
        return s in accepted or any(s and s.startswith(a) for a in accepted)
    return df_contracts[df_contracts['appointment_type'].apply(is_agency_like)].copy()

def uhc_mdc_prepare_agency_contracts(carrier_id: str,
                                     status_str: str,
                                     df_agency: pd.DataFrame) -> pd.DataFrame:
    """
    UHC-specific prep:
      - Agent.Type observed: 'Firm' and 'Individual'
      - Firm -> use uhc_aca_firm_adjustments (Responsible_Agents → principal info & email)
      - Individual -> use uhc_aca_subproducer_adjustments (pull parent agent/contract as responsible)
      - Normalize keys for webnav (responsible_* and agency_*), including agency legal name & FEIN
    """
    if df_agency is None or len(df_agency) == 0:
        print("==No UHC Agency contracts to prepare.")
        return df_agency
    def is_firm(agent_type):
        return str(agent_type or '').strip().lower() == 'firm'
    def is_individual(agent_type):
        return str(agent_type or '').strip().lower() == 'individual'
    df_firm = df_agency[df_agency['agent_type'].apply(is_firm)].copy()
    df_ind  = df_agency[df_agency['agent_type'].apply(is_individual)].copy()
    df_other = df_agency[~df_agency.index.isin(df_firm.index) & ~df_agency.index.isin(df_ind.index)].copy()
    print(f"==UHC Agency split: firm={len(df_firm)} individual={len(df_ind)} other={len(df_other)}")
    # Use your existing UHC helpers
    df_firm = uhc_aca_firm_adjustments(carrier_id, status_str, df_firm)
    df_ind  = uhc_aca_subproducer_adjustments(carrier_id, status_str, df_ind)
    # Normalize downstream keys for webnav (explicit responsible_* and agency_* fields)
    def normalize_keys(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or len(df) == 0:
            return df
        for i, row in df.iterrows():
            # Principal (responsible agent)
            df.loc[i, 'responsible_first_name'] = row.get('responsible_first_name') or row.get('agent_first_name')
            df.loc[i, 'responsible_last_name']  = row.get('responsible_last_name')  or row.get('agent_last_name')
            df.loc[i, 'responsible_email']      = row.get('responsible_email')      or row.get('email_address') or row.get('email') or row.get('contracting_email')
            df.loc[i, 'responsible_npn']        = row.get('responsible_npn')        or row.get('npn')
            # Agency (Corp) info
            # Use the contract 'Name' (post-format_mapping likely 'name') or the firm-computed full name
            agency_full_name = row.get('agency_full_name') or row.get('name')
            df.loc[i, 'agency_full_name'] = agency_full_name
            # Map agency legal name to the same (portal 'orgName' can accept this)
            df.loc[i, 'agency_legal_name'] = agency_full_name
            # FEIN to agency FEIN/Tax ID
            df.loc[i, 'agency_fein'] = row.get('agent_fein')  # from SELECT Agent.FEIN
            # Optional address/email/phone if present (kept as-is if not available)
            # df.loc[i, 'agency_email'] = row.get('agency_email') or df.loc[i, 'responsible_email']  # if you want default
            # df.loc[i, 'agency_phone'] = row.get('selected_phone') or row.get('phone') or row.get('other_phone') or row.get('mobile_phone')
        return df
    df_firm = normalize_keys(df_firm)
    df_ind  = normalize_keys(df_ind)
    df_other = normalize_keys(df_other)
    df_prepared = pd.concat([df_firm, df_ind, df_other], ignore_index=True)
    print(f"==Prepared UHC Agency contracts: {len(df_prepared)}")
    return df_prepared


def christus_lob_adjustments(contracts):
    print(f"==Handling Christus LOB adjustments...\n==Given data:\n{contracts.to_string()}\n")
    if len(contracts) == 0:
        print("==No contracts collected, skipping firm adjustments.")
    # Can't join deep enough for the main query, and have to avoid sending too many ORs through one query... have to fetch these one at a time
    zoho_auth = get_zoho_authentication_token()
    for i, contract in contracts.iterrows():
        print("Checking for existing contracts on:")
        print(contract)
        contract_df = None
        try:
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            query = {"select_query":
                                f"""SELECT Name
                                    ,Carrier.id
                                    ,Agent.id
                                    ,Agent.Type
                                    ,Agent.NPN
                                    ,Agent.First_Name
                                    ,Agent.Last_Name
                                    ,Status
                                    ,Agent_Status
                                    ,Writing_Number
                                    ,Agent.Contracting_Email
                                    ,Agent.Email
                                    ,Agent.Secondary_Email
                                    ,Upline.id
                                    ,Top_Upline.id
                                    ,Source
                                    ,Field_Sales_Director.id
                                    ,Appointment_Type
                                    ,id
                                    ,Status_Date
                                    ,Agent.FEIN
                                    ,Agent.Phone
                                    ,Agent.Other_Phone
                                    ,Agent.Mobile
                                    ,Parent_Contract.id
                                    ,Requested_States
                            FROM Agent_Contracts
                            WHERE ( ((Carrier.id = '2931751000020024158') OR (Carrier.id = '2931751000382772962'))
                            AND ((Agent.NPN = '{contract['npn']}') AND (id != '{contract['id']}')) )
                            AND ((Status != 'Possible Duplicate') AND ((Status != 'Terminated') AND (Status != 'Request Cancelled')))
                            """
            }

            print("==Posting COQL query...")
            print(query)
            response = requests.post('https://www.zohoapis.com/crm/v2/coql', headers=headers, json=query)
            print(f"==Received status code {response.status_code}")
            if response.status_code == 204:
                print("==No data in response, setting contract_df to None.")
                contract_df = None
            else:
                print(response.json())
                data = str(response.json().get("data", []))
                contract_df = pd.DataFrame(ast.literal_eval(data))
                contract_df.rename(columns=format_mapping, inplace=True)
                contract_df['sibling_contract'] = 'Yes'
                contract_df['process_flag'] = 0
                contract_df['retries'] = 0
                contract_df['batch_id'] = contracts['batch_id'].iloc[0]
                contract_df['requested_states'] = None
                uid_list = db.generate_unique_id(db.get_last_inserted_id(), len(contract_df.index))
                for i, contr in contract_df.iterrows():
                    contract_df.loc[i, 'txn_id'] = uid_list[i]
                contract_df['load_date'] = contracts['load_date'].iloc[0]
        except Exception as e:
            print(f"==Error during lob adjustments at responsible agent contract fetch level: {e}")

        if contract_df is None:
            print("==No existing contracts found for agent.")
            #contract['initial'] = 'YES'
        else:
            print("==Existing contracts found for agent.")
            contracts = pd.concat([contracts,contract_df], ignore_index=True, axis=0)
            contracts = contracts.fillna(np.nan).replace([np.nan], [None])
    print(contracts)
    return contracts


def upsert_contract(contract):
    print(f"==Inserting new contract: {contract}")
    print("==Assembling data...")
    data = f"""
                {{
                    "data": [
                        {{
                            "Agent": {json.dumps(contract['Agent'].item())},
                            "NPN": {json.dumps(contract['NPN'].item())},
                            "Carrier": {json.dumps(contract['Carrier'].item())},
                            "Status_Date": "{contract['Status_Date'].item()}",
                            "Source": {json.dumps(contract['Source'].item())},
                            "Field_Sales_Director": {json.dumps(contract['Field_Sales_Director'].item())},
                            "Appointment_Type": {json.dumps(contract['Appointment_Type'].item())},
                            "Requested_States": {json.dumps(contract['Requested_States'].item())},
                            "Schedule": {json.dumps(contract['Schedule'].item())},
                            "Parent_Contract": {json.dumps(contract['Parent_Contract'].item())},
                            "Upline": {json.dumps(contract['Upline'].item())},
                            "Top_Upline": {json.dumps(contract['Top_Upline'].item())},
                            "Status": {json.dumps(contract['Status'].item())}
                        }}
                    ]
                }}
    """
    print(data)

    zoho_auth = get_zoho_authentication_token()
    headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
    print("==Posting upsert request...")
    response = requests.post('https://www.zohoapis.com/crm/v2/Agent_Contracts/upsert', headers=headers, data=data)
    print(f"==Received status code {response.status_code}")
    print(response.json())
    data = str(response.json().get("data", []))
    contract_df = pd.DataFrame(ast.literal_eval(data))
    print(contract_df.to_string())
    if response.status_code == 400:
        return False
    return True


# uploading zip file for bulk write upload
def BulkWriteUpload(zip_file):
  base_url="https://content.zohoapis.com/crm/v2/upload"
  header = {
      'Authorization': f'Zoho-oauthtoken {get_zoho_authentication_token()}',
      'feature':'bulk-write',
      'X-CRM-ORG':'658450569'
  }
  files = {'file': open(zip_file,'rb')}
  response=requests.post(f'{base_url}',headers=header,files=files)
  print(response)
  if response.status_code == 200:
      upload_response = response.json()
      print(upload_response)
      file_id=upload_response["details"]["file_id"]
      return file_id
  else:
      print(f'Error: {response.status_code}')
      return f'Error: {response.status_code}'

# create bulk write job
def BulkWriteJob(response_body):
  base_url="https://www.zohoapis.com/crm/bulk/v2/write"
  header = {
      'Authorization': f'Zoho-oauthtoken {get_zoho_authentication_token()}'
  }
  print(header)
  response=requests.post(f'{base_url}',headers=header,json=response_body)
  print(response)
  if response.status_code == 201:
      UploadResponse = response.json()
      print(UploadResponse)
      response_id=UploadResponse["details"]["id"]
      print(response_id)
      return response_id
  else:
      print(response.json())
      print(f'Error: {response.status_code}')
      return f'Error: {response.status_code}'


# preparing response body for Bulk Writing CommItems to Zoho CRM
def BulkWriteBody(file_id):
    resource = [
        {
            "type": "data"
            , "module": "Agent_Contracts"  # Name of the module in the API
            , "file_id": file_id
            , "field_mappings": [
                {
                  "api_name": "Status",
                  "index": 0
                },
                {
                  "api_name": "id",
                  "index": 1
                },
                {
                  "api_name": "Status_Date",
                  "index": 2,
                  "ignore_empty": True
                },
                {
                  "api_name": "Process_Automation",
                  "index": 3
                }
              ]
            , "find_by": "id"
        }
    ]

    response_body = {
        "operation": "update"
        # ,"ignore_empty":False
        # ,"callback":{"url":"https://www.facebook.com/","method":"post"}
        , "resource": resource
    }

    return response_body


def BulkWriteStatus(response_id):
    base_url = "https://www.zohoapis.com/crm/bulk/v2/write/"
    header = {
        'Authorization': f'Zoho-oauthtoken {get_zoho_authentication_token()}'
    }
    print(header)
    while True:
        print("==Waiting for CRM Bulk Write...")
        time.sleep(40)
        response = requests.get(f'{base_url}{response_id}', headers=header)
        print(response)
        if response.status_code == 200:
            BulkWriteResponse = response.json()
            print(BulkWriteResponse)
            if BulkWriteResponse["resource"][0]["file"]["status"] == "COMPLETED":
                print(BulkWriteResponse["resource"][0]["file"])
                return BulkWriteResponse["result"]["download_url"]
        else:
            print(f'Error: {response.status_code}')
            return "Error"

def get_bulk_write_status(access_token, output_status_links):
    # Getting the return from the BulkWrite Job
    base_url = output_status_links
    header = {
        'Authorization': f'Zoho-oauthtoken {access_token}'
    }
    response = requests.get(f'{base_url}', headers=header, stream=True)

    # Step 3: Read the zip file content from the response
    zip_buffer = BytesIO(response.content)

    # Step 4: Extract the CSV file from the zip content
    with zipfile.ZipFile(zip_buffer, 'r') as z:
        # Assuming there's only one CSV file in the zip
        csv_filename = z.namelist()[0]
        with z.open(csv_filename) as csv_file:
            # Step 5: Load the CSV content into a pandas DataFrame
            df = pd.read_csv(csv_file)
    df['upload_date_crm'] = datetime.now(pytz.timezone('US/Central')).strftime('%Y-%m-%d %H:%M:%S')

    return df

def update_contract_batch_on_crm(df_contracts):
    # If any fields are added/reorganized in this section, you must address the field_mapping column indices in BulkWriteBody()
    print("==Beginning CRM contract update...")
    print(df_contracts.to_string())
    print(df_contracts[['contract_status', 'id', 'fail_status', 'write_to_crm', 'update_status_date', 'success_status', 'old_status_date']])
    print("=====Trimming data=====")
    trimmed_df = df_contracts[['contract_status', 'id', 'fail_status', 'write_to_crm', 'update_status_date', 'success_status', 'old_status_date']]
    print("=====write_to_crm filter=====")
    trimmed_df = trimmed_df.loc[trimmed_df['write_to_crm'] == 'Yes']
    trimmed_df = trimmed_df.drop(columns=['write_to_crm'])
    print("=====Success Contract Status logic=====")
    trimmed_df['contract_status'] = np.where((~trimmed_df['success_status'].isnull()) & (trimmed_df['success_status'] != 'None')
                                             ,trimmed_df['success_status'],trimmed_df['contract_status'])
    trimmed_df = trimmed_df.drop(columns=['success_status'])
    print("=====Failed Contract Status logic=====")
    trimmed_df['contract_status'] = np.where((~trimmed_df['fail_status'].isnull()) & (trimmed_df['fail_status'] != 'None')
                                             ,trimmed_df['fail_status'],trimmed_df['contract_status'])
    trimmed_df = trimmed_df.drop(columns=['fail_status'])
    print("=====Status Date logic=====")
    trimmed_df['Status_Date'] = None
    trimmed_df['Status_Date'] = np.where(trimmed_df['update_status_date'] == 'Yes', dt.now().date(), trimmed_df['old_status_date'])
    #trimmed_df['Status_Date'] = dt.now().date()
    trimmed_df = trimmed_df.drop(columns=['update_status_date'])
    trimmed_df = trimmed_df.drop(columns=['old_status_date'])
    print("==========")
    print(trimmed_df)
    format_mapping = {
        "contract_status": "Status"
    }
    trimmed_df.rename(columns=format_mapping, inplace=True)
    trimmed_df['Process_Automation'] = True
    print(f"==Beginning CRM contract update process with table:\n{trimmed_df.to_string()}")
    compression_opts = dict(method='zip', archive_name='agents_contracts.csv')
    zip_buffer = BytesIO()
    trimmed_df.to_csv(zip_buffer, index=False, compression=compression_opts)
    zip_buffer.seek(0)

    zip_file = 'agents_contracts.zip'
    with open(zip_file, 'wb') as downloaded_file:
      downloaded_file.write(zip_buffer.getvalue())

    print("==Uploading zip file to CRM")
    # Upload the file and get the file_id
    file_id = BulkWriteUpload(zip_file)

    print("==Generating BulkWrite Body")
    # Use the file_id to generate the BulkWrite body
    response_body = BulkWriteBody(file_id)

    print("==Beginning BulkWrite Job")
    # Upload the data to the CRM
    response_id = BulkWriteJob(response_body)
    if response_id[:4] != "Error":
      output_status_links = BulkWriteStatus(response_id)

    print(f"=====OUTPUT STATUS LINKS:\n{output_status_links}\n")
    try:
        print("==Fetching BulkWrite Status...")
        # Get the status of the BulkWrite Job
        output_status = get_bulk_write_status(get_zoho_authentication_token(), output_status_links)
        print(output_status.to_string())
    except Exception as e:
        print("==Error: Zoho BulkWrite job status retrieval failed. Maybe an empty table was sent?")
    return None

def update_notes(df_contracts):
    print("==Beginning CRM note upload process...\n")
    zoho_auth = get_zoho_authentication_token()
    for i, contract in df_contracts.iterrows():
        contract_note = contract['note_error']
        if contract_note is None or contract_note == 'None':
            continue
        try:
            print(f"==Uploading contract note for: {contract}")
            id = contract['id']
            print(f"==contract_id:\n{id}")
            headers = {'Authorization': f'Zoho-oauthtoken {zoho_auth}'}
            data = f"""
            {{
                "data": [
                    {{
                        "Parent_Id": {{
                            "module": {{
                                "api_name": "Agent_Contracts"
                            }},
                            "id": "{id}"
                        }},
                        "Note_Content": "{contract_note}"
                    }}
                ]
            }}
            """
            print(f"==Posting COQL query to fetch Responsible Agent data...\n{data}")
            response = requests.post(f'https://www.zohoapis.com/crm/v8/Agent_Contracts/{id}/Notes', headers=headers, data=data)
            print(f"==Finished uploading contract note:\n{response.text}")
        except Exception as e:
            print(f"==Error uploading contract note: {e}")
    print("\n==Finished all note uploads.")
    return None
