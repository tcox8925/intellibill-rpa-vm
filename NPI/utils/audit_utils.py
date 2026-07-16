def generate_audit_id(npi, source):
    from datetime import datetime
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{source.upper()}_{npi}_{timestamp}"
