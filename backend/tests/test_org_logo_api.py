"""
Unit tests for the Organization Logo API endpoints.
Uses unittest.mock — no database required.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def membership(org_id, user_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.db_id = str(user_id)
    m.user.email = "test@example.com"
    m.role = "admin"
    return m


def make_org(org_id, **overrides):
    org = MagicMock()
    org.id = org_id
    org.name = "Test Org"
    org.logo_data = overrides.get("logo_data")
    org.logo_content_type = overrides.get("logo_content_type")
    org.logo_filename = overrides.get("logo_filename")
    org.logo_updated_at = overrides.get("logo_updated_at")
    return org


def make_db(org):
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = org
    db.execute = AsyncMock(return_value=result)
    return db


def make_upload(content_type="image/png", filename="logo.png", data=PNG_BYTES):
    f = MagicMock()
    f.content_type = content_type
    f.filename = filename
    f.read = AsyncMock(return_value=data)
    return f


class TestUploadLogo:
    @pytest.mark.asyncio
    @patch("api.organizations.log_entity_changes", new_callable=AsyncMock)
    async def test_upload_happy_path(self, _audit, membership, org_id):
        from api.organizations import upload_organization_logo

        org = make_org(org_id)
        db = make_db(org)
        result = await upload_organization_logo(
            org_id=org_id, request=MagicMock(), file=make_upload(),
            membership=membership, db=db,
        )
        assert org.logo_data == PNG_BYTES
        assert org.logo_content_type == "image/png"
        assert result.filename == "logo.png"
        assert result.size_bytes == len(PNG_BYTES)
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_rejects_bad_content_type(self, membership, org_id):
        from api.organizations import upload_organization_logo

        with pytest.raises(HTTPException) as exc:
            await upload_organization_logo(
                org_id=org_id, request=MagicMock(),
                file=make_upload(content_type="text/plain", filename="x.txt"),
                membership=membership, db=make_db(make_org(org_id)),
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_oversize(self, membership, org_id):
        from api.organizations import upload_organization_logo, MAX_LOGO_SIZE_BYTES

        with pytest.raises(HTTPException) as exc:
            await upload_organization_logo(
                org_id=org_id, request=MagicMock(),
                file=make_upload(data=b"x" * (MAX_LOGO_SIZE_BYTES + 1)),
                membership=membership, db=make_db(make_org(org_id)),
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_empty_file(self, membership, org_id):
        from api.organizations import upload_organization_logo

        with pytest.raises(HTTPException) as exc:
            await upload_organization_logo(
                org_id=org_id, request=MagicMock(), file=make_upload(data=b""),
                membership=membership, db=make_db(make_org(org_id)),
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_404_when_org_missing(self, membership, org_id):
        from api.organizations import upload_organization_logo

        with pytest.raises(HTTPException) as exc:
            await upload_organization_logo(
                org_id=org_id, request=MagicMock(), file=make_upload(),
                membership=membership, db=make_db(None),
            )
        assert exc.value.status_code == 404


class TestGetLogo:
    @pytest.mark.asyncio
    async def test_returns_bytes_with_media_type(self, membership, org_id):
        from api.organizations import get_organization_logo

        org = make_org(org_id, logo_data=PNG_BYTES, logo_content_type="image/png")
        response = await get_organization_logo(org_id=org_id, membership=membership, db=make_db(org))
        assert response.body == PNG_BYTES
        assert response.media_type == "image/png"
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    @pytest.mark.asyncio
    async def test_404_when_no_logo(self, membership, org_id):
        from api.organizations import get_organization_logo

        with pytest.raises(HTTPException) as exc:
            await get_organization_logo(org_id=org_id, membership=membership, db=make_db(make_org(org_id)))
        assert exc.value.status_code == 404


class TestDeleteLogo:
    @pytest.mark.asyncio
    @patch("api.organizations.log_entity_changes", new_callable=AsyncMock)
    async def test_delete_clears_logo(self, _audit, membership, org_id):
        from api.organizations import delete_organization_logo

        org = make_org(org_id, logo_data=PNG_BYTES, logo_content_type="image/png", logo_filename="logo.png")
        db = make_db(org)
        await delete_organization_logo(org_id=org_id, request=MagicMock(), membership=membership, db=db)
        assert org.logo_data is None
        assert org.logo_filename is None
        db.commit.assert_awaited()
