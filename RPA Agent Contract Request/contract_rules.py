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


def rule_check(value, condition, target_str):
    target_str = target_str.replace('Blank','')
    if condition != 'is':
        target_list = target_str.split(',')
    else:
        target_list = [target_str]
    value = str(value)
    value = value.replace("None",'')
    if len(value) == 0:
        value = ''
    condition = str(condition)
    print(f"====Checking rule: {value} // {condition} // {target_list}")
    # Handle blanks
    
    match condition:
        case 'in':
            return value in target_list
        case 'not':
            return value not in target_list
        case 'min_length':
            return len(value) >= int(target_list[0])
        case 'is':
            return (value is target_list[0]) or (value == target_list[0])
        case 'contains':
            return target_list[0] in value
        case _:
            print(f"==Error - Rule condition [{condition}] not supported.")
            raise IndexError

def priority_or(target_list):
    print(target_list)
    for target in target_list:
        if target is not None and target != 'None':
            return target
    return None
    
def check_against_rules(df_contracts, df_rules, field_value_override=None, result_override=None):
    # df_rules should contain every rule marked as 'global' and every rule matching carrier_id
    print("==Checking data against given rules...\n")
    for i, contract in df_contracts.iterrows():
        print(f"==Checking contract:\n{contract}")
        for j, rule in df_rules.iterrows():
            if rule['appointment_type'] == "None":
                rule['appointment_type'] = None
            if (rule['appointment_type'] is not None) and (rule['appointment_type'] != contract['appointment_type']):
                print(f"==Skipping rule for field {rule['field']} because of appointment_type mismatch with contract.")
                continue
            if contract['agent_type'] == rule['agent_type']:
                try:
                    validation = True
                    if rule['condition'] == 'priority_or':
                        print("====Checking priority_or values...")
                        priority_list = []
                        for column_name in rule['expected_value'].split(','):
                            if contract[column_name] is not None and len(contract[column_name]) == 0:
                                contract[column_name] = None
                            priority_list.append(contract[column_name])
                        contract[rule['field']] = priority_or(priority_list)
                        print(contract[rule['field']])
                        print(rule['field'])
                        print(contract['email_address'])
                        if contract[rule['field']] is None:
                            validation = False
                    else:
                        if field_value_override is None:
                            print("==Field value override was blank")
                            validation = rule_check(contract[rule['field']], rule['condition'], rule['expected_value'])
                        else:
                            print(f"==Field value override present: {field_value_override}")
                            validation = rule_check(field_value_override, rule['condition'], rule['expected_value'])
                    print(f"{validation}")

                    contract['write_to_crm'] = rule['write_to_crm']
                    contract['update_status_date'] = rule['update_status_date']
                    if result_override is not None:
                        validation = result_override
                    if not validation:
                        # 'None' comparisons avoid overwrites for rules that can be failed without stopping the process
                        if rule['fail_status'] is not None:
                            contract['fail_status'] = rule['fail_status']
                        if rule['error_message'] is not None:
                            contract['error_message'] = rule['error_message']
                        if rule['note_error'] is not None:
                            contract['note_error'] = rule['note_error']
                        if rule['process_flag_fail'] is not None:
                            contract['process_flag'] = rule['process_flag_fail']
                            break
                    else:
                        contract['success_status'] = rule['success_status']
                        contract['note_error'] = rule['note_success']
                        print(f"==process_flag_success: {rule['process_flag_success']}")
                        if (rule['process_flag_success'] is not None) and (rule['process_flag_success'] != 'None'):
                            print("==Entered process_flag_success if statement")
                            contract['process_flag'] = rule['process_flag_success']
                except Exception as e:
                    print(f"====Error during rule handling: {e}")
        df_contracts.loc[i] = contract
    return df_contracts
            
            
            
            
            