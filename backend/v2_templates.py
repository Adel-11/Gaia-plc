"""Download the official Excel templates for the active PLC profile."""
from functools import lru_cache
import io
import zipfile

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, Response

from backend import v2_engine as engine
from backend import v2_store as store
from backend.v2_hmi import clean_template, is_hmi


TEMPLATES = {
    "TEMPLATE_TYPE.xlsx": "Equipment type, DUT members and FB code",
    "TEMPLATE_PARAMETERS_PA.xlsx": "Instance PA parameter values",
    "TEMPLATE_IO_PHYSICAL.xlsx": "Physical input and output channels",
    "TEMPLATE_MODBUS_TCP.xlsx": "Modbus TCP clients and address tables",
    "TEMPLATE_MODBUS_RTU.xlsx": "Modbus RTU slaves and channels",
    "TEMPLATE_EXCHANGES_EXC.xlsx": "Exchange functions and instance calls",
}


@lru_cache(maxsize=len(TEMPLATES))
def _hmi_template(path):
    workbook = clean_template(engine._read_wb(path.read_bytes(), data_only=False))
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def register_template_routes(app, get_session, current_user):
    def template_path(filename):
        if filename not in TEMPLATES:
            raise HTTPException(404, "Excel template not found")
        path = engine.DEFAULT_PACK / "02_excel" / "templates" / filename
        if not path.is_file():
            raise HTTPException(503, "Excel template is unavailable")
        return path

    @app.get("/api/v2/templates")
    def list_templates(user=Depends(current_user)):
        return {"templates": [{"filename": name, "description": description}
                              for name, description in TEMPLATES.items()]}

    @app.get("/api/v2/templates.zip")
    def download_templates(session=Depends(get_session), user=Depends(current_user)):
        panelview = is_hmi(store.load_project(session))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in TEMPLATES:
                path = template_path(name)
                archive.writestr(name, _hmi_template(path) if panelview else path.read_bytes())
        return Response(stream.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="gaia-excel-templates.zip"',
            "Cache-Control": "no-store",
        })

    @app.get("/api/v2/templates/{filename}")
    def download_template(filename: str, session=Depends(get_session), user=Depends(current_user)):
        path = template_path(filename)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if is_hmi(store.load_project(session)):
            return Response(_hmi_template(path), media_type=mime, headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            })
        return FileResponse(path, filename=filename, media_type=mime,
                            headers={"Cache-Control": "no-store"})
