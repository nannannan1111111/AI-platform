"""Python SaaS Web UI 的 FastAPI 路由。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_STATIC_ROOT = Path(__file__).with_name("static")
_LEGACY_ASSET_ROOT = Path(__file__).resolve().parents[3] / "static"


def mount_web_ui(app: FastAPI) -> None:
    """挂载账户、钱包与管理员页面使用的静态 Web 外壳。"""
    app.mount("/web-assets", StaticFiles(directory=_STATIC_ROOT), name="web-assets")
    for asset_directory in ("css", "images", "js", "vendor"):
        app.mount(
            f"/static/{asset_directory}",
            StaticFiles(directory=_LEGACY_ASSET_ROOT / asset_directory),
            name=f"legacy-asset-{asset_directory}",
        )

    @app.get("/", include_in_schema=False)
    @app.get("/login", include_in_schema=False)
    @app.get("/register", include_in_schema=False)
    @app.get("/verify-email", include_in_schema=False)
    @app.get("/workspace/account", include_in_schema=False)
    @app.get("/workspace/wallet", include_in_schema=False)
    @app.get("/workspace/models", include_in_schema=False)
    @app.get("/workspace/images", include_in_schema=False)
    @app.get("/workspace/inpainting", include_in_schema=False)
    @app.get("/workspace/canvases", include_in_schema=False)
    @app.get("/workspace/assets", include_in_schema=False)
    @app.get("/workspace/llm-settings", include_in_schema=False)
    @app.get("/workspace/generations", include_in_schema=False)
    @app.get("/admin/runninghub-capabilities", include_in_schema=False)
    @app.get("/admin/model-routing", include_in_schema=False)
    @app.get("/admin/provider-costs", include_in_schema=False)
    @app.get("/admin/model-prices", include_in_schema=False)
    @app.get("/admin/recharge-packages", include_in_schema=False)
    @app.get("/admin/payment-settings", include_in_schema=False)
    @app.get("/admin/users", include_in_schema=False)
    @app.get("/admin/generation-tasks", include_in_schema=False)
    @app.get("/admin/storage-allowance", include_in_schema=False)
    @app.get("/admin/generation-capacity", include_in_schema=False)
    @app.get("/admin/email-settings", include_in_schema=False)
    @app.get("/admin/platform-content", include_in_schema=False)
    def workspace_page() -> FileResponse:
        return FileResponse(_STATIC_ROOT / "index.html")

    @app.get("/workspace/canvases/{canvas_id}/classic", include_in_schema=False)
    def classic_canvas_page(canvas_id: str) -> FileResponse:
        """Serve the authenticated classic infinite-canvas editor shell."""
        return FileResponse(_STATIC_ROOT / "canvas.html")

    @app.get("/workspace/canvases/{canvas_id}/smart", include_in_schema=False)
    def smart_canvas_page(canvas_id: str) -> FileResponse:
        """Serve the authenticated smart infinite-canvas editor shell."""
        return FileResponse(_STATIC_ROOT / "smart-canvas.html")
