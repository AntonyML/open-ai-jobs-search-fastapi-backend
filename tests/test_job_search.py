"""Tests para job_search.py — verificar sin depender del frontend ni del microservicio."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_keywords_split_into_or_conditions():
    """'Software Engineer Backend' debe generar 3 términos, no 1 cadena gigante."""
    from app.services.job_search import search_jobs

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    req = MagicMock()
    req.keywords = "Software Engineer Backend"
    req.location = ""
    req.limit = 50

    user = MagicMock()

    with patch("app.services.job_search.trigger_ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = "fake-id"
        await search_jobs(mock_db, req, user)

    call_args = mock_db.execute.call_args
    query_str = str(call_args[0][0])
    # SQLAlchemy usa parámetros (:title_1, :description_1), no literales.
    # Verificamos que la query tenga 6 condiciones LIKE (3 términos x 2 columnas)
    like_count = query_str.count("LIKE")
    assert like_count >= 6, f"Esperaba >=6 LIKE, tengo {like_count}: {query_str}"
    # Verificar que NO hay un solo LIKE monstruoso (la cadena completa NO debe aparecer)
    assert "Software Engineer Backend" not in query_str
    # Verificar que usa OR entre condiciones
    assert "OR" in query_str


@pytest.mark.asyncio
async def test_trigger_ingest_returns_none_on_500():
    """Si el microservicio retorna 500, trigger_ingest debe retornar None, no crashear."""
    from app.services.job_search import trigger_ingest

    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await trigger_ingest("stem_cr", "Software Engineer")
        assert result is None


@pytest.mark.asyncio
async def test_trigger_ingest_returns_none_on_timeout():
    """Si el microservicio no responde (timeout), trigger_ingest retorna None."""
    from app.services.job_search import trigger_ingest

    import httpx

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("timed out", request=MagicMock())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await trigger_ingest("stem_cr", "Software Engineer")
        assert result is None


@pytest.mark.asyncio
async def test_search_returns_empty_when_db_empty():
    """Con BD vacía, search debe retornar results=0 y disparar ingesta."""
    from app.services.job_search import search_jobs

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result

    req = MagicMock()
    req.keywords = "Software Engineer"
    req.location = "Costa Rica"
    req.limit = 50

    user = MagicMock()

    with patch("app.services.job_search.trigger_ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = "ingest-123"
        result = await search_jobs(mock_db, req, user)

    assert result.count == 0
    assert result.fresh is False
    assert result.ingest_job_id == "ingest-123"
    mock_ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_trigger_ingest_raises_on_400():
    """Si el microservicio retorna 400, trigger_ingest debe lanzar excepción (no tragarse el error)."""
    from app.services.job_search import trigger_ingest
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Bad request", request=MagicMock(), response=mock_response
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await trigger_ingest("stem_cr", "")
