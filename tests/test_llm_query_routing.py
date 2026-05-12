import asyncio

import shinka.llm.query as query_module


def test_query_routes_openai(monkeypatch):
    monkeypatch.setattr(
        query_module,
        "get_client_llm",
        lambda model_name, structured_output=False: (
            "client",
            "gpt-5-mini",
            "openai",
        ),
    )
    called = {}

    def _fake_openai_query(
        client,
        model,
        msg,
        system_msg,
        msg_history,
        output_model,
        model_posteriors=None,
        **kwargs,
    ):
        called["provider"] = "openai"
        called["model"] = model
        return "ok"

    monkeypatch.setattr(query_module, "query_openai", _fake_openai_query)

    result = query_module.query(
        model_name="gpt-5-mini",
        msg="hello",
        system_msg="sys",
    )

    assert result == "ok"
    assert called["provider"] == "openai"
    assert called["model"] == "gpt-5-mini"


def test_query_routes_azure_openai(monkeypatch):
    monkeypatch.setattr(
        query_module,
        "get_client_llm",
        lambda model_name, structured_output=False: (
            "client",
            "gpt-4.1",
            "azure_openai",
        ),
    )
    called = {}

    def _fake_openai_query(
        client,
        model,
        msg,
        system_msg,
        msg_history,
        output_model,
        model_posteriors=None,
        **kwargs,
    ):
        called["provider"] = "azure_openai"
        called["model"] = model
        return "ok"

    monkeypatch.setattr(query_module, "query_openai", _fake_openai_query)

    result = query_module.query(
        model_name="azure-gpt-4.1",
        msg="hello",
        system_msg="sys",
    )

    assert result == "ok"
    assert called["provider"] == "azure_openai"
    assert called["model"] == "gpt-4.1"


def test_query_async_routes_openai(monkeypatch):
    monkeypatch.setattr(
        query_module,
        "get_async_client_llm",
        lambda model_name, structured_output=False: (
            "client",
            "gpt-5-mini",
            "openai",
        ),
    )
    called = {}

    async def _fake_openai_query_async(
        client,
        model,
        msg,
        system_msg,
        msg_history,
        output_model,
        model_posteriors=None,
        **kwargs,
    ):
        called["provider"] = "openai"
        called["model"] = model
        return "ok-async"

    monkeypatch.setattr(query_module, "query_openai_async", _fake_openai_query_async)

    result = asyncio.run(
        query_module.query_async(
            model_name="gpt-5-mini",
            msg="hello",
            system_msg="sys",
        )
    )

    assert result == "ok-async"
    assert called["provider"] == "openai"
    assert called["model"] == "gpt-5-mini"
