from app.security import hash_value, privacy_reduced_ip, sign_guest, ua_family, unsign_guest


def test_signed_guest_cookie_survives_refresh_and_tabs():
    token="same-guest-token"
    cookie=sign_guest(token)
    assert unsign_guest(cookie)==token
    assert unsign_guest(cookie)==token

def test_tampered_guest_cookie_is_rejected():
    cookie=sign_guest("guest")
    assert unsign_guest(cookie+"x") is None

def test_ip_is_privacy_reduced_before_hashing():
    assert privacy_reduced_ip("203.0.113.42")=="203.0.113.0/24"
    assert "/48" in privacy_reduced_ip("2001:db8:1234:5678::1")
    assert "203.0.113.42" not in hash_value(privacy_reduced_ip("203.0.113.42"),"ip-cluster")

def test_user_agent_is_coarse_not_invasive():
    assert ua_family("Mozilla Chrome Windows NT 10")==('chrome','windows')

