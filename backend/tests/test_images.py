import io

import pytest
from PIL import Image

from app import services
from app.security import ApiError


class Upload:
    def __init__(self,data:bytes,mime:str):self.data=data;self.content_type=mime
    async def read(self,_:int):return self.data

async def fake_settings(_):
    return {"free_max_upload_mb":2,"paid_max_upload_mb":10,"free_min_width":16,"free_min_height":16,"free_max_width":6000,"free_max_height":6000,"max_image_megapixels":1}

def image_bytes(fmt="PNG",size=(32,32)):
    out=io.BytesIO();Image.new("RGB",size,"coral").save(out,fmt);return out.getvalue()

@pytest.mark.asyncio
async def test_valid_image_is_reencoded_without_metadata(monkeypatch):
    monkeypatch.setattr(services,"setting_values",fake_settings)
    safe=await services.validate_image(Upload(image_bytes(),"image/png"),False)
    assert safe.mime=="image/webp" and safe.width==32
    with Image.open(io.BytesIO(safe.data)) as result:assert not result.getexif()

@pytest.mark.asyncio
async def test_unsupported_image_is_rejected(monkeypatch):
    monkeypatch.setattr(services,"setting_values",fake_settings)
    with pytest.raises(ApiError) as error:await services.validate_image(Upload(b"GIF89a","image/gif"),False)
    assert error.value.detail["code"]=="INVALID_IMAGE"

@pytest.mark.asyncio
async def test_corrupt_image_is_rejected(monkeypatch):
    monkeypatch.setattr(services,"setting_values",fake_settings)
    with pytest.raises(ApiError):await services.validate_image(Upload(b"not really a png","image/png"),False)

@pytest.mark.asyncio
async def test_decompression_dimension_bomb_is_rejected(monkeypatch):
    monkeypatch.setattr(services,"setting_values",fake_settings)
    with pytest.raises(ApiError) as error:await services.validate_image(Upload(image_bytes(size=(1100,1100)),"image/png"),False)
    assert error.value.detail["code"]=="IMAGE_DIMENSIONS_TOO_LARGE"

@pytest.mark.asyncio
async def test_free_upload_over_two_mb_rejected(monkeypatch):
    monkeypatch.setattr(services,"setting_values",fake_settings)
    with pytest.raises(ApiError) as error:await services.validate_image(Upload(b"0"*(2*1024*1024+1),"image/png"),False)
    assert error.value.detail["code"]=="UPLOAD_TOO_LARGE"

