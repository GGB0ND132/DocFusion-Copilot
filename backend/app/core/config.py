from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# 自动加载 backend/.env（存在即加载，不存在不报错）
_backend_dir = Path(__file__).resolve().parents[2]
load_dotenv(_backend_dir / ".env", override=False)


def _workspace_root() -> Path:
    """根据当前文件位置解析仓库根目录。
    Resolve the repository root from the current file location.
    """
    return Path(__file__).resolve().parents[3]


def _env_flag(name: str, default: bool = False) -> bool:
    """将环境变量解析为布尔值。
    Parse an environment variable into a boolean value.
    """
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """把逗号分隔的环境变量解析为字符串元组。
    Parse a comma-separated environment variable into a tuple of strings.
    """
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    items = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return items or default


@dataclass(slots=True)
class Settings:
    """MVP 后端运行时配置集合。
    Central runtime settings for the MVP backend.
    """

    project_name: str = "DocFusion Copilot Backend"
    api_prefix: str = "/api/v1"
    workspace_root: Path = field(default_factory=_workspace_root)
    max_workers: int = 4
    database_url: str = field(
        default_factory=lambda: (
            os.getenv("DOCFUSION_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or "postgresql+psycopg://postgres:postgres@localhost:5432/docfusion_copilot"
        )
    )
    database_echo: bool = field(
        default_factory=lambda: (
            os.getenv("DOCFUSION_DATABASE_ECHO", "").strip().lower() in {"1", "true", "yes", "on"}
        )
    )
    openai_api_key: str = field(default_factory=lambda: os.getenv("DOCFUSION_OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.getenv("DOCFUSION_OPENAI_BASE_URL", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("DOCFUSION_OPENAI_MODEL", "gpt-4o-mini"))
    openai_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("DOCFUSION_OPENAI_TIMEOUT_SECONDS", "45"))
    )
    cors_allow_origins_raw: tuple[str, ...] = field(
        default_factory=lambda: _split_csv_env(
            "DOCFUSION_CORS_ALLOW_ORIGINS",
            (
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:8080",
                "http://127.0.0.1:8080",
            ),
        )
    )
    cors_allow_methods_raw: tuple[str, ...] = field(
        default_factory=lambda: _split_csv_env("DOCFUSION_CORS_ALLOW_METHODS", ("*",))
    )
    cors_allow_headers_raw: tuple[str, ...] = field(
        default_factory=lambda: _split_csv_env("DOCFUSION_CORS_ALLOW_HEADERS", ("*",))
    )
    cors_expose_headers_raw: tuple[str, ...] = field(
        default_factory=lambda: _split_csv_env(
            "DOCFUSION_CORS_EXPOSE_HEADERS",
            ("Content-Disposition", "Content-Type"),
        )
    )
    cors_allow_credentials: bool = field(
        default_factory=lambda: _env_flag("DOCFUSION_CORS_ALLOW_CREDENTIALS", default=False)
    )

    @property
    def backend_dir(self) -> Path:
        """返回仓库中的 backend 目录。
        Return the backend directory inside the repository.
        """
        return self.workspace_root / "backend"

    @property
    def data_dir(self) -> Path:
        """返回项目共享的 data 目录。
        Return the shared project data directory.
        """
        return self.workspace_root / "data"

    @property
    def storage_dir(self) -> Path:
        """返回后端运行时产物可写入的存储目录。
        Return the writable runtime storage directory for backend artifacts.
        """
        return self.backend_dir / "storage"

    @property
    def uploads_dir(self) -> Path:
        """返回用于保存上传源文档的目录。
        Return the directory used to store uploaded source documents.
        """
        return self.storage_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        """返回用于保存生成模板结果的目录。
        Return the directory used to store generated template outputs.
        """
        return self.storage_dir / "outputs"

    @property
    def temp_dir(self) -> Path:
        """返回用于暂存模板文件的目录。
        Return the directory used for transient template files.
        """
        return self.storage_dir / "temp"

    @property
    def supported_document_extensions(self) -> tuple[str, ...]:
        """返回允许上传的源文档扩展名列表。
        Return allowed extensions for source documents.
        """
        return (".docx", ".md", ".txt", ".xlsx", ".pdf")

    @property
    def supported_template_extensions(self) -> tuple[str, ...]:
        """返回允许回填的模板扩展名列表。
        Return allowed extensions for fillable templates.
        """
        return (".xlsx", ".docx")

    @property
    def cors_allow_origins(self) -> list[str]:
        """返回允许跨域访问的前端源列表。
        Return the list of frontend origins allowed for CORS.
        """
        return list(self.cors_allow_origins_raw)

    @property
    def cors_allow_methods(self) -> list[str]:
        """返回允许跨域请求使用的 HTTP 方法列表。
        Return the HTTP methods allowed for CORS requests.
        """
        return list(self.cors_allow_methods_raw)

    @property
    def cors_allow_headers(self) -> list[str]:
        """返回允许跨域请求携带的请求头列表。
        Return the request headers allowed for CORS requests.
        """
        return list(self.cors_allow_headers_raw)

    @property
    def cors_expose_headers(self) -> list[str]:
        """杩斿洖鍏佽鍓嶇璇诲彇鐨勫搷搴斿ご鍒楄〃銆?
        Return the response headers exposed to the frontend via CORS.
        """
        return list(self.cors_expose_headers_raw)

    def ensure_directories(self) -> None:
        """确保后端运行所需目录全部存在。
        Create all backend runtime directories if they do not exist yet.
        """
        for directory in (self.backend_dir, self.storage_dir, self.uploads_dir, self.outputs_dir, self.temp_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回带目录初始化的缓存配置对象。
    Return a cached settings object with directories prepared.
    """
    settings = Settings()
    settings.ensure_directories()
    return settings
