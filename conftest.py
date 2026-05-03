import os

# Debe ejecutarse antes de que cualquier módulo del proyecto se importe.
# agent/config.py llama Settings() al cargarse — pydantic_settings lee os.environ
# con mayor prioridad que el archivo .env, por eso sobreescribimos aquí.

os.environ["GROQ_API_KEY"]            = "gsk_test_placeholder"
os.environ["KOMMO_SUBDOMAIN"]         = "test.kommo.com"
os.environ["KOMMO_ACCESS_TOKEN"]      = "test_access_token"
os.environ["KOMMO_WEBHOOK_SECRET"]    = "test_webhook_secret_xxxxx"
os.environ["INVENTORY_SHEET_CSV_URL"] = "https://example.com/fake.csv"
os.environ["AGENT_MODE"]              = "kommo"
os.environ["DATABASE_URL"]            = "sqlite+aiosqlite:///:memory:"
