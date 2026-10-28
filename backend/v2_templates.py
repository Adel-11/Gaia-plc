"""Download the official, unmodified Excel templates without changing a project."""
import io
import zipfile

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, Response

from backend import v2_engine as engine


TEMPLATES = {
    "TEMPLATE_TYPE.xlsx": "Equipment type, DUT members and FB code",
    "TEMPLATE_PARAMETERS_PA.xlsx": "Instance PA parameter values",
    "TEMPLATE_IO_PHYSICAL.xlsx": "Physical input and output channels",
    "TEMPLATE_MODBUS_TCP.xlsx": "Modbus TCP clients and address tables",
    "TEMPLATE_MODBUS_RTU.xlsx": "Modbus RTU slaves and channels",
    "TEMPLATE_EXCHANGES_EXC.xlsx": "Exchange functions and instance calls",
}


def register_template_routes(app, current_user):
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
    def download_templates(user=Depends(current_user)):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in TEMPLATES:
                archive.writestr(name, template_path(name).read_bytes())
        return Response(stream.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="gaia-excel-templates.zip"',
            "Cache-Control": "no-store",
        })

    @app.get("/api/v2/templates/{filename}")
    def download_template(filename: str, user=Depends(current_user)):
        return FileResponse(template_path(filename), filename=filename,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Cache-Control": "no-store"})
