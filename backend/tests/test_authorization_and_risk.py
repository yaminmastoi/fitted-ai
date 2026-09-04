import pytest

from app import security, services
from app.security import ApiError, Principal


class Pool:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, *_args):
        return self.row


@pytest.mark.asyncio
async def test_guest_cannot_access_another_guests_generation(monkeypatch):
    monkeypatch.setattr(services, "get_pool", lambda: Pool({"id": "generation", "guest_id": "guest-b", "user_id": None}))
    with pytest.raises(ApiError) as error:
        await services.owns_generation(Principal("guest", "guest-a"), "generation")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_access_another_users_generation(monkeypatch):
    monkeypatch.setattr(services, "get_pool", lambda: Pool({"id": "generation", "guest_id": None, "user_id": "user-b"}))
    with pytest.raises(ApiError) as error:
        await services.owns_generation(Principal("user", "user-a"), "generation")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_repeated_device_cluster_is_denied_free_trial(monkeypatch):
    async def values(_keys):
        return {"free_trial_ip_window_days": 30}

    monkeypatch.setattr(security, "setting_values", values)
    monkeypatch.setattr(security, "get_pool", lambda: Pool({"ip_count": 2, "device_count": 2, "used_count": 2}))
    decision = await security.evaluate_trial_risk("ip", "device")
    assert decision.action == "DENY_FREE_TRIAL"


@pytest.mark.asyncio
async def test_shared_ip_is_not_a_permanent_identity(monkeypatch):
    async def values(_keys):
        return {"free_trial_ip_window_days": 30}

    monkeypatch.setattr(security, "setting_values", values)
    monkeypatch.setattr(security, "get_pool", lambda: Pool({"ip_count": 2, "device_count": 0, "used_count": 0}))
    decision = await security.evaluate_trial_risk("shared-ip", None)
    assert decision.action == "ALLOW"

