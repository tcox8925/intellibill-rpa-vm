-- Seed common insurance carrier abbreviations into jay_entity_aliases
-- Idempotent: ON CONFLICT DO NOTHING ensures safe re-runs
-- Source: 'seed', Confidence: 0.95

INSERT INTO wpo.jay_entity_aliases
    (entity_type, alias_value, canonical_value, confidence, source, is_active)
VALUES
    -- Major national carriers
    ('carrier_name', 'bcbs',           'Blue Cross Blue Shield',    0.95, 'seed', TRUE),
    ('carrier_name', 'blue cross',     'Blue Cross Blue Shield',    0.95, 'seed', TRUE),
    ('carrier_name', 'blue shield',    'Blue Cross Blue Shield',    0.95, 'seed', TRUE),
    ('carrier_name', 'uhc',            'UnitedHealthcare',          0.95, 'seed', TRUE),
    ('carrier_name', 'united',         'UnitedHealthcare',          0.95, 'seed', TRUE),
    ('carrier_name', 'unitedhealthcare','UnitedHealthcare',         0.95, 'seed', TRUE),
    ('carrier_name', 'united healthcare','UnitedHealthcare',        0.95, 'seed', TRUE),
    ('carrier_name', 'cigna',          'Cigna',                     0.95, 'seed', TRUE),
    ('carrier_name', 'aetna',          'Aetna',                     0.95, 'seed', TRUE),
    ('carrier_name', 'humana',         'Humana',                    0.95, 'seed', TRUE),
    ('carrier_name', 'anthem',         'Anthem',                    0.95, 'seed', TRUE),
    ('carrier_name', 'kaiser',         'Kaiser Permanente',         0.95, 'seed', TRUE),
    ('carrier_name', 'kaiser permanente','Kaiser Permanente',       0.95, 'seed', TRUE),
    ('carrier_name', 'kp',             'Kaiser Permanente',         0.95, 'seed', TRUE),

    -- Managed care / Medicaid carriers
    ('carrier_name', 'molina',         'Molina Healthcare',         0.95, 'seed', TRUE),
    ('carrier_name', 'centene',        'Centene',                   0.95, 'seed', TRUE),
    ('carrier_name', 'wellcare',       'WellCare',                  0.95, 'seed', TRUE),
    ('carrier_name', 'well care',      'WellCare',                  0.95, 'seed', TRUE),
    ('carrier_name', 'ambetter',       'Ambetter',                  0.95, 'seed', TRUE),

    -- Newer / smaller carriers
    ('carrier_name', 'oscar',          'Oscar Health',              0.95, 'seed', TRUE),
    ('carrier_name', 'oscar health',   'Oscar Health',              0.95, 'seed', TRUE),
    ('carrier_name', 'bright',         'Bright Health',             0.95, 'seed', TRUE),
    ('carrier_name', 'bright health',  'Bright Health',             0.95, 'seed', TRUE),
    ('carrier_name', 'devoted',        'Devoted Health',            0.95, 'seed', TRUE),
    ('carrier_name', 'devoted health', 'Devoted Health',            0.95, 'seed', TRUE),
    ('carrier_name', 'clover',         'Clover Health',             0.95, 'seed', TRUE),
    ('carrier_name', 'clover health',  'Clover Health',             0.95, 'seed', TRUE),
    ('carrier_name', 'alignment',      'Alignment Healthcare',      0.95, 'seed', TRUE),
    ('carrier_name', 'alignment healthcare','Alignment Healthcare', 0.95, 'seed', TRUE),
    ('carrier_name', 'friday',         'Friday Health Plans',       0.95, 'seed', TRUE),
    ('carrier_name', 'friday health',  'Friday Health Plans',       0.95, 'seed', TRUE),

    -- Other well-known carriers
    ('carrier_name', 'metlife',        'MetLife',                   0.95, 'seed', TRUE),
    ('carrier_name', 'aflac',          'Aflac',                     0.95, 'seed', TRUE),
    ('carrier_name', 'guardian',       'Guardian',                  0.95, 'seed', TRUE),
    ('carrier_name', 'mutual of omaha','Mutual of Omaha',          0.95, 'seed', TRUE),
    ('carrier_name', 'wellpoint',      'WellPoint',                 0.95, 'seed', TRUE),
    ('carrier_name', 'highmark',       'Highmark',                  0.95, 'seed', TRUE),
    ('carrier_name', 'caresource',     'CareSource',               0.95, 'seed', TRUE),
    ('carrier_name', 'emblem',         'EmblemHealth',              0.95, 'seed', TRUE),
    ('carrier_name', 'emblemhealth',   'EmblemHealth',              0.95, 'seed', TRUE),
    ('carrier_name', 'horizon',        'Horizon Blue Cross Blue Shield', 0.95, 'seed', TRUE),
    ('carrier_name', 'horizon bcbs',   'Horizon Blue Cross Blue Shield', 0.95, 'seed', TRUE)
ON CONFLICT (entity_type, alias_value) DO NOTHING;
