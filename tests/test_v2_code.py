from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app
from backend.v2_code import _decl


def _client(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "code-author")
    monkeypatch.setenv("ADMIN_PASSWORD", "code-author-password")
    app=create_app(data_dir=tmp_path,secret_key="code-test-secret-"*3,cookie_secure=False)
    client=TestClient(app);client.__enter__()
    auth=client.post("/api/login",json={"username":"code-author","password":"code-author-password"})
    assert auth.status_code==200
    return client,{"X-CSRF-Token":auth.json()["csrf_token"]}


def test_code_get_is_read_only_and_has_actual_calculation_timestamp(tmp_path,monkeypatch):
    client,_=_client(tmp_path,monkeypatch)
    try:
        result=client.get("/api/v2/code")
        assert result.status_code==200,result.text
        data=result.json()
        assert data["calculated_at"]
        assert isinstance(data["objects"],list)
        assert client.get("/api/v2/project").json()["revision"]==data["revision"]
    finally: client.__exit__(None,None,None)


def test_declaration_is_rendered_as_st():
    ns="http://www.plcopen.org/xml/tc6_0200"
    pou=etree.fromstring(f'<pou xmlns="{ns}" name="FNC_EXAMPLE" pouType="function"><interface><inputVars><variable name="VALUE"><type><INT/></type></variable></inputVars><returnType><BOOL/></returnType></interface></pou>')
    rendered=_decl({"element":pou})
    assert rendered.startswith("FUNCTION FNC_EXAMPLE : BOOL")
    assert "VAR_INPUT" in rendered and "VALUE : INT;" in rendered


def test_generated_code_custom_edit_requires_csrf_and_apply_is_single_use(tmp_path,monkeypatch):
    client,headers=_client(tmp_path,monkeypatch)
    try:
        revision=client.get("/api/v2/code").json()["revision"]
        body={"revision":revision,"name":"GFC_COM_IN","custom_code":"// reviewed code"}
        assert client.post("/api/v2/code/preview",json=body).status_code==403
        forbidden=client.post("/api/v2/code/preview",headers=headers,json={**body,"body":"FORBIDDEN"})
        assert forbidden.status_code==400
        preview=client.post("/api/v2/code/preview",headers=headers,json=body)
        assert preview.status_code==200,preview.text
        assert preview.json()["errors"]==[]
        assert client.post("/api/v2/code/apply",headers=headers,json={"token":preview.json()["token"]}).status_code==200
        assert client.post("/api/v2/code/apply",headers=headers,json={"token":preview.json()["token"]}).status_code==404
        item=next(x for x in client.get("/api/v2/code").json()["objects"] if x["name"]=="GFC_COM_IN")
        assert "reviewed code" in item["custom_code"]
    finally: client.__exit__(None,None,None)


def test_stale_preview_and_unknown_references_are_rejected(tmp_path,monkeypatch):
    client,headers=_client(tmp_path,monkeypatch)
    try:
        revision=client.get("/api/v2/code").json()["revision"]
        bad=client.post("/api/v2/code/preview",headers=headers,json={"revision":revision,"name":"GFC_COM_IN","custom_code":"FB_DOES_NOT_EXIST();"})
        assert bad.status_code==200,bad.text
        assert bad.json()["errors"]
        assert bad.json()["token"] is None
        assert client.get("/api/v2/code").json()["revision"]==revision
        good=client.post("/api/v2/code/preview",headers=headers,json={"revision":revision,"name":"GFC_COM_IN","custom_code":"// valid"})
        assert good.status_code==200 and good.json()["token"]
        assert client.post("/api/v2/code/apply",headers=headers,json={"token":good.json()["token"]}).status_code==200
        stale=client.post("/api/v2/code/preview",headers=headers,json={"revision":revision,"name":"GFC_COM_IN","custom_code":""})
        assert stale.status_code==409
    finally: client.__exit__(None,None,None)
