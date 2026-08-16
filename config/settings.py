"""
Centralized project configuration.

Reads from environment variables (populated via .env locally, or real env vars
in production/CI). Import `settings` anywhere in the codebase instead of calling
os.environ directly — keeps config in one auditable place.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    environment: str = "development"
    log_level: str = "INFO"

    # MLflow
    mlflow_tracking_uri: str = "http://localhost:5000"
    mlflow_artifact_root: str = "./mlruns"
    mlflow_model_name: str = "my-model"
    mlflow_model_stage: str = "Production"

    # Database
    database_url: str = "postgresql://mlflow:changeme@localhost:5432/mlflow"

    # Serving app
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # AWS (only required for deploy)
    aws_region: str = "us-east-1"
    aws_account_id: str = ""
    ecr_repository_name: str = ""
    ecs_cluster_name: str = ""
    ecs_service_name: str = ""


settings = Settings()
