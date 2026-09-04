from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"

    input_price: float
    output_price: float

    tavily_api_key: str
    tavily_timeout: float = 10.0

    valkey_host: str = "localhost"
    valkey_port: int = 6379

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()