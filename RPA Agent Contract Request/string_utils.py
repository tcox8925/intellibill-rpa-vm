def trim_name(name):
    name = name.replace('-', '')
    name = name.replace(' ', '')
    name = name.replace('.', '')
    name = name.replace("'", '')
    return name


def state_name_to_state_code(state):
    if state == '' or state is None:
        return ''
    if len(state) == 2:
        return state
    state = state_map.get(state.lower())
    if state is None:
        return ''
    return state

state_map = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
    "american samoa": "AS",
    "guam": "GU",
    "northern mariana islands": "MP",
    "puerto rico": "PR",
    "united states minor outlying islands": "UM",
    "virgin islands, u.s.": "VI",
    '__default__':'??'
}

def cigna_state_producer_check(state_list):
    print(f'Running Cigna state producer alignment for state list: {state_list}')
    producer_output_map = {
        'Loyal American Life Insurance Company':False,
        'Cigna Insurance Company':False,
        'Medco Containment Life Insurance Company':False,
        'Cigna National Health Insurance Company':False,
        'Cigna Health and Life Insurance Company':False,
        'American Retirement Life Insurance Company':False,
    }
    for state in state_list:
        if state in cigna_loyal_american:
            producer_output_map['Loyal American Life Insurance Company'] = True
        if state in cigna_cigna_insurance:
            producer_output_map['Cigna Insurance Company'] = True
        if state in cigna_medco_containment:
            producer_output_map['Medco Containment Life Insurance Company'] = True
        if state in cigna_national_health:
            producer_output_map['Cigna National Health Insurance Company'] = True
        if state in cigna_health_and_life:
            producer_output_map['Cigna Health and Life Insurance Company'] = True
        if state in cigna_american_retirement:
            producer_output_map['American Retirement Life Insurance Company'] = True

    for key in producer_output_map:
        print(f'{key}: {producer_output_map.get(key)}')
    return producer_output_map


cigna_loyal_american =\
    ['Alabama','Alaska','Arizona','Arkansas','California',
     'Colorado','Connecticut','Delaware','District of Columbia','Florida',
     'Georgia','Hawaii','Idaho','Illinois','Indiana',
     'Iowa','Kansas','Kentucky','Louisiana','Maine',
     'Maryland','Massachusetts','Michigan','Minnesota','Mississippi',
     'Missouri','Montana','Nebraska','Nevada','New Hampshire',
     'New Jersey','New Mexico','North Carolina','North Dakota','Ohio',
     'Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina',
     'South Dakota','Tennessee','Texas','Utah','Vermont',
     'Virginia','Washington','West Virginia','Wisconsin','Wyoming']
cigna_cigna_insurance =\
    ['Colorado','Indiana','Kansas','Louisiana','Nevada',
     'Pennsylvania','Tennessee','Texas']
cigna_medco_containment =\
    ['Idaho','Maine','Rhode Island','Vermont']
cigna_national_health =\
    ['Alabama','Arizona','Arkansas','Connecticut','Florida',
     'Georgia','Illinois','Iowa','Kentucky','Maryland',
     'Michigan','Mississippi','Missouri','Montana','New Hampshire',
     'New Jersey','New Mexico','North Carolina','North Dakota','Ohio',
     'Oklahoma','South Carolina','South Dakota','Utah','Virginia',
     'West Virginia','Wisconsin','Wyoming']
cigna_health_and_life =\
    ['Delaware','Minnesota','Nebraska','Oregon','Rhode Island',
     'Washington']
cigna_american_retirement =\
    ['Arizona','California']

#cigna_state_producer_check(['Arizona'])

resident_state_list =\
    ["Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "District_of_Columbia",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana1",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New_Hampshire",
    "New_Jersey",
    "New_Mexico",
    "New_York",
    "North_Carolina",
    "North_Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Puerto_Rico",
    "Rhode_Island",
    "South_Carolina",
    "South_Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West_Virginia",
    "Wisconsin",
    "Wyoming",
    ]
