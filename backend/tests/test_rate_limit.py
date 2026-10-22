from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.main import rate_limit


@pytest.mark.asyncio
async def test_rate_limit_rejects_excess_requests():
    key = str(uuid4())
    await rate_limit("test", key, 2, 60)
    await rate_limit("test", key, 2, 60)
    with pytest.raises(HTTPException) as exc:
        await rate_limit("test", key, 2, 60)
    assert exc.value.status_code == 429
