"""Revisioned code browser and reviewed ST/custom-code edits."""
from __future__ import annotations
import copy, difflib, json, re, secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from lxml import etree
from pydantic import BaseModel
from backend.db import ImportPreview

GENERATED = {"GFC_COM_IN", "GFC_COM_OUT", "GFC_IO_IN", "GFC_IO_OUT", "GFC_EXC", "GFB_INST", "GFB_UPI", "PLC_PRG"}

class PreviewCode(BaseModel):
    revision: int
    name: str
    body: str | None = None
    custom_code: str | None = None

class ApplyCode(BaseModel): token: str

def _body(obj):
    el = obj.get("element")
    if el is None: return ""
    from backend.v2_plc import NS, HNS
    node = el.find(NS+"body/"+NS+"ST/"+HNS+"xhtml")
    return (node.text or "") if node is not None else ""

def _custom(text):
    m = re.search(r"// BEGIN CUSTOM CODE\n(.*?)\n// END CUSTOM CODE", text, re.S)
    return m.group(1) if m else ""

def _decl(obj):
    el=obj.get("element")
    if el is None: return ""
    from backend.v2_codesys import _st
    from backend.v2_plc import NS
    interface=el.find(NS+"interface"); sections=[]
    labels={"inputVars":"VAR_INPUT","outputVars":"VAR_OUTPUT","inOutVars":"VAR_IN_OUT","localVars":"VAR","tempVars":"VAR_TEMP","externalVars":"VAR_EXTERNAL"}
    if interface is not None:
        for group in interface:
            label=labels.get(etree.QName(group).localname)
            if label:
                clone=copy.deepcopy(group);clone.tag=NS+"globalVars"
                sections.append(_st(clone).replace("VAR_GLOBAL",label,1))
    ptype=el.get("pouType","program").upper()
    if ptype=="FUNCTION":
        ret=el.find(NS+"interface/"+NS+"returnType/*"); suffix=" : "+(etree.QName(ret).localname.upper() if ret is not None else "BOOL")
        header=f"FUNCTION {el.get('name','')}"+suffix
    else: header=f"{('FUNCTION_BLOCK' if ptype=='FUNCTIONBLOCK' else 'PROGRAM')} {el.get('name','')}"
    return header+("\n"+"\n\n".join(sections) if sections else "")

def _objects(state):
    from backend import v2_plc, v2_plc_validation, v2_codesys
    built=v2_plc.build_program(state); snapshot={x.get("name"):x for x in (state.get("snapshot") or {}).get("objects",[]) if x.get("name")}
    semantic_snapshots=v2_plc_validation._snapshot_nodes(state)
    result=[]
    names=set(GENERATED)|{n for n in built if n.startswith("FC_EXC_")}|{f.get("name") for f in state.get("functions",[]) if f.get("name","").startswith("FNC_")}
    for name in sorted(names):
        obj=built.get(name,{"name":name,"kind":"POU","folder":"Functions"})
        kind="exchange" if name.startswith("FC_EXC_") else "function" if name.startswith("FNC_") else "generated" if name in GENERATED else "generated"
        full_body=_body(obj); body=full_body
        function_model=next((f for f in state.get("functions",[]) if f.get("name")==name),None)
        if function_model is not None: body=function_model.get("body","") or ""
        custom=(function_model.get("custom_code","") or "") if function_model is not None else _custom(full_body)
        if name in GENERATED:
            custom=(state.get("generated_custom_code") or {}).get(name,custom)
            body=re.sub(r"\n?// BEGIN CUSTOM CODE\n.*?\n// END CUSTOM CODE\n?","\n",full_body,flags=re.S)
        ref=snapshot.get(name,{}).get("body","") or ""
        status="new" if name not in semantic_snapshots else "changed" if v2_plc_validation._changed(name,obj,semantic_snapshots) else "unchanged"
        diff="".join(difflib.unified_diff(ref.splitlines(True),full_body.splitlines(True),fromfile=f"reference/{name}",tofile=f"current/{name}"))
        result.append({"name":name,"kind":kind,"category":kind,"folder":obj.get("folder",""),"body":body,"custom_code":custom,
          "declaration":_decl(obj),
          "full_body":full_body,"changed":status!="unchanged","status":status,"reference_body":ref,"diff":diff,"dependencies":sorted(v2_plc_validation._refs(obj))})
    return result

def register_code_routes(app,get_session,current_user,csrf):
    from backend import v2_store, v2_plc, v2_plc_validation
    def project(session):
        state=v2_store.load_project(session)
        if state is None: raise HTTPException(503,"The v2 project has not been initialized")
        return state
    @app.get("/api/v2/code")
    def get_code(session=Depends(get_session), user=Depends(current_user)):
        state=project(session)
        try:
            built=v2_plc.build_program(state); xml=v2_plc.serialize_program(built,state,list(built)); validation=v2_plc_validation.validate_xml(xml,state,built)
        except Exception as exc: validation={"errors":[str(exc)],"warnings":[]}
        return {"revision":state.get("revision",0),"calculated_at":datetime.now(timezone.utc).isoformat(),"snapshot_imported_at":(state.get("snapshot") or {}).get("imported_at"),"objects":_objects(state),"validation":validation}

    @app.post("/api/v2/code/preview")
    def preview(req:PreviewCode,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        current=project(session)
        if req.revision!=current.get("revision",0): raise HTTPException(409,"Project changed; refresh code and try again")
        isgen=req.name in GENERATED
        fn=next((f for f in current.get("functions",[]) if f.get("name")==req.name),None)
        if not isgen and fn is None: raise HTTPException(404,"Code object is not editable")
        if isgen and req.body is not None: raise HTTPException(400,"Generated code body is read-only")
        state=copy.deepcopy(current)
        if isgen:
            if req.custom_code is not None: state.setdefault("generated_custom_code",{})[req.name]=req.custom_code
        else:
            target=next(f for f in state["functions"] if f.get("name")==req.name)
            if req.body is not None: target["body"]=req.body
            if req.custom_code is not None: target["custom_code"]=req.custom_code
        try:
            objects=v2_plc.build_program(state); xml=v2_plc.serialize_program(objects,state,list(objects)); checked=v2_plc_validation.validate_xml(xml,state,objects)
            oldobj=v2_plc.build_program(current); oldxml=v2_plc.serialize_program(oldobj,current,list(oldobj)); baseline=set(v2_plc_validation.validate_xml(oldxml,current,oldobj)["errors"])
            checked["errors"]=[e for e in checked["errors"] if e not in baseline]
        except Exception as exc: checked={"errors":[str(exc)],"warnings":[]}
        token=secrets.token_urlsafe(24)
        meta={"kind":"v2-code-edit","base_revision":current.get("revision",0),"name":req.name,"generated":isgen}
        item=next((x for x in _objects(state) if x["name"]==req.name),None)
        if checked["errors"]: return {"token":None,"errors":checked["errors"],"warnings":checked["warnings"],"changes":[],"object":item}
        session.add(ImportPreview(token=token,owner_id=user.id,created_at=datetime.now(timezone.utc),expires_at=datetime.now(timezone.utc)+timedelta(minutes=20),diff_payload=json.dumps(meta),state_payload=json.dumps(state,ensure_ascii=False)))
        session.commit()
        return {"token":token,"errors":checked["errors"],"warnings":checked["warnings"],"changes":[f"Update code in {req.name}"],"object":item}

    @app.post("/api/v2/code/apply")
    def apply(req:ApplyCode,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        preview=session.get(ImportPreview,req.token); now=datetime.now(timezone.utc)
        expiry=preview.expires_at.replace(tzinfo=timezone.utc) if preview and preview.expires_at.tzinfo is None else (preview.expires_at if preview else now)
        if not preview or preview.owner_id!=user.id or expiry<=now: raise HTTPException(404,"Code preview not found or expired")
        meta=json.loads(preview.diff_payload)
        if meta.get("kind")!="v2-code-edit": raise HTTPException(404,"Code preview not found or expired")
        current=project(session)
        if current.get("revision",0)!=meta.get("base_revision"): raise HTTPException(409,"Project changed after preview; create a new preview")
        state=json.loads(preview.state_payload)
        try:
            objects=v2_plc.build_program(state); xml=v2_plc.serialize_program(objects,state,list(objects)); checked=v2_plc_validation.validate_xml(xml,state,objects)
            oldobj=v2_plc.build_program(current); oldxml=v2_plc.serialize_program(oldobj,current,list(oldobj)); baseline=set(v2_plc_validation.validate_xml(oldxml,current,oldobj)["errors"])
            if [e for e in checked["errors"] if e not in baseline]: raise HTTPException(400,"Code is invalid")
            saved=v2_store.save_project(session,state,username=getattr(user,"username","admin"),expected_revision=meta["base_revision"])
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        session.delete(preview);session.commit()
        return {"revision":saved.get("revision"),"object":next((x for x in _objects(saved) if x["name"]==meta["name"]),None)}
