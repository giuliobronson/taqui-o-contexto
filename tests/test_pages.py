async def test_privacy_page_is_public(client) -> None:
    response = await client.get("/privacidade")
    assert response.status_code == 200
    assert "Política de privacidade" in response.text
    assert "nome, e-mail e foto" in response.text


async def test_every_page_links_to_privacy(client) -> None:
    assert 'href="/privacidade"' in (await client.get("/")).text
