import requests

def is_trademarked(word):
    """
    Checks the USPTO API to see if a word has an active trademark.
    Note: Requires a USPTO API Key from developer.uspto.gov
    """
    # USPTO TSDR API Endpoint (2026 Standard)
    api_url = f"https://developer.uspto.gov/api-catalog/tsdr-data-api/v1/search"
    
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer YOUR_USPTO_API_KEY"
    }
    
    # Query for the literal word in active trademarks
    params = {
        "q": f"mark_literal_text:{word} AND registration_date:[* TO *]",
        "rows": 1
    }

    try:
        response = requests.get(api_url, headers=headers, params=params)
        data = response.json()
        
        # If any results come back, it's a potential risk
        if data.get('count', 0) > 0:
            return True
        return False
    except Exception as e:
        print(f"Trademark check failed: {e}")
        return True # Default to True (safe) if API fails