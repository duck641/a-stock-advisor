from config import config

print(f"provider = '{config.provider}'")
print(f"model    = '{config.model}'")
print(f"api_key  = '{config.api_key[:8]}...' ({len(config.api_key)} chars)")
print(f"base_url = '{config.base_url}'")
print(f"detected = '{config.provider_name}'")
print(f"context  = {config.context_window}")
