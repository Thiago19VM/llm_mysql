import requests

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

def verify_recaptcha(secret_key: str, response_token: str, remoteip: str | None = None) -> tuple[bool, dict]:
    """
    Retorna (ok, payload_do_google).
    ok=True quando success==True.
    """
    data = {
        "secret": secret_key,
        "response": response_token,
    }
    if remoteip:
        data["remoteip"] = remoteip

    try:
        r = requests.post(VERIFY_URL, data=data, timeout=5)
        payload = r.json()
    except Exception:
        return False, {"success": False, "error-codes": ["request-failed"]}

    return bool(payload.get("success")), payload